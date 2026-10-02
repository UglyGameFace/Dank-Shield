from __future__ import annotations

"""Ephemeral localhost proxy for safely remuxing HLS/DASH media.

ffmpeg only receives loopback URLs. Every upstream manifest, playlist, segment,
key, init file, redirect, and byte-range request is fetched by Dank Shield
through the canonical public-network safety owner.
"""

import asyncio
import os
import re
import secrets
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import quote, unquote, urljoin, urlsplit

import aiohttp
from aiohttp import web

from stoney_verify.share_router_media_network import (
    UnsafeMediaURL,
    is_safe_media_download_url,
    public_get,
    public_tcp_connector,
    safe_media_headers,
)


_DEFAULT_MANIFEST_MAX_BYTES = 2 * 1024 * 1024
_DEFAULT_PROXY_CHUNK_BYTES = 64 * 1024
_DEFAULT_UPSTREAM_MULTIPLIER = 3
_HLS_URI_ATTR_RE = re.compile(r'URI="([^"]+)"', re.IGNORECASE)
_SCHEME_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\\s\\"'<>]+")
_DASH_URL_ATTRS = {
    "href",
    "initialization",
    "index",
    "media",
    "sourceurl",
}
_MANIFEST_CONTENT_TYPES = (
    "application/dash+xml",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
)


class ManifestProxyError(RuntimeError):
    pass


class ManifestBudgetExceeded(ManifestProxyError):
    pass


@dataclass(frozen=True)
class _ExactTarget:
    url: str
    headers: tuple[tuple[str, str], ...] = ()
    manifest_hint: bool = False


@dataclass(frozen=True)
class _BaseTarget:
    base_url: str
    headers: tuple[tuple[str, str], ...] = ()


class _ByteBudget:
    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self.used = 0
        self._lock = asyncio.Lock()

    async def consume(self, amount: int) -> None:
        delta = max(0, int(amount))
        async with self._lock:
            if self.used + delta > self.limit:
                raise ManifestBudgetExceeded("manifest proxy upstream budget exceeded")
            self.used += delta

    async def remaining(self) -> int:
        async with self._lock:
            return max(0, self.limit - self.used)


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default)) or default).strip())
    except Exception:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def _manifest_max_bytes() -> int:
    return _env_int(
        "DANK_SHARE_ROUTER_MANIFEST_MAX_BYTES",
        _DEFAULT_MANIFEST_MAX_BYTES,
        minimum=64 * 1024,
        maximum=4 * 1024 * 1024,
    )


def _upstream_budget(max_output_bytes: int) -> int:
    multiplier = _env_int(
        "DANK_SHARE_ROUTER_MANIFEST_UPSTREAM_MULTIPLIER",
        _DEFAULT_UPSTREAM_MULTIPLIER,
        minimum=1,
        maximum=4,
    )
    return min(
        max(1, int(max_output_bytes)) * multiplier,
        256 * 1024 * 1024,
    )


def _safe_headers_tuple(
    headers: Mapping[str, Any] | tuple[tuple[str, str], ...] | None,
) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(safe_media_headers(headers).items(), key=lambda item: item[0].lower()))


def _local_name(value: str) -> str:
    return str(value or "").split("}", 1)[-1].lower()


def _looks_like_manifest(url: str, content_type: str, prefix: bytes) -> bool:
    path = str(urlsplit(url).path or "").lower()
    media_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if path.endswith((".m3u8", ".mpd")):
        return True
    if media_type in _MANIFEST_CONTENT_TYPES:
        return True
    stripped = prefix.lstrip()
    return stripped.startswith(b"#EXTM3U") or stripped.startswith(b"<?xml") or b"<MPD" in stripped[:512]


def _hls_reference_is_manifest(value: str) -> bool:
    path = str(urlsplit(value).path or "").lower()
    return path.endswith(".m3u8")


