from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD_DIR = ROOT / "stoney_verify" / "startup_guards"

RETIRED_GUARDS = (
    "protection_center_invite_simple_flow_guard.py",
    "protection_center_invite_controls_guard.py",
    "protection_center_invite_status_guard.py",
    "spam_guard_invite_scope_pagination_guard.py",
    "invite_hard_block_all_bots_controls_guard.py",
    "protection_invite_cleanup_picker_guard.py",
    "protection_invite_toggle_cleanup_guard.py",
    "invite_live_enforcer_guard.py",
    "discord_invite_blocker_runtime_guard.py",
    "spam_guard_invite_hard_block.py",
    "spam_guard_invite_override_options.py",
    "protection_invite_target_precedence_guard.py",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_retired_invite_ui_guards_are_deleted() -> None:
    for filename in RETIRED_GUARDS:
        assert not (GUARD_DIR / filename).exists(), filename


def test_historical_registry_does_not_advertise_retired_invite_ui_owners() -> None:
    registry = _text(GUARD_DIR / "__init__.py")
    for filename in RETIRED_GUARDS:
        assert filename.removesuffix(".py") not in registry


def test_surviving_general_protection_guards_do_not_chain_retired_invite_ui() -> None:
    survivors = (
        GUARD_DIR / "protection_center_clear_categories_guard.py",
        GUARD_DIR / "protection_center_filter_list_guard.py",
    )
    for path in survivors:
        source = _text(path)
        for filename in RETIRED_GUARDS:
            assert filename.removesuffix(".py") not in source, f"{filename} referenced by {path}"


def test_native_invite_owner_is_explicit_and_startup_guard_free() -> None:
    commands = _text(ROOT / "stoney_verify" / "commands.py")
    native = _text(ROOT / "stoney_verify" / "commands_ext" / "public_protection_invite_ui.py")
    scope = _text(ROOT / "stoney_verify" / "invite_scope_settings.py")

    assert "_install_invite_policy_scope_binding" in commands
    assert "_install_native_invite_ui" in commands
    assert "DankGuildResourceBrowserView" in native
    assert "scan_channel_invites" in native
    assert "save_invite_scope_settings" in native
    assert "install_invite_policy_scope_binding" in scope
    assert "startup_guards" not in native
    assert "startup_guards" not in scope


def test_native_invite_ui_does_not_restore_raw_discord_resource_pickers() -> None:
    native = _text(ROOT / "stoney_verify" / "commands_ext" / "public_protection_invite_ui.py")
    assert "discord.ui.ChannelSelect" not in native
    assert "discord.ui.UserSelect" not in native
    assert "discord.ui.RoleSelect" not in native
    assert "await message.delete(" not in native


def test_native_live_and_recovery_invite_owners_are_explicit() -> None:
    globals_source = _text(ROOT / "stoney_verify" / "globals.py")
    policy_source = _text(ROOT / "stoney_verify" / "invite_policy_engine.py")
    recovery_source = _text(ROOT / "stoney_verify" / "invite_reconciliation_runtime.py")
    main_source = _text(ROOT / "main.py")

    assert "async def _dank_globals_live_invite_enforcer" in globals_source
    assert "enforce_live_invite_message" in globals_source
    assert "async def enforce_live_invite_message(" in policy_source
    assert "policy.scan_channel_invites(" in recovery_source
    assert "_install_invite_reconciliation_runtime()" in main_source
