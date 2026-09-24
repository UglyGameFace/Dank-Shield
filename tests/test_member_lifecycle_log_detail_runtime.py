from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from stoney_verify import modlog
from stoney_verify.startup_guards import member_lifecycle_router_guard as router


class FakeAvatar:
    url = "https://example.invalid/avatar.png"


class FakeRole:
    def __init__(self, role_id: int, name: str, position: int) -> None:
        self.id = role_id
        self.name = name
        self.position = position
        self.mention = f"<@&{role_id}>"

    def is_default(self) -> bool:
        return False


class FakeMember:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.id = 123
        self.mention = "<@123>"
        self.name = "member"
        self.display_name = "Member Display"
        self.created_at = now - timedelta(days=400)
        self.joined_at = now - timedelta(days=40)
        self.bot = False
        self.display_avatar = FakeAvatar()
        self.roles = [
            FakeRole(10, "Member", 1),
            FakeRole(20, "Trusted", 2),
        ]
        self.guild = SimpleNamespace(id=777, member_count=321)

    def __str__(self) -> str:
        return "member#0001"


def _field_map(embed):
    return {str(field.name): str(field.value) for field in embed.fields}


def test_operational_join_log_restores_useful_member_detail() -> None:
    member = FakeMember()
    embed = router._member_lifecycle_embed(member, joined=True)
    fields = _field_map(embed)

    assert embed.title == "🌿 Member Joined"
    assert "Account Created" in fields
    assert "400" in fields["Account Created"]
    assert "Profile" in fields
    assert "Member Display" in fields["Profile"]
    assert "Members" in fields
    assert "321" in fields["Members"]


def test_operational_leave_log_includes_membership_history() -> None:
    member = FakeMember()
    embed = router._member_lifecycle_embed(member, joined=False)
    fields = _field_map(embed)

    assert embed.title == "🍂 Member Left"
    assert "Account Created" in fields
    assert "Server Membership" in fields
    assert "40" in fields["Server Membership"]
    assert "Profile" in fields


def test_detailed_staff_leave_restores_roles_and_member_context(monkeypatch) -> None:
    member = FakeMember()
    guild = member.guild

    async def context(_guild, _member):
        return [
            ("Assessment", "LOW CONCERN", False),
            ("Relevant Context", "Invite: abc123", False),
        ]

    monkeypatch.setattr(modlog, "_build_member_context_fields", context)

    embed = asyncio.run(modlog.build_member_leave_embed(guild, member))
    fields = _field_map(embed)

    assert embed.title == "📤 Member Left"
    assert "Exit Classification" in fields
    assert "Voluntary/ordinary leave" in fields["Exit Classification"]
    assert "Account & Membership" in fields
    assert "Roles At Exit" in fields
    assert "Trusted" in fields["Roles At Exit"]
    assert "Member" in fields["Roles At Exit"]
    assert fields["Assessment"] == "LOW CONCERN"
    assert "abc123" in fields["Relevant Context"]


def test_events_uses_detailed_leave_builder_without_second_listener() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "stoney_verify" / "events.py").read_text(encoding="utf-8")

    start = source.index("async def on_member_remove(member: discord.Member):")
    end = source.index("@bot.event\nasync def on_member_update", start)
    leave_source = source[start:end]

    assert "await build_member_leave_embed(guild, member)" in leave_source
    assert 'event_key=f"member_leave:{member.id}"' in leave_source
    assert 'discord.Embed(title="📤 Member Left"' not in leave_source


def test_member_logs_configuration_no_longer_retargets_exit_card_studio() -> None:
    source = inspect.getsource(router._member_logs_command)

    assert 'payload["exit_card_channel_id"]' not in source
    assert "Exit Card Studio owns its own route" in source


def test_operational_log_is_not_suppressed_by_successful_studio_delivery() -> None:
    join_source = inspect.getsource(router._join_listener)
    leave_source = inspect.getsource(router._leave_listener)

    assert "duplicate suppressed" not in join_source
    assert "duplicate suppressed" not in leave_source
    assert "await _send_join_log_event(member, join_log_channel)" in join_source
    assert "await _send_leave_log_event(member, leave_log_channel)" in leave_source


def test_member_logs_health_exposes_intent_listener_and_channel_truth() -> None:
    source = inspect.getsource(router._member_logs_command)

    assert "Join/leave runtime health" in source
    assert "Server Members intent requested by this bot process" in source
    assert "Join listener registered" in source
    assert "Leave listener registered" in source
    assert "_lifecycle_channel_health" in source