class ManifestProxy:
    def __init__(
        self,
        upstream_url: str,
        *,
        headers: Mapping[str, Any] | tuple[tuple[str, str], ...] | None = None,
        max_output_bytes: int,
    ) -> None:
        if not is_safe_media_download_url(upstream_url):
            raise ManifestProxyError("manifest upstream URL is not safe")
        self.upstream_url = str(upstream_url)
        self.headers = _safe_headers_tuple(headers)
        self.token = secrets.token_urlsafe(24)
        self._budget = _ByteBudget(_upstream_budget(max_output_bytes))
        self._exact: dict[str, _ExactTarget] = {}
        self._bases: dict[str, _BaseTarget] = {}
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._port = 0
        self._closed = False
        self._root_id = self._register_exact(
            self.upstream_url,
            headers=self.headers,
            manifest_hint=True,
        )

    @property
    def entry_url(self) -> str:
        if self._port <= 0:
            raise ManifestProxyError("manifest proxy is not started")
        return self._exact_local_url(self._root_id)

    @property
    def upstream_bytes_used(self) -> int:
        return int(self._budget.used)

    def _next_id(self) -> str:
        return secrets.token_urlsafe(10)

    def _exact_local_url(self, resource_id: str) -> str:
        return f"http://127.0.0.1:{self._port}/{self.token}/r/{resource_id}"

    def _base_local_url(self, resource_id: str) -> str:
        return f"http://127.0.0.1:{self._port}/{self.token}/b/{resource_id}/"

    def _register_exact(
        self,
        url: str,
        *,
        headers: Mapping[str, Any] | tuple[tuple[str, str], ...] | None = None,
        manifest_hint: bool = False,
    ) -> str:
        resolved = str(url)
        if not is_safe_media_download_url(resolved):
            raise ManifestProxyError("manifest referenced an unsafe URL")
        resource_id = self._next_id()
        self._exact[resource_id] = _ExactTarget(
            url=resolved,
            headers=_safe_headers_tuple(headers),
            manifest_hint=bool(manifest_hint),
        )
        return resource_id

    def _register_base(
        self,
        base_url: str,
        *,
        headers: Mapping[str, Any] | tuple[tuple[str, str], ...] | None = None,
    ) -> str:
        resolved = str(base_url)
        if not is_safe_media_download_url(resolved):
            raise ManifestProxyError("manifest referenced an unsafe base URL")
        resource_id = self._next_id()
        self._bases[resource_id] = _BaseTarget(
            base_url=resolved,
            headers=_safe_headers_tuple(headers),
        )
        return resource_id

    def _rewrite_exact_reference(
        self,
        base_url: str,
        reference: str,
        *,
        headers: tuple[tuple[str, str], ...],
        manifest_hint: bool = False,
    ) -> str:
        raw = str(reference or "").strip()
        if not raw:
            raise ManifestProxyError("manifest contained an empty media URL")
        if raw.startswith("data:"):
            return raw
        resolved = urljoin(base_url, raw)
        resource_id = self._register_exact(
            resolved,
            headers=headers,
            manifest_hint=manifest_hint,
        )
        return self._exact_local_url(resource_id)

    def _assert_rewrite_is_local_only(self, text: str) -> None:
        local_prefix = f"http://127.0.0.1:{self._port}/"
        for match in _SCHEME_URL_RE.finditer(text or ""):
            value = match.group(0)
            if value.startswith(local_prefix):
                continue
            raise ManifestProxyError(
                "rewritten manifest retained a non-local network reference"
            )

    def _rewrite_hls(
        self,
        text: str,
        *,
        base_url: str,
        headers: tuple[tuple[str, str], ...],
    ) -> str:
        output: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line

            def replace_uri(match: re.Match[str]) -> str:
                reference = match.group(1)
                local = self._rewrite_exact_reference(
                    base_url,
                    reference,
                    headers=headers,
                    manifest_hint=_hls_reference_is_manifest(reference),
                )
                return f'URI="{local}"'

            if line.startswith("#"):
                line = _HLS_URI_ATTR_RE.sub(replace_uri, line)
            elif line.strip():
                reference = line.strip()
                line = self._rewrite_exact_reference(
                    base_url,
                    reference,
                    headers=headers,
                    manifest_hint=_hls_reference_is_manifest(reference),
                )
            output.append(line)
        return "\n".join(output) + ("\n" if text.endswith("\n") else "")

    def _dynamic_local_reference(
        self,
        base_url: str,
        reference: str,
        *,
        headers: tuple[tuple[str, str], ...],
    ) -> str:
        raw = str(reference or "").strip()
        if not raw:
            return raw
        if raw.startswith("data:"):
            return raw

        absolute = urljoin(base_url, raw)
        parsed = urlsplit(absolute)
        directory = urljoin(absolute, ".")
        tail = str(parsed.path or "").rsplit("/", 1)[-1]
        if parsed.query:
            tail += "?" + parsed.query
        base_id = self._register_base(directory, headers=headers)
        encoded = quote(tail, safe="$:@,;~.-_")
        return f"{self._base_local_url(base_id)}{encoded}"

    def _rewrite_dash(
        self,
        text: str,
        *,
        base_url: str,
        headers: tuple[tuple[str, str], ...],
    ) -> str:
        try:
            root = ET.fromstring(text)
        except Exception as exc:
            raise ManifestProxyError("invalid DASH manifest") from exc

        # Ensure relative references cannot resolve against the localhost
        # manifest-resource path by accident.
        root_base_id = self._register_base(urljoin(base_url, "."), headers=headers)
        base_tag = root.tag.rsplit("}", 1)[0] + "}BaseURL" if "}" in root.tag else "BaseURL"
        injected = ET.Element(base_tag)
        injected.text = self._base_local_url(root_base_id)
        root.insert(0, injected)

        for element in root.iter():
            local = _local_name(element.tag)
            if local == "baseurl" and element is not injected:
                raw = str(element.text or "").strip()
                if not raw:
                    continue
                resolved = urljoin(base_url, raw)
                base_id = self._register_base(resolved, headers=headers)
                element.text = self._base_local_url(base_id)
            elif local == "location":
                raw = str(element.text or "").strip()
                if raw:
                    element.text = self._rewrite_exact_reference(
                        base_url,
                        raw,
                        headers=headers,
                        manifest_hint=True,
                    )

            for key, value in list(element.attrib.items()):
                attr_name = _local_name(key)
                raw = str(value or "").strip()
                if not raw:
                    continue
                if attr_name == "href" and raw.startswith("#"):
                    continue
                if attr_name not in _DASH_URL_ATTRS:
                    continue
                if raw.startswith("data:"):
                    continue
                if raw.startswith(("http://", "https://")):
                    element.attrib[key] = self._dynamic_local_reference(
                        base_url,
                        raw,
                        headers=headers,
                    )

        try:
            return ET.tostring(root, encoding="unicode")
        except Exception as exc:
            raise ManifestProxyError("could not serialize DASH manifest") from exc

    async def start(self) -> "ManifestProxy":
        if self._runner is not None:
            return self

        timeout = aiohttp.ClientTimeout(total=20.0, connect=4.0, sock_read=12.0)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            connector=public_tcp_connector(ttl_dns_cache=30, limit=8),
        )

        app = web.Application(client_max_size=1024)
        app.router.add_route("*", f"/{self.token}/r/{{resource_id}}", self._handle_exact)
        app.router.add_route("*", f"/{self.token}/b/{{resource_id}}/{{tail:.*}}", self._handle_base)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()

        server = getattr(self._site, "_server", None)
        sockets = list(getattr(server, "sockets", ()) or ())
        if not sockets:
            await self.close()
            raise ManifestProxyError("manifest proxy did not bind a loopback socket")
        self._port = int(sockets[0].getsockname()[1])
        return self

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:
                pass
        self._runner = None
        self._site = None
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
        self._session = None
        self._exact.clear()
        self._bases.clear()
        self._port = 0

    async def __aenter__(self) -> "ManifestProxy":
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def _fetch(
        self,
        url: str,
        *,
        headers: tuple[tuple[str, str], ...],
        request: web.Request,
    ) -> tuple[aiohttp.ClientResponse, str]:
        session = self._session
        if session is None:
            raise web.HTTPServiceUnavailable(text="manifest proxy is closed")

        forwarded = dict(headers)
        range_value = str(request.headers.get("Range") or "").strip()
        if range_value:
            forwarded["Range"] = range_value[:200]

        try:
            response, final_url = await public_get(
                session,
                url,
                headers=forwarded,
                max_redirects=4,
                method="HEAD" if request.method == "HEAD" else "GET",
            )
        except UnsafeMediaURL as exc:
            raise web.HTTPForbidden(text="unsafe upstream media target") from exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise web.HTTPBadGateway(text="upstream media request failed") from exc
        return response, final_url

    async def _handle_exact(self, request: web.Request) -> web.StreamResponse:
        target = self._exact.get(str(request.match_info.get("resource_id") or ""))
        if target is None:
            raise web.HTTPNotFound()
        return await self._serve(
            request,
            target.url,
            headers=target.headers,
            manifest_hint=target.manifest_hint,
        )

    async def _handle_base(self, request: web.Request) -> web.StreamResponse:
        target = self._bases.get(str(request.match_info.get("resource_id") or ""))
        if target is None:
            raise web.HTTPNotFound()

        tail = unquote(str(request.match_info.get("tail") or ""))
        if request.query_string:
            tail += "?" + request.query_string
        resolved = urljoin(target.base_url, tail)
        if not is_safe_media_download_url(resolved):
            raise web.HTTPForbidden(text="unsafe upstream media target")
        return await self._serve(
            request,
            resolved,
            headers=target.headers,
            manifest_hint=_hls_reference_is_manifest(resolved),
        )

    async def _read_manifest(
        self,
        response: aiohttp.ClientResponse,
    ) -> bytes:
        declared = int(response.headers.get("Content-Length") or 0)
        limit = _manifest_max_bytes()
        if declared > limit > 0:
            raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=declared)

        body = await response.content.read(limit + 1)
        if len(body) > limit:
            raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=len(body))
        await self._budget.consume(len(body))
        return body

    async def _serve(
        self,
        request: web.Request,
        url: str,
        *,
        headers: tuple[tuple[str, str], ...],
        manifest_hint: bool,
    ) -> web.StreamResponse:
        response, final_url = await self._fetch(
            url,
            headers=headers,
            request=request,
        )
        try:
            content_type = str(response.headers.get("Content-Type") or "")
            if request.method != "HEAD" and (
                manifest_hint
                or _looks_like_manifest(final_url, content_type, b"")
            ):
                body = await self._read_manifest(response)
                if not _looks_like_manifest(final_url, content_type, body[:512]):
                    raise web.HTTPUnsupportedMediaType(text="expected a media manifest")
                try:
                    text = body.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise web.HTTPUnsupportedMediaType(text="manifest is not UTF-8") from exc

                stripped = text.lstrip()
                if stripped.startswith("#EXTM3U"):
                    rewritten = self._rewrite_hls(
                        text,
                        base_url=final_url,
                        headers=headers,
                    )
                    media_type = "application/vnd.apple.mpegurl"
                else:
                    rewritten = self._rewrite_dash(
                        text,
                        base_url=final_url,
                        headers=headers,
                    )
                    media_type = "application/dash+xml"
                self._assert_rewrite_is_local_only(rewritten)
                return web.Response(
                    text=rewritten,
                    content_type=media_type,
                    headers={"Cache-Control": "no-store"},
                )

            if request.method == "HEAD":
                passthrough = {}
                for key in ("Content-Length", "Content-Type", "Accept-Ranges", "Content-Range"):
                    value = response.headers.get(key)
                    if value:
                        passthrough[key] = value
                return web.Response(status=response.status, headers=passthrough)

            declared = int(response.headers.get("Content-Length") or 0)
            remaining = await self._budget.remaining()
            if declared > 0 and declared > remaining:
                raise web.HTTPRequestEntityTooLarge(
                    max_size=self._budget.limit,
                    actual_size=self._budget.used + declared,
                )

            downstream = web.StreamResponse(status=response.status)
            for key in ("Content-Type", "Accept-Ranges", "Content-Range"):
                value = response.headers.get(key)
                if value:
                    downstream.headers[key] = value
            downstream.headers["Cache-Control"] = "no-store"
            await downstream.prepare(request)

            chunk_size = _env_int(
                "DANK_SHARE_ROUTER_MANIFEST_PROXY_CHUNK_BYTES",
                _DEFAULT_PROXY_CHUNK_BYTES,
                minimum=16 * 1024,
                maximum=256 * 1024,
            )
            async for chunk in response.content.iter_chunked(chunk_size):
                if not chunk:
                    continue
                await self._budget.consume(len(chunk))
                await downstream.write(chunk)
            await downstream.write_eof()
            return downstream
        except ManifestBudgetExceeded as exc:
            raise web.HTTPRequestEntityTooLarge(
                max_size=self._budget.limit,
                actual_size=self._budget.used + 1,
            ) from exc
        finally:
            response.release()


__all__ = [
    "ManifestBudgetExceeded",
    "ManifestProxy",
    "ManifestProxyError",
]
