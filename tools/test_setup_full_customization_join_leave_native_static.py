from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FULL = (ROOT / "stoney_verify/commands_ext/public_setup_full_customization.py").read_text(encoding="utf-8")


def test_more_channels_natively_shows_join_leave_picker() -> None:
    assert "class ChannelCustomizationPageTwo" in FULL
    assert "Join / leave log channel — not welcome" in FULL
    assert "join_leave_log_channel_id" in FULL
    assert "JOIN_LEAVE_LOG_ALIASES" in FULL


def test_more_channels_no_longer_has_only_three_selects() -> None:
    section = FULL.split("class ChannelCustomizationPageTwo", 1)[1].split("class LogStatusCustomizationView", 1)[0]
    assert section.count("self.add_item(SaveChannelSelect") >= 4
    assert "VC verification queue/status text channel" in section
    assert "Join / leave log channel — not welcome" in section
    assert "General support text channel fallback" in section
    assert "Bot health/status text channel" in section


def test_logs_status_writes_join_leave_aliases() -> None:
    section = FULL.split("class LogStatusCustomizationView", 1)[1].split("class BehaviorSettingsModal", 1)[0]
    assert "Join / leave log channel — not welcome" in section
    assert "join_leave_log_channel_id" in section
    assert "JOIN_LEAVE_LOG_ALIASES" in section
    assert "Moderation / staff audit log channel" in section
    assert "STAFF_LOG_ALIASES" in section


if __name__ == "__main__":
    for test in (
        test_more_channels_natively_shows_join_leave_picker,
        test_more_channels_no_longer_has_only_three_selects,
        test_logs_status_writes_join_leave_aliases,
    ):
        test()
        print(f"PASS {test.__name__}")
