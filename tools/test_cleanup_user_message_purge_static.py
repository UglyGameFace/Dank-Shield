from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEANUP = (ROOT / "stoney_verify/commands_ext/public_cleanup_group.py").read_text(encoding="utf-8")


def test_existing_purge_command_owns_user_target_flow() -> None:
    assert '@cleanup_group.command(name="purge"' in CLEANUP
    assert '@cleanup_group.command(name="user-messages"' not in CLEANUP
    assert "async def cleanup_user_messages" not in CLEANUP
    assert 'user="Optional user whose messages should be targeted"' in CLEANUP
    assert 'user_id="Raw Discord user ID, useful after the user left/kicked"' in CLEANUP


def test_user_purge_has_channel_and_server_scope() -> None:
    assert "scope=[" in CLEANUP
    assert 'app_commands.Choice(name="This channel", value="channel")' in CLEANUP
    assert 'app_commands.Choice(name="Whole server", value="server")' in CLEANUP
    assert "Delete Across Server" in CLEANUP
    assert "Delete From Channel" in CLEANUP


def test_user_purge_uses_preview_buttons_not_typed_confirm() -> None:
    assert "CleanupUserPurgeConfirmView" in CLEANUP
    assert "No typed confirmation needed" in CLEANUP
    assert "@discord.ui.button" in CLEANUP
    assert "interaction.response.edit_message(view=self)" in CLEANUP
    assert "PURGE SERVER" not in CLEANUP
    assert "confirm_phrase" not in CLEANUP
    assert "Confirmation required before deleting user messages" not in CLEANUP


def test_user_purge_still_requires_native_manage_messages() -> None:
    assert "_require_manage_messages_native" in CLEANUP
    assert "You need Discord **Manage Messages**" in CLEANUP
    assert "_cleanup_user_purge_channel_skip" in CLEANUP


def test_user_purge_scans_channels_and_deletes_individually() -> None:
    assert "_cleanup_scan_user_messages_in_channel" in CLEANUP
    assert "async for msg in channel.history" in CLEANUP
    assert "await msg.delete()" in CLEANUP
    assert "await msg.delete(reason=reason)" not in CLEANUP
    assert "include_pinned" in CLEANUP
    assert "limit_per_channel" in CLEANUP


if __name__ == "__main__":
    for test in (
        test_existing_purge_command_owns_user_target_flow,
        test_user_purge_has_channel_and_server_scope,
        test_user_purge_uses_preview_buttons_not_typed_confirm,
        test_user_purge_still_requires_native_manage_messages,
        test_user_purge_scans_channels_and_deletes_individually,
    ):
        test()
        print(f"PASS {test.__name__}")
