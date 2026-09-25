from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_self_action_runtime as runtime
from stoney_verify import anti_nuke_zero_damage_runtime as hardening


class FakeHTTP:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict]] = []

    async def request(self, route, *args, **kwargs):
        _ = args
        self.calls.append((route, dict(kwargs)))
        return {"ok": True}


class FakeGuild:
    def __init__(self, guild_id: int = 7) -> None:
        self.id = guild_id
        self.owner_id = 999
        self.leave_calls = 0

    async def leave(self) -> None:
        self.leave_calls += 1


class FakeBot:
    def __init__(self, user_id: int = 55) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.http = FakeHTTP()
        self.guilds = []
        self.listeners: list[tuple[object, str]] = []

    def add_listener(self, listener, event_name: str) -> None:
        self.listeners.append((listener, event_name))

    def get_channel(self, _channel_id: int):
        return None

    def get_user(self, _user_id: int):
        return None


class FakeRoute:
    def __init__(self, method: str, path: str) -> None:
        self.method = method
        self.path = path
        self.url = f"https://discord.com/api/v10{path}"


def _entry(
    guild: FakeGuild,
    *,
    action: str,
    actor_id: int = 55,
    target_id: int | None = None,
    related_bot_id: int | None = None,
    reason: str = "",
    channel_id: int | None = None,
):
    target = SimpleNamespace(id=target_id) if target_id is not None else None
    if target is not None and related_bot_id is not None:
        target.application = SimpleNamespace(id=related_bot_id)
    extra = None
    if channel_id is not None:
        extra = SimpleNamespace(
            channel=SimpleNamespace(id=int(channel_id)),
            channel_id=int(channel_id),
        )
    return SimpleNamespace(
        id=1234,
        guild=guild,
        action=SimpleNamespace(name=action),
        user=SimpleNamespace(id=actor_id),
        user_id=actor_id,
        target=target,
        reason=reason,
        extra=extra,
    )


def _reset() -> None:
    runtime._PENDING.clear()  # noqa: SLF001
    runtime._EXPECTED_SIDE_EFFECTS.clear()  # noqa: SLF001
    runtime._COMPROMISE_GUILDS.clear()  # noqa: SLF001


def test_sensitive_http_request_gets_one_time_audit_authorization(monkeypatch) -> None:
    _reset()
    bot = FakeBot()
    guild = FakeGuild()
    route = FakeRoute("DELETE", "/guilds/7/roles/9")

    assert runtime._patch_http(bot) is True  # noqa: SLF001
    asyncio.run(bot.http.request(route, reason="cleanup"))

    assert len(bot.http.calls) == 1
    reason = bot.http.calls[0][1]["reason"]
    assert reason.startswith("cleanup ")
    assert "[DSA:" in reason
    assert len(runtime._PENDING) == 1  # noqa: SLF001

    async def should_not_read_settings(_guild_id: int):
        raise AssertionError("valid self authorization should finish before settings lookup")

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", should_not_read_settings)
    event = _entry(guild, action="role_delete", target_id=9, reason=reason)
    asyncio.run(runtime._audit_guard(bot, event))  # noqa: SLF001

    assert guild.leave_calls == 0
    assert runtime._PENDING == {}  # noqa: SLF001


def test_authorization_is_action_guild_and_target_scoped() -> None:
    _reset()
    guild = FakeGuild(7)
    spec = runtime._spec(("role_delete",), 7, "id:9")  # noqa: SLF001
    nonce, reason = runtime._authorize(spec, "test")  # noqa: SLF001

    wrong_target = _entry(guild, action="role_delete", target_id=10, reason=reason)
    assert runtime._consume(guild, wrong_target, "role_delete") is False  # noqa: SLF001
    assert nonce in runtime._PENDING  # noqa: SLF001

    wrong_action = _entry(guild, action="channel_delete", target_id=9, reason=reason)
    assert runtime._consume(guild, wrong_action, "channel_delete") is False  # noqa: SLF001
    assert nonce in runtime._PENDING  # noqa: SLF001

    wrong_guild = FakeGuild(8)
    matching_target = _entry(wrong_guild, action="role_delete", target_id=9, reason=reason)
    assert runtime._consume(wrong_guild, matching_target, "role_delete") is False  # noqa: SLF001
    assert nonce in runtime._PENDING  # noqa: SLF001

    good = _entry(guild, action="role_delete", target_id=9, reason=reason)
    assert runtime._consume(guild, good, "role_delete") is True  # noqa: SLF001
    assert nonce not in runtime._PENDING  # noqa: SLF001
    assert runtime._consume(guild, good, "role_delete") is False  # noqa: SLF001


