from __future__ import annotations

from pathlib import Path

from discord import app_commands

from stoney_verify.commands_ext.public_direct_purge import build_direct_purge_group

ROOT = Path(__file__).resolve().parents[1]


def test_direct_purge_group_is_small_and_explicit() -> None:
    group = build_direct_purge_group()
    assert isinstance(group, app_commands.Group)
    assert group.name == "purge"
    assert {str(command.name) for command in group.commands} == {"messages", "members"}


def test_direct_purge_facade_delegates_to_existing_canonical_engines() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_direct_purge.py").read_text(encoding="utf-8")
    assert "from .public_cleanup_group import cleanup_purge" in source
    assert "from .public_members_cleanup_group import members_purge_all" in source
    assert "await _invoke(\n        cleanup_purge," in source
    assert "await _invoke(\n        members_purge_all," in source

    # The facade must stay a routing layer, not grow a second purge engine.
    assert "channel.history(" not in source
    assert "scan_inactive_members(" not in source
    assert "msg.delete(" not in source
    assert "execute_member_cleanup(" not in source


def test_direct_message_purge_keeps_user_and_server_scope_options() -> None:
    group = build_direct_purge_group()
    command = group.get_command("messages")
    assert command is not None
    names = {str(parameter.name) for parameter in command.parameters}
    assert {
        "channel",
        "amount",
        "older_than_hours",
        "include_pinned",
        "dry_run",
        "user",
        "user_id",
        "scope",
    } <= names


def test_direct_member_purge_keeps_inactivity_safety_options() -> None:
    group = build_direct_purge_group()
    command = group.get_command("members")
    assert command is not None
    names = {str(parameter.name) for parameter in command.parameters}
    assert {"inactive_days", "grace_days", "include_low_confidence", "reason"} <= names
