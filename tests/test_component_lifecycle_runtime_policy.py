from __future__ import annotations

import inspect
from pathlib import Path

import discord
from discord.ui.view import ViewStore

from stoney_verify.commands_ext.public_command_surface_v2 import (
    CardAssetView,
    CompactDankHomeView,
    CompactHelpView,
)
from stoney_verify.panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from stoney_verify.runtime_release import runtime_release_snapshot


ROOT = Path(__file__).resolve().parents[1]


def _custom_ids(view: discord.ui.View) -> list[str]:
    return [
        str(getattr(item, "custom_id", "") or "")
        for item in view.children
        if not bool(getattr(item, "url", None))
    ]


def test_discord_py_viewstore_contract_is_pinned_before_private_recovery() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "discord.py==2.7.1" in requirements
    assert discord.__version__ == "2.7.1"

    source = inspect.getsource(ViewStore.dispatch_view)
    assert "self.dispatch_dynamic_items(component_type, custom_id, interaction)" in source
    assert "self._views.get(message_id, {}).get(key)" in source
    assert "self._views.get(None, {}).get(key)" in source


def test_control_center_uses_one_shared_private_session_lifetime() -> None:
    assert PRIVATE_MENU_TTL_SECONDS == 15 * 60
    assert CompactDankHomeView(1).timeout == PRIVATE_MENU_TTL_SECONDS

    modules = (
        "public_command_hub.py",
        "public_mod_command_center.py",
        "public_ticket_command_center.py",
        "public_verify_command_center.py",
        "public_cleanup_command_center.py",
        "public_community_tools.py",
        "public_community_hub.py",
        "public_share_router.py",
        "public_quiet_notice.py",
        "public_server_stats.py",
        "public_protection_center.py",
    )
    for name in modules:
        source = (ROOT / "stoney_verify" / "commands_ext" / name).read_text(encoding="utf-8")
        assert "PRIVATE_MENU_TTL_SECONDS" in source


def test_control_center_navigation_has_stable_semantic_component_ids() -> None:
    home_ids = _custom_ids(CompactDankHomeView(1))
    assert len(home_ids) == len(set(home_ids))
    assert len(home_ids) >= 17
    assert all(custom_id.startswith("dank:home:") for custom_id in home_ids)

    help_ids = _custom_ids(CompactHelpView(1))
    asset_ids = _custom_ids(CardAssetView(1))
    assert all(custom_id.startswith("dank:help:") for custom_id in help_ids)
    assert all(custom_id.startswith("dank:assets:") for custom_id in asset_ids)


def test_component_runtime_is_single_prelogin_lifecycle_owner() -> None:
    app = (ROOT / "stoney_verify" / "app.py").read_text(encoding="utf-8")
    guard = (ROOT / "stoney_verify" / "interaction_guard.py").read_text(encoding="utf-8")

    assert app.count("_install_component_interaction_runtime(bot)") == 1
    assert "install_component_interaction_observer as" not in app
    assert "def install_component_interaction_runtime(" in guard
    assert "_view_store_component_owner_state" in guard
    assert "_dynamic_items" in guard
    assert "_message_is_ephemeral" in guard
    assert "_recover_unowned_private_component" in guard
    assert "replace_with_compact_dank_home" in guard
    assert "open_community_hub" in guard
    assert "stale action was not executed" in guard


def test_private_stale_recovery_never_replays_feature_business_logic() -> None:
    guard = (ROOT / "stoney_verify" / "interaction_guard.py").read_text(encoding="utf-8")
    start = guard.index("async def _recover_unowned_private_component")
    end = guard.index("def _interaction_age_ms", start)
    body = guard[start:end]

    assert "replace_with_compact_dank_home" in body
    assert "open_community_hub" in body
    assert 'custom_id.startswith("dank:hub:")' in body
    assert 'not custom_id.startswith("dank:hub:public:")' in body
    assert "interaction.response.defer" not in body
    assert "apply_basic_verification" not in body
    assert "handle_public_ticket_panel_click" not in body
    assert "create_text_channel" not in body
    assert "add_roles" not in body
    assert "remove_roles" not in body


def test_interaction_runtime_and_profile_owner_fail_closed_at_boot() -> None:
    app = (ROOT / "stoney_verify" / "app.py").read_text(encoding="utf-8")
    commands = (ROOT / "stoney_verify" / "commands.py").read_text(encoding="utf-8")
    profile = (ROOT / "stoney_verify" / "commands_ext" / "public_self_roles_group.py").read_text(encoding="utf-8")

    assert 'raise RuntimeError("shared component lifecycle runtime did not register")' in app
    assert "install_profile_interaction_runtime" in commands
    assert "_install_profile_interaction_runtime(bot, strict=True)" in commands
    assert commands.index("_install_profile_interaction_runtime(bot, strict=True)") < commands.index(
        "register_all_commands(bot, bot.tree)"
    )

    interaction_handlers = (ROOT / "stoney_verify" / "interaction_handlers.py").read_text(encoding="utf-8")

    assert "def install_profile_interaction_runtime(" in profile
    assert 'add_listener(_interaction_listener, "on_interaction")' in profile
    assert "persistent ProfilePanelView registration failed" in profile
    assert "if strict:" in profile
    assert "raise RuntimeError(message)" in profile

    assert "def register_interaction_handlers(bot_instance: Any) -> bool:" in interaction_handlers
    assert "interaction_handlers_runtime_status" in interaction_handlers
    assert 'raise RuntimeError("central verification/VC interaction listener did not register")' in commands


def test_runtime_proof_is_host_independent_and_component_status_is_exposed() -> None:
    snapshot = runtime_release_snapshot()
    assert snapshot["proof_version"] == "runtime-proof-v1"
    assert snapshot["source_fingerprint"]
    assert snapshot["source_fingerprint"] != "unavailable"

    guard = (ROOT / "stoney_verify" / "interaction_guard.py").read_text(encoding="utf-8")
    status = (ROOT / "stoney_verify" / "commands_ext" / "public_status_reporter.py").read_text(encoding="utf-8")
    assert "def component_runtime_status(" in guard
    assert "_record_component_ingress(bot, interaction)" in guard
    assert "_COMPONENT_UNACKNOWLEDGED_COUNT" in guard
    assert "release={runtime_release_label()}" in guard
    assert 'name="Runtime Proof"' in status
    assert "component_runtime_status(bot)" in status


def test_durable_public_panels_remain_persistent_not_private_sessions() -> None:
    verify = (ROOT / "stoney_verify" / "verification_new" / "basic_verify.py").read_text(encoding="utf-8")
    tickets = (ROOT / "stoney_verify" / "commands_ext" / "public_ticket_panel_clean.py").read_text(encoding="utf-8")
    profile = (ROOT / "stoney_verify" / "commands_ext" / "public_self_roles_group.py").read_text(encoding="utf-8")

    assert "class BasicVerifyView" in verify
    assert "super().__init__(timeout=None)" in verify

    ticket_start = tickets.index("class PublicCreateTicketPanelView")
    ticket_end = tickets.index("def _panel_embed", ticket_start)
    assert "super().__init__(timeout=None)" in tickets[ticket_start:ticket_end]

    assert "class AdvancedSelfRolePanelView" in profile
    profile_start = profile.index("class AdvancedSelfRolePanelView")
    profile_end = profile.index("async def _post_advanced_panel", profile_start)
    assert "super().__init__(timeout=None)" in profile[profile_start:profile_end]
