from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import discord
import pytest

from stoney_verify import action_canary_audit as audit
from stoney_verify import action_canary_delivery as dispatcher
from stoney_verify.action_delivery_readiness import ActionDeliveryReadiness
from stoney_verify.commands_ext import public_action_canary as ui
from stoney_verify.commands_ext import public_action_schedule


class FakeMember:
    id = 601
    bot = False

    def __init__(self, guild):
        self.guild = guild


def _guild():
    guild = SimpleNamespace(id=501)
    user = FakeMember(guild)
    guild.get_member = lambda value: user if value == 601 else None
    return guild, user


def _interaction(guild, actor=42):
    return SimpleNamespace(
        guild=guild, guild_id=guild.id,
        user=SimpleNamespace(id=actor),
        client=SimpleNamespace(),
    )


def _env(monkeypatch):
    monkeypatch.setenv(dispatcher.ENABLE_ENV, "1")
    monkeypatch.setenv(dispatcher.GUILD_ENV, "501")
    monkeypatch.setenv(dispatcher.USER_ENV, "601")
    monkeypatch.setenv(dispatcher.REVISION_ENV, "3")
    monkeypatch.setenv(dispatcher.APPROVED_SOURCE_ENV, "a" * 16)
    monkeypatch.setattr(dispatcher, "runtime_source_fingerprint", lambda: "a" * 16)


def test_unconfigured_wrong_guild_or_non_member_never_selects_anyone(monkeypatch):
    guild, member = _guild()
    monkeypatch.setattr(ui.discord, "Member", FakeMember)
    assert ui._target(guild) is None
    _env(monkeypatch)
    assert ui._target(guild) == (member, 3)
    monkeypatch.setenv(dispatcher.GUILD_ENV, "502")
    assert ui._target(guild) is None
    _env(monkeypatch)
    guild.get_member = lambda uid: None
    assert ui._target(guild) is None


def test_only_true_application_owner_can_access(monkeypatch):
    guild, _ = _guild()
    event = _interaction(guild)
    async def no(_event):
        return False
    async def yes(_event):
        return True
    monkeypatch.setattr(ui, "_is_app_owner", no)
    assert not asyncio.run(ui._is_app_owner(event))
    monkeypatch.setattr(ui, "_is_app_owner", yes)
    assert asyncio.run(ui._is_app_owner(event))
    # No owner-id configuration fallback: actual Discord client authority.
    source = Path(ui.__file__).read_text()
    assert "interaction.client.is_owner(interaction.user)" in source
    assert "guild_permissions.administrator" not in source


def test_canary_requires_both_private_schemas_and_member_opt_in(monkeypatch):
    guild, member = _guild()
    event = _interaction(guild)
    monkeypatch.setattr(ui.discord, "Member", FakeMember)
    _env(monkeypatch)
    async def owner(_event):
        return True
    monkeypatch.setattr(ui, "_is_app_owner", owner)
    monkeypatch.setattr(ui, "ledger_ready_sync", lambda: True)
    monkeypatch.setattr(ui, "audit_ready_sync", lambda: False)
    result = asyncio.run(ui._canary_state(event))
    assert result[0] is None
    assert "not both ready" in result[2]
    monkeypatch.setattr(ui, "audit_ready_sync", lambda: True)
    async def readiness(_member, *, expected_revision):
        return ActionDeliveryReadiness("opted_out", "no consent", revision=expected_revision)
    monkeypatch.setattr(ui, "inspect_member_setup_readiness", readiness)
    result = asyncio.run(ui._canary_state(event))
    assert result[0] is None
    assert "opted_out" in result[2]
    async def ready(_member, *, expected_revision):
        return ActionDeliveryReadiness(
            "preview_due", "ready", revision=expected_revision, ledger_checked=True
        )
    monkeypatch.setattr(ui, "inspect_member_setup_readiness", ready)
    assert asyncio.run(ui._canary_state(event)) == (member, 3, "ready")


def _modal(monkeypatch, *, match=True):
    modal = ui.ConfirmSingleCanaryModal(42, 501, 601, 3)
    modal.confirmation._value = "SEND 501/601/3" if match else "SEND 501/602/3"
    return modal