def test_stale_authorization_expires_after_request_completion() -> None:
    _reset()
    spec = runtime._spec(("channel_delete",), 7, "id:90")  # noqa: SLF001
    nonce, _reason = runtime._authorize(spec, "test")  # noqa: SLF001
    created = runtime._PENDING[nonce].created_at  # noqa: SLF001
    runtime._complete(nonce, created)  # noqa: SLF001

    runtime._prune_pending(created + runtime._AUTH_TTL_SECONDS + 1.0)  # noqa: SLF001

    assert nonce not in runtime._PENDING  # noqa: SLF001


def test_rate_limited_channel_patch_keeps_self_proof_until_request_completes(
    monkeypatch,
) -> None:
    _reset()
    clock = [1000.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])

    class RateLimitedHTTP:
        def __init__(self) -> None:
            self.calls: list[tuple[object, dict]] = []

        async def request(self, route, *args, **kwargs):
            _ = args
            self.calls.append((route, dict(kwargs)))
            assert len(runtime._PENDING) == 1  # noqa: SLF001
            auth = next(iter(runtime._PENDING.values()))  # noqa: SLF001
            assert auth.completed_at is None
            clock[0] += runtime._AUTH_TTL_SECONDS + 30.0  # noqa: SLF001
            runtime._prune_pending(clock[0])  # noqa: SLF001
            assert auth.nonce in runtime._PENDING  # noqa: SLF001
            return {"ok": True}

    bot = FakeBot()
    bot.http = RateLimitedHTTP()
    guild = FakeGuild(7)
    bot.get_channel = lambda channel_id: (
        SimpleNamespace(id=int(channel_id), guild=guild)
        if int(channel_id) == 90
        else None
    )
    route = FakeRoute("PATCH", "/channels/90")

    assert runtime._patch_http(bot) is True  # noqa: SLF001
    asyncio.run(bot.http.request(route, reason="rate-limited stats rename"))

    assert len(bot.http.calls) == 1
    reason = bot.http.calls[0][1]["reason"]
    assert len(runtime._PENDING) == 1  # noqa: SLF001
    auth = next(iter(runtime._PENDING.values()))  # noqa: SLF001
    assert auth.completed_at == clock[0]

    async def should_not_read_settings(_guild_id: int):
        raise AssertionError(
            "rate-limited local channel PATCH must retain self-action proof"
        )

    monkeypatch.setattr(
        anti_nuke,
        "get_antinuke_settings",
        should_not_read_settings,
    )
    event = _entry(
        guild,
        action="channel_update",
        target_id=90,
        reason=reason,
    )
    asyncio.run(runtime._audit_guard(bot, event))  # noqa: SLF001

    assert guild.leave_calls == 0
    assert runtime._PENDING == {}  # noqa: SLF001


def test_cancelled_http_request_discards_inflight_authorization() -> None:
    _reset()

    class CancelledHTTP:
        async def request(self, route, *args, **kwargs):
            _ = route, args, kwargs
            raise asyncio.CancelledError()

    bot = FakeBot()
    bot.http = CancelledHTTP()
    route = FakeRoute("PATCH", "/guilds/7")

    assert runtime._patch_http(bot) is True  # noqa: SLF001

    async def run_cancelled() -> None:
        try:
            await bot.http.request(route, reason="cancel me")
        except asyncio.CancelledError:
            return
        raise AssertionError("cancelled request must propagate cancellation")

    asyncio.run(run_cancelled())

    assert runtime._PENDING == {}  # noqa: SLF001


