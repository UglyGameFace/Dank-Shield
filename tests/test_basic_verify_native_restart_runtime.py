from __future__ import annotations

import asyncio
import subprocess
import sys
from types import SimpleNamespace

import discord
import pytest

from stoney_verify.verification_new import basic_verify as runtime


class FakeBot:
    def __init__(
        self,
        *,
        fail_view: bool = False,
        fail_listener: bool = False,
    ) -> None:
        self.views: list[object] = []
        self.listeners: list[tuple[object, str]] = []
        self.fail_view = fail_view
        self.fail_listener = fail_listener

    def add_view(self, view: object, *, message_id: int | None = None) -> None:
        if self.fail_view:
            raise RuntimeError("view registration failed")
        self.views.append((view, message_id))

    def add_listener(self, listener: object, name: str) -> None:
        if self.fail_listener:
            raise RuntimeError("listener registration failed")
        self.listeners.append((listener, name))


def _reset_runtime_state() -> None:
    runtime._RUNTIME_VIEW_REGISTERED = False
    runtime._RUNTIME_FALLBACK_LISTENER_REGISTERED = False
    runtime._RUNTIME_REGISTRATION_ERROR = ""
    runtime._RUNTIME_READY_RECONCILER_REGISTERED = False
    runtime._RUNTIME_READY_RECONCILE_STARTED = False
    runtime._BOUND_PANEL_MESSAGE_IDS.clear()
    runtime._BASIC_VERIFY_LOCKS.clear()


@pytest.fixture(autouse=True)
def _isolated_runtime_state():
    _reset_runtime_state()
    try:
        yield
    finally:
        _reset_runtime_state()


def test_runtime_registers_persistent_view_and_delayed_safety_listener(
    monkeypatch,
) -> None:
    fake_bot = FakeBot()
    sentinel_view = object()
    monkeypatch.setattr(runtime, "BasicVerifyView", lambda: sentinel_view)

    assert runtime.install_basic_verify_runtime(fake_bot, strict=True) is True

    assert fake_bot.views == [(sentinel_view, None)]
    assert len(fake_bot.listeners) == 2
    assert fake_bot.listeners[0] == (
        runtime._basic_verify_fallback_listener,
        "on_interaction",
    )
    assert fake_bot.listeners[1][1] == "on_ready"
    assert runtime.basic_verify_runtime_status() == {
        "persistent_view_registered": True,
        "fallback_listener_registered": True,
        "ready": True,
        "error": "",
    }


def test_runtime_uses_listener_only_when_persistent_view_registration_fails(
    monkeypatch,
) -> None:
    fake_bot = FakeBot(fail_view=True)
    monkeypatch.setattr(runtime, "BasicVerifyView", lambda: object())

    assert runtime.install_basic_verify_runtime(fake_bot, strict=True) is True

    assert fake_bot.views == []
    assert len(fake_bot.listeners) == 2
    assert fake_bot.listeners[0] == (
        runtime._basic_verify_fallback_listener,
        "on_interaction",
    )
    assert fake_bot.listeners[1][1] == "on_ready"
    status = runtime.basic_verify_runtime_status()
    assert status["persistent_view_registered"] is False
    assert status["fallback_listener_registered"] is True
    assert status["ready"] is True
    assert "view registration failed" in status["error"]


def test_runtime_can_fill_missing_persistent_view_without_duplicate_listener(
    monkeypatch,
) -> None:
    fake_bot = FakeBot(fail_view=True)
    monkeypatch.setattr(runtime, "BasicVerifyView", lambda: object())

    assert runtime.install_basic_verify_runtime(fake_bot, strict=True) is True
    assert len(fake_bot.listeners) == 2
    assert fake_bot.views == []

    fake_bot.fail_view = False
    assert runtime.install_basic_verify_runtime(fake_bot, strict=True) is True

    assert len(fake_bot.listeners) == 2
    assert len(fake_bot.views) == 1
    status = runtime.basic_verify_runtime_status()
    assert status["persistent_view_registered"] is True
    assert status["fallback_listener_registered"] is True


