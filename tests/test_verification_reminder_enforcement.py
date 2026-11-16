from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord

from stoney_verify import verification_reminder_enforcement as enforcement
from stoney_verify import verification_reminders as reminders


def run(coro):
    return asyncio.run(coro)


def test_enforcement_policy_defaults_off_and_rejects_untrusted_actions():
    assert reminders.parse_policy({}).after_max_action == "none"
    cfg = {reminders.REMINDER_CONFIG_KEY: {
        "enabled": True, "after_max_action": "BAN", "after_max_grace_hours": -99,
    }}
    policy = reminders.parse_policy(cfg)
    assert policy.after_max_action == "none"
    assert policy.after_max_grace_hours == 1
    configured = reminders.ReminderPolicy(after_max_action="kick", after_max_grace_hours=24)
    assert reminders.parse_policy({reminders.REMINDER_CONFIG_KEY: configured.to_payload()}) == configured


def _setup_action(monkeypatch, *, action="kick", pending=True, joined=None, bot_can=True):
    from stoney_verify import guild_config
    from stoney_verify.setup_engine import verification_modes

    joined = joined or datetime.now(timezone.utc) - timedelta(days=5)
    old = reminders.ReminderPolicy(
        enabled=True, after_max_action=action, after_max_grace_hours=24,
    )
    cfg = {
        reminders.REMINDER_CONFIG_KEY: old.to_payload(),
        "unverified_role_id": "10", "verified_role_id": "11",
        "verify_channel_id": "77", "source": "supabase:guild_configs",
    }
    async def get_cfg(*args, **kwargs):
        return cfg

    monkeypatch.setattr(guild_config, "get_guild_config", get_cfg)
    monkeypatch.setattr(verification_modes, "basic_verify_allowed_for_guild", lambda *args: True)
    monkeypatch.setattr(reminders, "pending_for_reminder", lambda member, cfg: member.pending)
    monkeypatch.setattr(reminders, "verification_channel", lambda *args: SimpleNamespace(
        permissions_for=lambda member: SimpleNamespace(view_channel=True),
    ))
    monkeypatch.setattr(enforcement, "enforcement_ready_sync", lambda: True)
    monkeypatch.setattr(enforcement, "due_candidates_sync", lambda *a, **kw: [(100, joined)])
    outcome = []
    monkeypatch.setattr(enforcement, "reserve_action_sync", lambda *a, **kw: True)
    monkeypatch.setattr(enforcement, "finish_action_sync", lambda *args: outcome.append(args) or True)
    async def log(*args):
        return None
    monkeypatch.setattr(enforcement, "_write_modlog", log)

    me = SimpleNamespace(
        id=999, top_role=20,
        guild_permissions=SimpleNamespace(kick_members=bot_can, ban_members=bot_can),
    )
    member = SimpleNamespace(
        id=100, joined_at=joined, pending=pending, bot=False, top_role=5,
        guild_permissions=SimpleNamespace(
            administrator=False, manage_guild=False, manage_roles=False,
            moderate_members=False, kick_members=False, ban_members=False,
        ),
    )
    actions = []
    async def fetch_member(member_id):
        assert member_id == 100
        return member
    async def kick(member, *, reason):
        actions.append(("kick", member.id, reason))
    async def ban(member, *, reason, delete_message_seconds):
        actions.append(("ban", member.id, reason, delete_message_seconds))

    guild = SimpleNamespace(
        id=1234, me=me, owner_id=1, get_role=lambda rid: object() if rid in (10, 11) else None,
        fetch_member=fetch_member, kick=kick, ban=ban, get_channel=lambda cid: None,
    )
    return guild, member, cfg, actions, outcome


def test_does_not_remove_verified_or_rejoined_member(monkeypatch):
    guild, member, cfg, actions, outcomes = _setup_action(monkeypatch, pending=False)
    assert run(enforcement.enforce_guild(guild)) == 0
    assert not actions and not outcomes

    member.pending = True
    member.joined_at += timedelta(days=1)
    assert run(enforcement.enforce_guild(guild)) == 0
    assert not actions and not outcomes


def test_removal_requires_bot_privileges_and_below_bot_role(monkeypatch):
    guild, member, cfg, actions, outcomes = _setup_action(monkeypatch, bot_can=False)
    assert run(enforcement.enforce_guild(guild)) == 0
    guild.me.guild_permissions.kick_members = True
    member.top_role = 30
    assert run(enforcement.enforce_guild(guild)) == 0
    assert not actions and not outcomes


