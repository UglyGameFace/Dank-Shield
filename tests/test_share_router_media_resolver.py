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


@pytest.mark.parametrize(
    ("error_text", "expected"),
    [
        ("Sign in to confirm you're not a bot. This text might include https://secret.invalid/?token=123", "provider_bot_verification"),
        ("Private video. Sign in if you've been granted access.", "provider_private_or_restricted"),
        ("This video is age-restricted", "provider_login_required"),
        ("HTTP Error 429: Too Many Requests", "provider_rate_limited"),
        ("HTTP Error 403: Forbidden", "provider_forbidden"),
        ("Video unavailable", "provider_video_unavailable"),
        ("Connection timed out", "provider_extract_timeout"),
        ("Some new provider issue with signed CDN token=SECRET", "extract_failed"),
        ("[facebook] 100: Cannot parse data; https://fbcdn.net/?oh=private", "provider_extractor_incompatible"),
        ("[facebook] 100: No video formats found!", "provider_no_downloadable_formats"),
    ],
)
def test_provider_exception_is_categorized_without_leaking_source(
    error_text: str, expected: str,
) -> None:
    reason = media._classify_extraction_failure(RuntimeError(error_text))
    assert reason == expected
    assert "token" not in reason
    assert "https://" not in reason


def test_facebook_public_link_compatibility_cases() -> None:
    for url in (
        "https://www.facebook.com/reel/1591316522498163",
        "https://www.facebook.com/watch/?v=1591316522498163",
        "https://www.facebook.com/AssembleeNationale/videos/818149149511725/",
        "https://fb.watch/SAMPLE456/",
        "https://www.facebook.com/share/v/SAMPLE456/",
        "https://m.facebook.com/watch/?v=1591316522498163",
    ):
        assert media.provider_for_url(url) == "facebook"


def test_facebook_parser_failure_produces_distinct_safe_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        media, "_extract_info_sync",
        lambda url: media.ExtractionFailure("provider_extractor_incompatible"),
    )
    async def scenario():
        return await media.resolve_media_url(
            "https://www.facebook.com/reel/1591316522498163",
            max_bytes=20 * 1024 * 1024,
        )
    result = asyncio.run(scenario())
    assert result.provider == "facebook"
    assert result.delivery == "link"
    assert result.reason == "provider_extractor_incompatible"
    assert media.media_resolver_snapshot()["providers"]["facebook"]["last_reason"] == (
        "provider_extractor_incompatible"
    )


def test_facebook_progressive_format_retains_only_one_safe_declared_alternate() -> None:
    info = {"formats": [
        {
            "url": "https://video.xx.fbcdn.net/hd.mp4?token=short",
            "protocol": "https", "ext": "mp4", "vcodec": "avc1.64001f",
            "acodec": "mp4a.40.2", "height": 1080, "filesize": 7_000_000,
            "http_headers": {"Referer": "https://www.facebook.com/"},
        },
        {
            "url": "https://video.xx.fbcdn.net/sd.mp4?token=short",
            "protocol": "https", "ext": "mp4", "vcodec": "avc1.64001f",
            "acodec": "mp4a.40.2", "height": 480, "filesize": 2_000_000,
        },
        {
            "url": "https://video.xx.fbcdn.net/third.mp4",
            "protocol": "https", "ext": "mp4", "vcodec": "h264",
            "acodec": "aac", "height": 240, "filesize": 1_000_000,
        },
    ]}
    resolved = media.select_media_resolution(
        info, source_url="https://www.facebook.com/reel/10000001",
        provider="facebook", max_bytes=10_000_000,
    )
    assert resolved.progressive
    assert resolved.media_url.endswith("hd.mp4?token=short")
    assert len(resolved.alternate_progressive) == 1
    assert resolved.alternate_progressive[0][0].endswith("sd.mp4?token=short")
    # Reuse only one same-container alternate for other providers too.
    vimeo = media.select_media_resolution(
        info, source_url="https://vimeo.com/10000001",
        provider="vimeo", max_bytes=10_000_000,
    )
    assert len(vimeo.alternate_progressive) == 1
    assert vimeo.alternate_progressive[0][0].endswith("sd.mp4?token=short")