def test_inflight_channel_patch_can_be_consumed_before_http_returns(
    monkeypatch,
) -> None:
    _reset()
    clock = [3000.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    guild = FakeGuild(7)
    holder: dict[str, object] = {}

    async def should_not_read_settings(_guild_id: int):
        raise AssertionError(
            "in-flight local audit proof must consume before compromise checks"
        )

    monkeypatch.setattr(
        anti_nuke,
        "get_antinuke_settings",
        should_not_read_settings,
    )

    class AuditBeforeResponseHTTP:
        async def request(self, route, *args, **kwargs):
            _ = route, args
            clock[0] += runtime._AUTH_TTL_SECONDS + 30.0  # noqa: SLF001
            runtime._prune_pending(clock[0])  # noqa: SLF001
            event = _entry(
                guild,
                action="channel_update",
                target_id=90,
                reason=str(kwargs["reason"]),
            )
            await runtime._audit_guard(holder["bot"], event)  # type: ignore[arg-type]  # noqa: SLF001
            assert runtime._PENDING == {}  # noqa: SLF001
            return {"ok": True}

    bot = FakeBot()
    holder["bot"] = bot
    bot.http = AuditBeforeResponseHTTP()
    bot.get_channel = lambda channel_id: (
        SimpleNamespace(id=int(channel_id), guild=guild)
        if int(channel_id) == 90
        else None
    )

    assert runtime._patch_http(bot) is True  # noqa: SLF001
    asyncio.run(
        bot.http.request(
            FakeRoute("PATCH", "/channels/90"),
            reason="slow local rename",
        )
    )

    assert guild.leave_calls == 0
    assert runtime._PENDING == {}  # noqa: SLF001


def test_valid_provenance_is_not_count_evicted_before_ttl() -> None:
    _reset()
    now = 4000.0
    actions = frozenset({"channel_update"})

    for index in range(4100):
        nonce = f"{index:024x}"
        runtime._PENDING[nonce] = runtime._Authorization(  # noqa: SLF001
            nonce=nonce,
            actions=actions,
            guild_id=7,
            target_key=f"id:{index + 1}",
            created_at=now,
            completed_at=now,
        )
        token = f"s{index}"
        runtime._EXPECTED_SIDE_EFFECTS[token] = runtime._ExpectedSideEffect(  # noqa: SLF001
            token=token,
            action="integration_delete",
            guild_id=7,
            related_bot_id=index + 1,
            target_key="",
            source_action="load-test",
            created_at=now,
            completed_at=now,
        )

    runtime._prune_pending(now + runtime._AUTH_TTL_SECONDS - 1.0)  # noqa: SLF001
    runtime._prune_expected_side_effects(  # noqa: SLF001
        now + runtime._SIDE_EFFECT_TTL_SECONDS - 1.0
    )

    assert len(runtime._PENDING) == 4100  # noqa: SLF001
    assert len(runtime._EXPECTED_SIDE_EFFECTS) == 4100  # noqa: SLF001

    runtime._prune_pending(now + runtime._AUTH_TTL_SECONDS + 1.0)  # noqa: SLF001
    runtime._prune_expected_side_effects(  # noqa: SLF001
        now + runtime._SIDE_EFFECT_TTL_SECONDS + 1.0
    )

    assert runtime._PENDING == {}  # noqa: SLF001
    assert runtime._EXPECTED_SIDE_EFFECTS == {}  # noqa: SLF001


def test_expected_local_message_delete_is_channel_scoped_and_one_time() -> None:
    _reset()
    guild = FakeGuild()
    token = runtime._expect_side_effect(  # noqa: SLF001
        guild.id,
        "message_delete",
        target_key="id:123",
        source_action="local_message_delete",
    )

    wrong_channel = _entry(
        guild,
        action="message_delete",
        target_id=9001,
        channel_id=124,
    )
    assert (
        runtime._consume_expected_side_effect(  # noqa: SLF001
            guild,
            wrong_channel,
            "message_delete",
        )
        is False
    )
    assert token in runtime._EXPECTED_SIDE_EFFECTS  # noqa: SLF001

    matching = _entry(
        guild,
        action="message_delete",
        target_id=9001,
        channel_id=123,
    )
    assert (
        runtime._entry_target_key(matching, "message_delete")  # noqa: SLF001
        == "id:123"
    )
    assert (
        runtime._consume_expected_side_effect(  # noqa: SLF001
            guild,
            matching,
            "message_delete",
        )
        is True
    )
    assert token not in runtime._EXPECTED_SIDE_EFFECTS  # noqa: SLF001
    assert (
        runtime._consume_expected_side_effect(  # noqa: SLF001
            guild,
            matching,
            "message_delete",
        )
        is False
    )


def test_http_message_delete_without_audit_reason_is_expected_self_action(
    monkeypatch,
) -> None:
    _reset()
    bot = FakeBot()
    guild = FakeGuild()
    bot.get_channel = lambda channel_id: (
        SimpleNamespace(id=channel_id, guild=guild)
        if int(channel_id) == 123
        else None
    )

    assert runtime._patch_http(bot) is True  # noqa: SLF001
    route = FakeRoute("DELETE", "/channels/123/messages/456")
    asyncio.run(bot.http.request(route))

    assert len(runtime._EXPECTED_SIDE_EFFECTS) == 1  # noqa: SLF001
    expected = next(
        iter(runtime._EXPECTED_SIDE_EFFECTS.values())  # noqa: SLF001
    )
    assert expected.action == "message_delete"
    assert expected.target_key == "id:123"

    async def should_not_read_settings(_guild_id: int):
        raise AssertionError(
            "expected local message cleanup must finish before compromise checks"
        )

    monkeypatch.setattr(
        anti_nuke,
        "get_antinuke_settings",
        should_not_read_settings,
    )
    event = _entry(
        guild,
        action="message_delete",
        actor_id=55,
        target_id=777,
        channel_id=123,
        reason="",
    )
    asyncio.run(runtime._audit_guard(bot, event))  # noqa: SLF001

    assert guild.leave_calls == 0
    assert runtime._EXPECTED_SIDE_EFFECTS == {}  # noqa: SLF001


def test_message_delete_marker_consumption_clears_fallback_receipt(
    monkeypatch,
) -> None:
    _reset()
    bot = FakeBot()
    guild = FakeGuild()
    bot.get_channel = lambda channel_id: (
        SimpleNamespace(id=channel_id, guild=guild)
        if int(channel_id) == 123
        else None
    )

    assert runtime._patch_http(bot) is True  # noqa: SLF001
    route = FakeRoute("DELETE", "/channels/123/messages/456")
    asyncio.run(bot.http.request(route))
    reason = bot.http.calls[-1][1]["reason"]

    async def should_not_read_settings(_guild_id: int):
        raise AssertionError(
            "DSA marker must be consumed before compromise checks"
        )

    monkeypatch.setattr(
        anti_nuke,
        "get_antinuke_settings",
        should_not_read_settings,
    )
    event = _entry(
        guild,
        action="message_delete",
        actor_id=55,
        target_id=777,
        channel_id=123,
        reason=reason,
    )
    asyncio.run(runtime._audit_guard(bot, event))  # noqa: SLF001

    assert runtime._PENDING == {}  # noqa: SLF001
    assert runtime._EXPECTED_SIDE_EFFECTS == {}  # noqa: SLF001
    assert guild.leave_calls == 0


def test_expected_bot_removal_integration_delete_is_one_time(monkeypatch) -> None:
    _reset()
    bot = FakeBot()
    guild = FakeGuild()

    async def should_not_read_settings(_guild_id: int):
        raise AssertionError(
            "expected Discord cleanup must be consumed before compromise checks"
        )

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", should_not_read_settings)

    token = runtime._expect_side_effect(  # noqa: SLF001
        guild.id,
        "integration_delete",
        related_bot_id=444,
        source_action="bot_kick",
    )
    assert token in runtime._EXPECTED_SIDE_EFFECTS  # noqa: SLF001

    expected = _entry(
        guild,
        action="integration_delete",
        target_id=9001,
        related_bot_id=444,
    )
    asyncio.run(runtime._audit_guard(bot, expected))  # noqa: SLF001

    assert guild.leave_calls == 0
    assert runtime._EXPECTED_SIDE_EFFECTS == {}  # noqa: SLF001


def test_expected_bot_removal_side_effect_does_not_hide_unrelated_or_second_delete(
    monkeypatch,
) -> None:
    _reset()
    bot = FakeBot()
    guild = FakeGuild()

    async def settings(_guild_id: int):
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}

    async def no_warning(*_args, **_kwargs):
        return None

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(runtime, "_warn_owner", no_warning)

    runtime._expect_side_effect(  # noqa: SLF001
        guild.id,
        "integration_delete",
        related_bot_id=444,
        source_action="bot_kick",
    )

    unrelated = _entry(
        guild,
        action="integration_delete",
        target_id=9002,
        related_bot_id=777,
    )
    asyncio.run(runtime._audit_guard(bot, unrelated))  # noqa: SLF001
    assert guild.leave_calls == 1
    assert len(runtime._EXPECTED_SIDE_EFFECTS) == 1  # noqa: SLF001

    _reset()
    runtime._expect_side_effect(  # noqa: SLF001
        guild.id,
        "integration_delete",
        related_bot_id=444,
        source_action="bot_kick",
    )
    first = _entry(
        guild,
        action="integration_delete",
        target_id=9001,
        related_bot_id=444,
    )
    asyncio.run(runtime._audit_guard(bot, first))  # noqa: SLF001
    assert guild.leave_calls == 1

    second = _entry(
        guild,
        action="integration_delete",
        target_id=9003,
        related_bot_id=444,
    )
    asyncio.run(runtime._audit_guard(bot, second))  # noqa: SLF001
    assert guild.leave_calls == 2


