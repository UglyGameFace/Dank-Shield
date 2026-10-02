from __future__ import annotations

import asyncio
import threading
import time

import pytest

from stoney_verify import share_router_media_resolver as media


@pytest.fixture(autouse=True)
def _reset_media_state():
    media.reset_media_resolver_state_for_tests()
    yield
    media.reset_media_resolver_state_for_tests()


@pytest.mark.parametrize(
    ("url", "provider"),
    [
        ("https://x.com/user/status/123", "x"),
        ("https://www.tiktok.com/@user/video/123", "tiktok"),
        ("https://www.instagram.com/reel/ABC123/", "instagram"),
        ("https://youtu.be/abc123", "youtube"),
        ("https://www.reddit.com/r/test/comments/abc123/post/", "reddit"),
        ("https://clips.twitch.tv/FancyClip", "twitch"),
        ("https://www.facebook.com/watch/?v=123", "facebook"),
        ("https://vimeo.com/123456", "vimeo"),
        ("https://streamable.com/abc123", "streamable"),
        ("https://imgur.com/gallery/abc123", "imgur"),
        ("https://example.tumblr.com/post/123/title", "tumblr"),
        ("https://bsky.app/profile/example/post/abc", "bluesky"),
        ("https://www.pinterest.com/pin/123456/", "pinterest"),
        ("https://cdn.example.com/media/video.mp4?token=signed", "direct"),
    ],
)
def test_provider_matrix(url: str, provider: str) -> None:
    assert media.provider_for_url(url) == provider


def test_canonicalization_and_identity_normalize_provider_aliases() -> None:
    x = media.canonicalize_media_url(
        "https://mobile.twitter.com/Test/status/123456?s=20&utm_source=test"
    )
    assert x == "https://x.com/Test/status/123456"
    assert media.media_url_identity(x) == "x-status:123456"

    short = media.canonicalize_media_url("https://youtu.be/abcDEF?si=tracking")
    shorts = media.canonicalize_media_url(
        "https://www.youtube.com/shorts/abcDEF?feature=share"
    )
    assert short == "https://www.youtube.com/watch?v=abcDEF"
    assert shorts == short
    assert media.media_url_identity(short) == "youtube:abcDEF"

    instagram = media.canonicalize_media_url(
        "https://www.instagram.com/reel/ABC123/?utm_source=ig_web_copy_link"
    )
    assert instagram == "https://www.instagram.com/reel/ABC123/"

    direct = media.canonicalize_media_url(
        "https://cdn.example.com/video.mp4?token=abc&expires=123"
    )
    assert direct.endswith("?token=abc&expires=123")


def test_provider_policy_supports_allow_and_deny(monkeypatch) -> None:
    monkeypatch.setenv(
        "DANK_SHARE_ROUTER_MEDIA_PROVIDERS",
        "youtube,tiktok,direct",
    )
    monkeypatch.setenv(
        "DANK_SHARE_ROUTER_MEDIA_BLOCKED_PROVIDERS",
        "tiktok",
    )
    policy = media.media_provider_policy()
    assert policy["youtube"] is True
    assert policy["direct"] is True
    assert policy["tiktok"] is False
    assert policy["x"] is False


@pytest.mark.parametrize(
    ("url", "safe"),
    [
        ("https://cdn.example.com/video.mp4", True),
        ("http://8.8.8.8/video.mp4", True),
        ("http://127.0.0.1/video.mp4", False),
        ("http://10.0.0.1/video.mp4", False),
        ("http://169.254.169.254/latest/meta-data", False),
        ("http://localhost/video.mp4", False),
        ("http://metadata.service.internal/video.mp4", False),
        ("https://cdn.example.com:8443/video.mp4", False),
        ("file:///tmp/video.mp4", False),
    ],
)
def test_safe_media_download_url_rejects_local_private_targets(
    url: str,
    safe: bool,
) -> None:
    assert media.is_safe_media_download_url(url) is safe


