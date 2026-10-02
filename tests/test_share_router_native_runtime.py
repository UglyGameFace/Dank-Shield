from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from stoney_verify.share_router_resources import (
    SHARE_ROUTER_CATEGORY_KEY,
    is_share_router_category_name,
    is_share_router_design_resource,
    normalize_share_router_name,
    share_source_key,
)
from stoney_verify.services import server_design_apply_service as design_apply_service
from stoney_verify import share_router_runtime as share_runtime
from stoney_verify.share_router_runtime import (
    _canonical_share_url,
    _configured_share_target_id,
    _dedupe_key,
    _first_x_status_url,
    _message_has_inline_video,
    _relay_direct_memes_video,
    _message_share_text,
    _merged_overwrite,
    _select_progressive_video_url,
    _suppress_url_previews,
    _trusted_video_url,
    _url_identity,
    _video_source_urls,
    ensure_share_router_runtime,
    route_for_source,
    source_age_blocker,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")
PUBLIC_UI = (ROOT / "stoney_verify/commands_ext/public_share_router.py").read_text(encoding="utf-8")
COMMUNITY = (ROOT / "stoney_verify/commands_ext/public_community_tools.py").read_text(encoding="utf-8")
DESIGN = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "stoney_verify/share_router_runtime.py").read_text(encoding="utf-8")
LEGACY = (ROOT / "stoney_verify/startup_guards/share_router_guard.py").read_text(encoding="utf-8")


def test_share_router_name_identity_survives_current_dank_design_wrappers() -> None:
    assert normalize_share_router_name("✦──── 🔗 share-routes ────✦") == SHARE_ROUTER_CATEGORY_KEY
    assert normalize_share_router_name("🔗 SHARE ROUTES") == SHARE_ROUTER_CATEGORY_KEY
    assert is_share_router_category_name("✦──── 🔗 share-routes ────✦")
    assert share_source_key("「📣」share-gaming-news") == "share-gaming-news"
    assert share_source_key("「🏷️」share-deals") == "share-deals"
    assert share_source_key("「🤡」share-memes") == "share-memes"
    assert share_source_key("「🕯️」share-announcements") == "share-announcements"


def test_share_router_design_resource_is_structural_not_global_name_matching() -> None:
    router_category = SimpleNamespace(name="✦──── 🔗 share-routes ────✦")
    router_child = SimpleNamespace(name="「🤡」share-memes", category=router_category)
    unrelated_category = SimpleNamespace(name="community")
    unrelated_same_name = SimpleNamespace(name="share-memes", category=unrelated_category)

    assert is_share_router_design_resource(router_category)
    assert is_share_router_design_resource(router_child)
    assert not is_share_router_design_resource(unrelated_same_name)


def test_x_and_twitter_status_aliases_collapse_to_one_identity() -> None:
    x_url = "https://x.com/AIslop_/status/2105553400381505781"
    twitter_url = "https://twitter.com/AIslop_/status/2105553400381505781"

    assert _canonical_share_url(twitter_url) == x_url
    assert _url_identity(x_url) == "x-status:2105553400381505781"
    assert _url_identity(twitter_url) == _url_identity(x_url)
    assert _dedupe_key(x_url) == _dedupe_key(twitter_url)


def test_share_text_does_not_duplicate_x_alias_or_generated_embed_copy() -> None:
    status_id = "2105553400381505781"
    x_url = f"https://x.com/AIslop_/status/{status_id}"
    twitter_url = f"https://twitter.com/AIslop_/status/{status_id}"
    message = SimpleNamespace(
        content=x_url,
        embeds=[
            SimpleNamespace(
                url=twitter_url,
                title="AI Slop (@AIslop_) on X",
                description="Generated provider preview copy",
            )
        ],
        attachments=[],
    )

    routed = _message_share_text(message)

    assert routed == x_url
    assert routed.count(status_id) == 1
    assert "twitter.com" not in routed
    assert "Generated provider preview copy" not in routed
    assert "AI Slop (@AIslop_) on X" not in routed


