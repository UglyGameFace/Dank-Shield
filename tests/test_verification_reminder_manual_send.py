from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import verification_reminder_manual as manual
from stoney_verify import verification_reminders as reminders
from stoney_verify.commands_ext import public_verify_reminder_ui as ui
from stoney_verify.members_new.membership_authority import MembershipSnapshot


def run(coro):
    return asyncio.run(coro)


def setup(monkeypatch, *, count=24, delivery="channel"):
    cfg = {
        "source": "supabase:guild_configs",
        "unverified_role_id": "10", "verified_role_id": "11",
        "verify_channel_id": "77",
        "unverified_reminders_v1": {
            "enabled": True, "delivery": delivery, "reminder_channel_id": "44",
            "delay_hours": 24, "repeat_hours": 48, "max_reminders": 3,
        },
    }
    sent, dms = [], []
    members = [
        SimpleNamespace(
            id=100 + i, bot=False, pending=True,
            display_name=f"🍃 Pending {i+1}",
            roles=[SimpleNamespace(id=10, name="🍃 Awaiting Access", is_default=lambda: False)],
            joined_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            mention=f"<@{100+i}>",
        )
        for i in range(count)
    ]
    for member in members:
        async def dm(text, *, allowed_mentions, _m=member):
            dms.append((_m.id, text, allowed_mentions))
        member.send = dm

    class Channel:
        def __init__(self, cid):
            self.id = cid

        def permissions_for(self, member):
            return SimpleNamespace(view_channel=True, send_messages=True)

        async def send(self, text, *, allowed_mentions):
            sent.append((text, allowed_mentions))

    verify, notice = Channel(77), Channel(44)

    async def fetch_member(uid):
        return next(m for m in members if m.id == uid)

    async def fetch_members(*, limit, after):
        for member in members:
            if after is None or member.id > after.id:
                yield member

    guild = SimpleNamespace(
        id=1234, name="The Guild", me=object(), member_count=count,
        fetch_member=fetch_member, fetch_members=fetch_members,
        get_role=lambda rid: object() if rid in (10, 11) else None,
    )
    for member in members:
        member.guild = guild
    bot = SimpleNamespace(get_guild=lambda gid: guild if gid == guild.id else None)
    worker = reminders.ReminderWorker(bot)
    worker.task = SimpleNamespace(done=lambda: False)
    monkeypatch.setattr(reminders, "_WORKER", worker)

    async def get_cfg(*args, **kwargs):
        return cfg

    async def ready():
        return True

    async def roster(_guild):
        return MembershipSnapshot(tuple(members), True, "discord_fetch_members")

    async def no_sleep(*args):
        return None

    monkeypatch.setattr(manual, "get_guild_config", get_cfg)
    monkeypatch.setattr(manual, "collect_membership_snapshot", roster)
    monkeypatch.setattr(manual, "basic_verify_allowed_for_guild", lambda *a: True)
    monkeypatch.setattr(manual, "verification_channel", lambda *a: verify)
    monkeypatch.setattr(manual, "delivery_channel", lambda *a: notice if delivery == "channel" else None)
    monkeypatch.setattr(manual, "pending_for_reminder", lambda m, cfg: m.pending)
    monkeypatch.setattr(manual, "clear_role_truth_cache", lambda *_: None)
    monkeypatch.setattr(manual, "ledger_ready", ready)
    monkeypatch.setattr(manual, "manual_ready_sync", lambda: True)
    monkeypatch.setattr(
        manual, "available_manual_member_ids_sync",
        lambda gid, batch: {uid for uid, _ in batch},
    )
    monkeypatch.setattr(manual.asyncio, "sleep", no_sleep)
    return cfg, guild, bot, worker, members, sent, dms


def _reserve_everyone(monkeypatch):
    monkeypatch.setattr(
        manual, "reserve_manual_batch_sync",
        lambda gid, batch: tuple(uid for uid, _ in batch),
    )


