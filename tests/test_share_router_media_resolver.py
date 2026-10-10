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
        ("https://pro.tiktok.com/t/ZPLr92WaR/", "tiktok"),
        ("https://vm.tiktok.com/Short1/", "tiktok"),
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



def test_video_only_progressive_format_falls_back_for_later_merge() -> None:
    info = {
        "formats": [
            {
                "url": "https://cdn.example.com/video-only.mp4",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "none",
                "filesize": 5_000_000,
            }
        ]
    }
    resolved = media.select_media_resolution(
        info,
        source_url="https://www.youtube.com/watch?v=abc123",
        provider="youtube",
        max_bytes=25_000_000,
    )
    assert resolved.delivery == "link"
    assert resolved.reason == "separate_audio_video_requires_merge"


def test_fragmented_https_format_is_not_misclassified_as_progressive() -> None:
    info = {
        "formats": [
            {
                "url": "https://cdn.example.com/fragments/base.mp4",
                "manifest_url": "https://cdn.example.com/manifest.mpd",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "aac",
                "fragments": [
                    {"url": "https://cdn.example.com/fragments/1.m4s"},
                    {"url": "https://cdn.example.com/fragments/2.m4s"},
                ],
            }
        ]
    }
    resolved = media.select_media_resolution(
        info,
        source_url="https://vimeo.com/123456",
        provider="vimeo",
        max_bytes=25_000_000,
    )
    assert resolved.delivery == "manifest"
    assert resolved.media_url == "https://cdn.example.com/manifest.mpd"



def test_separate_audio_video_resolves_to_merge_when_no_combined_format() -> None:
    info = {
        "formats": [
            {
                "url": "https://video.example.com/video-only.mp4",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "none",
                "height": 1080,
                "tbr": 3500,
                "filesize": 12_000_000,
                "http_headers": {"Referer": "https://www.youtube.com/"},
            },
            {
                "url": "https://audio.example.com/audio-only.m4a",
                "protocol": "https",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "aac",
                "abr": 128,
                "filesize": 2_000_000,
                "http_headers": {"User-Agent": "Provider UA"},
            },
        ]
    }
    resolved = media.select_media_resolution(
        info,
        source_url="https://www.youtube.com/watch?v=abc123",
        provider="youtube",
        max_bytes=25_000_000,
    )
    assert resolved.delivery == "merge"
    assert resolved.media_url.endswith("video-only.mp4")
    assert resolved.audio_url.endswith("audio-only.m4a")
    assert resolved.known_size == 12_000_000
    assert resolved.audio_known_size == 2_000_000
    assert dict(resolved.request_headers)["Referer"] == "https://www.youtube.com/"
    assert dict(resolved.audio_request_headers)["User-Agent"] == "Provider UA"


def test_combined_progressive_still_beats_separate_merge() -> None:
    info = {
        "formats": [
            {
                "url": "https://cdn.example.com/combined.mp4",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "aac",
                "height": 720,
                "filesize": 8_000_000,
            },
            {
                "url": "https://cdn.example.com/video-only.mp4",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "none",
                "height": 1080,
                "filesize": 10_000_000,
            },
            {
                "url": "https://cdn.example.com/audio.m4a",
                "protocol": "https",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "aac",
                "filesize": 2_000_000,
            },
        ]
    }
    resolved = media.select_media_resolution(
        info,
        source_url="https://vimeo.com/123",
        provider="vimeo",
        max_bytes=25_000_000,
    )
    assert resolved.delivery == "progressive"
    assert resolved.media_url.endswith("combined.mp4")
    assert resolved.audio_url == ""


def test_known_separate_stream_pair_must_fit_upload_budget() -> None:
    info = {
        "formats": [
            {
                "url": "https://cdn.example.com/video-only.mp4",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "none",
                "filesize": 24_000_000,
            },
            {
                "url": "https://cdn.example.com/audio.m4a",
                "protocol": "https",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "aac",
                "filesize": 3_000_000,
            },
        ]
    }
    resolved = media.select_media_resolution(
        info,
        source_url="https://www.youtube.com/watch?v=abc123",
        provider="youtube",
        max_bytes=25_000_000,
    )
    assert resolved.delivery == "link"
    assert resolved.reason == "separate_audio_video_requires_merge"