def test_expected_bot_removal_accepts_sparse_integration_target_once(
    monkeypatch,
) -> None:
    _reset()
    bot = FakeBot()
    guild = FakeGuild()

    async def should_not_read_settings(_guild_id: int):
        raise AssertionError(
            "sparse expected Discord cleanup must be consumed before compromise checks"
        )

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", should_not_read_settings)

    runtime._expect_side_effect(  # noqa: SLF001
        guild.id,
        "integration_delete",
        related_bot_id=444,
        source_action="bot_kick",
    )
    sparse = _entry(
        guild,
        action="integration_delete",
        target_id=9001,
    )
    asyncio.run(runtime._audit_guard(bot, sparse))  # noqa: SLF001

    assert guild.leave_calls == 0
    assert runtime._EXPECTED_SIDE_EFFECTS == {}  # noqa: SLF001


def test_expected_bot_removal_side_effect_expires() -> None:
    _reset()
    token = runtime._expect_side_effect(  # noqa: SLF001
        7,
        "integration_delete",
        related_bot_id=444,
        source_action="bot_kick",
    )
    created = runtime._EXPECTED_SIDE_EFFECTS[token].created_at  # noqa: SLF001

    runtime._prune_expected_side_effects(  # noqa: SLF001
        created + runtime._SIDE_EFFECT_TTL_SECONDS + 1.0
    )

    assert token not in runtime._EXPECTED_SIDE_EFFECTS  # noqa: SLF001


