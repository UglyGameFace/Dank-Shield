from __future__ import annotations

import asyncio
import threading
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from stoney_verify import birthday_runtime, birthday_service, guild_config, vc_sessions
from stoney_verify import member_lifecycle_setup_center as lifecycle
from stoney_verify import welcome_event_services as welcome
from stoney_verify.commands_ext import public_birthdays as birthdays
from stoney_verify.commands_ext import public_setup_group
from stoney_verify.startup_guards import process_health
from stoney_verify.verification_new import service as verification
from stoney_verify.verification_new import voice_verify


def interaction(*, private=True):
    response = SimpleNamespace(done=False)
    response.is_done = lambda: response.done

    async def acknowledge(**kwargs):
        assert not response.done
        response.done = True

    response.defer = AsyncMock(side_effect=acknowledge)
    response.send_message = AsyncMock(side_effect=acknowledge)
    response.send_modal = AsyncMock(side_effect=lambda modal: acknowledge())
    guild = SimpleNamespace(id=123, get_channel=lambda _: None, get_role=lambda _: None)
    return SimpleNamespace(
        guild=guild, user=SimpleNamespace(id=456), response=response,
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
        message=SimpleNamespace(flags=SimpleNamespace(ephemeral=private)),
        type=discord.InteractionType.component,
    )


RECORD = dict(birth_month=6, birth_day=20, birth_year=2002,
              public_wishes=True, birthday_role=False, dm_wishes=True)


def birthday_storage(monkeypatch, event, record=None):
    state = dict(record) if record else None

    async def member(*args):
        assert event.response.is_done(), "storage ran before Discord acknowledgement"
        return state

    async def settings(*args):
        assert event.response.is_done()
        return {"timezone": "America/New_York"}

    async def save(*args, **payload):
        nonlocal state
        assert event.response.is_done()
        state = {"birth_month": payload.pop("month"), "birth_day": payload.pop("day"),
                 "birth_year": payload.pop("year"), **payload}
        return state

    async def remove(*args):
        nonlocal state
        assert event.response.is_done()
        state = None

    monkeypatch.setattr(birthdays, "get_member", member)
    monkeypatch.setattr(birthdays, "get_guild", settings)
    monkeypatch.setattr(birthdays, "save_member", save)
    monkeypatch.setattr(birthdays, "delete_member", remove)


@pytest.mark.parametrize("field", ["public_wishes", "birthday_role", "dm_wishes"])
def test_birthday_preferences_ack_first_and_preserve_other_choices(monkeypatch, field):
    async def run():
        event = interaction()
        birthday_storage(monkeypatch, event, RECORD)
        view = birthdays.MemberBirthdayView(owner_id=456, saved=RECORD)
        await view._toggle(event, field)
        embed = event.edit_original_response.call_args.kwargs["embed"]
        assert "06-20-2002" in str(embed.to_dict())
        saved = event.edit_original_response.call_args.kwargs["view"].saved
        for key in ("public_wishes", "birthday_role", "dm_wishes"):
            assert saved[key] == (not RECORD[key] if key == field else RECORD[key])
        assert saved["birth_year"] == 2002
    asyncio.run(run())


def test_date_submit_ack_first_keeps_preferences_and_refreshes_private_panel(monkeypatch):
    async def run():
        event = interaction(private=False)
        event.type = discord.InteractionType.modal_submit
        birthday_storage(monkeypatch, event, RECORD)
        modal = birthdays.BirthdayDateModal(RECORD)
        modal.full_date._value = "02-29-2004"
        await modal.on_submit(event)
        event.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        embed = event.edit_original_response.call_args.kwargs["embed"]
        assert "02-29-2004" in str(embed.to_dict())
        assert event.edit_original_response.call_args.kwargs["view"].saved["public_wishes"]
        assert not event.followup.send.called
    asyncio.run(run())


