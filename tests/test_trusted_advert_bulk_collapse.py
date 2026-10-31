"""Durable Smart Bulk Collapse: safe cursor, cancellation, budgets and restart."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path

import discord
import pytest

from stoney_verify import trusted_advert_bulk as bulk
from stoney_verify import invite_trusted_advertisers as trust
from stoney_verify.commands_ext import trusted_advert_ui as ui


GUILD = 111111111111111111
CHANNEL = 222222222222222222
BOT = 333333333333333333
OWNER = 444444444444444444


def _policy():
    return {trust.CONFIG_KEY: [{
        "bot_id": BOT, "channel_id": CHANNEL,
        "cleanup_enabled": True, "retention_hours": 24,
        "suppress_embeds": True,
    }]}


def _message(mid, *, other=False, suppressed=False):
    return SimpleNamespace(
        id=mid, guild=None, channel=None,
        author=SimpleNamespace(id=555555555555555555 if other else BOT, bot=True),
        webhook_id=None, application_id=None, interaction=None,
        interaction_metadata=None,
        embeds=[SimpleNamespace(title="Public gaming advert")],
        flags=SimpleNamespace(suppress_embeds=suppressed),
        edit=AsyncMock(), delete=AsyncMock(),
    )


class FakeChannel:
    id = CHANNEL
    last_message_id = 70
    def __init__(self, messages):
        self.messages = sorted(messages, key=lambda m: m.id, reverse=True)
        self.before = []
    def permissions_for(self, me):
        return SimpleNamespace(
            view_channel=True, read_message_history=True,
            manage_messages=True,
        )
    async def history(self, *, limit, before=None):
        self.before.append((limit, getattr(before, "id", None)))
        data = [m for m in self.messages
                if before is None or m.id < before.id][:limit]
        for msg in data:
            yield msg


def _setup(monkeypatch, *, count=50, suppressed=()):
    messages = [_message(70 - idx, other=idx % 13 == 0,
                         suppressed=idx in suppressed) for idx in range(count)]
    channel = FakeChannel(messages)
    guild = SimpleNamespace(
        id=GUILD, me=SimpleNamespace(id=666666666666666666, bot=True),
        get_channel=lambda _id: channel if _id == CHANNEL else None,
    )
    for m in messages:
        m.guild, m.channel = guild, channel
    bot = SimpleNamespace(get_guild=lambda _id: guild if _id == GUILD else None)
    monkeypatch.setattr(bulk.discord, "TextChannel", FakeChannel)
    monkeypatch.setattr(bulk, "_approved", AsyncMock(return_value=True))
    monkeypatch.setattr(bulk, "_take_auto_slot", AsyncMock(return_value=True))
    from stoney_verify import invite_policy_engine
    monkeypatch.setattr(invite_policy_engine, "load_invite_policy",
                        AsyncMock(return_value=(_policy(), {})))
    monkeypatch.setattr(bulk, "_CANCELLED_LOCAL", set())
    return bot, channel, messages


def _claim(*, cursor=None, manual=False, scanned=0, collapsed=0, skipped=0):
    return {
        "guild_id": str(GUILD), "channel_id": str(CHANNEL),
        "bot_id": str(BOT), "claim_version": 1,
        "manual_only": manual, "cursor_before": cursor,
        "scanned": scanned, "collapsed": collapsed, "skipped": skipped,
        "failures": 0,
    }


def test_bulk_scans_in_bounded_chunks_and_persists_exact_next_cursor(monkeypatch):
    bot, channel, messages = _setup(monkeypatch)
    edits = AsyncMock(return_value=True)
    checkpoint = AsyncMock(return_value=True)
    monkeypatch.setattr(bulk, "suppress_one", edits)
    monkeypatch.setattr(bulk, "_checkpoint", checkpoint)
    result = asyncio.run(bulk.process_bulk_claim(bot, _claim()))
    assert result == "checkpoint"
    assert channel.before == [(bulk.CHUNK_SCAN_LIMIT, None)]
    # First ad by another bot is skipped; six authorized embeds processed.
    assert edits.await_count == 6
    assert all(call.kwargs["slot_reserved"] for call in edits.await_args_list)
    kwargs = checkpoint.await_args.kwargs
    assert kwargs["cursor"] == str(messages[6].id)
    assert kwargs["scanned"] == 7
    assert kwargs["collapsed"] == 6
    assert kwargs["skipped"] == 1
    assert kwargs["complete"] is False
    assert all(m.delete.await_count == 0 for m in messages)


def test_when_write_budget_is_full_bulk_does_not_skip_eligible_post(monkeypatch):
    bot, channel, messages = _setup(monkeypatch, count=4)
    slots = AsyncMock(return_value=False)
    checkpoint = AsyncMock(return_value=True)
    edit = AsyncMock(return_value=True)
    monkeypatch.setattr(bulk, "_take_auto_slot", slots)
    monkeypatch.setattr(bulk, "suppress_one", edit)
    monkeypatch.setattr(bulk, "_checkpoint", checkpoint)
    result = asyncio.run(bulk.process_bulk_claim(bot, _claim(cursor="71")))
    assert result == "checkpoint"
    assert checkpoint.await_args.kwargs["cursor"] == str(messages[0].id)
    assert checkpoint.await_args.kwargs["scanned"] == 1
    assert checkpoint.await_args.kwargs["retry_delay"] == 60
    edit.assert_not_awaited()


def test_auto_off_one_time_scan_is_separately_authorized(monkeypatch):
    bot, channel, messages = _setup(monkeypatch, count=2)
    monkeypatch.setattr(bulk, "_checkpoint", AsyncMock(return_value=True))
    edit = AsyncMock(return_value=True)
    monkeypatch.setattr(bulk, "suppress_one", edit)
    outcome = asyncio.run(bulk.process_bulk_claim(bot, _claim(manual=True)))
    assert outcome == "completed"
    assert edit.await_args.kwargs["automatic"] is False


def test_cancel_stops_before_next_edit_and_revocation_never_edits(monkeypatch):
    bot, channel, messages = _setup(monkeypatch, count=5)
    bulk._CANCELLED_LOCAL.add(bulk._key(GUILD, CHANNEL, BOT))
    edit = AsyncMock(return_value=True)
    monkeypatch.setattr(bulk, "suppress_one", edit)
    result = asyncio.run(bulk.process_bulk_claim(bot, _claim()))
    assert result == "cancelled"
    edit.assert_not_awaited()
    bulk._CANCELLED_LOCAL.clear()
    monkeypatch.setattr(bulk, "_approved", AsyncMock(return_value=False))
    abort = AsyncMock()
    monkeypatch.setattr(bulk, "_abort", abort)
    result = asyncio.run(bulk.process_bulk_claim(bot, _claim()))
    assert result == "revoked"
    abort.assert_awaited_once()
    edit.assert_not_awaited()


def test_missing_channel_permission_never_scans(monkeypatch):
    bot, channel, messages = _setup(monkeypatch, count=2)
    monkeypatch.setattr(bulk, "_permissions", lambda guild, chan: False)
    checkpoint = AsyncMock(return_value=True)
    monkeypatch.setattr(bulk, "_checkpoint", checkpoint)
    assert asyncio.run(bulk.process_bulk_claim(bot, _claim())) == "retry"
    assert channel.before == []
    assert checkpoint.await_args.kwargs["error"] == "RuntimeError"


def test_status_and_cancel_are_staff_only_and_old_cleanup_independent():
    view = ui.TrustedAdsView(OWNER, SimpleNamespace(), CHANNEL,
                             rules=trust.clean_rules(_policy()), section="retention")
    labels = [getattr(child, "label", "") for child in view.children]
    assert "Bulk Progress" in labels
    assert "Cancel Bulk" in labels
    assert "Enable Cleanup" in labels
    assert "Collapse Previews" in labels
    assert len(labels) <= 25
    modal = ui.TrustedPreviewModal(OWNER, channel_id=CHANNEL, bot_id=BOT)
    assert len(modal.action.label) <= 45
    assert modal.action.default == "ON+RECENT"
    assert "Checked" not in ui._bulk_status_message({
        "status": "running", "scanned": 30, "collapsed": 6, "skipped": 24,
    })
    assert "Messages checked: 30" in ui._bulk_status_message({
        "status": "running", "scanned": 30, "collapsed": 6, "skipped": 24,
    })


def test_persisted_job_privacy_atomic_claims_and_no_delete_operations():
    root = Path(__file__).resolve().parents[1]
    sql = (root / "supabase/migrations/20261026193000_trusted_advert_bulk_collapse.sql").read_text()
    code = (root / "stoney_verify/trusted_advert_bulk.py").read_text()
    assert "enable row level security" in sql
    assert "for update skip locked" in sql
    assert "job.status in ('completed','cancelled','failed')" in sql
    assert "revoke all on public.dank_trusted_advert_collapse_jobs" in sql
    assert "grant execute on function public.dank_claim_trusted_embed_jobs" in sql
    assert ".purge(" not in code
    assert ".delete(" not in code
    assert ".delete_messages(" not in code
    assert "CHUNK_SCAN_LIMIT = 25" in code
    assert "CHUNK_EDIT_LIMIT = 6" in code
    assert "claim_version" in code
    assert "cursor_before" in code
