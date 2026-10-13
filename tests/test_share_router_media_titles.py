from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stoney_verify import share_router_media_resolver as resolver
from stoney_verify.share_router_media_titles import (
    clean_video_title, title_from_embeds, title_from_extraction,
    title_from_filename, title_from_matching_attachment,
    title_from_media_url, title_prefix,
)


@pytest.mark.parametrize("provider_url", [
    "https://www.facebook.com/reel/123456789",
    "https://www.youtube.com/watch?v=ABC123",
    "https://www.instagram.com/reel/ABC123/",
    "https://www.tiktok.com/@creator/video/123456789",
    "https://x.com/creator/status/123456789",
    "https://www.reddit.com/r/videos/comments/12345/title/",
    "https://clips.twitch.tv/ABCD",
    "https://vimeo.com/1234567",
    "https://streamable.com/abc123",
    "https://bsky.app/profile/creator/post/123",
])
def test_provider_title_survives_existing_media_resolution(provider_url, monkeypatch):
    resolver.reset_media_resolver_state_for_tests()
    def info(_url):
        return {
            "title": "Real provider title",
            "formats": [{
                "url": "https://cdn.example.com/play.mp4",
                "ext": "mp4", "protocol": "https",
                "vcodec": "h264", "acodec": "aac", "filesize": 1000,
            }],
        }
    monkeypatch.setattr(resolver, "_extract_info_sync", info)
    result = asyncio.run(resolver.resolve_media_url(provider_url, max_bytes=2000000))
    assert result.progressive
    assert result.title == "Real provider title"


def test_extractor_title_precedence_and_playlist_single_entry():
    assert title_from_extraction({
        "title": "Misleading playlist heading",
        "entries": [{"title": "Actual clip", "formats": []}],
    }) == "Actual clip"
    assert title_from_extraction({"title": "TikTok", "fulltitle": "Fun jump shot"}) == "Fun jump shot"
    assert not title_from_extraction({"title": "https://cdn.example/video?token=secret"})


def test_title_is_single_line_bounded_and_mentions_are_not_pingable():
    assert clean_video_title("A\n\tB\u202eC") == "A B C"
    prefix = title_prefix("* New @everyone <@123456> #fun _clip_")
    assert prefix.startswith("🎬 **")
    assert "@everyone" not in prefix
    assert "@\u200beveryone" in prefix
    assert "\\*" in prefix
    assert len(clean_video_title("x" * 1000)) == 160
    assert title_prefix(None) == ""


def test_embed_title_uses_single_post_only():
    source = "https://www.facebook.com/reel/123456789"
    message = SimpleNamespace(
        content=source,
        embeds=[SimpleNamespace(url=source, title="Cat jumps", video=None)],
    )
    assert title_from_embeds(message, source) == "Cat jumps"
    message.content += " https://www.facebook.com/reel/9999"
    assert title_from_embeds(message, source) == ""
    message.content = source
    message.embeds = [SimpleNamespace(url="https://unrelated.example/not-video", title="Wrong", video=None)]
    assert title_from_embeds(message, source) == ""


def test_filename_only_used_when_meaningful_and_not_a_signed_url():
    assert title_from_filename("Best_Match_Highlights.mp4") == "Best Match Highlights"
    assert not title_from_filename("video.mp4")
    assert not title_from_filename("123456789012345.mp4")
    assert not title_from_filename("https://cdn.example.com/secret.mp4?token=private")


def test_direct_media_filename_without_token_leak_or_fabricated_label():
    assert title_from_media_url(
        "https://cdn.example.com/clips/Great_Gameplay_Moments.mp4?token=do-not-show"
    ) == "Great Gameplay Moments"
    assert title_from_media_url("https://cdn.example.com/video.mp4?token=secret") == ""
    assert title_from_filename("share-router-video.mp4") == ""


def test_only_matching_upload_attachment_can_supply_title():
    message = SimpleNamespace(
        attachments=[
            SimpleNamespace(url="https://cdn.discordapp.com/other.mp4",
                            proxy_url=None, filename="Unrelated_Clip.mp4"),
            SimpleNamespace(url="https://cdn.discordapp.com/matching.mp4",
                            proxy_url="https://media.discordapp.net/matching.mp4",
                            filename="My_Gaming_Clip.mp4"),
        ],
    )
    assert title_from_matching_attachment(
        message, "https://media.discordapp.net/matching.mp4"
    ) == "My Gaming Clip"
    assert title_from_matching_attachment(message, "https://cdn.example.com/no.mp4") == ""