def test_successful_kick_and_ban_are_one_shot_and_logged(monkeypatch):
    guild, member, cfg, actions, outcomes = _setup_action(monkeypatch, action="kick")
    assert run(enforcement.enforce_guild(guild)) == 1
    assert [a[0] for a in actions] == ["kick"]
    assert outcomes[-1][-1] == "kicked"

    cfg[reminders.REMINDER_CONFIG_KEY]["after_max_action"] = "ban"
    assert run(enforcement.enforce_guild(guild)) == 1
    assert [a[0] for a in actions] == ["kick", "ban"]
    assert actions[-1][-1] == 0
    assert outcomes[-1][-1] == "banned"


def test_unchanged_policy_rechecked_after_reservation(monkeypatch):
    guild, member, cfg, actions, outcomes = _setup_action(monkeypatch)
    from stoney_verify import guild_config

    calls = []
    async def changing_cfg(*args, **kwargs):
        calls.append(1)
        if len(calls) >= 3:
            return {**cfg, reminders.REMINDER_CONFIG_KEY: {**cfg[reminders.REMINDER_CONFIG_KEY], "after_max_action": "none"}}
        return cfg
    monkeypatch.setattr(guild_config, "get_guild_config", changing_cfg)
    assert run(enforcement.enforce_guild(guild)) == 0
    assert not actions
    assert outcomes[-1][-1] == "ineligible"


def test_action_reservation_failure_never_kicks(monkeypatch):
    guild, member, cfg, actions, outcomes = _setup_action(monkeypatch)
    monkeypatch.setattr(enforcement, "reserve_action_sync", lambda *a, **kw: False)
    assert run(enforcement.enforce_guild(guild)) == 0
    assert not actions and not outcomes


def test_manual_configuration_requires_server_manager_and_action_permission():
    from stoney_verify.commands_ext.public_verify_reminder_ui import _can_configure_action

    perms = SimpleNamespace(
        administrator=False, manage_guild=False,
        ban_members=False, kick_members=False,
    )
    interaction = SimpleNamespace(
        guild=SimpleNamespace(owner_id=1),
        user=SimpleNamespace(id=100, guild_permissions=perms),
    )
    assert not _can_configure_action(interaction, "none")
    perms.manage_guild = True
    assert _can_configure_action(interaction, "none")
    assert not _can_configure_action(interaction, "kick")
    assert not _can_configure_action(interaction, "ban")
    perms.kick_members = True
    assert _can_configure_action(interaction, "kick")
    assert not _can_configure_action(interaction, "ban")


def test_mod_and_verify_centers_expose_action_settings():
    from stoney_verify.commands_ext.public_verify_reminder_ui import ReminderSettingsView, ReminderSetupMenuView
    from stoney_verify.commands_ext.public_mod_command_center import ModerationCenterView

    assert any(b.label == "Configure" for b in ReminderSettingsView(123, reminders.ReminderPolicy()).children)
    assert any(b.label == "After 3 Reminders" for b in ReminderSetupMenuView(123, reminders.ReminderPolicy()).children)
    assert len([b for b in ReminderSettingsView(123, reminders.ReminderPolicy()).children
                if isinstance(b, discord.ui.Button)]) <= 11
    assert any(b.label == "Unverified Enforcement" for b in ModerationCenterView(123).children)


def test_only_discord_accepted_scheduled_sends_are_recorded(monkeypatch):
    now = datetime.now(timezone.utc)
    cfg = {
        reminders.REMINDER_CONFIG_KEY: {
            "enabled": True, "after_max_action": "kick", "max_reminders": 3,
        },
        "unverified_role_id": "10", "verified_role_id": "11",
        "verify_channel_id": "77",
    }
    member = SimpleNamespace(
        id=100, joined_at=now - timedelta(days=5), bot=False, pending=True,
        mention="<@100>",
    )
    successes = []
    async def send(*args, **kwargs):
        successes.append("sent")
    member.send = send

    async def fetch_member(member_id):
        return member
    async def fetch_members(*, limit, after):
        yield member
    guild = SimpleNamespace(
        id=123, name="Demo", me=SimpleNamespace(id=999),
        get_role=lambda rid: object(),
        fetch_members=fetch_members, fetch_member=fetch_member,
    )
    async def config(*args, **kwargs):
        return cfg
    async def ready():
        return True
    async def claim(*args):
        return True
    async def no_sleep(*args):
        return None

    monkeypatch.setattr(reminders, "get_guild_config", config)
    monkeypatch.setattr(reminders, "basic_verify_allowed_for_guild", lambda *args: True)
    monkeypatch.setattr(reminders, "ledger_ready", ready)
    monkeypatch.setattr(reminders, "_claim_delivery", claim)
    monkeypatch.setattr(reminders, "clear_role_truth_cache", lambda *args: None)
    monkeypatch.setattr(reminders, "pending_for_reminder", lambda member, cfg: member.pending)
    monkeypatch.setattr(reminders, "verification_channel", lambda *args: SimpleNamespace(
        permissions_for=lambda member: SimpleNamespace(view_channel=True),
    ))
    monkeypatch.setattr(reminders, "reminder_text", lambda *args: "Verify here")
    monkeypatch.setattr(reminders.asyncio, "sleep", no_sleep)
    recorded = []
    monkeypatch.setattr(enforcement, "record_delivery_sync", lambda *args: recorded.append(args) or True)

    worker = reminders.ReminderWorker(SimpleNamespace())
    assert run(worker.scan_guild(guild, remaining=1)) == 1
    assert successes and len(recorded) == 1

    async def rejected(*args, **kwargs):
        raise discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"),
            {"code": 50007, "message": "Cannot send messages to this user"},
        )
    member.send = rejected
    recorded.clear()
    assert run(worker.scan_guild(guild, remaining=1)) == 1
    assert recorded == []


