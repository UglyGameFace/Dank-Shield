from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAID = (ROOT / "stoney_verify/raidguard.py").read_text(encoding="utf-8")
CLEANUP = (ROOT / "stoney_verify/commands_ext/public_cleanup_group.py").read_text(encoding="utf-8")


def test_risk_wording_separates_bot_alt_and_dm_report() -> None:
    assert "Official Bot: Yes" in RAID
    assert "Official Bot: No" in RAID
    assert "Alt/Raid Risk:" in RAID
    assert "DM Raider Report Risk" in CLEANUP
    assert "BOT ACCOUNT • excluded from raid/alt scoring" not in RAID


def test_dm_report_command_exists_under_cleanup() -> None:
    assert "report-dm-spam" in CLEANUP
    assert "async def cleanup_report_dm_spam" in CLEANUP
    assert "DM Raider Report" in CLEANUP
    assert "Dank Shield cannot read private DMs" in CLEANUP


def test_dm_report_has_staff_actions_without_auto_purge() -> None:
    assert "class DmRaiderReportActionView" in CLEANUP
    assert "Ban by ID" in CLEANUP
    assert "Purge User Messages" in CLEANUP
    assert "guild.ban(" in CLEANUP
    assert "Run a fresh purge preview" in CLEANUP


def test_dm_report_is_not_private_dm_surveillance() -> None:
    assert "report-based evidence" in CLEANUP
    assert "not private-message surveillance" in CLEANUP or "not DM reading" in CLEANUP


if __name__ == "__main__":
    for test in (
        test_risk_wording_separates_bot_alt_and_dm_report,
        test_dm_report_command_exists_under_cleanup,
        test_dm_report_has_staff_actions_without_auto_purge,
        test_dm_report_is_not_private_dm_surveillance,
    ):
        test()
        print(f"PASS {test.__name__}")