def test_live_provider_stream_skips_remux_classification() -> None:
    info = {
        "is_live": True,
        "live_status": "is_live",
        "formats": [
            {
                "url": "https://cdn.example.com/live/master.m3u8",
                "protocol": "m3u8_native",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "aac",
            }
        ],
    }
    resolved = media.select_media_resolution(
        info,
        source_url="https://www.twitch.tv/example",
        provider="twitch",
        max_bytes=25_000_000,
    )
    assert resolved.delivery == "link"
    assert resolved.reason == "live_stream_requires_player"



_SHORT_SHARE_CASES = (
    ("https://pro.tiktok.com/t/ZPLr92WaR/", "https://www.tiktok.com/@creator/video/7555555555555555555", "tiktok"),
    ("https://www.tiktok.com/t/ZPLr92WaR/", "https://www.tiktok.com/@creator/video/7555555555555555555", "tiktok"),
    ("https://vm.tiktok.com/Short1/", "https://www.tiktok.com/@creator/video/7555555555555555555", "tiktok"),
    ("https://vt.tiktok.com/Short1/", "https://www.tiktok.com/@creator/video/7555555555555555555", "tiktok"),
    ("https://fb.watch/ABCabc123/", "https://www.facebook.com/watch/?v=123456789", "facebook"),
    ("https://www.facebook.com/share/r/ABCabc123/", "https://www.facebook.com/reel/123456789", "facebook"),
    ("https://redd.it/abc123", "https://www.reddit.com/r/test/comments/abc123/post/", "reddit"),
    ("https://www.reddit.com/r/test/s/ABC123", "https://www.reddit.com/r/test/comments/abc123/post/", "reddit"),
    ("https://www.instagram.com/share/reel/ABC123/", "https://www.instagram.com/reel/XYZ123/", "instagram"),
    ("https://pin.it/ABC123", "https://www.pinterest.com/pin/123456/", "pinterest"),
)


@pytest.mark.parametrize(("short_url", "canonical", "provider"), _SHORT_SHARE_CASES)
def test_short_share_across_supported_providers_uses_canonical_media_identity(
    monkeypatch, short_url: str, canonical: str, provider: str
) -> None:
    assert media._short_share_provider(short_url) == provider
    extracted: list[str] = []
    expanded: list[str] = []

    async def fake_expand(url: str) -> str:
        expanded.append(url)
        return canonical

    def fake_extract(url: str):
        extracted.append(url)
        return {
            "formats": [{
                "url": "https://cdn.example.com/combined.mp4",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "aac",
                "filesize": 6_000_000,
            }]
        }

    monkeypatch.setattr(media, "_expand_short_share_url", fake_expand)
    monkeypatch.setattr(media, "_extract_info_sync", fake_extract)
    result = asyncio.run(media.resolve_media_url(short_url, max_bytes=25_000_000))

    assert expanded == [short_url]
    assert extracted == [canonical]
    assert result.progressive
    assert result.identity == media.media_url_identity(canonical)
    assert result.canonical_url == canonical
    assert result.source_url == short_url
    assert result.provider == provider


@pytest.mark.parametrize(
    "permalink",
    (
        "https://x.com/user/status/123",
        "https://www.tiktok.com/@user/video/123",
        "https://www.instagram.com/reel/ABC123/",
        "https://youtu.be/abc123",
        "https://www.reddit.com/r/test/comments/abc123/post/",
        "https://clips.twitch.tv/FancyClip",
        "https://www.facebook.com/watch/?v=123",
        "https://vimeo.com/123456",
        "https://streamable.com/abc123",
        "https://imgur.com/gallery/abc123",
        "https://example.tumblr.com/post/123/title",
        "https://bsky.app/profile/example/post/abc",
        "https://www.pinterest.com/pin/123456/",
        "https://cdn.example.com/clip.mp4",
    ),
)
def test_existing_supported_permalinks_skip_redirect_resolution(monkeypatch, permalink: str) -> None:
    assert not media._short_share_provider(permalink)

    async def should_not_redirect(_url: str) -> str:
        raise AssertionError("canonical URL must bypass extra shortlink network requests")

    monkeypatch.setattr(media, "_expand_short_share_url", should_not_redirect)
    monkeypatch.setattr(media, "_extract_info_sync", lambda _url: None)
    result = asyncio.run(media.resolve_media_url(permalink, max_bytes=25_000_000))
    assert result.provider == media.provider_for_url(permalink)
    assert result.source_url == permalink


