from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from stoney_verify.share_router_resources import (
    SHARE_ROUTER_CATEGORY_KEY,
    is_share_router_category_name,
    is_share_router_design_resource,
    normalize_share_router_name,
    share_source_key,
)
from stoney_verify.share_router_runtime import ensure_share_router_runtime, route_for_source


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


def test_dank_design_excludes_share_router_from_the_shared_editable_scan() -> None:
    assert "from stoney_verify.share_router_resources import is_share_router_design_resource" in DESIGN
    start = DESIGN.index("def _editable_channels")
    end = DESIGN.index("def _can_user_design", start)
    block = DESIGN[start:end]
    assert "is_share_router_design_resource(channel)" in block
    assert block.index("is_share_router_design_resource(channel)") < block.index('if _kind(channel) != "other"')


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
