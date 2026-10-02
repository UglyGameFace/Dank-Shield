from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from stoney_verify.share_router_manifest_proxy import (
    ManifestBudgetExceeded,
    ManifestProxy,
    ManifestProxyError,
)


def _proxy() -> ManifestProxy:
    proxy = ManifestProxy(
        "https://cdn.example.com/master.m3u8",
        headers={"Referer": "https://provider.example/watch"},
        max_output_bytes=25_000_000,
    )
    proxy._port = 43123
    return proxy


def test_hls_rewrites_playlists_segments_keys_and_maps_to_loopback() -> None:
    proxy = _proxy()
    source = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1200000
variant/720.m3u8
#EXT-X-KEY:METHOD=AES-128,URI="https://keys.example.com/key.bin"
#EXT-X-MAP:URI="init.mp4"
#EXTINF:6,
segment-1.ts
"""
    rewritten = proxy._rewrite_hls(
        source,
        base_url="https://cdn.example.com/path/master.m3u8",
        headers=proxy.headers,
    )

    assert "https://cdn.example.com" not in rewritten
    assert "https://keys.example.com" not in rewritten
    assert rewritten.count("http://127.0.0.1:43123/") == 4
    proxy._assert_rewrite_is_local_only(rewritten)


def test_hls_rejects_private_nested_target() -> None:
    proxy = _proxy()
    source = """#EXTM3U
#EXTINF:6,
http://127.0.0.1/private.ts
"""
    with pytest.raises(ManifestProxyError):
        proxy._rewrite_hls(
            source,
            base_url="https://cdn.example.com/master.m3u8",
            headers=proxy.headers,
        )


def test_rewrite_guard_rejects_non_local_network_scheme() -> None:
    proxy = _proxy()
    proxy._assert_rewrite_is_local_only(
        "http://127.0.0.1:43123/token/r/id"
    )
    with pytest.raises(ManifestProxyError):
        proxy._assert_rewrite_is_local_only(
            "tcp://203.0.113.9:4444"
        )
    with pytest.raises(ManifestProxyError):
        proxy._assert_rewrite_is_local_only(
            "https://cdn.example.com/escaped.ts"
        )


def test_dash_rewrites_baseurl_and_absolute_media_reference() -> None:
    proxy = _proxy()
    source = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
  <Period>
    <AdaptationSet>
      <Representation id="v1">
        <BaseURL>https://video.example.com/path/</BaseURL>
        <SegmentTemplate media="chunk-$Number$.m4s" initialization="init.mp4"/>
        <SegmentURL media="https://alt.example.com/fixed.m4s"/>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""
    rewritten = proxy._rewrite_dash(
        source,
        base_url="https://cdn.example.com/root/manifest.mpd",
        headers=proxy.headers,
    )

    assert "https://video.example.com" not in rewritten
    assert "https://alt.example.com" not in rewritten
    assert "http://127.0.0.1:43123/" in rewritten
    assert "chunk-$Number$.m4s" in rewritten
    proxy._assert_rewrite_is_local_only(rewritten)


def test_dash_rejects_private_baseurl() -> None:
    proxy = _proxy()
    source = """<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
  <Period><BaseURL>http://10.0.0.5/media/</BaseURL></Period>
</MPD>"""
    with pytest.raises(ManifestProxyError):
        proxy._rewrite_dash(
            source,
            base_url="https://cdn.example.com/manifest.mpd",
            headers=proxy.headers,
        )


def test_proxy_binds_loopback_and_uses_secret_entry_path() -> None:
    proxy = ManifestProxy(
        "https://cdn.example.com/master.m3u8",
        max_output_bytes=25_000_000,
    )

    async def scenario():
        await proxy.start()
        try:
            return proxy.entry_url
        finally:
            await proxy.close()

    entry = asyncio.run(scenario())
    assert entry.startswith("http://127.0.0.1:")
    assert f"/{proxy.token}/r/" in entry


def test_proxy_byte_budget_rejects_excess() -> None:
    proxy = _proxy()

    async def scenario():
        await proxy._budget.consume(proxy._budget.limit)
        with pytest.raises(ManifestBudgetExceeded):
            await proxy._budget.consume(1)

    asyncio.run(scenario())


def test_proxy_source_contract_has_no_public_listener_or_shell() -> None:
    source = Path(__file__).resolve().parents[1].joinpath(
        "stoney_verify",
        "share_router_manifest_proxy.py",
    ).read_text(encoding="utf-8")

    assert 'web.TCPSite(self._runner, "127.0.0.1", 0)' in source
    assert "0.0.0.0" not in source
    assert "create_subprocess" not in source
    assert "public_get(" in source
    assert "public_tcp_connector(" in source
    assert "ManifestBudgetExceeded" in source



def test_dash_nested_relative_baseurl_stays_relative_inside_proxy_namespace() -> None:
    proxy = _proxy()
    source = """<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
  <BaseURL>https://cdn.example.com/root/</BaseURL>
  <Period>
    <BaseURL>video/</BaseURL>
    <AdaptationSet>
      <Representation>
        <SegmentTemplate media="chunk-$Number$.m4s" initialization="init.mp4"/>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>"""

    rewritten = proxy._rewrite_dash(
        source,
        base_url="https://cdn.example.com/manifest.mpd",
        headers=proxy.headers,
    )

    assert "https://cdn.example.com" not in rewritten
    assert ">video/<" in rewritten
    assert "http://127.0.0.1:43123/" in rewritten
    proxy._assert_rewrite_is_local_only(rewritten)