def test_public_profile_birthday_button_creates_private_response(monkeypatch):
    async def run():
        event = interaction(private=False)
        birthday_storage(monkeypatch, event, RECORD)
        await birthdays.open_member_birthday(event)
        event.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        assert "2002" in str(event.edit_original_response.call_args.kwargs["embed"].to_dict())
    asyncio.run(run())


def test_invalid_birth_date_does_no_storage_io(monkeypatch):
    async def run():
        event = interaction()
        read = AsyncMock()
        monkeypatch.setattr(birthdays, "get_member", read)
        modal = birthdays.BirthdayDateModal(None)
        modal.full_date._value = "02-29-2003"
        await modal.on_submit(event)
        assert event.response.send_message.call_args.kwargs["ephemeral"]
        assert not read.called
    asyncio.run(run())


def test_removal_ack_first_and_reports_partial_role_cleanup_honestly(monkeypatch):
    async def run():
        event = interaction()
        birthday_storage(monkeypatch, event, RECORD)
        view = birthdays.MemberBirthdayView(owner_id=456)
        view._revoke_lease = AsyncMock(side_effect=birthday_service.BirthdayStorageUnavailable())
        await view.remove.callback(event)
        assert "was removed" in event.followup.send.call_args.kwargs["content"]
        assert event.edit_original_response.call_args.kwargs["view"].saved is None
    asyncio.run(run())


@pytest.mark.parametrize("toggle", ["enabled", "leap_policy"])
def test_staff_toggle_acknowledges_before_first_read(monkeypatch, toggle):
    async def run():
        event = interaction()
        monkeypatch.setattr(birthdays, "_manager", lambda _: True)
        reads = []

        async def get(*args):
            assert event.response.is_done()
            reads.append(1)
            return {"channel_id": "789", "enabled": False, "leap_day_policy": "feb28"}

        save = AsyncMock(return_value={})
        monkeypatch.setattr(birthdays, "get_guild", get)
        monkeypatch.setattr(birthdays, "save_guild", save)
        await birthdays._save_staff(event, toggle=toggle)
        assert len(reads) == 2  # One authoritative read plus the resulting panel.
        assert save.call_args.kwargs[toggle] == (True if toggle == "enabled" else "mar1")
    asyncio.run(run())


def test_birthday_panel_ownership_and_manager_recheck(monkeypatch):
    async def run():
        event = interaction()
        view = birthdays.MemberBirthdayView(owner_id=999)
        assert not await view.interaction_check(event)
        monkeypatch.setattr(birthdays, "_manager", lambda _: False)
        read = AsyncMock()
        monkeypatch.setattr(birthdays, "get_guild", read)
        await birthdays._save_staff(event, enabled=True)
        assert not read.called
    asyncio.run(run())


@pytest.mark.parametrize("year", [2000, 2027, 2028, 2100])
@pytest.mark.parametrize("policy", ["feb28", "mar1"])
def test_leap_birthday_occurs_on_exactly_one_day_per_year(year, policy):
    day = date(year, 1, 1)
    dates = []
    while day.year == year:
        if birthday_service.celebrated_on(2, 29, day, policy):
            dates.append(day)
        day += timedelta(days=1)
    assert len(dates) == 1
    assert birthday_service.next_birthday(2, 29, date(year, 1, 1), policy) == dates[0]
    assert birthday_service.next_birthday(2, 29, dates[0], policy) == dates[0]


def test_birthday_ui_and_worker_share_role_policy():
    assert birthdays._safe_birthday_role is birthday_runtime._manageable_role


async def verify_nonblocking(operation, entered, release):
    task = asyncio.create_task(operation())
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        assert not task.done(), "synchronous persistence blocked the event loop"
    finally:
        release.set()
        await task


