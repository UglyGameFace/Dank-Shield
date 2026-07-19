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
        "protection.open_test_filter_modal",
        "protection.allow_links",
        "protection.live_stats",
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
