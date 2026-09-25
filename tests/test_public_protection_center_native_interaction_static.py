from __future__ import annotations

from pathlib import Path

SOURCE = Path("stoney_verify/commands_ext/public_protection_center.py").read_text(encoding="utf-8")


def test_protection_center_imports_native_guard() -> None:
    assert "run_guarded_interaction" in SOURCE
    assert "log_interaction_failure" in SOURCE
    assert "safe_send_interaction" in SOURCE


def test_protection_center_command_uses_native_guard() -> None:
    command_block = SOURCE[SOURCE.index("async def protection_center") : SOURCE.index("def register_public_protection_center_commands")]
    assert "await run_guarded_interaction(" in command_block
    assert "print(" not in command_block
    assert "except Exception" not in command_block


def test_protection_center_buttons_use_guarded_actions() -> None:
    required_actions = [
        "protection.safe",
        "protection.strict",
        "protection.off",
        "protection.open_spamguard_editor",
        "protection.invite_blocker",
        "protection.link_shield",
        "protection.open_add_filter_modal",
        "protection.open_import_filter_pack_modal",
        "protection.import_filter_pack_modal",
        "protection.open_test_filter_modal",
        "protection.allow_links",
        "protection.refresh",
        "protection.close",
        "protection.spam_response_mode",
        "protection.spam_detection_modal",
        "protection.spam_action_modal",
    ]
    for action in required_actions:
        assert action in SOURCE


def test_protection_center_removed_legacy_local_open_error_prints() -> None:
    assert "public_protection_center open failed" not in SOURCE
    assert "failed to send Protection Center" not in SOURCE
    assert "Protection Center could not open safely" not in SOURCE


def test_protection_center_owns_import_pack_without_startup_patch() -> None:
    assert 'label="Import Pack"' in SOURCE
    assert 'custom_id="dank_protection:import_pack"' in SOURCE
    assert "StarterPackImportModal" in SOURCE
    assert "_merge_imported_filter_terms" in SOURCE
    assert "protection_pack_manual_import_guard" not in SOURCE
    assert "protection_import_button_patch" not in SOURCE

def test_protection_center_reports_invite_delete_health_and_v2_coverage() -> None:
    assert "def _invite_delete_health(" in SOURCE
    assert "**Live delete access here:**" in SOURCE
    assert "**Modern app cards:** visible Components V2 text is checked" in SOURCE
    assert "manage_messages" in SOURCE


def test_protection_center_close_removes_panel_instead_of_greying_it_out() -> None:
    start = SOURCE.index("async def close_button")
    end = SOURCE.index("@dank_group.command", start)
    block = SOURCE[start:end]

    assert "embed=None" in block
    assert "view=None" in block
    assert "child.disabled = True" not in block