def test_rate_limited_bot_removal_keeps_derived_side_effect_until_completion(
    monkeypatch,
) -> None:
    _reset()
    clock = [2000.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])

    class RateLimitedHTTP:
        async def request(self, route, *args, **kwargs):
            _ = route, args, kwargs
            assert len(runtime._EXPECTED_SIDE_EFFECTS) == 1  # noqa: SLF001
            expected = next(  # noqa: SLF001
                iter(runtime._EXPECTED_SIDE_EFFECTS.values())  # noqa: SLF001
            )
            assert expected.completed_at is None
            clock[0] += runtime._SIDE_EFFECT_TTL_SECONDS + 30.0  # noqa: SLF001
            runtime._prune_expected_side_effects(clock[0])  # noqa: SLF001
            assert expected.token in runtime._EXPECTED_SIDE_EFFECTS  # noqa: SLF001
            return {"ok": True}

    bot = FakeBot()
    bot.http = RateLimitedHTTP()
    bot.get_user = lambda user_id: (
        SimpleNamespace(id=444, bot=True) if int(user_id) == 444 else None
    )
    route = FakeRoute("DELETE", "/guilds/7/members/444")

    assert runtime._patch_http(bot) is True  # noqa: SLF001
    asyncio.run(
        bot.http.request(
            route,
            reason="Dank Shield AntiNuke rollback: unauthorized bot addition",
        )
    )

    assert len(runtime._EXPECTED_SIDE_EFFECTS) == 1  # noqa: SLF001
    expected = next(iter(runtime._EXPECTED_SIDE_EFFECTS.values()))  # noqa: SLF001
    assert expected.completed_at == clock[0]

    runtime._prune_expected_side_effects(  # noqa: SLF001
        clock[0] + runtime._SIDE_EFFECT_TTL_SECONDS - 1.0
    )
    assert expected.token in runtime._EXPECTED_SIDE_EFFECTS  # noqa: SLF001

    runtime._prune_expected_side_effects(  # noqa: SLF001
        clock[0] + runtime._SIDE_EFFECT_TTL_SECONDS + 1.0
    )
    assert expected.token not in runtime._EXPECTED_SIDE_EFFECTS  # noqa: SLF001


