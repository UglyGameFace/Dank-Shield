from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from stoney_verify import interaction_guard as guard
from stoney_verify.interaction_guard import (
    InteractionActionLocks,
    clear_recent_interaction_failures,
    install_component_interaction_observer,
    interaction_action_key,
    recent_interaction_failures,
    run_guarded_interaction,
    safe_defer_interaction,
    safe_send_interaction,
)


class Obj:
    def __init__(self, **values) -> None:
        self.__dict__.update(values)


class FakeResponse:
    def __init__(self, *, fail_defer: bool = False, fail_send: bool = False) -> None:
        self.done = False
        self.deferred = False
        self.sent: list[dict] = []
        self.defer_ephemeral: bool | None = None
        self.fail_defer = fail_defer
        self.fail_send = fail_send

    def is_done(self) -> bool:
        return self.done

    async def defer(self, *, ephemeral: bool = True) -> None:
        if self.fail_defer:
            raise RuntimeError("defer exploded")
        self.done = True
        self.deferred = True
        self.defer_ephemeral = ephemeral

    async def send_message(self, **payload) -> None:
        if self.fail_send:
            raise RuntimeError("initial send exploded")
        self.done = True
        self.sent.append(payload)


class FakeFollowup:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail_send = fail_send

    async def send(self, **payload) -> None:
        if self.fail_send:
            raise RuntimeError("followup send exploded")
        self.sent.append(payload)


class FakeInteraction:
    def __init__(self, *, fail_defer: bool = False, fail_initial_send: bool = False, fail_followup_send: bool = False) -> None:
        self.id = 123456789012345678
        self.guild = Obj(id=111)
        self.channel = Obj(id=222)
        self.user = Obj(id=333)
        self.message = Obj(id=444, author=Obj(id=999))
        self.data = {"custom_id": "dank:test", "component_type": 2}
        self.type = guard.discord.InteractionType.component
        self.application_id = 999
        self.created_at = datetime.now(timezone.utc)
        self.response = FakeResponse(fail_defer=fail_defer, fail_send=fail_initial_send)
        self.followup = FakeFollowup(fail_send=fail_followup_send)


def test_safe_send_interaction_uses_initial_response_when_not_done():
    async def run() -> None:
        clear_recent_interaction_failures()
        interaction = FakeInteraction()

        sent = await safe_send_interaction(interaction, content="hello", ephemeral=True)

        assert sent is True
        assert interaction.response.sent[0]["content"] == "hello"
        assert interaction.response.sent[0]["ephemeral"] is True
        assert interaction.followup.sent == []
        assert recent_interaction_failures() == []

    asyncio.run(run())


def test_safe_send_interaction_uses_followup_after_defer():
    async def run() -> None:
        clear_recent_interaction_failures()
        interaction = FakeInteraction()
        await interaction.response.defer(ephemeral=True)

        sent = await safe_send_interaction(interaction, content="after defer", ephemeral=True)

        assert sent is True
        assert interaction.response.sent == []
        assert interaction.followup.sent[0]["content"] == "after defer"
        assert recent_interaction_failures() == []

    asyncio.run(run())


def test_safe_send_interaction_logs_when_initial_and_followup_fail():
    async def run() -> None:
        clear_recent_interaction_failures()
        interaction = FakeInteraction(fail_initial_send=True, fail_followup_send=True)

        sent = await safe_send_interaction(interaction, content="nope", action_name="test send")

        assert sent is False
        failures = recent_interaction_failures()
        assert len(failures) == 1
        assert failures[0].stage == "send_failed"
        assert failures[0].context.guild_id == 111
        assert failures[0].context.channel_id == 222
        assert failures[0].context.user_id == 333
        assert failures[0].context.custom_id == "dank:test"
        assert failures[0].error_id.startswith("DANK-")

    asyncio.run(run())


def test_safe_defer_interaction_logs_defer_failure():
    async def run() -> None:
        clear_recent_interaction_failures()
        interaction = FakeInteraction(fail_defer=True)

        ok = await safe_defer_interaction(interaction, action_name="defer test")

        assert ok is False
        failures = recent_interaction_failures()
        assert len(failures) == 1
        assert failures[0].stage == "defer_failed"
        assert failures[0].error_type == "RuntimeError"
        assert "defer exploded" in failures[0].error_message

    asyncio.run(run())


def test_safe_defer_interaction_accepts_preacknowledged_interaction():
    async def run() -> None:
        clear_recent_interaction_failures()
        interaction = FakeInteraction()
        interaction.response.done = True

        ok = await safe_defer_interaction(interaction, action_name="already acked")

        assert ok is True
        assert interaction.response.deferred is False
        assert recent_interaction_failures() == []

    asyncio.run(run())