def test_short_share_redirect_uses_safe_network_and_releases_response(monkeypatch) -> None:
    class Response:
        def __init__(self):
            self.released = False

        def release(self):
            self.released = True

    class Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    received: list[str] = []
    response = Response()
    destination = "https://www.tiktok.com/@creator/video/7555555555555555555?utm_source=copy"

    async def fake_public_get(_session, url, **kwargs):
        received.append(url)
        assert kwargs["max_redirects"] == 4
        assert kwargs["headers"]["User-Agent"]
        return response, destination

    monkeypatch.setattr(media.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(media, "public_tcp_connector", lambda **_kwargs: object())
    monkeypatch.setattr(media, "public_get", fake_public_get)

    expanded = asyncio.run(media._expand_short_share_url("https://pro.tiktok.com/t/ZPLr92WaR/"))
    assert expanded == "https://www.tiktok.com/@creator/video/7555555555555555555"
    assert received == ["https://pro.tiktok.com/t/ZPLr92WaR/"]
    assert response.released


@pytest.mark.parametrize(
    "bad_destination",
    (
        "https://unrelated.example.com/anything",
        "http://127.0.0.1/internal",
        "https://www.facebook.com/watch/?v=42",
        "https://pro.tiktok.com/t/AnotherToken/",
        "https://www.tiktok.com/@creator/profile",
    ),
)
def test_short_share_rejects_unsafe_foreign_unresolved_and_nonvideo_targets(
    monkeypatch, bad_destination: str
) -> None:
    class Response:
        released = False

        def release(self):
            self.released = True

    class Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    response = Response()

    async def fake_public_get(*_args, **_kwargs):
        return response, bad_destination

    monkeypatch.setattr(media.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(media, "public_tcp_connector", lambda **_kwargs: object())
    monkeypatch.setattr(media, "public_get", fake_public_get)

    result = asyncio.run(media._expand_short_share_url("https://pro.tiktok.com/t/ZPLr92WaR/"))
    assert result == ""
    assert response.released


@pytest.mark.parametrize(("short_url", "_canonical", "provider"), _SHORT_SHARE_CASES)
def test_unavailable_short_share_redirect_keeps_original_link_fallback(
    monkeypatch, short_url: str, _canonical: str, provider: str
) -> None:
    attempted: list[str] = []

    async def unavailable(_url: str) -> str:
        return ""

    def no_extraction(url: str):
        attempted.append(url)
        return None

    monkeypatch.setattr(media, "_expand_short_share_url", unavailable)
    monkeypatch.setattr(media, "_extract_info_sync", no_extraction)

    result = asyncio.run(media.resolve_media_url(short_url, max_bytes=25_000_000))
    assert attempted == [short_url]
    assert result.delivery == "link"
    assert result.reason == "extract_failed"
    assert result.source_url == short_url
    assert result.provider == provider


def test_disabled_short_share_provider_never_follows_redirect(monkeypatch) -> None:
    monkeypatch.setenv("DANK_SHARE_ROUTER_MEDIA_BLOCKED_PROVIDERS", "reddit")

    async def no_redirect(_url: str):
        raise AssertionError("blocked providers cannot initiate redirect checks")

    monkeypatch.setattr(media, "_expand_short_share_url", no_redirect)
    result = asyncio.run(media.resolve_media_url("https://redd.it/abc123", max_bytes=25_000_000))
    assert result.delivery == "link"
    assert result.reason == "provider_disabled"


def test_supported_short_share_redirect_uses_same_provider_and_preserves_original(monkeypatch) -> None:
    # A Facebook Watch redirect exercises the generic public-redirect boundary,
    # rather than only testing TikTok through its own special handler.
    class Response:
        def __init__(self):
            self.released = False

        def release(self):
            self.released = True

    class Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    response = Response()

    async def fake_public_get(*_args, **_kwargs):
        return response, "https://www.facebook.com/watch/?v=123456789"

    monkeypatch.setattr(media.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(media, "public_tcp_connector", lambda **_kwargs: object())
    monkeypatch.setattr(media, "public_get", fake_public_get)
    assert asyncio.run(media._expand_short_share_url("https://fb.watch/ABCabc123/")) == (
        "https://www.facebook.com/watch/?v=123456789"
    )
    assert response.released