def test_http_bot_kick_arms_expected_integration_cleanup(monkeypatch) -> None:
    _reset()
    bot = FakeBot()
    bot.get_user = lambda user_id: (
        SimpleNamespace(id=444, bot=True) if int(user_id) == 444 else None
    )
    route = FakeRoute("DELETE", "/guilds/7/members/444")

    assert runtime._patch_http(bot) is True  # noqa: SLF001
    asyncio.run(
        bot.http.request(
            route,
            reason="Dank Shield AntiNuke rollback: unauthorized bot addition",
        )
    )

    assert len(runtime._EXPECTED_SIDE_EFFECTS) == 1  # noqa: SLF001
    expected = next(iter(runtime._EXPECTED_SIDE_EFFECTS.values()))  # noqa: SLF001
    assert expected.guild_id == 7
    assert expected.action == "integration_delete"
    assert expected.related_bot_id == 444
    assert expected.source_action == "bot_kick"

    async def should_not_read_settings(_guild_id: int):
        raise AssertionError(
            "derived integration cleanup must be consumed before compromise checks"
        )

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", should_not_read_settings)
    event = _entry(
        FakeGuild(7),
        action="integration_delete",
        target_id=9001,
        related_bot_id=444,
    )
    asyncio.run(runtime._audit_guard(bot, event))  # noqa: SLF001

    assert runtime._EXPECTED_SIDE_EFFECTS == {}  # noqa: SLF001


def test_http_bot_kick_direct_audit_then_integration_cleanup_is_order_safe(
    monkeypatch,
) -> None:
    _reset()
    bot = FakeBot()
    bot.get_user = lambda user_id: (
        SimpleNamespace(id=444, bot=True) if int(user_id) == 444 else None
    )
    guild = FakeGuild(7)
    route = FakeRoute("DELETE", "/guilds/7/members/444")

    assert runtime._patch_http(bot) is True  # noqa: SLF001
    asyncio.run(
        bot.http.request(
            route,
            reason="Dank Shield AntiNuke rollback: unauthorized bot addition",
        )
    )

    stamped_reason = bot.http.calls[0][1]["reason"]
    assert len(runtime._PENDING) == 1  # noqa: SLF001
    assert len(runtime._EXPECTED_SIDE_EFFECTS) == 1  # noqa: SLF001

    async def should_not_read_settings(_guild_id: int):
        raise AssertionError(
            "authorized direct and derived actions must be consumed before settings"
        )

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", should_not_read_settings)

    direct = _entry(
        guild,
        action="kick",
        target_id=444,
        reason=stamped_reason,
    )
    asyncio.run(runtime._audit_guard(bot, direct))  # noqa: SLF001

    assert runtime._PENDING == {}  # noqa: SLF001
    assert len(runtime._EXPECTED_SIDE_EFFECTS) == 1  # noqa: SLF001

    derived = _entry(
        guild,
        action="integration_delete",
        target_id=9001,
        related_bot_id=444,
    )
    asyncio.run(runtime._audit_guard(bot, derived))  # noqa: SLF001

    assert guild.leave_calls == 0
    assert runtime._PENDING == {}  # noqa: SLF001
    assert runtime._EXPECTED_SIDE_EFFECTS == {}  # noqa: SLF001