def test_live_extractor_adapter_returns_safe_failure_not_raw_error(monkeypatch) -> None:
    from types import SimpleNamespace
    import sys

    class YoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, url, *, download):
            assert download is False
            raise RuntimeError("Sign in to confirm you're not a bot. https://cdn.private/video?token=secret")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=YoutubeDL))
    outcome = media._extract_info_sync("https://www.youtube.com/watch?v=tiq6mOEwsow")
    assert isinstance(outcome, media.ExtractionFailure)
    assert outcome.reason == "provider_bot_verification"


def test_youtube_uses_distinct_stable_reason_for_provider_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        media,
        "_extract_info_sync",
        lambda url: media.ExtractionFailure("provider_bot_verification"),
    )
    async def resolve():
        return await media.resolve_media_url(
            "https://www.youtube.com/watch?v=tiq6mOEwsow",
            max_bytes=20 * 1024 * 1024,
        )
    result = asyncio.run(resolve())
    assert result.provider == "youtube"
    assert result.delivery == "link"
    assert result.reason == "provider_bot_verification"
    assert media.media_resolver_snapshot()["providers"]["youtube"]["last_reason"] == (
        "provider_bot_verification"
    )


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


def test_selector_prefers_h264_aac_mp4_over_higher_resolution_hevc_av1() -> None:
    # A larger HEVC/AV1 file may upload successfully while failing inline on
    # some clients; prefer the available widely compatible format.
    for codec in ("hvc1.1.6.L93", "av01.0.08M.08"):
        info = {"formats": [
            {
                "url": "https://cdn.example.com/newer.mp4",
                "protocol": "https", "ext": "mp4",
                "vcodec": codec, "acodec": "aac",
                "height": 2160, "filesize": 9_000_000,
            },
            {
                "url": "https://cdn.example.com/compatible.mp4",
                "protocol": "https", "ext": "mp4",
                "vcodec": "avc1.64001f", "acodec": "mp4a.40.2",
                "height": 720, "filesize": 7_000_000,
            },
        ]}
        result = media.select_media_resolution(
            info, source_url="https://vimeo.com/123", provider="vimeo",
            max_bytes=20_000_000,
        )
        assert result.delivery == "progressive"
        assert result.media_url.endswith("/compatible.mp4")


def test_selector_prefers_known_sized_candidate_over_uncertain_same_codec() -> None:
    info = {"formats": [
        {
            "url": "https://cdn.example.com/unknown.mp4",
            "protocol": "https", "ext": "mp4",
            "vcodec": "h264", "acodec": "aac",
            "height": 1080,
        },
        {
            "url": "https://cdn.example.com/known.mp4",
            "protocol": "https", "ext": "mp4",
            "vcodec": "h264", "acodec": "aac",
            "height": 720, "filesize": 6_000_000,
        },
    ]}
    result = media.select_media_resolution(
        info, source_url="https://streamable.com/abc", provider="streamable",
        max_bytes=20_000_000,
    )
    assert result.media_url.endswith("/known.mp4")
    assert result.known_size == 6_000_000


def test_merge_prefers_h264_aac_pair_over_higher_res_unsupported_codecs() -> None:
    info = {"formats": [
        {
            "url": "https://cdn.example.com/hevc-only.mp4",
            "protocol": "https", "ext": "mp4", "vcodec": "hevc",
            "acodec": "none", "height": 2160, "filesize": 9_000_000,
        },
        {
            "url": "https://cdn.example.com/h264-only.mp4",
            "protocol": "https", "ext": "mp4", "vcodec": "h264",
            "acodec": "none", "height": 720, "filesize": 6_000_000,
        },
        {
            "url": "https://cdn.example.com/opus.webm",
            "protocol": "https", "ext": "webm", "vcodec": "none",
            "acodec": "opus", "abr": 190, "filesize": 1_000_000,
        },
        {
            "url": "https://cdn.example.com/aac.m4a",
            "protocol": "https", "ext": "m4a", "vcodec": "none",
            "acodec": "aac", "abr": 128, "filesize": 1_000_000,
        },
    ]}
    result = media.select_media_resolution(
        info, source_url="https://youtu.be/abc123", provider="youtube",
        max_bytes=20_000_000,
    )
    assert result.delivery == "merge"
    assert result.media_url.endswith("/h264-only.mp4")
    assert result.audio_url.endswith("/aac.m4a")