def test_preview_scans_124_members_and_finds_all_24_with_custom_named_role(monkeypatch):
    cfg, guild, bot, worker, members, sent, dms = setup(monkeypatch, count=124)
    for member in members[:100]:
        member.pending = False
        member.roles = [SimpleNamespace(id=11, name="🎮 Players", is_default=lambda: False)]
    preview = run(manual.preview_manual_reminders(bot, guild, channel_id=44))
    assert preview.scanned == 124
    assert preview.mapped_pending == 24
    assert preview.pending == 24
    assert preview.potential == 24
    assert preview.candidates == tuple(range(200, 224))
    assert preview.before_automatic_delay == 24
    assert preview.role_id == 10 and preview.delivery == "channel"
    assert sent == [] and worker.member_cursors == {}


def test_confirm_sends_24_mentions_in_one_channel_message(monkeypatch):
    cfg, guild, bot, worker, members, sent, dms = setup(monkeypatch)
    _reserve_everyone(monkeypatch)
    preview = run(manual.preview_manual_reminders(bot, guild, channel_id=44))
    report = run(manual.send_previewed_manual_reminders(bot, guild, preview))
    assert (report.selected, report.reserved, report.delivered, report.failed) == (24, 24, 24, 0)
    assert len(sent) == 1 and len(dms) == 0
    assert all(member.mention in sent[0][0] for member in members)
    assert sent[0][1].roles is False and sent[0][1].everyone is False
    assert worker.member_cursors == {}


def test_manual_dm_sends_same_full_group_without_public_post(monkeypatch):
    cfg, guild, bot, worker, members, sent, dms = setup(monkeypatch, delivery="dm")
    _reserve_everyone(monkeypatch)
    preview = run(manual.preview_manual_reminders(bot, guild, channel_id=0))
    assert preview.delivery == "dm" and preview.channel_id == 0
    assert preview.pending == 24 and preview.potential == 24
    report = run(manual.send_previewed_manual_reminders(bot, guild, preview))
    assert report.delivered == 24 and report.failed == 0
    assert len(dms) == 24 and sent == []
    assert all(ping.users is False for _, _, ping in dms)


def test_member_selection_can_reduce_not_expand_eligible_members(monkeypatch):
    cfg, guild, bot, worker, members, sent, dms = setup(monkeypatch)
    _reserve_everyone(monkeypatch)
    from dataclasses import replace
    preview = run(manual.preview_manual_reminders(bot, guild, channel_id=44))
    selected = replace(preview, candidates=(100, 105))
    assert run(manual.send_previewed_manual_reminders(bot, guild, selected)).delivered == 2
    assert len(sent) == 1
    assert "<@100>" in sent[0][0] and "<@105>" in sent[0][0]
    assert "<@101>" not in sent[0][0]
    with pytest.raises(reminders.ManualReminderUnavailable, match="invalid"):
        run(manual.send_previewed_manual_reminders(
            bot, guild, replace(preview, candidates=(99999,)),
        ))


def test_additional_eligible_groups_do_not_fetch_same_guild_again(monkeypatch):
    cfg, guild, bot, worker, members, sent, dms = setup(monkeypatch, count=60)
    first = run(manual.preview_manual_reminders(bot, guild, channel_id=44))
    assert first.scanned == 60 and first.pending == 60
    assert first.candidates == tuple(range(100,125))
    second = manual.select_manual_group(first)
    assert second.candidates == tuple(range(125,150))
    third = manual.select_manual_group(second)
    assert third.candidates == tuple(range(150,160))
    assert third.next_after == 0


def test_unverified_role_conflict_is_visible_but_not_eligible(monkeypatch):
    cfg, guild, bot, worker, members, sent, dms = setup(monkeypatch, count=24)
    for member in members[:5]:
        member.pending = False  # stands in for canonical role-truth verified conflict
    preview = run(manual.preview_manual_reminders(bot, guild, channel_id=44))
    assert preview.mapped_pending == 24 and preview.already_verified_or_staff == 5
    assert preview.pending == 19 and preview.potential == 19


def test_incomplete_full_scan_blocks_instead_of_claiming_zero(monkeypatch):
    cfg, guild, bot, worker, members, sent, dms = setup(monkeypatch)
    async def partial(_):
        return MembershipSnapshot(tuple(members[:3]), False, "discord_member_cache")
    monkeypatch.setattr(manual, "collect_membership_snapshot", partial)
    with pytest.raises(reminders.ManualReminderUnavailable, match="Could not finish"):
        run(manual.preview_manual_reminders(bot, guild, channel_id=44))
    assert sent == [] and dms == []