def _fake_confirm(monkeypatch, *, begin=True, finish=True, result_status="sent", operator=True):
    guild, member = _guild()
    event = _interaction(guild)
    events = []
    async def owner(_event):
        events.append("owner")
        return operator
    async def state(_event):
        events.append("ready")
        return member, 3, "ready"
    async def defer(_event):
        events.append("defer")
    async def reply(_event, message):
        events.append(("reply", message))
    def audit_begin(*args):
        events.append("begin")
        return begin
    def audit_finish(*args, **kwargs):
        events.append("finish")
        return finish
    async def send(*args, **kwargs):
        events.append("dispatch")
        return dispatcher.CanaryDeliveryResult(
            result_status, claimed=result_status in ("sent", "cancelled", "send_failed"),
            sent=result_status == "sent",
        )
    monkeypatch.setattr(ui, "_is_app_owner", owner)
    monkeypatch.setattr(ui, "_canary_state", state)
    monkeypatch.setattr(ui, "_defer", defer)
    monkeypatch.setattr(ui, "_msg", reply)
    monkeypatch.setattr(ui, "begin_canary_audit_sync", audit_begin)
    monkeypatch.setattr(ui, "finish_canary_audit_sync", audit_finish)
    monkeypatch.setattr(ui, "dispatch_single_member_setup_canary", send)
    return event, events


def test_exact_typed_confirmation_audit_before_one_dispatch(monkeypatch):
    event, events = _fake_confirm(monkeypatch)
    modal = _modal(monkeypatch)
    asyncio.run(modal.on_submit(event))
    assert "begin" in events and "dispatch" in events and "finish" in events
    assert events.index("begin") < events.index("dispatch") < events.index("finish")
    assert ("reply", "Single-recipient canary finished: **sent**. Claim recorded: **True**. Discord DM confirmed: **True**. This operation cannot be replayed from the same confirmation.") in events
    asyncio.run(modal.on_submit(event))
    assert events.count("dispatch") == 1
    assert any("already submitted" in v[1] for v in events if isinstance(v, tuple))


def test_wrong_confirmation_never_writes_audit_or_dispatches(monkeypatch):
    event, events = _fake_confirm(monkeypatch)
    modal = _modal(monkeypatch, match=False)
    asyncio.run(modal.on_submit(event))
    assert "begin" not in events and "dispatch" not in events
    assert any("rejected" in event[1] for event in events if isinstance(event, tuple))


def test_owner_permission_revoked_and_audit_insert_failure_block(monkeypatch):
    event, events = _fake_confirm(monkeypatch, operator=False)
    asyncio.run(_modal(monkeypatch).on_submit(event))
    assert "begin" not in events and "dispatch" not in events
    event, events = _fake_confirm(monkeypatch, begin=False)
    asyncio.run(_modal(monkeypatch).on_submit(event))
    assert "begin" in events and "dispatch" not in events


def test_final_audit_failure_warns_no_retry_after_possible_send(monkeypatch):
    event, events = _fake_confirm(monkeypatch, finish=False)
    asyncio.run(_modal(monkeypatch).on_submit(event))
    assert "dispatch" in events and "finish" in events
    assert any("Do NOT retry" in v[1] for v in events if isinstance(v, tuple))


def test_invalid_audit_write_args_fail_closed():
    assert not audit.begin_canary_audit_sync(uuid4(), 0, 2, 3, 4)
    assert not audit.finish_canary_audit_sync(uuid4(), status="sent", claimed=False, sent=True)
    assert not audit.finish_canary_audit_sync(uuid4(), status="requested", claimed=False, sent=False)
    assert not audit.finish_canary_audit_sync(uuid4(), status="unknown", claimed=False, sent=False)


def test_schedule_navigation_has_owner_canary_without_root_command():
    view = public_action_schedule.ScheduleView(42, 501, public_action_schedule.ActionSchedule())
    labels = {v.label for v in view.children if isinstance(v, discord.ui.Button)}
    assert "Owner Canary Console" in labels
    source = Path(ui.__file__).read_text()
    assert "app_commands" not in source
    assert "create_task(" not in source
    assert "fetch_members(" not in source
    assert "member.send(" not in source
    assert "operation = uuid4()" in source
    assert "begin_canary_audit_sync" in source
    assert "dispatch_single_member_setup_canary" in source
    assert "finish_canary_audit_sync" in source