def test_resolve_first_media_prefers_later_remuxable_over_earlier_link(
    monkeypatch,
) -> None:
    # A social extractor failing must not discard a later legitimate manifest.
    monkeypatch.setattr(media, "_extract_info_sync", lambda _: None)

    async def scenario():
        return await media.resolve_first_media(
            "https://www.youtube.com/watch?v=unknown "
            "https://cdn.example.com/approved.m3u8?token=xyz",
            max_bytes=20_000_000,
        )

    result = asyncio.run(scenario())
    assert result is not None
    assert result.delivery == "manifest"
    assert result.media_url.endswith("approved.m3u8?token=xyz")


def test_resolve_first_media_returns_progressive_even_after_manifest() -> None:
    async def scenario():
        return await media.resolve_first_media(
            "https://cdn.example.com/master.mpd "
            "https://cdn.example.com/final.mp4",
            max_bytes=20_000_000,
        )

    result = asyncio.run(scenario())
    assert result is not None
    assert result.delivery == "progressive"
    assert result.media_url.endswith("final.mp4")


def test_media_url_normalization_rejects_credential_and_nonstandard_port_links() -> None:
    for value in (
        "https://user:password@www.youtube.com/watch?v=abc123",
        "https://www.youtube.com:8080/watch?v=abc123",
        "https://127.0.0.1/video.mp4",
        "https://cdn.example.com:8443/test.mp4",
    ):
        assert not media.provider_for_url(value)
    assert media.canonicalize_media_url("https://cdn.example.com/final.mp4]") == (
        "https://cdn.example.com/final.mp4"
    )


def test_share_router_media_health_is_private_aggregate_only(monkeypatch) -> None:
    from stoney_verify.commands_ext import public_share_router as ui

    monkeypatch.setattr(ui, "media_resolver_snapshot", lambda: {
        "cache_entries": 1,
        "inflight": 0,
        "extract_concurrency": 2,
        "providers": {
            "youtube": {
                "attempts": 3, "progressive": 1, "manifest": 1,
                "merge": 0, "fallback": 1,
                "last_reason": "https://secret.example/video?token=private",
            },
        },
    })
    monkeypatch.setattr(ui, "media_remux_snapshot", lambda: {
        "success": 1, "failed": 2, "oversize": 0, "timeout": 0,
    })
    embed = ui._share_media_health_embed()
    flattened = str(embed.to_dict())
    assert "YouTube" in flattened
    assert "1 classified" not in flattened  # 1 progressive + 1 manifest
    assert "2 classified" in flattened
    assert "secret.example" not in flattened
    assert "token=private" not in flattened
    assert len(embed.fields) <= 25
    assert all(len(f.value) <= 1024 for f in embed.fields)


def test_staff_provider_probe_does_not_echo_signed_url(monkeypatch) -> None:
    from types import SimpleNamespace
    from stoney_verify.commands_ext import public_share_router as ui

    records = []
    class Response:
        done = False

        def is_done(self):
            return self.done

        async def defer(self, **kwargs):
            self.done = True
            records.append("defer")

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=101),
        guild=SimpleNamespace(id=55, premium_tier=1),
        response=Response(),
    )

    async def authorized(*args):
        return True

    async def classify(url, *, max_bytes):
        assert interaction.response.is_done()
        assert "token=hidden" in url
        assert max_bytes > 0
        return media.MediaResolution(
            source_url=url,
            canonical_url=url,
            identity="private",
            provider="youtube",
            delivery="link",
            reason="extract_failed",
        )

    async def private(*args, **kwargs):
        records.append(kwargs)

    monkeypatch.setattr(ui, "_require_manage", authorized)
    monkeypatch.setattr(ui, "resolve_media_url", classify)
    monkeypatch.setattr(ui, "_private", private)

    async def check():
        modal = ui.ShareRouterProviderProbeModal(owner_id=101)
        modal.media_url._value = "https://www.youtube.com/watch?v=abc&token=hidden"
        await modal.on_submit(interaction)

    asyncio.run(check())
    assert records[0] == "defer"
    body = str(records[1]["embed"].to_dict())
    assert "extract_failed" in body
    assert "token=hidden" not in body
    assert "https://www.youtube.com/watch" not in body


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