def test_enforcement_ignores_staff_control_roles_and_moderation_permissions(monkeypatch):
    guild, member, cfg, actions, outcomes = _setup_action(monkeypatch)
    member.guild_permissions.manage_roles = True
    assert run(enforcement.enforce_guild(guild)) == 0
    member.guild_permissions.manage_roles = False
    cfg["staff_role_id"] = "12345"
    member.roles = [SimpleNamespace(id=12345)]
    assert run(enforcement.enforce_guild(guild)) == 0
    assert actions == [] and outcomes == []


requires_postgres = pytest.mark.skipif(
    os.getenv("RUN_REMINDER_PG_INTEGRATION") != "1",
    reason="Private PostgreSQL integration suite runs in isolated CI",
)


@requires_postgres
def test_postgres_confirmed_deliveries_atomic_reservation_and_rejoin_isolation():
    guild_id = int(str(uuid4().int)[:18])
    member_id = int(str(uuid4().int)[:18])
    joined = datetime.now(timezone.utc) - timedelta(days=5)
    assert enforcement.enforcement_ready_sync()

    for n in range(3):
        assert enforcement.record_delivery_sync(guild_id, member_id, joined)
    assert enforcement.due_candidates_sync(guild_id, grace_hours=24) == []

    with enforcement._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update public.dank_verify_reminder_enforcement "
                "set last_delivered_at = now() - interval '25 hours' "
                "where guild_id = %s and user_id = %s and joined_at = %s",
                (str(guild_id), str(member_id), joined),
            )
    assert enforcement.due_candidates_sync(guild_id, grace_hours=24) == [(member_id, joined)]

    def attempt(_):
        return enforcement.reserve_action_sync(
            guild_id, member_id, joined, grace_hours=24,
        )
    with ThreadPoolExecutor(max_workers=8) as executor:
        attempts = list(executor.map(attempt, range(8)))
    assert attempts.count(True) == 1 and attempts.count(False) == 7
    assert not enforcement.reserve_action_sync(
        guild_id, member_id, joined, grace_hours=24,
    )
    assert not enforcement.record_delivery_sync(guild_id, member_id, joined)
    assert enforcement.finish_action_sync(guild_id, member_id, joined, "kicked")
    assert enforcement.due_candidates_sync(guild_id, grace_hours=24) == []

    rejoined = joined + timedelta(days=1)
    assert enforcement.record_delivery_sync(guild_id, member_id, rejoined)
    assert enforcement.due_candidates_sync(guild_id, grace_hours=24) == []


@requires_postgres
def test_enforcement_retention_requires_old_delivery_and_exact_join():
    guild_id = int(str(uuid4().int)[:18])
    member_id = int(str(uuid4().int)[:18])
    joined = datetime.now(timezone.utc) - timedelta(days=150)
    assert enforcement.record_delivery_sync(guild_id, member_id, joined)
    with enforcement._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update public.dank_verify_reminder_enforcement "
                "set last_delivered_at = now() - interval '95 days' "
                "where guild_id = %s and user_id = %s",
                (str(guild_id), str(member_id)),
            )
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    rows = enforcement.stale_candidates_sync(cutoff, limit=50)
    assert (str(guild_id), str(member_id), joined) in [
        (gid, uid, started) for gid, uid, started, _ in rows
    ]
    assert not enforcement.delete_departed_sync(
        str(guild_id), str(member_id), joined + timedelta(days=1), cutoff,
    )
    assert enforcement.delete_departed_sync(str(guild_id), str(member_id), joined, cutoff)
    assert not enforcement.delete_departed_sync(str(guild_id), str(member_id), joined, cutoff)
