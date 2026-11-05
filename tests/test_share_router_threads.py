"""Threads Share Router regression tests: identity, eligibility, extraction and fallback.

Public Threads pages can contain unrelated recommended videos. Never infer video
from a Threads URL alone and never forward media from a different post.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from stoney_verify import share_router_media_resolver as media
from stoney_verify import share_router_private_downloads as downloads
from stoney_verify import share_router_runtime as router


@pytest.fixture(autouse=True)
def reset_media_cache():
    media.reset_media_resolver_state_for_tests()
    yield
    media.reset_media_resolver_state_for_tests()


@pytest.mark.parametrize("url", [
    "https://www.threads.com/@creator/post/POST123",
    "https://threads.net/@creator/post/POST123?xmt=1",
    "https://threads.com/@creator/post/POST123/media",
    "https://www.threads.com/t/Short123",
    "https://threads.net/share/Short123/",
])
def test_both_threads_domains_and_post_share_shapes(url):
    assert media.provider_for_url(url) == "threads"
    assert media.is_threads_post_url(url)
    assert downloads._valid_request_url(url)


@pytest.mark.parametrize("url", [
    "https://threads.com/@creator",
    "https://threads.com/search?q=video",
    "https://threads.com/",
    "https://threads.com.evil.example/@creator/post/POST123",
    "https://example.org/share/POST123",
])
def test_non_post_urls_are_not_threads_video_posts(url):
    assert not media.is_threads_post_url(url)


def test_threads_identity_is_shared_across_domains_not_profiles():
    a = media.canonicalize_media_url("https://threads.net/@creator/post/POST123")
    b = media.canonicalize_media_url("https://www.threads.com/@creator/post/POST123")
    assert a == b
    assert media.media_url_identity(a) == "threads:POST123"
    assert media.media_url_identity(a) == media.media_url_identity(b)
    assert media.provider_for_url("https://threads.com/@creator") == "threads"


def test_threads_shortlinks_require_verified_target_metadata():
    assert media._short_share_provider("https://threads.net/t/tokenABC") == "threads"
    assert media._short_share_provider("https://threads.com/share/tokenABC/") == "threads"
    payload = b'<meta content="https://threads.net/@creator/post/POST123" property="og:url">'
    assert media._threads_post_from_html(payload) == (
        "https://www.threads.com/@creator/post/POST123"
    )
    assert media._threads_post_from_html(
        b'<meta property="og:url" content="https://threads.com/@creator">'
    ) == ""
    assert media._threads_post_from_html(
        b'<meta property="og:url" content="https://threads.com.evil.example/@creator/post/POST123">'
    ) == ""


def test_public_threads_shortlink_uses_bounded_crawler_metadata(monkeypatch):
    class Content:
        async def read(self, limit):
            assert limit == 131073
            return b'<meta property="og:url" content="https://threads.com/@creator/post/REALCODE">'

    class Response:
        content = Content()
        def release(self):
            pass

    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return None

    async def fake_public_get(_session, url, **kwargs):
        assert url == "https://threads.net/share/shr123/"
        assert "Googlebot" in kwargs["headers"]["User-Agent"]
        assert kwargs["max_redirects"] == 4
        return Response(), url

    monkeypatch.setattr(media.aiohttp, "ClientSession", lambda **kwargs: Session())
    monkeypatch.setattr(media, "public_tcp_connector", lambda **kwargs: None)
    monkeypatch.setattr(media, "public_get", fake_public_get)
    result = asyncio.run(media._expand_short_share_url(
        "https://threads.net/share/shr123/"
    ))
    assert result == "https://www.threads.com/@creator/post/REALCODE"


def test_videos_only_never_assumes_any_threads_post_contains_video(monkeypatch):
    msg = SimpleNamespace(
        content="https://threads.com/@creator/post/POST123",
        attachments=[],
        embeds=[],
    )
    assert not router.route_content_matches(msg, "videos")
    assert router.route_content_matches(msg, "links")
    assert router.route_content_matches(msg, "everything")

    def video(_url):
        return {"id": "POST123", "title": "Some real video", "formats": [
            {"url": "https://scontent.cdninstagram.com/video.mp4", "ext": "mp4",
             "protocol": "https", "vcodec": "h264", "acodec": "aac",
             "filesize": 1_000_000}
        ]}

    monkeypatch.setattr(media, "_extract_info_sync", video)
    assert asyncio.run(router._threads_video_only_match(msg, max_bytes=20_000_000))

    media.reset_media_resolver_state_for_tests()
    monkeypatch.setattr(media, "_extract_info_sync", lambda _url: media.ExtractionFailure(
        "provider_no_downloadable_formats"
    ))
    assert not asyncio.run(router._threads_video_only_match(msg, max_bytes=20_000_000))


def test_threads_shortlink_expansion_preserves_original_link_and_size_cap(monkeypatch):
    source = "https://threads.net/share/shortABC/"
    post = "https://www.threads.com/@owner/post/REALCODE123"
    calls = []
    async def expand(_url):
        return post
    def extract(url):
        calls.append(url)
        return {"id": "REALCODE123", "formats": [
            {"url": "https://scontent.cdninstagram.com/v.mp4", "ext": "mp4",
             "protocol": "https", "vcodec": "h264", "acodec": "aac",
             "filesize": 3_000_000}
        ]}
    monkeypatch.setattr(media, "_expand_short_share_url", expand)
    monkeypatch.setattr(media, "_extract_info_sync", extract)

    result = asyncio.run(media.resolve_media_url(source, max_bytes=4_000_000))
    assert result.progressive
    assert result.identity == "threads:REALCODE123"
    assert result.source_url == source  # Preserve the member’s original share URL.
    assert calls == [post]

    media.reset_media_resolver_state_for_tests()
    too_large = asyncio.run(media.resolve_media_url(source, max_bytes=1_000_000))
    assert not too_large.progressive


def test_unresolved_threads_shortlink_never_extracts_someone_elses_video(monkeypatch):
    async def no_redirect(_url):
        return ""
    monkeypatch.setattr(media, "_expand_short_share_url", no_redirect)
    def no_extraction(_url):
        raise AssertionError("unverified share URL must never be extracted")
    monkeypatch.setattr(media, "_extract_info_sync", no_extraction)
    outcome = asyncio.run(media.resolve_media_url(
        "https://threads.com/share/noTarget/", max_bytes=20_000_000,
    ))
    assert outcome.delivery == "link"
    assert outcome.reason == "short_share_unresolved"


def _threads_page(posts, *, og_url=None):
    head = f'<meta property="og:url" content="{og_url}">' if og_url else ""
    payload = {"require": [{"thread_items": [{"post": post} for post in posts]}]}
    return (
        f"<html><head>{head}</head><body>"
        f'<script type="application/json" data-sjs>{json.dumps(payload)}</script>'
        "</body></html>"
    )


def _post(code, *, video=False):
    item = {
        "code": code,
        "user": {"username": "creator"},
        "caption": {"text": "Real post"},
        "image_versions2": {"candidates": []},
    }
    if video:
        item["video_versions"] = [{
            "type": 101,
            "url": f"https://scontent.cdninstagram.com/{code}.mp4",
        }]
    return item


def _extract_threads_page(monkeypatch, url, html):
    # These are the actual pinned plugin entry points, with the HTTP request
    # replaced by synthetic Threads HTML. The parser is not mocked.
    from yt_dlp import YoutubeDL
    from yt_dlp_plugins.extractor.threads import ThreadsIE
    ie = ThreadsIE(YoutubeDL({"quiet": True, "skip_download": True}))
    monkeypatch.setattr(ie, "_download_webpage", lambda *_a, **_kw: html)
    return ie._real_extract(url)


def test_pinned_threads_extractor_targets_requested_post_not_recommendation(monkeypatch):
    html = _threads_page([_post("WRONG", video=True), _post("RIGHT", video=True)])
    info = _extract_threads_page(
        monkeypatch, "https://threads.com/@creator/post/RIGHT", html
    )
    assert info["id"] == "RIGHT"
    assert info["formats"][0]["url"].endswith("RIGHT.mp4")


def test_pinned_threads_extractor_rejects_image_only_and_missing_post(monkeypatch):
    from yt_dlp.utils import ExtractorError
    html = _threads_page([_post("WRONG", video=True), _post("IMAGE")])
    with pytest.raises(ExtractorError):
        _extract_threads_page(monkeypatch, "https://threads.com/@creator/post/IMAGE", html)
    with pytest.raises(ExtractorError):
        _extract_threads_page(monkeypatch, "https://threads.com/@creator/post/MISSING", html)


def test_pinned_threads_extractor_accepts_verified_short_share_og_url(monkeypatch):
    html = _threads_page(
        [_post("RIGHT", video=True), _post("WRONG", video=True)],
        og_url="https://threads.com/@creator/post/RIGHT",
    )
    info = _extract_threads_page(
        monkeypatch, "https://threads.com/share/something/", html
    )
    assert info["id"] == "RIGHT"
    assert info["formats"][0]["url"].endswith("RIGHT.mp4")