def test_progressive_selector_prefers_combined_media_under_byte_cap() -> None:
    info = {
        "formats": [
            {
                "url": "https://cdn.example.com/master.m3u8",
                "protocol": "m3u8_native",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "aac",
                "height": 1080,
            },
            {
                "url": "https://cdn.example.com/combined.mp4",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "aac",
                "height": 720,
                "tbr": 1800,
                "filesize": 8_000_000,
            },
            {
                "url": "https://cdn.example.com/video-only.mp4",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "none",
                "height": 1080,
                "tbr": 3000,
                "filesize": 9_000_000,
            },
        ]
    }
    resolved = media.select_media_resolution(
        info,
        source_url="https://www.tiktok.com/@user/video/123",
        provider="tiktok",
        max_bytes=25_000_000,
    )
    assert resolved.progressive is True
    assert resolved.media_url.endswith("/combined.mp4")
    assert resolved.known_size == 8_000_000


def test_selector_reports_manifest_instead_of_fake_native_success() -> None:
    info = {
        "formats": [
            {
                "url": "https://cdn.example.com/master.m3u8",
                "manifest_url": "https://cdn.example.com/master.m3u8",
                "protocol": "m3u8_native",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "aac",
            }
        ]
    }
    resolved = media.select_media_resolution(
        info,
        source_url="https://www.twitch.tv/videos/123",
        provider="twitch",
        max_bytes=25_000_000,
    )
    assert resolved.manifest is True
    assert resolved.protocol == "m3u8_native"
    assert resolved.reason == "manifest_requires_controlled_transcode_or_player"


def test_selector_rejects_oversize_and_private_progressive_urls() -> None:
    info = {
        "formats": [
            {
                "url": "https://cdn.example.com/too-big.mp4",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "aac",
                "filesize": 30_000_000,
            },
            {
                "url": "http://127.0.0.1/private.mp4",
                "protocol": "http",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "aac",
                "filesize": 5_000_000,
            },
        ]
    }
    resolved = media.select_media_resolution(
        info,
        source_url="https://vimeo.com/123",
        provider="vimeo",
        max_bytes=25_000_000,
    )
    assert resolved.delivery == "link"
    assert resolved.reason == "no_safe_progressive_format"


def test_direct_media_resolves_without_yt_dlp(monkeypatch) -> None:
    def should_not_extract(_url: str):
        raise AssertionError("direct media must not invoke yt-dlp")

    monkeypatch.setattr(media, "_extract_info_sync", should_not_extract)

    async def scenario():
        return await media.resolve_media_url(
            "https://cdn.example.com/video.mp4?token=signed",
            max_bytes=25_000_000,
        )

    resolved = asyncio.run(scenario())
    assert resolved.progressive is True
    assert resolved.provider == "direct"
    assert resolved.media_url.endswith("video.mp4?token=signed")


def test_blocked_provider_falls_back_without_extraction(monkeypatch) -> None:
    monkeypatch.setenv("DANK_SHARE_ROUTER_MEDIA_BLOCKED_PROVIDERS", "youtube")

    def should_not_extract(_url: str):
        raise AssertionError("disabled provider must not invoke yt-dlp")

    monkeypatch.setattr(media, "_extract_info_sync", should_not_extract)

    async def scenario():
        return await media.resolve_media_url(
            "https://youtu.be/abc123",
            max_bytes=25_000_000,
        )

    resolved = asyncio.run(scenario())
    assert resolved.delivery == "link"
    assert resolved.provider == "youtube"
    assert resolved.reason == "provider_disabled"


def test_same_media_resolution_is_coalesced_and_cached(monkeypatch) -> None:
    calls: list[str] = []

    def fake_extract(url: str):
        calls.append(url)
        time.sleep(0.04)
        return {
            "formats": [
                {
                    "url": "https://cdn.example.com/video.mp4",
                    "protocol": "https",
                    "ext": "mp4",
                    "vcodec": "h264",
                    "acodec": "aac",
                    "filesize": 5_000_000,
                }
            ]
        }

    monkeypatch.setattr(media, "_extract_info_sync", fake_extract)

    async def scenario():
        first, second = await asyncio.gather(
            media.resolve_media_url(
                "https://youtu.be/abc123?si=one",
                max_bytes=25_000_000,
            ),
            media.resolve_media_url(
                "https://www.youtube.com/shorts/abc123?feature=share",
                max_bytes=25_000_000,
            ),
        )
        third = await media.resolve_media_url(
            "https://www.youtube.com/watch?v=abc123",
            max_bytes=25_000_000,
        )
        return first, second, third

    first, second, third = asyncio.run(scenario())
    assert first.progressive and second.progressive and third.progressive
    assert calls == ["https://www.youtube.com/watch?v=abc123"]
    snapshot = media.media_resolver_snapshot()
    assert snapshot["providers"]["youtube"]["coalesced"] >= 1
    assert snapshot["providers"]["youtube"]["cache_hits"] >= 1
    assert snapshot["cache_entries"] == 1