def test_concurrent_bot_removal_receipts_match_rich_identity_out_of_order() -> None:
    _reset()
    first = runtime._expect_side_effect(  # noqa: SLF001
        7,
        "integration_delete",
        related_bot_id=444,
        source_action="bot_kick",
    )
    second = runtime._expect_side_effect(  # noqa: SLF001
        7,
        "integration_delete",
        related_bot_id=555,
        source_action="bot_kick",
    )

    guild = FakeGuild(7)
    second_entry = _entry(
        guild,
        action="integration_delete",
        target_id=9002,
        related_bot_id=555,
    )
    assert (
        runtime._consume_expected_side_effect(  # noqa: SLF001
            guild,
            second_entry,
            "integration_delete",
        )
        is True
    )

    assert first in runtime._EXPECTED_SIDE_EFFECTS  # noqa: SLF001
    assert second not in runtime._EXPECTED_SIDE_EFFECTS  # noqa: SLF001

    first_entry = _entry(
        guild,
        action="integration_delete",
        target_id=9001,
        related_bot_id=444,
    )
    assert (
        runtime._consume_expected_side_effect(  # noqa: SLF001
            guild,
            first_entry,
            "integration_delete",
        )
        is True
    )
    assert runtime._EXPECTED_SIDE_EFFECTS == {}  # noqa: SLF001


def test_http_bot_ban_uses_cache_or_antinuke_reason_fallback() -> None:
    _reset()
    bot = FakeBot()
    route = FakeRoute("PUT", "/guilds/7/bans/447")

    assert runtime._patch_http(bot) is True  # noqa: SLF001
    asyncio.run(
        bot.http.request(
            route,
            reason="Dank Shield AntiNuke: fast known-hostile bot re-add block",
        )
    )

    expected = list(runtime._EXPECTED_SIDE_EFFECTS.values())  # noqa: SLF001
    assert len(expected) == 1
    assert expected[0].related_bot_id == 447
    assert expected[0].source_action == "bot_ban"


def test_http_human_removal_does_not_arm_integration_cleanup() -> None:
    _reset()
    bot = FakeBot()
    bot.get_user = lambda user_id: SimpleNamespace(id=int(user_id), bot=False)
    route = FakeRoute("DELETE", "/guilds/7/members/446")

    assert runtime._patch_http(bot) is True  # noqa: SLF001
    asyncio.run(
        bot.http.request(
            route,
            reason=(
                "Dank Shield AntiNuke containment: unauthorized bot addition "
                "• definitive AntiNuke containment"
            ),
        )
    )

    assert runtime._EXPECTED_SIDE_EFFECTS == {}  # noqa: SLF001


