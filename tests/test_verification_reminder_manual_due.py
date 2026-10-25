
"""Due-now verification reminder path: same worker, same ledger, saved channel only."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
import discord

from stoney_verify import verification_reminders as reminders
from stoney_verify.commands_ext import public_verify_reminder_ui as ui


def run(coro):
    return asyncio.run(coro)


def setup(monkeypatch, *, mode="channel", enabled=True, channel_id=44, age_hours=120):
    cfg = {
        "source":"supabase:guild_configs",
        "unverified_role_id":"10","verified_role_id":"11",
        "verify_channel_id":"77",
        reminders.REMINDER_CONFIG_KEY:{
            "enabled":enabled,"delivery":mode,"reminder_channel_id":str(channel_id),
            "delay_hours":24,"repeat_hours":48,"max_reminders":3,
        },
    }
    members=[]
    for i in range(4):
        m=SimpleNamespace(
            id=100+i,bot=False,pending=True,
            joined_at=datetime.now(timezone.utc)-timedelta(hours=age_hours),
            mention=f"<@{100+i}>",sent=[],
        )
        members.append(m)
    async def fetch_member(uid):
        return next(m for m in members if m.id==uid)
    async def fetch_members(*,limit,after):
        for m in members:
            if after is None or m.id>after.id:
                yield m
    notice=[]
    class Channel:
        id=44
        def permissions_for(self, member):
            return SimpleNamespace(view_channel=True,send_messages=True)
        async def send(self, text, *, allowed_mentions):
            notice.append((text,allowed_mentions))
    chosen=Channel()
    guild=SimpleNamespace(
        id=1234,name="Test Server",me=object(),
        fetch_member=fetch_member,fetch_members=fetch_members,
        get_role=lambda uid:object() if uid in (10,11) else None,
    )
    bot=SimpleNamespace(get_guild=lambda uid:guild if uid==1234 else None)
    worker=reminders.ReminderWorker(bot)
    worker.task=SimpleNamespace(done=lambda:False)
    monkeypatch.setattr(reminders,"_WORKER",worker)
    async def get_cfg(*args,**kwargs):return cfg
    async def ready():return True
    monkeypatch.setattr(reminders,"get_guild_config",get_cfg)
    monkeypatch.setattr(reminders,"basic_verify_allowed_for_guild",lambda *args:True)
    monkeypatch.setattr(reminders,"verification_channel",lambda *args:chosen)
    monkeypatch.setattr(reminders,"delivery_channel",lambda *args:chosen if mode=="channel" else None)
    monkeypatch.setattr(reminders,"pending_for_reminder",lambda m,cfg:m.pending)
    monkeypatch.setattr(reminders,"clear_role_truth_cache",lambda *args:None)
    monkeypatch.setattr(reminders,"ledger_ready",ready)
    return cfg,guild,bot,worker,members,notice


def test_manual_send_only_due_designated_channel_and_cooldown(monkeypatch):
    cfg,guild,bot,worker,members,notice=setup(monkeypatch)
    claimed=[]
    async def claim(gid,member,policy):
        claimed.append((gid,member.id,policy.repeat_hours,policy.max_reminders))
        return True
    monkeypatch.setattr(reminders,"_claim_delivery",claim)
    async def fast_sleep(*args):return None
    monkeypatch.setattr(reminders.asyncio,"sleep",fast_sleep)
    assert run(reminders.send_due_now_in_designated_channel(bot,guild,channel_id=44))==3
    assert len(notice)==3
    assert [c[1] for c in claimed]==[100,101,102]
    assert all(n[1].everyone is False and n[1].roles is False for n in notice)
    assert worker.member_cursors[guild.id]==102
    with pytest.raises(reminders.ManualReminderUnavailable,match="minute"):
        run(reminders.send_due_now_in_designated_channel(bot,guild,channel_id=44))
    assert len(notice)==3
    worker.manual_trigger_at[guild.id]-=reminders.MANUAL_COOLDOWN_SECONDS+1
    assert run(reminders.send_due_now_in_designated_channel(bot,guild,channel_id=44))==1
    assert claimed[-1][1]==103 and len(notice)==4


def test_manual_due_honors_first_delay_and_verified_status(monkeypatch):
    cfg,guild,bot,worker,members,notice=setup(monkeypatch,age_hours=2)
    claimed=[]
    async def claim(*args):claimed.append(args);return True
    monkeypatch.setattr(reminders,"_claim_delivery",claim)
    assert run(reminders.send_due_now_in_designated_channel(bot,guild,channel_id=44))==0
    assert not claimed and not notice
    worker.manual_trigger_at[guild.id]-=reminders.MANUAL_COOLDOWN_SECONDS+1
    for member in members:
        member.joined_at=datetime.now(timezone.utc)-timedelta(hours=100)
        member.pending=False
    assert run(reminders.send_due_now_in_designated_channel(bot,guild,channel_id=44))==0
    assert not claimed and not notice


@pytest.mark.parametrize("enabled,mode,channel_id", [
    (False,"channel",44),(True,"dm",44),(True,"channel",55),
])
def test_no_manual_dm_or_disabled_or_wrong_channel(monkeypatch,enabled,mode,channel_id):
    cfg,guild,bot,worker,members,notice=setup(
        monkeypatch,enabled=enabled,mode=mode,channel_id=channel_id,
    )
    with pytest.raises(reminders.ManualReminderUnavailable):
        run(reminders.send_due_now_in_designated_channel(bot,guild,channel_id=44))
    assert worker.member_cursors=={} and not notice


def test_manual_db_failure_and_unknown_worker_do_not_send(monkeypatch):
    cfg,guild,bot,worker,members,notice=setup(monkeypatch)
    async def offline():return False
    monkeypatch.setattr(reminders,"ledger_ready",offline)
    with pytest.raises(reminders.ManualReminderUnavailable,match="ledger"):
        run(reminders.send_due_now_in_designated_channel(bot,guild,channel_id=44))
    assert not worker.manual_trigger_at and not notice
    monkeypatch.setattr(reminders,"_WORKER",None)
    with pytest.raises(reminders.ManualReminderUnavailable,match="not running"):
        run(reminders.send_due_now_in_designated_channel(bot,guild,channel_id=44))


def test_manual_ui_shows_exact_saved_destination_and_requires_confirm(monkeypatch):
    policy=reminders.ReminderPolicy(
        enabled=True,delivery="channel",reminder_channel_id=44,
        delay_hours=24,repeat_hours=48,max_reminders=3,
    )
    events=[]
    async def staff(interaction):return True
    async def load(guild):return {},policy
    async def private(interaction,text="",**kwargs):events.append((text,kwargs))
    async def defer(**kwargs):events.append(("defer",kwargs))
    monkeypatch.setattr(ui,"_require_staff",staff)
    monkeypatch.setattr(ui,"_load",load)
    monkeypatch.setattr(ui,"_can_enable",lambda *args:"")
    monkeypatch.setattr(ui,"delivery_channel",lambda *args:SimpleNamespace(id=44,mention="<#44>"))
    monkeypatch.setattr(ui,"_private",private)
    interaction=SimpleNamespace(
        guild=SimpleNamespace(id=1234),user=SimpleNamespace(id=7),
        response=SimpleNamespace(is_done=lambda:False,defer=defer),
    )
    view=ui.ReminderSettingsView(7,policy)
    action=next(item for item in view.children if item.label=="Check Scheduled Due")
    run(action.callback(interaction))
    assert events[0][0]=="defer"
    assert "<#44>" in events[-1][0]
    assert "24 hours" in events[-1][0]
    assert "48 hours" in events[-1][0]
    confirm=events[-1][1]["view"]
    assert isinstance(confirm,ui.ConfirmDueReminderView)
    assert {"Confirm Scheduled Check","Cancel"}=={x.label for x in confirm.children}


def test_confirmation_is_one_shot_and_never_uses_invoking_channel(monkeypatch):
    calls,notes=[],[]
    async def staff(interaction):return True
    async def request(bot,guild,*,channel_id):
        calls.append((guild.id,channel_id))
        return 2
    async def private(interaction,text="",**kwargs):notes.append(text)
    async def defer(**kwargs):pass
    monkeypatch.setattr(ui,"_require_staff",staff)
    monkeypatch.setattr(ui,"send_due_now_in_designated_channel",request)
    monkeypatch.setattr(ui,"_private",private)
    interaction=SimpleNamespace(
        guild=SimpleNamespace(id=1234),user=SimpleNamespace(id=7),client=object(),
        channel=SimpleNamespace(id=999),
        response=SimpleNamespace(is_done=lambda:False,defer=defer),
    )
    view=ui.ConfirmDueReminderView(7,1234,44)
    button=next(x for x in view.children if x.label=="Confirm Scheduled Check")
    run(button.callback(interaction))
    run(button.callback(interaction))
    assert calls==[(1234,44)]
    assert "<#44>" in notes[0]
    assert "attempt(s)" in notes[0]
