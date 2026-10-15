#!/usr/bin/env python3
"""Read-only anonymous Cinema origin probe. No cookies, tokens or user data.

This proves whether the public site proxy routes requests to the API. It does
NOT prove an authenticated Cinema Home request or playback works. Use the
mobile/Discord signed-link canary separately after this passes.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Probe:
    name: str
    path: str
    status: int
    content_type: str
    body: bytes
    redirected_to: str = ""


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validated_origin(raw: str) -> str:
    value = str(raw or "").strip().rstrip("/")
    p = urlsplit(value)
    host = str(p.hostname or "").lower()
    if (
        p.scheme != "https" or not host or p.username or p.password
        or p.path or p.query or p.fragment or p.port not in (None, 443)
        or any(ch.isspace() for ch in value)
    ):
        raise ValueError("Origin must be a public HTTPS host with no path, port, query, or credentials.")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Internal hosts cannot be probed.")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not addr.is_global:
            raise ValueError("Private or local IP addresses cannot be probed.")
    return value


def parse_guild_id(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{15,22}", text) or int(text) <= 0:
        raise ValueError("Provide a Discord guild snowflake (15-22 digits).")
    return text


def classify(probe: Probe) -> tuple[bool, str]:
    content_type = probe.content_type.split(";", 1)[0].strip().lower()
    if probe.status in (301, 302, 303, 307, 308):
        return False, "unexpected HTTP redirect; inspect proxy and OAuth routing"
    if probe.name == "health":
        if probe.status != 200 or content_type != "application/json":
            return False, "health is not JSON HTTP 200"
        try:
            data = json.loads(probe.body)
        except (ValueError, UnicodeDecodeError):
            return False, "health payload is not valid JSON"
        if not isinstance(data, dict) or data.get("ok") is not True or data.get("service") != "dank_torrent_media":
            return False, "health route does not identify the Dank Cinema media backend"
        return True, "correct Cinema media backend"
    if probe.name == "auth-debug":
        if probe.status != 200 or content_type != "application/json":
            return False, "Cinema API auth-debug route did not return JSON HTTP 200"
        try:
            data = json.loads(probe.body)
        except (ValueError, UnicodeDecodeError):
            return False, "Cinema API auth-debug response is not valid JSON"
        if not isinstance(data, dict) or data.get("contract") != "signed-session-v8-snowflake-safe":
            return False, "Cinema API auth contract does not match deployed code"
        return True, "Cinema API route reached, JSON contract valid"
    if probe.name == "home-anonymous":
        if probe.status == 401:
            if content_type != "text/plain" or b"Sign in to Dank Cinema again." not in probe.body:
                return False, "anonymous HTTP 401 did not match Cinema server auth error"
            return True, "Cinema API denied anonymous access as expected; authenticated Home NOT tested"
        if probe.status == 200 and "html" in content_type:
            return False, "HTTP 200 HTML instead of Home API response; proxy/site fallback suspected"
        if probe.status == 200:
            return False, "Home unexpectedly returned HTTP 200 to an anonymous client"
        return False, "unexpected anonymous Home status; expected 401"
    raise ValueError(f"Unknown probe type: {probe.name}")


def _fetch(name: str, origin: str, path: str) -> Probe:
    req = urllib.request.Request(
        origin + path,
        headers={
            "Accept": "application/json",
            "User-Agent": "DankShield-Cinema-ReadOnly-Route-Probe/1",
            "Cache-Control": "no-cache",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirects())
    try:
        response = opener.open(req, timeout=12)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        status = int(response.status)
        headers = response.headers
        body = response.read(8192)
    return Probe(
        name=name,
        path=path,
        status=status,
        content_type=str(headers.get("Content-Type", "")),
        body=body,
        redirected_to=urlsplit(str(headers.get("Location", ""))).path,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True, help="Public HTTPS Cinema origin (no credentials)")
    parser.add_argument("--guild-id", required=True, help="Public Discord guild snowflake (not a token)")
    args = parser.parse_args(argv)
    try:
        origin = validated_origin(args.origin)
        guild_id = parse_guild_id(args.guild_id)
    except ValueError as exc:
        parser.error(str(exc))

    cases = (
        ("health", "/health"),
        ("auth-debug", f"/cinema/{guild_id}/api/auth-debug"),
        ("home-anonymous", f"/cinema/{guild_id}/api/home"),
    )
    print("Cinema public-route diagnostic: GET only, no cookies, no signed links, no writes.")
    passed = True
    for name, path in cases:
        try:
            probe = _fetch(name, origin, path)
            ok, reason = classify(probe)
            print(
                f"{'PASS' if ok else 'FAIL'} {name} status={probe.status} "
                f"type={probe.content_type.split(';', 1)[0] or 'missing'} "
                f"path={path}: {reason}"
            )
            if probe.redirected_to:
                print(f"  redirect path={probe.redirected_to}")
            passed &= ok
        except (OSError, TimeoutError) as exc:
            print(f"FAIL {name} path={path}: transport error ({type(exc).__name__})")
            passed = False
    print(
        "Anonymous public-route checks passed; now perform authenticated Android canary."
        if passed else "Public-route failure: investigate Discloud host/proxy and media listener."
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
