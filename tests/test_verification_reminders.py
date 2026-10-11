from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from stoney_verify import verification_reminders as reminders


def run(coro):
    return asyncio.run(coro)


def test_policy_defaults_off_and_bounded():
    assert reminders.parse_policy({}) == reminders.ReminderPolicy()
    config = {
        reminders.REMINDER_CONFIG_KEY: {
            "enabled": "true",  # Not authorized by a string.
            "delay_hours": -1,
            "repeat_hours": 0,
            "max_reminders": 900,
            "message": "a" * 400,
        }
    }
    policy = reminders.parse_policy(config)
    assert not policy.enabled
    assert (policy.delay_hours, policy.repeat_hours, policy.max_reminders) == (1, 12, 5)
    assert len(policy.message) == 240


def test_member_due_requires_a_known_old_join():
    now = datetime.now(timezone.utc)
    assert not reminders.member_is_due(SimpleNamespace(joined_at=None), delay_hours=24, now=now)
    assert not reminders.member_is_due(SimpleNamespace(joined_at=now), delay_hours=24, now=now)
    assert reminders.member_is_due(
        SimpleNamespace(joined_at=now - timedelta(hours=25)), delay_hours=24, now=now,
    )


def test_pending_uses_existing_role_truth_not_role_names(monkeypatch):
    monkeypatch.setattr(reminders, "member_is_pending_verification", lambda member: member.pending)
    cfg = {"unverified_role_id": "10", "verified_role_id": "11"}
    assert reminders.pending_for_reminder(SimpleNamespace(bot=False, pending=True), cfg)
    assert not reminders.pending_for_reminder(SimpleNamespace(bot=False, pending=False), cfg)
    assert not reminders.pending_for_reminder(SimpleNamespace(bot=True, pending=True), cfg)
    assert not reminders.pending_for_reminder(SimpleNamespace(bot=False, pending=True), {"unverified_role_id": "10"})


def test_text_links_to_saved_panel_and_does_not_ping():
    guild = SimpleNamespace(id=123, name="Demo")
    channel = SimpleNamespace(id=987)
    text = reminders.reminder_text(
        guild, channel, {"basic_verify_panel_message_id": "111"}, reminders.ReminderPolicy(),
    )
    assert "https://discord.com/channels/123/987" in text
    assert "https://discord.com/channels/123/987/111" not in text  # stale panel IDs can break
    assert "tap **Verify**" in text
    assert "@everyone" not in text


def _guild(*, fresh_pending=True, count=2):
    now = datetime.now(timezone.utc)
    members = []
    for i in range(count):
        member = SimpleNamespace(
            id=100 + i, joined_at=now - timedelta(days=5),
            bot=False, pending=True, sent=[], mention=f"<@{100 + i}>",
        )

        async def send(content, *, allowed_mentions, _m=member):
            _m.sent.append((content, allowed_mentions))
        member.send = send
        members.append(member)

    async def fetch_member(member_id):
        user = next(m for m in members if m.id == member_id)
        return SimpleNamespace(**{**vars(user), "pending": fresh_pending})

    async def fetch_members(*, limit, after):
        for user in members:
            if after is None or user.id > after.id:
                yield user

    def get_role(role_id):
        return object() if role_id in (10, 11) else None

    return SimpleNamespace(
        id=1234, name="Demo guild", me=SimpleNamespace(id=999), members=members, fetch_members=fetch_members,
        fetch_member=fetch_member, get_role=get_role,
    )


def _setup(monkeypatch, *, enabled=True):
    cfg = {
        "source": "supabase:guild_configs",
        reminders.REMINDER_CONFIG_KEY: {"enabled": enabled},
        "unverified_role_id": "10",
        "verified_role_id": "11",
        "verify_channel_id": "77",
    }

    async def get_config(*args, **kwargs):
        return cfg

    monkeypatch.setattr(reminders, "get_guild_config", get_config)
    monkeypatch.setattr(reminders, "basic_verify_allowed_for_guild", lambda *a: True)
    monkeypatch.setattr(reminders, "clear_role_truth_cache", lambda *a: None)
    async def ready():
        return True

    monkeypatch.setattr(reminders, "ledger_ready", ready)
    monkeypatch.setattr(reminders, "verification_channel", lambda *a: SimpleNamespace(
        id=77, permissions_for=lambda member: SimpleNamespace(view_channel=True),
    ))
    monkeypatch.setattr(reminders, "pending_for_reminder", lambda member, config: member.pending)
    return cfg


def test_disabled_is_safe_and_never_fetches_members(monkeypatch):
    _setup(monkeypatch, enabled=False)
    guild = _guild()
    worker = reminders.ReminderWorker(SimpleNamespace())
    assert run(worker.scan_guild(guild, remaining=10)) == 0
    assert worker.member_cursors == {}


def test_verified_during_scan_is_not_claimed_or_messaged(monkeypatch):
    _setup(monkeypatch)
    guild = _guild(fresh_pending=False)
    worker = reminders.ReminderWorker(SimpleNamespace())
    claimed = []

    async def claim(*args):
        claimed.append(args)
        return True

    monkeypatch.setattr(reminders, "_claim_delivery", claim)
    assert run(worker.scan_guild(guild, remaining=10)) == 0
    assert not claimed
    assert all(not member.sent for member in guild.members)