def test_signed_media_expiration_limits_cache_lifetime(monkeypatch) -> None:
    # A provider returning a signed URL with a near-term expiration should not
    # keep a stale player URL in the successful 15-minute extraction cache.
    expires = int(time.time()) + 20
    calls = []

    def extract(_):
        calls.append(1)
        return {
            "formats": [{
                "url": f"https://cdn.example.com/video.mp4?expire={expires}&token=secret",
                "protocol": "https", "ext": "mp4",
                "vcodec": "h264", "acodec": "aac",
                "filesize": 1_000_000,
            }]
        }

    monkeypatch.setattr(media, "_extract_info_sync", extract)

    async def scenario():
        first = await media.resolve_media_url("https://vimeo.com/91234", max_bytes=20_000_000)
        second = await media.resolve_media_url("https://vimeo.com/91234", max_bytes=20_000_000)
        return first, second

    first, second = asyncio.run(scenario())
    assert first.progressive and second.progressive
    assert len(calls) == 2
    assert media.media_resolver_snapshot()["cache_entries"] == 0

    safe = media.MediaResolution(
        source_url="https://vimeo.com/1",
        canonical_url="https://vimeo.com/1",
        identity="vimeo:1",
        provider="vimeo",
        delivery="progressive",
        media_url=f"https://cdn.example.com/video.mp4?expire={int(time.time()) + 120}&token=private",
    )
    ttl = media._resolution_cache_ttl(safe)
    assert 0 < ttl < 120

    no_expiry = media.MediaResolution(
        source_url=safe.source_url,
        canonical_url=safe.canonical_url,
        identity=safe.identity,
        provider=safe.provider,
        delivery="progressive",
        media_url="https://cdn.example.com/video.mp4",
    )
    assert media._resolution_cache_ttl(no_expiry) == media._POSITIVE_CACHE_TTL_SECONDS


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

    def playable_media(_url: str):
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

    monkeypatch.setattr(media, "_expand_short_share_url", should_not_redirect)
    monkeypatch.setattr(media, "_extract_info_sync", playable_media)
    result = asyncio.run(media.resolve_media_url(permalink, max_bytes=25_000_000))
    assert result.provider == media.provider_for_url(permalink)
    assert result.source_url == permalink
    assert result.progressive
    assert result.identity == media.media_url_identity(result.canonical_url)


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


def test_facebook_failure_probe_gives_specific_actionable_privacy_safe_status():
    from stoney_verify.commands_ext import public_share_router as ui

    assert "current extractor cannot parse" in ui._provider_probe_outcome(
        "facebook", "provider_extractor_incompatible",
    )
    assert "video stream" in ui._provider_probe_outcome(
        "facebook", "provider_no_downloadable_formats",
    )
    assert "does not" in ui._provider_probe_outcome(
        "facebook", "provider_login_required",
    )
    assert "CAPTCHA" in ui._provider_probe_outcome(
        "facebook", "provider_bot_verification",
    )
    assert ui._provider_probe_outcome(
        "youtube", "provider_login_required",
    ) == "provider_login_required"
    for result in (
        ui._provider_probe_outcome("facebook", "provider_extractor_incompatible"),
        ui._provider_probe_outcome("facebook", "provider_no_downloadable_formats"),
    ):
        assert "token=" not in result
        assert "https://" not in result


def test_yt_dlp_upgrade_uses_released_version_not_unpinned_nightly():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pinned = (root / "requirements.txt").read_text(encoding="utf-8")
    assert "yt-dlp[default]==2026.10.21" in pinned
    assert "yt-dlp>=" not in pinned



def test_yt_dlp_adapter_selects_existing_js_runtime_without_network(monkeypatch):
    from types import SimpleNamespace
    import sys

    options_seen = []

    class YoutubeDL:
        def __init__(self, options):
            options_seen.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def extract_info(self, url, *, download):
            assert download is False
            return {"id": "test", "formats": []}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=YoutubeDL))

    monkeypatch.setattr(media.shutil, "which", lambda binary: {
        "deno": "/usr/bin/deno", "node": "/usr/bin/node",
    }.get(binary))
    assert isinstance(media._extract_info_sync("https://youtu.be/Test"), dict)
    assert options_seen[-1]["js_runtimes"] == {"deno": {}}

    monkeypatch.setattr(media.shutil, "which", lambda binary: {
        "node": "/usr/bin/node",
    }.get(binary))
    media._extract_info_sync("https://youtu.be/Test")
    assert options_seen[-1]["js_runtimes"] == {"node": {}}

    monkeypatch.setattr(media.shutil, "which", lambda _binary: None)
    media._extract_info_sync("https://youtu.be/Test")
    assert "js_runtimes" not in options_seen[-1]
    assert "remote_components" not in options_seen[-1]
