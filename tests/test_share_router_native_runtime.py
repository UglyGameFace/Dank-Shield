from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify.share_router_resources import (
    SHARE_ROUTER_CATEGORY_KEY,
    is_share_router_category_name,
    is_share_router_design_resource,
    normalize_share_router_name,
    share_source_key,
)
from stoney_verify.services import server_design_apply_service as design_apply_service
from stoney_verify.share_router_runtime import (
    _merged_overwrite,
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


@pytest.mark.asyncio
async def test_transaction_preflight_skips_reserved_share_router_resources() -> None:
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
    ready, skipped, errors = await design_apply_service.preflight_plan(Guild(), items, name_limit=100)

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


def test_runtime_keeps_legacy_route_storage_and_sender_permission_boundary() -> None:
    assert "DANK_SHARE_ROUTES_FILE" in RUNTIME
    assert '"share_routes.json"' in RUNTIME
    assert "author_perms = target.permissions_for(author)" in RUNTIME
    assert "author_perms.view_channel" in RUNTIME
    assert "author_perms.send_messages" in RUNTIME
    assert "source_age_blocker(message.channel)" in RUNTIME
    assert "allowed_mentions=discord.AllowedMentions.none()" in RUNTIME


def test_legacy_startup_guard_is_side_effect_free_compatibility_only() -> None:
    assert "Historical Share Router compatibility shim" in LEGACY
    assert "ensure_share_router_runtime" in LEGACY
    assert "app_commands" not in LEGACY
    assert "dank_group" not in LEGACY
    assert "\ninstall()\n" not in LEGACY
