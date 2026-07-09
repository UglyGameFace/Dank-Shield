from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEANUP = (ROOT / "stoney_verify/commands_ext/public_cleanup_group.py").read_text(encoding="utf-8")


def test_user_messages_command_exists_under_cleanup_group() -> None:
    assert '@cleanup_group.command(name="user-messages"' in CLEANUP
    assert "async def cleanup_user_messages" in CLEANUP
    assert "scope=[" in CLEANUP
    assert 'app_commands.Choice(name="Whole server", value="server")' in CLEANUP
    assert 'app_commands.Choice(name="One channel", value="channel")' in CLEANUP


def test_user_messages_supports_left_user_id() -> None:
    assert "_cleanup_parse_user_id" in CLEANUP
    assert "Raw Discord user ID, useful after the user left/kicked" in CLEANUP
    assert "Pick a user or provide a numeric user_id" in CLEANUP


def test_user_messages_requires_native_manage_messages_and_confirmation() -> None:
    assert "_require_manage_messages_native" in CLEANUP
    assert "You need Discord **Manage Messages**" in CLEANUP
    assert "_cleanup_user_purge_confirm_phrase" in CLEANUP
    assert "PURGE SERVER" in CLEANUP
    assert "Confirmation required before deleting user messages" in CLEANUP


def test_user_messages_scans_channels_and_deletes_individually() -> None:
    assert "_cleanup_scan_user_messages_in_channel" in CLEANUP
    assert "async for msg in channel.history" in CLEANUP
    assert "await msg.delete(reason=reason)" in CLEANUP
    assert "include_pinned" in CLEANUP
    assert "dry_run" in CLEANUP


def test_user_messages_summary_is_channel_by_channel() -> None:
    assert "_cleanup_user_purge_summary" in CLEANUP
    assert "Channels checked" in CLEANUP
    assert "Matched user messages" in CLEANUP
    assert "Samples / notes" in CLEANUP


if __name__ == "__main__":
    for test in (
        test_user_messages_command_exists_under_cleanup_group,
        test_user_messages_supports_left_user_id,
        test_user_messages_requires_native_manage_messages_and_confirmation,
        test_user_messages_scans_channels_and_deletes_individually,
        test_user_messages_summary_is_channel_by_channel,
    ):
        test()
        print(f"PASS {test.__name__}")