@pytest.mark.parametrize("module,method,store_name,args,kwargs", [
    (verification, "_refresh_token_info", "sb_get_token_info", ("test-token",), {}),
    (verification, "_mark_decision_safe", "sb_mark_decision", ("test-token", "APPROVED", 7), {}),
    (verification, "_set_used_safe", "sb_set_used", ("test-token", True), {}),
    (voice_verify, "_mark_decision_safe", "sb_mark_decision", ("test-token", "APPROVED", 7), {}),
    (vc_sessions, "start_session", "ensure_session", (), dict(guild_id=1, token="test-token", ticket_channel_id=2, vc_channel_id=3, user_id=4, staff_id=7)),
    (vc_sessions, "end_session", "mark_completed", (), dict(guild_id=1, token="test-token", status="COMPLETED", staff_id=7)),
])
def test_slow_verification_persistence_allows_other_interactions(monkeypatch, module, method, store_name, args, kwargs):
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    calls = []

    def store(*passed, **options):
        calls.append((threading.get_ident(), passed, options))
        entered.set()
        release.wait(timeout=1)
        return {"token": "test-token"}

    monkeypatch.setattr(module, store_name, store)
    if method == "start_session":
        monkeypatch.setattr(module, "mark_started", lambda **_: None)
    operation = lambda: getattr(module, method)(*args, **kwargs)
    asyncio.run(verify_nonblocking(operation, entered, release))
    assert len(calls) == 1 and calls[0][0] != loop_thread
    assert "test-token" in str(calls[0][1:])


def test_config_latency_splits_thread_queue_from_database_work(monkeypatch):
    clock = iter([100.0, 100.4, 102.0, 102.1, 103.0, 103.4, 105.0, 105.1])
    monkeypatch.setattr(guild_config, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    monkeypatch.setattr(guild_config, "_LAST_SLOW_DB_LOG_AT", 0)
    logs = []
    monkeypatch.setattr(guild_config, "_warn", logs.append)

    async def run():
        assert await guild_config._run_db("read config guild=private-id", lambda: 42) == 42
        assert await guild_config._run_db("read config guild=private-id", lambda: 43) == 43
    asyncio.run(run())
    assert len(logs) == 1
    assert "queue_ms=400" in logs[0] and "work_ms=1600" in logs[0]
    assert "private-id" not in logs[0]


def test_trace_identifies_nested_command_without_recording_arguments():
    event = SimpleNamespace(data={"name": "dank", "options": [
        {"name": "birthdays", "type": 2, "options": [
            {"name": "set", "type": 1, "options": [
                {"name": "private-field", "type": 3, "value": "06-20-2002"},
            ]},
        ]},
    ]})
    assert process_health._interaction_trace_label(event) == "dank.birthdays.set"
    event.data = {"custom_id": "private-token", "components": [{"value": "private-date"}]}
    assert "private" not in process_health._interaction_trace_label(event)


def test_welcome_editor_opens_modal_without_database_read(monkeypatch):
    async def run():
        event = interaction()
        monkeypatch.setattr(public_setup_group, "_require_setup_permission", AsyncMock(return_value=True))
        get = AsyncMock(side_effect=AssertionError("modal tried to read storage"))
        monkeypatch.setattr(guild_config, "get_guild_config", get)
        button = welcome.WelcomeEditButton(kind="join", cfg={"welcome_join_title": "Existing title"})
        await button.callback(event)
        assert event.response.send_modal.called
        assert not event.response.defer.called and not get.called
        assert event.response.send_modal.call_args.args[0].title_input.default == "Existing title"
    asyncio.run(run())


def test_lifecycle_storage_error_is_visible_after_acknowledgement(monkeypatch):
    async def run():
        event = interaction()
        monkeypatch.setattr(public_setup_group, "_require_setup_permission", AsyncMock(return_value=True))

        async def fail(*args, **kwargs):
            assert event.response.is_done()
            raise RuntimeError("storage unavailable")

        monkeypatch.setattr(lifecycle, "get_guild_config", fail)
        await lifecycle.open_member_lifecycle_setup(event)
        assert "Could not load" in event.followup.send.call_args.args[0]
        assert event.followup.send.call_args.kwargs["ephemeral"]
    asyncio.run(run())
