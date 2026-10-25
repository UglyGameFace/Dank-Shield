from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import verification_reminder_manual as manual
from stoney_verify import verification_reminders as reminders
from stoney_verify.commands_ext import public_verify_reminder_ui as ui


def run(coro):
    return asyncio.run(coro)


def setup(monkeypatch, *, count=4, initial_age_minutes=5):
    cfg = {
        "source": "supabase:guild_configs",
        "unverified_role_id": "10",
        "verified_role_id": "11",
        "verify_channel_id": "77",
        "unverified_reminders_v1": {
            "enabled": True, "delivery": "channel",
            "reminder_channel_id": "44", "delay_hours": 24,
            "repeat_hours": 48, "max_reminders": 3,
        },
    }
    members = [
        SimpleNamespace(
            id=100 + i, bot=False, pending=True,
            joined_at=datetime.now(timezone.utc)-timedelta(minutes=initial_age_minutes),
            mention=f"<@{100+i}>",
        ) for i in range(count)
    ]
    sent = []

    class Channel:
        def __init__(self, cid):
            self.id = cid
        def permissions_for(self, member):
            return SimpleNamespace(view_channel=True, send_messages=True)
        async def send(self, body, *, allowed_mentions):
            sent.append((body, allowed_mentions))

    verify, notice = Channel(77), Channel(44)

    async def fetch_member(uid):
        return next(m for m in members if m.id == uid)

    async def fetch_members(*, limit, after):
        entries = [m for m in members if after is None or m.id > after.id]
        for m in entries[:limit]:
            yield m

    guild = SimpleNamespace(
        id=1234, name="Guild", me=object(), fetch_member=fetch_member,
        fetch_members=fetch_members,
        get_role=lambda rid: object() if rid in (10, 11) else None,
    )
    bot = SimpleNamespace(get_guild=lambda gid: guild if gid == 1234 else None)
    worker = reminders.ReminderWorker(bot)
    worker.task = SimpleNamespace(done=lambda: False)
    monkeypatch.setattr(reminders, "_WORKER", worker)

    async def get_cfg(*args, **kwargs):
        return cfg

    async def ready():
        return True

    monkeypatch.setattr(manual, "get_guild_config", get_cfg)
    monkeypatch.setattr(manual, "basic_verify_allowed_for_guild", lambda *a: True)
    monkeypatch.setattr(manual, "verification_channel", lambda *a: verify)
    monkeypatch.setattr(manual, "delivery_channel", lambda *a: notice)
    monkeypatch.setattr(manual, "pending_for_reminder", lambda m, cfg: m.pending)
    monkeypatch.setattr(manual, "clear_role_truth_cache", lambda *_: None)
    monkeypatch.setattr(manual, "ledger_ready", ready)
    monkeypatch.setattr(manual, "manual_ready_sync", lambda: True)

    async def no_sleep(*_):
        return None
    monkeypatch.setattr(manual.asyncio, "sleep", no_sleep)
    return cfg, guild, bot, worker, members, sent


def test_manual_preview_finds_new_unverified_without_automatic_delay(monkeypatch):
    cfg, guild, bot, worker, members, sent = setup(monkeypatch)
    preview = run(manual.preview_manual_reminders(bot, guild, channel_id=44))
    assert preview.scanned == 4
    assert preview.pending == 4
    assert preview.before_automatic_delay == 4
    assert preview.potential == 4
    assert preview.candidates == (100, 101, 102)
    assert not sent
    assert not worker.member_cursors  # a manual peek cannot move the automatic sweep


def test_manual_confirm_uses_saved_channel_and_separate_reservations(monkeypatch):
    cfg, guild, bot, worker, members, sent = setup(monkeypatch)
    seen = []

    def reserve(gid, batch):
        seen.append((gid, batch))
        return tuple(uid for uid, _ in batch)

    monkeypatch.setattr(manual, "reserve_manual_batch_sync", reserve)
    preview = run(manual.preview_manual_reminders(bot, guild, channel_id=44))
    delivery = run(manual.send_previewed_manual_reminders(bot, guild, preview))
    assert delivery.reserved == delivery.delivered == 3
    assert delivery.failed == delivery.skipped == 0
    assert len(sent) == 3
    assert all(m.everyone is False and m.roles is False for _, m in sent)
    assert seen[0][0] == 1234
    assert not worker.member_cursors


def test_manual_confirm_rechecks_role_changes_and_policy_before_claim(monkeypatch):
    cfg, guild, bot, worker, members, sent = setup(monkeypatch)
    preview = run(manual.preview_manual_reminders(bot, guild, channel_id=44))
    members[0].pending = False
    recorded = []
    monkeypatch.setattr(
        manual, "reserve_manual_batch_sync",
        lambda gid, batch: recorded.append(batch) or tuple(uid for uid, _ in batch),
    )
    delivery = run(manual.send_previewed_manual_reminders(bot, guild, preview))
    assert delivery.delivered == 2
    assert 100 not in [i for i, _ in recorded[0]]
    cfg["unverified_reminders_v1"]["message"] = "New text"
    with pytest.raises(reminders.ManualReminderUnavailable, match="changed"):
        run(manual.send_previewed_manual_reminders(bot, guild, preview))


def test_manual_fails_closed_on_missing_migration_without_fetch(monkeypatch):
    cfg, guild, bot, worker, members, sent = setup(monkeypatch)
    monkeypatch.setattr(manual, "manual_ready_sync", lambda: False)
    with pytest.raises(reminders.ManualReminderUnavailable, match="manual reminder ledger"):
        run(manual.preview_manual_reminders(bot, guild, channel_id=44))
    assert not sent


def test_manual_paging_sees_unverified_after_first_100(monkeypatch):
    cfg, guild, bot, worker, members, sent = setup(monkeypatch, count=110)
    for member in members[:100]:
        member.pending = False
    first = run(manual.preview_manual_reminders(bot, guild, channel_id=44))
    assert first.scanned == 100 and first.pending == 0
    assert first.candidates == () and first.next_after == 199
    second = run(manual.preview_manual_reminders(
        bot, guild, channel_id=44, after=first.next_after,
    ))
    assert second.scanned == 10 and second.pending == 10
    assert second.candidates == (200, 201, 202)


def test_manual_discord_ui_has_separate_actions_with_valid_rows():
    policy = reminders.ReminderPolicy(
        enabled=True, delivery="channel", reminder_channel_id=44,
    )
    view = ui.ReminderSettingsView(7, policy)
    labels = {child.label for child in view.children}
    assert "Check Scheduled Due" in labels
    assert "Send Reminder Now" in labels
    assert "Check Database" in labels
    assert all(child.row is not None and 0 <= child.row <= 4 for child in view.children)
    assert len(view.children) <= 25
    preview = manual.ManualPreview(
        after=0, next_after=100, scanned=100, pending=4, without_access=0,
        before_automatic_delay=4, potential=4, candidates=(100, 101, 102),
        guild_id=1234, channel_id=44, policy_proof="ok", message="Please verify",
    )
    modal = ui.ManualPreviewView(7, preview)
    assert {button.label for button in modal.children} == {
        "Confirm Manual Send", "Next 100 Members", "Cancel",
    }
    assert not modal.confirm_manual.disabled and not modal.next_page.disabled