def test_extraction_concurrency_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv("DANK_SHARE_ROUTER_MEDIA_EXTRACT_CONCURRENCY", "1")
    media.reset_media_resolver_state_for_tests()

    gate = threading.Lock()
    active = 0
    max_active = 0

    def fake_extract(_url: str):
        nonlocal active, max_active
        with gate:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.03)
            return {
                "formats": [
                    {
                        "url": "https://cdn.example.com/video.mp4",
                        "protocol": "https",
                        "ext": "mp4",
                        "vcodec": "h264",
                        "acodec": "aac",
                        "filesize": 5_000_000,
                    }
                ]
            }
        finally:
            with gate:
                active -= 1

    monkeypatch.setattr(media, "_extract_info_sync", fake_extract)

    async def scenario():
        return await asyncio.gather(
            media.resolve_media_url(
                "https://vimeo.com/111",
                max_bytes=25_000_000,
            ),
            media.resolve_media_url(
                "https://streamable.com/xyz",
                max_bytes=25_000_000,
            ),
        )

    results = asyncio.run(scenario())
    assert all(item.progressive for item in results)
    assert max_active == 1


def test_resolve_first_media_skips_unknown_page_and_uses_direct_media() -> None:
    async def scenario():
        return await media.resolve_first_media(
            "https://unknown.example/page then https://cdn.example.com/final.webm",
            max_bytes=25_000_000,
        )

    resolved = asyncio.run(scenario())
    assert resolved is not None
    assert resolved.progressive is True
    assert resolved.provider == "direct"
    assert resolved.media_url.endswith("/final.webm")



def test_progressive_selector_preserves_only_safe_extractor_headers() -> None:
    info = {
        "http_headers": {
            "User-Agent": "Provider UA",
            "Cookie": "do-not-forward",
        },
        "formats": [
            {
                "url": "https://cdn.example.com/video.mp4",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "aac",
                "filesize": 5_000_000,
                "http_headers": {
                    "Referer": "https://www.tiktok.com/",
                    "Authorization": "do-not-forward",
                },
            }
        ],
    }
    resolved = media.select_media_resolution(
        info,
        source_url="https://www.tiktok.com/@user/video/123",
        provider="tiktok",
        max_bytes=25_000_000,
    )
    assert dict(resolved.request_headers) == {
        "Referer": "https://www.tiktok.com/",
        "User-Agent": "Provider UA",
    }


@pytest.mark.parametrize(
    ("url", "protocol"),
    [
        ("https://cdn.example.com/live/master.m3u8?token=abc", "m3u8_native"),
        ("https://cdn.example.com/video/manifest.mpd?token=abc", "http_dash_segments"),
    ],
)
def test_direct_manifests_are_classified_for_later_controlled_playback(
    url: str,
    protocol: str,
) -> None:
    async def scenario():
        return await media.resolve_media_url(url, max_bytes=25_000_000)

    resolved = asyncio.run(scenario())
    assert resolved.provider == "direct"
    assert resolved.manifest is True
    assert resolved.protocol == protocol
    assert resolved.reason == "manifest_requires_controlled_transcode_or_player"


def test_public_address_helper_rejects_private_and_reserved_ips() -> None:
    assert media.is_public_address("8.8.8.8") is True
    assert media.is_public_address("1.1.1.1") is True
    assert media.is_public_address("127.0.0.1") is False
    assert media.is_public_address("10.0.0.1") is False
    assert media.is_public_address("169.254.169.254") is False
    assert media.is_public_address("::1") is False
