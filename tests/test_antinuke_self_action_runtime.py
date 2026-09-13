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
    reason: str = "",
):
    target = SimpleNamespace(id=target_id) if target_id is not None else None
    return SimpleNamespace(
        id=1234,
        guild=guild,
        action=SimpleNamespace(name=action),
        user=SimpleNamespace(id=actor_id),
        user_id=actor_id,
        target=target,
        reason=reason,
    )


def _reset() -> None:
    runtime._PENDING.clear()  # noqa: SLF001
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


def test_stale_authorization_expires() -> None:
    _reset()
    spec = runtime._spec(("channel_delete",), 7, "id:90")  # noqa: SLF001
    nonce, _reason = runtime._authorize(spec, "test")  # noqa: SLF001
    created = runtime._PENDING[nonce].created_at  # noqa: SLF001

    runtime._prune_pending(created + runtime._AUTH_TTL_SECONDS + 1.0)  # noqa: SLF001

    assert nonce not in runtime._PENDING  # noqa: SLF001


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