def test_runtime_install_is_idempotent_after_full_registration(
    monkeypatch,
) -> None:
    fake_bot = FakeBot()
    monkeypatch.setattr(runtime, "BasicVerifyView", lambda: object())

    assert runtime.install_basic_verify_runtime(fake_bot, strict=True) is True
    assert runtime.install_basic_verify_runtime(fake_bot, strict=True) is True

    assert len(fake_bot.views) == 1
    assert len(fake_bot.listeners) == 2
    assert fake_bot.listeners[0] == (
        runtime._basic_verify_fallback_listener,
        "on_interaction",
    )
    assert fake_bot.listeners[1][1] == "on_ready"


def test_strict_runtime_fails_closed_when_no_interaction_route_can_register(
    monkeypatch,
) -> None:
    fake_bot = FakeBot(fail_view=True, fail_listener=True)
    monkeypatch.setattr(runtime, "BasicVerifyView", lambda: object())

    with pytest.raises(RuntimeError, match="no registered interaction handler"):
        runtime.install_basic_verify_runtime(fake_bot, strict=True)

    status = runtime.basic_verify_runtime_status()
    assert status["ready"] is False
    assert "view registration failed" in status["error"]
    assert "listener registration failed" in status["error"]


def test_emergency_fallback_only_delegates_unacknowledged_basic_verify(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        calls: list[object] = []

        async def fake_handler(interaction) -> bool:
            calls.append(interaction)
            return True

        class Response:
            def __init__(self, done: bool = False) -> None:
                self.done = done

            def is_done(self) -> bool:
                return self.done

        monkeypatch.setattr(
            runtime,
            "maybe_handle_basic_verify_interaction",
            fake_handler,
        )

        matching = SimpleNamespace(
            type=discord.InteractionType.component,
            data={"custom_id": runtime.BASIC_VERIFY_CUSTOM_ID},
            response=Response(),
        )
        acknowledged = SimpleNamespace(
            type=discord.InteractionType.component,
            data={"custom_id": runtime.BASIC_VERIFY_CUSTOM_ID},
            response=Response(done=True),
        )
        unrelated = SimpleNamespace(
            type=discord.InteractionType.component,
            data={"custom_id": "something:else"},
            response=Response(),
        )

        await runtime._basic_verify_fallback_listener(unrelated)
        await runtime._basic_verify_fallback_listener(acknowledged)
        await runtime._basic_verify_fallback_listener(matching)

        assert calls == [matching]

    asyncio.run(scenario())


def test_delayed_fallback_gives_persistent_callback_first_chance(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        calls: list[str] = []

        class Response:
            def __init__(self) -> None:
                self.done = False

            def is_done(self) -> bool:
                return self.done

        response = Response()
        interaction = SimpleNamespace(
            id=4101,
            type=discord.InteractionType.component,
            data={"custom_id": runtime.BASIC_VERIFY_CUSTOM_ID},
            response=response,
            guild=SimpleNamespace(id=77),
            user=SimpleNamespace(id=88),
        )

        async def fake_handler(_interaction) -> bool:
            calls.append("handler")
            await asyncio.sleep(0.01)
            response.done = True
            return True

        monkeypatch.setattr(
            runtime,
            "maybe_handle_basic_verify_interaction",
            fake_handler,
        )
        monkeypatch.setattr(
            runtime,
            "_BASIC_VERIFY_FALLBACK_GRACE_SECONDS",
            0.05,
        )
        runtime._RUNTIME_VIEW_REGISTERED = True

        button = runtime.BasicVerifyButton()
        persistent_task = asyncio.create_task(button.callback(interaction))
        fallback_task = asyncio.create_task(
            runtime._basic_verify_fallback_listener(interaction)
        )
        await asyncio.gather(persistent_task, fallback_task)

        assert calls == ["handler"]
        assert response.is_done() is True

    asyncio.run(scenario())


def test_delayed_fallback_claims_click_when_persistent_dispatch_misses(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        calls: list[object] = []

        class Response:
            def __init__(self) -> None:
                self.done = False

            def is_done(self) -> bool:
                return self.done

        response = Response()
        interaction = SimpleNamespace(
            id=4102,
            type=discord.InteractionType.component,
            data={"custom_id": runtime.BASIC_VERIFY_CUSTOM_ID},
            response=response,
            guild=SimpleNamespace(id=77),
            user=SimpleNamespace(id=88),
        )

        async def fake_handler(received) -> bool:
            calls.append(received)
            response.done = True
            return True

        monkeypatch.setattr(
            runtime,
            "maybe_handle_basic_verify_interaction",
            fake_handler,
        )
        monkeypatch.setattr(
            runtime,
            "_BASIC_VERIFY_FALLBACK_GRACE_SECONDS",
            0,
        )
        runtime._RUNTIME_VIEW_REGISTERED = True

        await runtime._basic_verify_fallback_listener(interaction)

        assert calls == [interaction]
        assert response.is_done() is True

    asyncio.run(scenario())


def test_saved_current_application_panel_binds_exact_message_without_rest(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        class FakeGuild:
            id = 77
            me = SimpleNamespace(id=42)

            def get_channel(self, _channel_id: int):
                raise AssertionError("known current application must not use REST")

        fake_bot = FakeBot()
        sentinel_view = object()
        monkeypatch.setattr(runtime, "BasicVerifyView", lambda: sentinel_view)

        result = await runtime._reconcile_one_basic_verify_panel(
            fake_bot,
            FakeGuild(),
            {
                runtime._BASIC_VERIFY_PANEL_MESSAGE_ID_KEY: "123456",
                runtime._BASIC_VERIFY_PANEL_APPLICATION_ID_KEY: "42",
                "verify_channel_id": "999",
            },
        )

        assert result == "bound"
        assert fake_bot.views == [(sentinel_view, 123456)]
        assert 123456 in runtime._BOUND_PANEL_MESSAGE_IDS

    asyncio.run(scenario())


def test_saved_panel_without_application_identity_fetches_and_migrates(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        writes: list[tuple[int, int, int]] = []
        fetched: list[int] = []

        class FakeTextChannel:
            id = 99

            async def fetch_message(self, message_id: int):
                fetched.append(message_id)
                return SimpleNamespace(
                    id=message_id,
                    author=SimpleNamespace(id=42),
                    components=[
                        SimpleNamespace(
                            custom_id=runtime.BASIC_VERIFY_CUSTOM_ID,
                            children=[],
                        )
                    ],
                    embeds=[],
                )

        class FakeGuild:
            id = 77
            me = SimpleNamespace(id=42)

            def get_channel(self, channel_id: int):
                assert channel_id == 99
                return FakeTextChannel()

        async def no_reserve(*, label: str) -> None:
            assert "identity fetch" in label

        async def fake_persist(
            guild_id: int,
            message_id: int,
            *,
            application_id: int = 0,
        ) -> None:
            writes.append((guild_id, message_id, application_id))

        fake_bot = FakeBot()
        sentinel_view = object()
        monkeypatch.setattr(runtime.discord, "TextChannel", FakeTextChannel)
        monkeypatch.setattr(runtime, "BasicVerifyView", lambda: sentinel_view)
        monkeypatch.setattr(runtime, "_reserve_basic_verify_recovery_request", no_reserve)
        monkeypatch.setattr(runtime, "_persist_basic_verify_panel_message_id", fake_persist)

        result = await runtime._reconcile_one_basic_verify_panel(
            fake_bot,
            FakeGuild(),
            {
                runtime._BASIC_VERIFY_PANEL_MESSAGE_ID_KEY: "123456",
                "verify_channel_id": "99",
            },
            allow_legacy_rest=True,
        )

        assert result == "migrated_current"
        assert fetched == [123456]
        assert writes == [(77, 123456, 42)]
        assert fake_bot.views == [(sentinel_view, 123456)]

    asyncio.run(scenario())


def test_saved_foreign_application_panel_is_replaced(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        replacement_calls: list[tuple[int, bool]] = []
        lifecycle: list[str] = []

        class FakeTextChannel:
            id = 99

            async def fetch_message(self, message_id: int):
                return SimpleNamespace(
                    id=message_id,
                    author=SimpleNamespace(id=999),
                    components=[
                        SimpleNamespace(
                            custom_id=runtime.BASIC_VERIFY_CUSTOM_ID,
                            children=[],
                        )
                    ],
                    embeds=[],
                )

        class FakeGuild:
            id = 77
            me = SimpleNamespace(id=42)

            def get_channel(self, channel_id: int):
                assert channel_id == 99
                return FakeTextChannel()

        async def no_reserve(*, label: str) -> None:
            assert "identity fetch" in label

        async def fake_delete(_message) -> bool:
            lifecycle.append("delete")
            return True

        async def fake_post(
            channel,
            *,
            actor_id: int = 0,
            bot_instance=None,
            require_history_scan_for_post: bool = False,
        ) -> str:
            assert bot_instance is fake_bot
            lifecycle.append("post")
            replacement_calls.append((channel.id, require_history_scan_for_post))
            return "posted"

        fake_bot = FakeBot()
        monkeypatch.setattr(runtime.discord, "TextChannel", FakeTextChannel)
        monkeypatch.setattr(runtime, "_reserve_basic_verify_recovery_request", no_reserve)
        monkeypatch.setattr(runtime, "_delete_stale_foreign_basic_verify_panel", fake_delete)
        monkeypatch.setattr(runtime, "post_basic_verify_panel", fake_post)

        result = await runtime._reconcile_one_basic_verify_panel(
            fake_bot,
            FakeGuild(),
            {
                runtime._BASIC_VERIFY_PANEL_MESSAGE_ID_KEY: "123456",
                runtime._BASIC_VERIFY_PANEL_APPLICATION_ID_KEY: "999",
                "verify_channel_id": "99",
            },
            allow_legacy_rest=True,
        )

        assert result == "replaced_foreign_deleted"
        assert replacement_calls == [(99, True)]
        assert lifecycle == ["post", "delete"]

    asyncio.run(scenario())


def test_foreign_panel_is_preserved_when_replacement_cannot_be_confirmed(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        lifecycle: list[str] = []

        class FakeTextChannel:
            id = 99

            async def fetch_message(self, message_id: int):
                return SimpleNamespace(
                    id=message_id,
                    author=SimpleNamespace(id=999),
                    components=[
                        SimpleNamespace(
                            custom_id=runtime.BASIC_VERIFY_CUSTOM_ID,
                            children=[],
                        )
                    ],
                    embeds=[],
                )

        class FakeGuild:
            id = 77
            me = SimpleNamespace(id=42)

            def get_channel(self, channel_id: int):
                assert channel_id == 99
                return FakeTextChannel()

        async def no_reserve(*, label: str) -> None:
            assert "identity fetch" in label

        async def forbidden_delete(_message) -> bool:
            lifecycle.append("delete")
            raise AssertionError("stale panel must survive a failed replacement")

        async def fake_post(
            channel,
            *,
            actor_id: int = 0,
            bot_instance=None,
            require_history_scan_for_post: bool = False,
        ) -> str:
            _ = actor_id
            assert bot_instance is fake_bot
            assert require_history_scan_for_post is True
            lifecycle.append("post")
            return "scan_failed"

        fake_bot = FakeBot()
        monkeypatch.setattr(runtime.discord, "TextChannel", FakeTextChannel)
        monkeypatch.setattr(runtime, "_reserve_basic_verify_recovery_request", no_reserve)
        monkeypatch.setattr(runtime, "_delete_stale_foreign_basic_verify_panel", forbidden_delete)
        monkeypatch.setattr(runtime, "post_basic_verify_panel", fake_post)

        result = await runtime._reconcile_one_basic_verify_panel(
            fake_bot,
            FakeGuild(),
            {
                runtime._BASIC_VERIFY_PANEL_MESSAGE_ID_KEY: "123456",
                runtime._BASIC_VERIFY_PANEL_APPLICATION_ID_KEY: "999",
                "verify_channel_id": "99",
            },
            allow_legacy_rest=True,
        )

        assert result == "foreign_replacement_scan_failed"
        assert lifecycle == ["post"]

    asyncio.run(scenario())


def test_saved_unknown_application_panel_respects_startup_rest_cap(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        class FakeTextChannel:
            id = 99

            async def fetch_message(self, _message_id: int):
                raise AssertionError("REST must remain deferred when budget is exhausted")

        class FakeGuild:
            id = 77
            me = SimpleNamespace(id=42)

            def get_channel(self, channel_id: int):
                assert channel_id == 99
                return FakeTextChannel()

        monkeypatch.setattr(runtime.discord, "TextChannel", FakeTextChannel)

        result = await runtime._reconcile_one_basic_verify_panel(
            FakeBot(),
            FakeGuild(),
            {
                runtime._BASIC_VERIFY_PANEL_MESSAGE_ID_KEY: "123456",
                "verify_channel_id": "99",
            },
            allow_legacy_rest=False,
        )

        assert result == "legacy_deferred"

    asyncio.run(scenario())


def test_legacy_current_bot_panel_is_updated_persisted_and_bound(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        edits: list[object] = []
        writes: list[tuple[int, int]] = []

        class FakeMessage:
            id = 555
            author = SimpleNamespace(id=42)
            embeds = [discord.Embed(title="Verify to unlock server access")]

            async def edit(self, *, embed, view) -> None:
                edits.append((embed, view))

        class FakeHistory:
            def __aiter__(self):
                self._done = False
                return self

            async def __anext__(self):
                if self._done:
                    raise StopAsyncIteration
                self._done = True
                return FakeMessage()

        class FakeTextChannel:
            id = 99
            guild = None

            def history(self, *, limit: int):
                assert limit == 80
                return FakeHistory()

            async def send(self, *args, **kwargs):
                raise AssertionError("existing current-bot panel must be updated")

        class FakeGuild:
            id = 77
            me = SimpleNamespace(id=42)

            def __init__(self) -> None:
                self.channel = FakeTextChannel()
                self.channel.guild = self

            def get_channel(self, channel_id: int):
                assert channel_id == 99
                return self.channel

        async def fake_cfg(_guild_id: int, refresh: bool = False):
            assert refresh is True
            return {
                "verify_channel_id": "99",
                "basic_verify_enabled": True,
            }

        async def fake_persist(
            guild_id: int,
            message_id: int,
            *,
            application_id: int = 0,
        ) -> None:
            _ = application_id
            writes.append((guild_id, message_id))

        monkeypatch.setattr(runtime.discord, "TextChannel", FakeTextChannel)
        monkeypatch.setattr(runtime, "get_guild_config", fake_cfg)
        monkeypatch.setattr(
            runtime,
            "basic_verify_allowed_for_guild",
            lambda _guild, _cfg: True,
        )
        monkeypatch.setattr(runtime, "build_basic_verify_embed", lambda *_a: discord.Embed(title="fresh"))
        monkeypatch.setattr(runtime, "is_basic_verify_panel_embed", lambda _embed: True)
        monkeypatch.setattr(
            runtime,
            "_persist_basic_verify_panel_message_id",
            fake_persist,
        )

        bot = FakeBot()
        guild = FakeGuild()

        result = await runtime.post_basic_verify_panel(
            guild.channel,
            bot_instance=bot,
        )

        assert result == "updated"
        assert len(edits) == 1
        assert writes == [(77, 555)]
        assert bot.views[0][1] == 555

    asyncio.run(scenario())


def test_foreign_legacy_panel_causes_fresh_current_bot_panel(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        writes: list[tuple[int, int]] = []

        class FakeMessage:
            def __init__(self, *, message_id: int, author_id: int) -> None:
                self.id = message_id
                self.author = SimpleNamespace(id=author_id)
                self.embeds = [discord.Embed(title="Verify to unlock server access")]

        class FakeHistory:
            def __aiter__(self):
                self._items = iter([FakeMessage(message_id=444, author_id=999)])
                return self

            async def __anext__(self):
                try:
                    return next(self._items)
                except StopIteration:
                    raise StopAsyncIteration

        class FakeTextChannel:
            id = 99
            guild = None

            def history(self, *, limit: int):
                assert limit == 80
                return FakeHistory()

            async def send(self, *args, **kwargs):
                return SimpleNamespace(id=777)

        class FakeGuild:
            id = 77
            me = SimpleNamespace(id=42)

            def __init__(self) -> None:
                self.channel = FakeTextChannel()
                self.channel.guild = self

        async def fake_cfg(_guild_id: int, refresh: bool = False):
            return {
                "verify_channel_id": "99",
                "basic_verify_enabled": True,
            }

        async def fake_persist(
            guild_id: int,
            message_id: int,
            *,
            application_id: int = 0,
        ) -> None:
            _ = application_id
            writes.append((guild_id, message_id))

        monkeypatch.setattr(runtime.discord, "TextChannel", FakeTextChannel)
        monkeypatch.setattr(runtime, "get_guild_config", fake_cfg)
        monkeypatch.setattr(
            runtime,
            "basic_verify_allowed_for_guild",
            lambda _guild, _cfg: True,
        )
        monkeypatch.setattr(runtime, "build_basic_verify_embed", lambda *_a: discord.Embed(title="fresh"))
        monkeypatch.setattr(runtime, "is_basic_verify_panel_embed", lambda _embed: True)
        monkeypatch.setattr(
            runtime,
            "_persist_basic_verify_panel_message_id",
            fake_persist,
        )

        bot = FakeBot()
        guild = FakeGuild()

        result = await runtime.post_basic_verify_panel(
            guild.channel,
            bot_instance=bot,
        )

        assert result == "posted"
        assert writes == [(77, 777)]
        assert bot.views[0][1] == 777

    asyncio.run(scenario())


def test_button_callback_delegates_to_canonical_interaction_handler(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        calls: list[object] = []

        async def fake_handler(interaction) -> bool:
            calls.append(interaction)
            return True

        monkeypatch.setattr(
            runtime,
            "maybe_handle_basic_verify_interaction",
            fake_handler,
        )
        interaction = SimpleNamespace(id=1001)

        button = runtime.BasicVerifyButton()
        await button.callback(interaction)

        assert calls == [interaction]

    asyncio.run(scenario())


def test_verify_acknowledges_before_role_or_database_work(monkeypatch) -> None:
    async def scenario() -> None:
        events: list[tuple[str, object]] = []
        guild = SimpleNamespace(id=77)

        class FakeMember:
            def __init__(self) -> None:
                self.id = 88
                self.guild = guild

        class Response:
            def __init__(self) -> None:
                self.done = False

            def is_done(self) -> bool:
                return self.done

            async def defer(
                self,
                *,
                ephemeral: bool,
                thinking: bool,
            ) -> None:
                events.append(("defer", (ephemeral, thinking)))
                self.done = True

        class Followup:
            async def send(self, content: str, **kwargs) -> None:
                events.append(("followup", content))

        member = FakeMember()
        response = Response()
        interaction = SimpleNamespace(
            id=9001,
            type=discord.InteractionType.component,
            data={"custom_id": runtime.BASIC_VERIFY_CUSTOM_ID},
            guild=guild,
            channel=SimpleNamespace(id=99),
            user=member,
            response=response,
            followup=Followup(),
        )

        async def fake_apply(received_member) -> tuple[bool, str]:
            assert received_member is member
            assert response.is_done() is True
            events.append(("apply", received_member.id))
            return True, "verified"

        monkeypatch.setattr(runtime.discord, "Member", FakeMember)
        monkeypatch.setattr(runtime, "apply_basic_verification", fake_apply)

        handled = await runtime.maybe_handle_basic_verify_interaction(interaction)

        assert handled is True
        assert events[0] == ("defer", (True, True))
        assert events[1] == ("apply", member.id)
        assert events[2][0] == "followup"
        assert str(events[2][1]).startswith("✅ ")

    asyncio.run(scenario())


def test_already_acknowledged_duplicate_never_repeats_role_mutation(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        guild = SimpleNamespace(id=77)

        class FakeMember:
            id = 88

        class Response:
            def is_done(self) -> bool:
                return True

        class Followup:
            async def send(self, *args, **kwargs) -> None:
                raise AssertionError("duplicate route must not send a second reply")

        async def forbidden_apply(_member):
            raise AssertionError("duplicate route must not mutate roles")

        monkeypatch.setattr(runtime.discord, "Member", FakeMember)
        monkeypatch.setattr(runtime, "apply_basic_verification", forbidden_apply)

        interaction = SimpleNamespace(
            id=9002,
            type=discord.InteractionType.component,
            data={"custom_id": runtime.BASIC_VERIFY_CUSTOM_ID},
            guild=guild,
            channel=SimpleNamespace(id=99),
            user=FakeMember(),
            response=Response(),
            followup=Followup(),
        )

        handled = await runtime.maybe_handle_basic_verify_interaction(interaction)

        assert handled is True

    asyncio.run(scenario())


def test_host_compatibility_path_does_not_install_a_second_basic_verify_dispatcher() -> None:
    code = """
from stoney_verify import interaction_handlers

handler = interaction_handlers.handle_component_interaction
assert not getattr(handler, "_basic_verify_ready", False)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