def test_attempt_budget_counts_even_failed_dm(monkeypatch):
    _setup(monkeypatch)
    guild = _guild(count=4)
    worker = reminders.ReminderWorker(SimpleNamespace())
    claimed = []

    async def claim(*args):
        claimed.append(args)
        return True

    monkeypatch.setattr(reminders, "_claim_delivery", claim)
    async def no_sleep(*args):
        return None
    monkeypatch.setattr(reminders.asyncio, "sleep", no_sleep)
    assert run(worker.scan_guild(guild, remaining=2)) == 2
    assert len(claimed) == 2
    assert sum(len(m.sent) for m in guild.members) == 2
    assert worker.member_cursors[guild.id] == guild.members[1].id

    # A capped scan resumes at the NEXT unsent member, not the end of the page.
    assert run(worker.scan_guild(guild, remaining=2)) == 2
    assert len(claimed) == 4
    assert worker.member_cursors[guild.id] == 0
    assert sum(len(m.sent) for m in guild.members) == 4


def test_no_reminders_when_role_mapping_missing(monkeypatch):
    _setup(monkeypatch)
    guild = _guild()
    guild.get_role = lambda role_id: None
    worker = reminders.ReminderWorker(SimpleNamespace())
    assert run(worker.scan_guild(guild, remaining=10)) == 0


def test_worker_registration_is_idempotent(monkeypatch):
    # Static contract: worker single-owner startup and saved per-guild config gate.
    from pathlib import Path
    app = Path("stoney_verify/app.py").read_text()
    center = Path("stoney_verify/commands_ext/public_verify_command_center.py").read_text()
    assert "start_unverified_reminder_worker(bot)" in app
    assert "Unverified Reminders" in center
    assert "on_ready" in app

def test_channel_delivery_scoped_to_one_member_mention(monkeypatch):
    config = _setup(monkeypatch)
    config[reminders.REMINDER_CONFIG_KEY] = {
        "enabled": True, "delivery": "channel", "reminder_channel_id": "44",
    }
    guild = _guild(count=1)
    worker = reminders.ReminderWorker(SimpleNamespace())
    channel_messages = []

    class NoticeChannel:
        def permissions_for(self, member):
            return SimpleNamespace(view_channel=True, send_messages=True)

        async def send(self, content, *, allowed_mentions):
            channel_messages.append((content, allowed_mentions))

    monkeypatch.setattr(reminders, "delivery_channel", lambda *args: NoticeChannel())

    async def claim(*args):
        return True

    monkeypatch.setattr(reminders, "_claim_delivery", claim)

    async def no_sleep(*args):
        return None

    monkeypatch.setattr(reminders.asyncio, "sleep", no_sleep)
    assert run(worker.scan_guild(guild, remaining=1)) == 1
    assert len(channel_messages) == 1
    content, mentions = channel_messages[0]
    assert "<@100>" in content
    assert mentions.everyone is False
    assert mentions.roles is False
    assert len(mentions.users) == 1
    assert mentions.users[0].id == guild.members[0].id
    assert not guild.members[0].sent


def test_channel_disabled_when_bot_cannot_send(monkeypatch):
    cfg = _setup(monkeypatch)
    cfg[reminders.REMINDER_CONFIG_KEY] = {
        "enabled": True, "delivery": "channel", "reminder_channel_id": "44",
    }
    guild = _guild()
    worker = reminders.ReminderWorker(SimpleNamespace())
    monkeypatch.setattr(reminders, "delivery_channel", lambda *a: SimpleNamespace(
        permissions_for=lambda member: SimpleNamespace(view_channel=True, send_messages=False),
    ))
    assert run(worker.scan_guild(guild, remaining=10)) == 0
    assert worker.member_cursors == {}


def test_database_not_ready_blocks_scanning_before_member_fetch(monkeypatch):
    _setup(monkeypatch)
    guild = _guild()
    worker = reminders.ReminderWorker(SimpleNamespace())

    async def not_ready():
        return False

    monkeypatch.setattr(reminders, "ledger_ready", not_ready)
    assert run(worker.scan_guild(guild, remaining=10)) == 0
    assert worker.member_cursors == {}


def test_postgres_claim_path_is_single_owner(monkeypatch):
    class FakeMember:
        id = 3344
        joined_at = datetime.now(timezone.utc)

    calls = []
    def claim(guild_id, user_id, joined_at, *, repeat_hours, max_reminders):
        calls.append((guild_id, user_id, joined_at, repeat_hours, max_reminders))
        return True

    monkeypatch.setattr(reminders, "_pg_claim_sync", claim)
    assert run(reminders._claim_delivery(1234, FakeMember(), reminders.ReminderPolicy()))
    assert calls == [(1234, 3344, FakeMember.joined_at, 48, 3)]


def test_verifies_during_postgres_claim_cannot_receive_stale_reminder(monkeypatch):
    _setup(monkeypatch)
    guild = _guild(count=1)
    worker = reminders.ReminderWorker(SimpleNamespace())
    original_fetch = guild.fetch_member
    calls = []

    async def fetch_member(member_id):
        calls.append(member_id)
        fetched = await original_fetch(member_id)
        fetched.pending = len(calls) == 1
        return fetched

    async def claimed(*args):
        return True

    guild.fetch_member = fetch_member
    monkeypatch.setattr(reminders, "_claim_delivery", claimed)
    assert run(worker.scan_guild(guild, remaining=1)) == 1
    assert len(calls) == 2
    assert not guild.members[0].sent