def test_role_change_and_policy_change_are_rechecked(monkeypatch):
    cfg, guild, bot, worker, members, sent, dms = setup(monkeypatch)
    preview = run(manual.preview_manual_reminders(bot, guild, channel_id=44))
    members[0].pending = False
    recorded = []
    monkeypatch.setattr(
        manual, "reserve_manual_batch_sync",
        lambda gid, batch: recorded.append(batch) or tuple(uid for uid, _ in batch),
    )
    report = run(manual.send_previewed_manual_reminders(bot, guild, preview))
    assert report.delivered == 23
    assert 100 not in [uid for uid, _ in recorded[0]]
    cfg["unverified_reminders_v1"]["message"] = "Changed after preview"
    with pytest.raises(reminders.ManualReminderUnavailable, match="changed"):
        run(manual.send_previewed_manual_reminders(bot, guild, preview))


def test_claim_time_policy_change_skips_reserved_sends(monkeypatch):
    cfg, guild, bot, worker, members, sent, dms = setup(monkeypatch)
    preview = run(manual.preview_manual_reminders(bot, guild, channel_id=44))
    def reserve(gid, batch):
        cfg["unverified_reminders_v1"]["message"] = "New policy after claim"
        return tuple(uid for uid, _ in batch)
    monkeypatch.setattr(manual, "reserve_manual_batch_sync", reserve)
    report = run(manual.send_previewed_manual_reminders(bot, guild, preview))
    assert report.reserved == 24 and report.delivered == 0 and report.skipped == 24
    assert sent == []


def test_manual_fails_closed_on_missing_database_and_config(monkeypatch):
    cfg, guild, bot, worker, members, sent, dms = setup(monkeypatch)
    monkeypatch.setattr(manual, "manual_ready_sync", lambda: False)
    with pytest.raises(reminders.ManualReminderUnavailable, match="private reminder database"):
        run(manual.preview_manual_reminders(bot, guild, channel_id=44))
    async def config_down(*args, **kwargs):
        raise OSError("secret connection value")
    monkeypatch.setattr(manual, "get_guild_config", config_down)
    with pytest.raises(reminders.ManualReminderUnavailable, match="Could not read"):
        run(manual.preview_manual_reminders(bot, guild, channel_id=44))


def test_manual_channel_uncertain_failure_never_retries(monkeypatch):
    cfg, guild, bot, worker, members, sent, dms = setup(monkeypatch)
    preview = run(manual.preview_manual_reminders(bot, guild, channel_id=44))
    calls = []
    async def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("Discord failed")
    monkeypatch.setattr(manual, "send_channel_reminder_message", fail)
    _reserve_everyone(monkeypatch)
    report = run(manual.send_previewed_manual_reminders(bot, guild, preview))
    assert report.reserved == 24 and report.failed == 24 and report.delivered == 0
    assert calls == [1]


def test_discord_ui_has_selectable_25_member_groups_and_two_delivery_modes():
    for delivery in ("channel", "dm"):
        policy = reminders.ReminderPolicy(
            enabled=True, delivery=delivery, reminder_channel_id=44,
        )
        menu = ui.ReminderSettingsView(7, policy)
        labels = {child.label for child in menu.children}
        assert "Send Reminder Now" in labels
        assert "Check Scheduled Due" in labels
        assert len(menu.children) <= 25
    preview = manual.ManualPreview(
        after=0, next_after=25, scanned=60, pending=60,
        without_access=0, before_automatic_delay=60, manual_limited=0,
        potential=60, candidates=tuple(range(100,125)), guild_id=1234,
        channel_id=44, policy_proof="proof", message="Verify",
        eligible_ids=tuple(range(100,160)),
        eligible_names=tuple(f"Person {i}" for i in range(60)),
        role_id=10,
    )
    view = ui.ManualPreviewView(7, preview)
    assert {item.label for item in view.children if isinstance(item, discord.ui.Button)} == {
        "Confirm Manual Send", "Next Eligible Group", "Cancel",
    }
    assert isinstance(view.recipient_selector, discord.ui.Select)
    assert len(view.recipient_selector.options) == 25
    assert all(option.default for option in view.recipient_selector.options)
    assert view.next_page.disabled is False
