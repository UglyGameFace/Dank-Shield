from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from stoney_verify.members_new import activity_scope


@dataclass
class FakePermissions:
    view_channel: bool = True
    read_message_history: bool = True
    manage_threads: bool = True
    create_private_threads: bool = False
    send_messages_in_threads: bool = False


class FakeMember:
    pass


class FakeRole:
    def __init__(self, permissions: FakePermissions, *, managed: bool = False) -> None:
        self.permissions = permissions
        self.managed = managed


class FakeTextChannel:
    def __init__(self, channel_id: int, name: str, member_permissions: FakePermissions, role_permissions: FakePermissions | None = None) -> None:
        self.id = channel_id
        self.name = name
        self._member_permissions = member_permissions
        self._role_permissions = role_permissions or FakePermissions()

    def permissions_for(self, target):
        if isinstance(target, FakeRole):
            return self._role_permissions
        return self._member_permissions


class FakeForumChannel(FakeTextChannel):
    pass


class FakeThread:
    def __init__(self, channel_id: int, name: str, permissions: FakePermissions, *, parent=None, private: bool = False) -> None:
        self.id = channel_id
        self.name = name
        self._permissions = permissions
        self.parent = parent
        self._private = private

    def permissions_for(self, _target):
        return self._permissions

    def is_private(self):
        return self._private


class FakeGuild:
    def __init__(self, *, channels, threads=(), roles=(), me=None) -> None:
        self.channels = list(channels)
        self.threads = list(threads)
        self.roles = list(roles)
        self.me = me


@pytest.fixture
def fake_discord_types(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(activity_scope.discord, "Member", FakeMember)
    monkeypatch.setattr(activity_scope.discord, "TextChannel", FakeTextChannel)
    monkeypatch.setattr(activity_scope.discord, "ForumChannel", FakeForumChannel)
    monkeypatch.setattr(activity_scope.discord, "Thread", FakeThread)


def test_scope_report_lists_every_inaccessible_channel_and_exact_missing_permissions(fake_discord_types) -> None:
    me = FakeMember()
    channels = [
        FakeTextChannel(1, "general", FakePermissions()),
        FakeTextChannel(2, "moderator-only", FakePermissions(view_channel=False, read_message_history=False)),
        FakeTextChannel(3, "song-recommendations", FakePermissions(view_channel=True, read_message_history=False)),
    ]
    guild = FakeGuild(channels=channels, me=me)

    report = activity_scope.audit_activity_scope(guild)

    assert report.complete is False
    assert report.total_channels == 3
    assert report.accessible_channels == 1
    assert report.coverage_percent == 33
    by_name = {problem.channel_name: problem for problem in report.problems}
    assert by_name["moderator-only"].missing_permissions == ("View Channel", "Read Message History")
    assert by_name["song-recommendations"].missing_permissions == ("Read Message History",)
    summary = report.summary()
    assert "#moderator-only" in summary
    assert "#song-recommendations" in summary
    assert "View Channel" in summary
    assert "Read Message History" in summary


def test_private_thread_capability_gap_reports_manage_threads_and_reduces_scope(fake_discord_types) -> None:
    me = FakeMember()
    parent = FakeTextChannel(
        10,
        "staff",
        FakePermissions(view_channel=True, read_message_history=True, manage_threads=False),
        role_permissions=FakePermissions(
            view_channel=True,
            read_message_history=True,
            manage_threads=False,
            create_private_threads=True,
            send_messages_in_threads=True,
        ),
    )
    guild = FakeGuild(channels=[parent], roles=[FakeRole(parent._role_permissions)], me=me)

    report = activity_scope.audit_activity_scope(guild)

    assert report.complete is False
    assert report.coverage_percent == 0
    assert any(problem.missing_permissions == ("Manage Threads",) for problem in report.problems)


def test_complete_scope_reports_full_coverage(fake_discord_types) -> None:
    me = FakeMember()
    guild = FakeGuild(
        channels=[
            FakeTextChannel(1, "general", FakePermissions()),
            FakeForumChannel(2, "help-forum", FakePermissions()),
        ],
        me=me,
    )

    report = activity_scope.audit_activity_scope(guild)

    assert report.complete is True
    assert report.coverage_percent == 100
    assert report.problems == ()


def test_diagnostics_and_setup_check_surface_scope_without_auto_granting_permissions() -> None:
    diagnostics_source = Path("stoney_verify/commands_ext/public_diagnostics_group.py").read_text(encoding="utf-8")
    setup_source = Path("stoney_verify/commands_ext/public_setup_group.py").read_text(encoding="utf-8")
    scope_source = Path("stoney_verify/members_new/activity_scope.py").read_text(encoding="utf-8")

    assert "Activity Tracking Coverage" in diagnostics_source
    assert "Inactive-member cleanup remains fail-closed" in diagnostics_source

    assert "activity_scope = audit_activity_scope(guild)" in setup_source
    assert "format_activity_scope_problems(activity_scope, limit=20)" in setup_source
    assert "Activity tracking coverage is incomplete:" in setup_source
    assert "Activity tracking access: {problem}" in setup_source
    assert "Inactivity cleanup stays fail-closed until access is restored." in setup_source

    assert "View Channel" in scope_source
    assert "Read Message History" in scope_source
    assert "Manage Threads" in scope_source
    assert ".set_permissions(" not in scope_source
    assert "edit(overwrites" not in scope_source