def test_run_guarded_interaction_stops_when_defer_fails():
    async def run() -> None:
        clear_recent_interaction_failures()
        interaction = FakeInteraction(fail_defer=True)
        called: list[str] = []

        async def action() -> None:
            called.append("must not run")

        result = await run_guarded_interaction(
            interaction,
            action,
            defer=True,
            ephemeral=True,
            action_name="claim boundary",
        )

        assert result.ok is False
        assert result.error_id.startswith("DANK-")
        assert result.error_type == "RuntimeError"
        assert "defer exploded" in result.error_message
        assert result.sent_to_user is False
        assert called == []
        failures = recent_interaction_failures()
        assert len(failures) == 1
        assert failures[0].stage == "defer_failed"
        assert failures[0].context.trace_id == guard._trace_id(interaction)

    asyncio.run(run())


def test_component_observer_records_only_still_unacknowledged_click(monkeypatch):
    async def run() -> None:
        clear_recent_interaction_failures()
        guard._COMPONENT_OBSERVER_LOG_TIMES.clear()
        monkeypatch.setattr(guard, "_COMPONENT_OBSERVER_GRACE_SECONDS", 0.0)

        bot = Obj(
            user=Obj(id=999),
            application_id=999,
            persistent_views=[Obj()],
        )
        interaction = FakeInteraction()

        await guard._observe_component_ack(bot, interaction)

        failures = recent_interaction_failures()
        assert len(failures) == 1
        record = failures[0]
        assert record.stage == "component_unacknowledged"
        assert record.context.custom_id == "dank:test"
        assert record.extra["message_author_id"] == 999
        assert record.extra["bot_user_id"] == 999
        assert record.extra["interaction_application_id"] == 999
        assert record.extra["message_author_matches_bot"] is True
        assert record.extra["persistent_view_count"] == 1

        clear_recent_interaction_failures()
        interaction.response.done = True
        await guard._observe_component_ack(bot, interaction)
        assert recent_interaction_failures() == []

    asyncio.run(run())


def test_component_observer_registration_is_idempotent(monkeypatch):
    class FakeBot:
        def __init__(self) -> None:
            self.listeners: list[tuple[object, str]] = []
            self.persistent_views = []
            self.user = Obj(id=123)
            self.application_id = 123

        def add_listener(self, callback, name: str) -> None:
            self.listeners.append((callback, name))

    monkeypatch.setattr(guard, "_COMPONENT_OBSERVER_INSTALLED", False)
    monkeypatch.setattr(guard, "_COMPONENT_OBSERVER_READY_LOGGED", False)
    bot = FakeBot()

    assert install_component_interaction_observer(bot) is True
    assert install_component_interaction_observer(bot) is True
    assert [name for _callback, name in bot.listeners] == [
        "on_interaction",
        "on_ready",
    ]


def test_run_guarded_interaction_defers_and_reports_success():
    async def run() -> None:
        clear_recent_interaction_failures()
        interaction = FakeInteraction()
        called: list[str] = []

        async def action() -> None:
            called.append("ran")

        result = await run_guarded_interaction(interaction, action, defer=True, ephemeral=True, action_name="success action")

        assert result.ok is True
        assert called == ["ran"]
        assert interaction.response.deferred is True
        assert interaction.response.defer_ephemeral is True
        assert interaction.followup.sent == []
        assert recent_interaction_failures() == []

    asyncio.run(run())


def test_run_guarded_interaction_sends_safe_error_on_exception():
    async def run() -> None:
        clear_recent_interaction_failures()
        interaction = FakeInteraction()

        async def action() -> None:
            raise ValueError("bad thing")

        result = await run_guarded_interaction(interaction, action, defer=True, ephemeral=True, action_name="boom action")

        assert result.ok is False
        assert result.error_id.startswith("DANK-")
        assert result.error_type == "ValueError"
        assert result.error_message == "bad thing"
        assert result.sent_to_user is True
        assert interaction.response.deferred is True
        assert interaction.response.defer_ephemeral is True
        assert len(interaction.followup.sent) == 1
        payload = interaction.followup.sent[0]
        assert payload["ephemeral"] is True
        assert payload["embed"].title == "❌ Command failed safely"
        assert "bad thing" in payload["embed"].description
        assert "Error ID" in payload["embed"].description
        assert result.error_id in payload["embed"].description
        failures = recent_interaction_failures()
        assert failures[-1].error_id == result.error_id
        assert failures[-1].sent_to_user is True

    asyncio.run(run())


def test_run_guarded_interaction_rejects_duplicate_locked_action():
    async def run() -> None:
        clear_recent_interaction_failures()
        interaction = FakeInteraction()
        key = interaction_action_key(interaction, action_name="duplicate action")
        lock = InteractionActionLocks.setdefault(key, asyncio.Lock())
        await lock.acquire()
        try:
            called: list[str] = []

            async def action() -> None:
                called.append("should not run")

            result = await run_guarded_interaction(
                interaction,
                action,
                defer=False,
                action_name="duplicate action",
                lock_key=key,
            )

            assert result.ok is False
            assert result.duplicate is True
            assert called == []
            assert interaction.response.sent[0]["content"].startswith("⏳")
            failures = recent_interaction_failures()
            assert failures[-1].stage == "duplicate_action"
            assert failures[-1].error_id == result.error_id
        finally:
            lock.release()

    asyncio.run(run())