def test_share_text_collapses_duplicate_aliases_inside_human_content() -> None:
    status_id = "2105553400381505781"
    message = SimpleNamespace(
        content=(
            f"https://x.com/AIslop_/status/{status_id}\n"
            f"https://twitter.com/AIslop_/status/{status_id}"
        ),
        embeds=[],
        attachments=[],
    )

    routed = _message_share_text(message)
    assert routed.count(status_id) == 1
    assert "twitter.com" not in routed


def test_x_status_url_is_recovered_from_canonical_routed_text() -> None:
    routed = (
        "watch this\n"
        "https://x.com/AIslop_/status/2105553400381505781"
    )
    assert _first_x_status_url(routed) == (
        "https://x.com/AIslop_/status/2105553400381505781"
    )
    assert _first_x_status_url("https://example.com/video/123") == ""


def test_progressive_x_video_selection_prefers_combined_http_media() -> None:
    info = {
        "formats": [
            {
                "url": "https://video.twimg.com/ext_tw_video/id/pu/pl/playlist.m3u8",
                "protocol": "m3u8_native",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "aac",
                "height": 1080,
                "tbr": 3500,
            },
            {
                "url": "https://video.twimg.com/ext_tw_video/id/pu/vid/720x1280/a.mp4",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "aac",
                "height": 720,
                "tbr": 1800,
                "filesize": 8_000_000,
            },
            {
                "url": "https://video.twimg.com/ext_tw_video/id/pu/vid/1080x1920/b.mp4",
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

    assert _select_progressive_video_url(info, max_bytes=25_000_000).endswith(
        "/720x1280/a.mp4"
    )


def test_progressive_x_video_selection_rejects_oversize_and_untrusted_media() -> None:
    info = {
        "formats": [
            {
                "url": "https://video.twimg.com/ext_tw_video/id/pu/vid/720x1280/too-big.mp4",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "aac",
                "height": 720,
                "filesize": 30_000_000,
            },
            {
                "url": "https://evil.example/video.mp4",
                "protocol": "https",
                "ext": "mp4",
                "vcodec": "h264",
                "acodec": "aac",
                "height": 720,
                "filesize": 5_000_000,
            },
        ]
    }

    assert _select_progressive_video_url(info, max_bytes=25_000_000) == ""


def test_share_router_video_candidates_prefer_trusted_discord_proxy() -> None:
    message = SimpleNamespace(
        attachments=[],
        embeds=[
            SimpleNamespace(
                video=SimpleNamespace(
                    proxy_url="https://media.discordapp.net/external/token/video.mp4",
                    url="https://video.twimg.com/ext_tw_video/example/pu/vid/720x1280/file.mp4",
                )
            )
        ],
    )

    assert _video_source_urls(message) == [
        "https://media.discordapp.net/external/token/video.mp4",
        "https://video.twimg.com/ext_tw_video/example/pu/vid/720x1280/file.mp4",
    ]
    assert _trusted_video_url(_video_source_urls(message)[0])
    assert _trusted_video_url(_video_source_urls(message)[1])
    assert not _trusted_video_url("https://example.com/user-controlled/video.mp4")


def test_native_video_mode_suppresses_provider_unfurl_but_keeps_link_clickable() -> None:
    url = "https://x.com/AIslop_/status/2105553400381505781"
    suppressed = _suppress_url_previews(f"watch {url}")
    assert suppressed == f"watch <{url}>"


def _direct_memes_fixture():
    router_category = SimpleNamespace(name="🔗 SHARE ROUTES")
    source = SimpleNamespace(id=11, name="share-memes", category=router_category)

    class Channel:
        id = 22

        def __init__(self) -> None:
            self.sent: list[dict[str, object]] = []

        async def send(self, **kwargs):
            self.sent.append(dict(kwargs))
            return SimpleNamespace(id=999)

    channel = Channel()

    class Guild:
        id = 101

        def get_channel(self, channel_id: int):
            return {11: source, 22: channel}.get(channel_id)

    author = SimpleNamespace(id=202, mention="<@202>", bot=False)
    message = SimpleNamespace(
        id=303,
        guild=Guild(),
        channel=channel,
        author=author,
        content="https://x.com/example/status/123456789",
        embeds=[],
        attachments=[],
        webhook_id=None,
        to_reference=lambda **_kwargs: "message-reference",
    )
    routes = [
        {
            "source_channel_id": "11",
            "target_channel_id": "22",
            "enabled": True,
            "delete_source": True,
        }
    ]
    return message, routes, channel


def test_configured_memes_target_comes_from_canonical_share_memes_route() -> None:
    message, routes, _channel = _direct_memes_fixture()
    assert _configured_share_target_id(message.guild, routes, "share-memes") == 22

    unrelated_source = SimpleNamespace(
        id=33,
        name="share-memes",
        category=SimpleNamespace(name="community"),
    )

    class OtherGuild:
        def get_channel(self, channel_id: int):
            return {33: unrelated_source}.get(channel_id)

    assert (
        _configured_share_target_id(
            OtherGuild(),
            [{"source_channel_id": "33", "target_channel_id": "44", "enabled": True}],
            "share-memes",
        )
        == 0
    )


def test_direct_memes_detects_existing_inline_video_without_relay() -> None:
    attachment_message = SimpleNamespace(
        attachments=[SimpleNamespace(content_type="video/mp4", filename="clip.mp4")],
        embeds=[],
    )
    embed_message = SimpleNamespace(
        attachments=[],
        embeds=[
            SimpleNamespace(
                video=SimpleNamespace(
                    proxy_url="https://media.discordapp.net/external/token/video.mp4",
                    url=None,
                )
            )
        ],
    )
    static_preview = SimpleNamespace(
        attachments=[],
        embeds=[SimpleNamespace(video=None, thumbnail=SimpleNamespace(url="https://example.com/x.jpg"))],
    )

    assert _message_has_inline_video(attachment_message)
    assert _message_has_inline_video(embed_message)
    assert not _message_has_inline_video(static_preview)


def test_direct_memes_supported_video_uses_canonical_native_relay(monkeypatch) -> None:
    message, routes, channel = _direct_memes_fixture()
    share_runtime._RECENT_ROUTE_KEYS.clear()

    class FakeFile:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake_file = FakeFile()

    async def fake_prepare(source_message, target, routed_text):
        assert source_message is message
        assert target is channel
        assert routed_text == message.content
        return SimpleNamespace(file=fake_file, size_bytes=12345)

    monkeypatch.setattr(share_runtime, "_prepare_native_video", fake_prepare)

    assert asyncio.run(_relay_direct_memes_video(message, routes)) is True
    assert len(channel.sent) == 1
    payload = channel.sent[0]
    assert payload["file"] is fake_file
    assert payload["reference"] == "message-reference"
    assert payload["mention_author"] is False
    assert "<@202>" in str(payload["content"])
    assert fake_file.closed is True

    key = (101, 22, _dedupe_key(message.content))
    assert key in share_runtime._RECENT_ROUTE_KEYS


def test_direct_memes_failure_and_duplicate_are_non_destructive(monkeypatch) -> None:
    message, routes, channel = _direct_memes_fixture()
    share_runtime._RECENT_ROUTE_KEYS.clear()

    async def no_media(*_args, **_kwargs):
        return None

    monkeypatch.setattr(share_runtime, "_prepare_native_video", no_media)
    assert asyncio.run(_relay_direct_memes_video(message, routes)) is True
    assert channel.sent == []
    assert (101, 22, _dedupe_key(message.content)) not in share_runtime._RECENT_ROUTE_KEYS

    share_runtime._RECENT_ROUTE_KEYS[
        (101, 22, _dedupe_key(message.content))
    ] = share_runtime.time.monotonic()
    assert asyncio.run(_relay_direct_memes_video(message, routes)) is True
    assert channel.sent == []


def test_direct_memes_runtime_reuses_one_listener_and_ignores_webhooks() -> None:
    assert "await _relay_direct_memes_video(message, routes)" in RUNTIME
    assert 'getattr(message, "webhook_id", None) is not None' in RUNTIME
    assert RUNTIME.count('bot.add_listener(route_message, "on_message")') == 1
    assert "_prepare_native_video(message, channel, text)" in RUNTIME


def test_route_lookup_preserves_existing_enabled_semantics() -> None:
    routes = [
        {"source_channel_id": "11", "target_channel_id": "21", "enabled": False},
        {"source_channel_id": "12", "target_channel_id": "22", "enabled": True},
    ]
    assert route_for_source(routes, 11) is None
    assert route_for_source(routes, 12) == routes[1]


class _FakeBot:
    def __init__(self) -> None:
        self.extra_events: dict[str, list[object]] = {}

    def add_listener(self, callback, event_name: str) -> None:
        self.extra_events.setdefault(event_name, []).append(callback)




def test_proxy_overwrite_merge_preserves_unrelated_fields() -> None:
    import discord

    existing = discord.PermissionOverwrite(
        add_reactions=True,
        manage_threads=False,
    )
    merged = _merged_overwrite(existing, view_channel=False, send_messages=True)

    assert merged.add_reactions is True
    assert merged.manage_threads is False
    assert merged.view_channel is False
    assert merged.send_messages is True


def test_age_restricted_proxy_source_is_rejected() -> None:
    source = SimpleNamespace(is_nsfw=lambda: True)
    assert "age-restricted" in source_age_blocker(source)

    normal_source = SimpleNamespace(is_nsfw=lambda: False)
    assert source_age_blocker(normal_source) == ""


def test_runtime_listener_install_is_explicit_and_idempotent() -> None:
    bot = _FakeBot()
    assert ensure_share_router_runtime(bot) is True
    assert ensure_share_router_runtime(bot) is True
    assert len(bot.extra_events.get("on_message", [])) == 1


def test_share_router_is_a_public_core_runtime_without_new_slash_child() -> None:
    assert '("public_share_router", "register_public_share_router"' in REGISTRY
    assert REGISTRY.count('"public_share_router"') >= 2
    assert "app_commands" not in PUBLIC_UI
    assert "register_public_share_router" in PUBLIC_UI
    assert "ensure_share_router_runtime(bot)" in PUBLIC_UI


def test_share_router_is_reachable_from_community_tools_with_dank_browser() -> None:
    assert 'label="Share Router"' in COMMUNITY
    assert "open_share_router" in COMMUNITY
    assert "DankGuildResourceBrowserView" in PUBLIC_UI
    assert "discord.ui.ChannelSelect" not in PUBLIC_UI
    assert 'resource_kinds=("text",)' in PUBLIC_UI


def test_share_router_destination_selection_is_search_first_on_mobile() -> None:
    target_region = PUBLIC_UI.split("async def _open_target_browser", 1)[1].split(
        "async def _open_remove_picker", 1
    )[0]

    assert "class ShareRouterTargetLandingView(_OwnedView)" in PUBLIC_UI
    assert 'label="Search Destination"' in PUBLIC_UI
    assert 'label="Browse Channels"' in PUBLIC_UI
    assert 'label="Back to Proxy Sources"' in PUBLIC_UI
    assert 'label="Close"' in PUBLIC_UI

    assert "DankGuildResourceBrowserView(" in target_region
    assert 'title=f"Browse Destinations for #{source.name}"' in target_region
    assert 'placeholder="Choose a destination channel…"' in target_region
    assert 'home_label="Destination options"' in target_region

    assert "embed=_target_options_embed(source)" in target_region
    assert "view=ShareRouterTargetLandingView(" in target_region
    assert "await _open_target_browser(back_interaction, source)" in target_region

    final_replace = target_region.rsplit("await _replace(", 1)[1]
    assert "view=browser" not in final_replace


def test_share_router_search_first_copy_explains_search_vs_browse() -> None:
    assert "Search is the fastest way to find the real destination on mobile." in PUBLIC_UI
    assert "current/styled name, a saved previous name, Discord ID, or mention" in PUBLIC_UI
    assert "type the channel name instead of scrolling through the server" in PUBLIC_UI
    assert "open the paged destination list when you want to look manually" in PUBLIC_UI


def test_transaction_preflight_skips_reserved_share_router_resources() -> None:
    category = SimpleNamespace(id=101, name="✦──── 🔗 share-routes ────✦", category=None)
    proxy = SimpleNamespace(id=102, name="「🤡」share-memes", category=category)
    ordinary_category = SimpleNamespace(id=201, name="community", category=None)
    ordinary = SimpleNamespace(id=202, name="share-memes", category=ordinary_category)

    class Guild:
        channels = [proxy, ordinary]
        categories = [category, ordinary_category]

        async def fetch_channels(self):
            return [category, proxy, ordinary_category, ordinary]

        def get_channel(self, channel_id: int):
            return {101: category, 102: proxy, 201: ordinary_category, 202: ordinary}.get(channel_id)

    items = [
        {"channel_id": "102", "status": "changed", "before": proxy.name, "after": "styled-proxy"},
        {"channel_id": "202", "status": "changed", "before": ordinary.name, "after": "styled-normal-channel"},
    ]
    ready, skipped, errors = asyncio.run(
        design_apply_service.preflight_plan(Guild(), items, name_limit=100)
    )

    assert errors == []
    assert skipped == 1
    assert [prepared.channel_id for prepared in ready] == [202]


def test_dank_design_excludes_share_router_from_batch_and_exact_edit_paths() -> None:
    assert "from stoney_verify.share_router_resources import is_share_router_design_resource" in DESIGN

    start = DESIGN.index("def _editable_channels")
    end = DESIGN.index("def _can_user_design", start)
    block = DESIGN[start:end]
    assert "is_share_router_design_resource(channel)" in block
    assert block.index("is_share_router_design_resource(channel)") < block.index('if _kind(channel) != "other"')

    assert "def _designable_editor_categories" in DESIGN
    assert "def _reject_reserved_design_target" in DESIGN
    assert "design.direct_rename.reserved" in DESIGN
    assert "design.exact.open_reserved" in DESIGN
    assert "design.format_lock.reserved_category" in DESIGN
    assert "design.format_lock.reserved_channel" in DESIGN
    assert "design.protection.reserved" in DESIGN


def test_runtime_native_video_relay_is_bounded_and_fail_open() -> None:
    assert "DANK_SHARE_ROUTER_MAX_VIDEO_BYTES" in RUNTIME
    assert "DANK_SHARE_ROUTER_VIDEO_TIMEOUT_SECONDS" in RUNTIME
    assert "tempfile.SpooledTemporaryFile" in RUNTIME
    assert "aiohttp.ClientTimeout" in RUNTIME
    assert "allow_redirects=False" in RUNTIME
    assert "_trusted_video_url" in RUNTIME
    assert "target.permissions_for(me).attach_files" in RUNTIME
    assert 'send_payload["file"] = native_video.file' in RUNTIME
    assert "_suppress_url_previews(routed_text)" in RUNTIME
    assert "native video send fallback" in RUNTIME
    assert "await target.send(" in RUNTIME
    assert "asyncio.to_thread" in RUNTIME
    assert "yt_dlp.YoutubeDL" in RUNTIME
    assert "_X_EXTRACT_SEMAPHORE" in RUNTIME
    assert "_X_VIDEO_CACHE" in RUNTIME
    assert "_first_x_status_url(routed_text)" in RUNTIME


def test_runtime_keeps_legacy_route_storage_and_sender_permission_boundary() -> None:
    assert "DANK_SHARE_ROUTES_FILE" in RUNTIME
    assert '"share_routes.json"' in RUNTIME
    assert "author_perms = target.permissions_for(author)" in RUNTIME
    assert "author_perms.view_channel" in RUNTIME
    assert "author_perms.send_messages" in RUNTIME
    assert "source_age_blocker(message.channel)" in RUNTIME
    assert "source_privacy_blocker(message.channel)" in RUNTIME
    assert "allowed_mentions=discord.AllowedMentions.none()" in RUNTIME


def test_legacy_startup_guard_is_side_effect_free_compatibility_only() -> None:
    assert "Historical Share Router compatibility shim" in LEGACY
    assert "ensure_share_router_runtime" in LEGACY
    assert "app_commands" not in LEGACY
    assert "dank_group" not in LEGACY
    assert "\ninstall()\n" not in LEGACY