def test_failed_http_bot_removal_cancels_side_effect_and_nonce() -> None:
    _reset()

    class FailingHTTP:
        async def request(self, route, *args, **kwargs):
            _ = route, args, kwargs
            raise RuntimeError("request failed")

    bot = FakeBot()
    bot.http = FailingHTTP()
    bot.get_user = lambda user_id: (
        SimpleNamespace(id=444, bot=True) if int(user_id) == 444 else None
    )
    route = FakeRoute("DELETE", "/guilds/7/members/444")

    assert runtime._patch_http(bot) is True  # noqa: SLF001
    try:
        asyncio.run(bot.http.request(route, reason="ordinary bot moderation"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("failed bot removal must propagate the original error")

    assert runtime._PENDING == {}  # noqa: SLF001
    assert runtime._EXPECTED_SIDE_EFFECTS == {}  # noqa: SLF001


def test_unmatched_bot_attributed_action_self_ejects_in_contain_mode(monkeypatch) -> None:
    _reset()
    bot = FakeBot()
    guild = FakeGuild()

    async def settings(_guild_id: int):
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}

    async def no_warning(*_args, **_kwargs):
        return None

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(runtime, "_warn_owner", no_warning)

    event = _entry(guild, action="channel_delete", target_id=90, reason="external action")
    asyncio.run(runtime._audit_guard(bot, event))  # noqa: SLF001

    assert guild.leave_calls == 1
    assert guild.id in runtime._COMPROMISE_GUILDS  # noqa: SLF001

    asyncio.run(runtime._audit_guard(bot, event))  # noqa: SLF001
    assert guild.leave_calls == 1


def test_unmatched_bot_attributed_action_only_alerts_in_alert_mode(monkeypatch) -> None:
    _reset()
    bot = FakeBot()
    guild = FakeGuild()
    incidents: list[dict] = []

    async def settings(_guild_id: int):
        return {"antinuke_enabled": True, "antinuke_mode": "alert"}

    async def post(_guild, **kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(anti_nuke, "_post_incident", post)

    event = _entry(guild, action="role_delete", target_id=9, reason="")
    asyncio.run(runtime._audit_guard(bot, event))  # noqa: SLF001

    assert guild.leave_calls == 0
    assert len(incidents) == 1
    assert "Unverified Dank Shield Action" in incidents[0]["title"]


def test_other_actor_and_disabled_antinuke_do_not_trigger_self_ejection(monkeypatch) -> None:
    _reset()
    bot = FakeBot()
    guild = FakeGuild()
    settings_calls = 0

    async def settings(_guild_id: int):
        nonlocal settings_calls
        settings_calls += 1
        return {"antinuke_enabled": False, "antinuke_mode": "contain"}

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)

    other_actor = _entry(guild, action="channel_delete", actor_id=77, target_id=90)
    asyncio.run(runtime._audit_guard(bot, other_actor))  # noqa: SLF001
    assert settings_calls == 0

    self_actor = _entry(guild, action="channel_delete", actor_id=55, target_id=90)
    asyncio.run(runtime._audit_guard(bot, self_actor))  # noqa: SLF001
    assert settings_calls == 1
    assert guild.leave_calls == 0


def test_route_classifier_covers_webhook_message_and_authority_mutations() -> None:
    bot = FakeBot()
    original_request_spec = runtime._request_spec  # noqa: SLF001
    original_actions = runtime._PROTECTED_ACTIONS  # noqa: SLF001
    original_unmatched = runtime._unmatched_self_action  # noqa: SLF001
    had_flag = hasattr(runtime, hardening._SELF_FLAG)  # noqa: SLF001
    old_flag = getattr(runtime, hardening._SELF_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(runtime, hardening._SELF_FLAG)  # noqa: SLF001

    try:
        assert hardening._patch_self_action() is True  # noqa: SLF001
        webhook = runtime._request_spec(bot, FakeRoute("DELETE", "/webhooks/444"), {})  # noqa: SLF001
        assert webhook is not None
        assert webhook.actions == frozenset({"webhook_delete"})
        assert webhook.target_key == "id:444"

        bulk = runtime._request_spec(  # noqa: SLF001
            bot,
            FakeRoute("POST", "/channels/123/messages/bulk-delete"),
            {"json": {"messages": [1, 2]}},
        )
        assert bulk is not None
        assert bulk.actions == frozenset({"message_bulk_delete"})
        assert bulk.target_key == "id:123"

        single = runtime._request_spec(  # noqa: SLF001
            bot,
            FakeRoute("DELETE", "/channels/123/messages/456"),
            {},
        )
        assert single is not None
        assert single.actions == frozenset({"message_delete"})
        assert single.target_key == "id:123"

        role_grant = runtime._request_spec(  # noqa: SLF001
            bot,
            FakeRoute("PUT", "/guilds/7/members/88/roles/99"),
            {},
        )
        assert role_grant is not None
        assert role_grant.actions == frozenset({"member_role_update"})
        assert role_grant.guild_id == 7
        assert role_grant.target_key == "id:88"
    finally:
        runtime._request_spec = original_request_spec  # noqa: SLF001
        runtime._PROTECTED_ACTIONS = original_actions  # noqa: SLF001
        runtime._unmatched_self_action = original_unmatched  # noqa: SLF001
        if had_flag:
            setattr(runtime, hardening._SELF_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(runtime, hardening._SELF_FLAG):  # noqa: SLF001
            delattr(runtime, hardening._SELF_FLAG)  # noqa: SLF001
