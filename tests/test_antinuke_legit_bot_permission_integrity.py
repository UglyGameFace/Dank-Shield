from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stoney_verify import anti_nuke_hostile_actor_runtime as hostile
from stoney_verify import anti_nuke_lockdown_runtime as lockdown
from stoney_verify import guild_config


def _settings() -> dict[str, object]:
    return {
        "antinuke_enabled": True,
        "antinuke_mode": "contain",
        "antinuke_role_delete_threshold": 3,
        "antinuke_channel_delete_threshold": 2,
        "antinuke_ban_threshold": 3,
        "antinuke_kick_threshold": 3,
        "antinuke_webhook_create_threshold": 2,
        "antinuke_trusted_role_ids": [],
        "antinuke_trusted_user_ids": [],
        "antinuke_trusted_bot_ids": [],
    }


def _fake_anti_nuke():
    calls: dict[str, list[object]] = {
        "process_overrides": [],
        "contain": [],
        "member_grant": [],
        "role_escalation": [],
    }

    async def get_settings(_guild_id: int, *, refresh: bool = False):
        _ = refresh
        return dict(_settings())

    def configured_trust(_actor, _settings, *, ignore_role_ids=None):
        _ = ignore_role_ids
        return False

    async def process(guild, **kwargs):
        _ = guild
        calls["process_overrides"].append(kwargs.get("threshold_override"))
        actor = getattr(kwargs["entry"], "user", None)
        if fake._actor_is_configured_trusted(actor, _settings()):
            await fake._contain_actor(
                guild,
                actor,
                reason="threshold reached",
            )
        return True

    async def authorize(_guild, _target, actor, settings=None):
        allowed = fake._actor_is_configured_trusted(actor, settings or _settings())
        return bool(allowed), "delegated" if allowed else "not delegated"

    async def contain(_guild, actor, *, reason: str):
        calls["contain"].append((int(actor.id), reason))
        return ["contained"], []

    async def member_grant(before, after):
        calls["member_grant"].append((before, after))

    async def role_escalation(before, after):
        calls["role_escalation"].append((before, after))

    fake = SimpleNamespace(
        get_antinuke_settings=get_settings,
        _process_claimed_destructive_event=process,
        antinuke_permission_health=lambda _guild, _settings=None: [],
        _actor_is_configured_trusted=configured_trust,
        bot_add_authorization=authorize,
        _contain_actor=contain,
        _handle_member_dangerous_role_grant=member_grant,
        _handle_role_permission_escalation=role_escalation,
        DANGEROUS_PERMISSION_NAMES=("administrator",),
        normalize_antinuke_settings=lambda value: dict(value or {}),
    )
    return fake, calls


def test_operational_bot_uses_bounded_thresholds_without_weakening_bot_add(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, calls = _fake_anti_nuke()
    bot = SimpleNamespace(intents=SimpleNamespace(moderation=True))

    async def config(_guild_id: int, *, refresh: bool = False):
        _ = refresh
        return {}

    async def no_reputation(_guild_id: int, _user_id: int, *, refresh: bool = False):
        _ = refresh
        return None

    monkeypatch.setattr(guild_config, "get_guild_config", config)
    monkeypatch.setattr(hostile, "get_actor_reputation", no_reputation)
    monkeypatch.setattr(
        fake,
        lockdown._POLICY_PATCH_FLAG,  # noqa: SLF001
        False,
        raising=False,
    )

    assert lockdown._patch_anti_nuke_policy(fake, bot) is True  # noqa: SLF001

    guild = SimpleNamespace(id=1)
    actor = SimpleNamespace(id=55, bot=True, roles=[])
    entry = SimpleNamespace(user=actor)

    assert fake._actor_is_configured_trusted(actor, _settings()) is True

    allowed, _reason = asyncio.run(
        fake.bot_add_authorization(
            guild,
            SimpleNamespace(id=77, bot=True),
            actor,
            _settings(),
        )
    )
    assert allowed is False

    asyncio.run(
        fake._process_claimed_destructive_event(
            guild,
            entry=entry,
            action_key="channel_delete",
            action_label="Channel delete",
            target_label="#general",
            threshold_key="antinuke_channel_delete_threshold",
            threshold_override=1,
        )
    )

    assert calls["process_overrides"] == [None]
    assert calls["contain"] == [(55, "threshold reached")]


def test_direct_containment_preserves_bot_until_threshold_or_hostile_reputation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, calls = _fake_anti_nuke()
    bot = SimpleNamespace(intents=SimpleNamespace(moderation=True))

    async def config(_guild_id: int, *, refresh: bool = False):
        _ = refresh
        return {}

    async def no_reputation(_guild_id: int, _user_id: int, *, refresh: bool = False):
        _ = refresh
        return None

    monkeypatch.setattr(guild_config, "get_guild_config", config)
    monkeypatch.setattr(hostile, "get_actor_reputation", no_reputation)
    monkeypatch.setattr(fake, lockdown._POLICY_PATCH_FLAG, False, raising=False)  # noqa: SLF001
    lockdown._patch_anti_nuke_policy(fake, bot)  # noqa: SLF001

    guild = SimpleNamespace(id=1)
    actor = SimpleNamespace(id=55, bot=True, roles=[])

    removed, blocked = asyncio.run(
        fake._contain_actor(guild, actor, reason="immediate panic")
    )

    assert removed == []
    assert blocked == [
        "operational bot preserved; bounded AntiNuke thresholds apply"
    ]
    assert calls["contain"] == []

    async def hostile_reputation(_guild_id: int, _user_id: int, *, refresh: bool = False):
        _ = refresh
        return {"active": True}

    monkeypatch.setattr(hostile, "get_actor_reputation", hostile_reputation)
    removed, blocked = asyncio.run(
        fake._contain_actor(guild, actor, reason="known hostile")
    )
    assert removed == ["contained"]
    assert blocked == []
    assert calls["contain"] == [(55, "known hostile")]


def test_native_member_role_guard_does_not_strip_bot_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, calls = _fake_anti_nuke()
    bot = SimpleNamespace(intents=SimpleNamespace(moderation=True))

    async def config(_guild_id: int, *, refresh: bool = False):
        _ = refresh
        return {}

    monkeypatch.setattr(guild_config, "get_guild_config", config)
    monkeypatch.setattr(fake, lockdown._POLICY_PATCH_FLAG, False, raising=False)  # noqa: SLF001
    lockdown._patch_anti_nuke_policy(fake, bot)  # noqa: SLF001

    before = SimpleNamespace(id=77, bot=True)
    after = SimpleNamespace(id=77, bot=True)
    asyncio.run(fake._handle_member_dangerous_role_grant(before, after))

    assert calls["member_grant"] == []

    bot_only_role = SimpleNamespace(members=[SimpleNamespace(id=77, bot=True)])
    asyncio.run(fake._handle_role_permission_escalation(object(), bot_only_role))
    assert calls["role_escalation"] == []


def test_guardian_strict_rollback_does_not_force_bot_through_untrusted_proxy() -> None:
    seen: list[int] = []

    async def overwrite(_guild, _entry, actor, _action_name):
        seen.append(int(actor.id))
        return "ok"

    async def automod(_guild, _entry, actor, _action_name):
        seen.append(int(actor.id))
        return "ok"

    guardian = SimpleNamespace(
        _ACTIONS={},
        _PANIC_WEIGHTS={},
        _PANIC_ACTIONS=frozenset(),
        _PANIC_SEVERE_ACTIONS=frozenset(),
        _rollback_untrusted_overwrite=overwrite,
        _rollback_untrusted_automod=automod,
    )
    anti_nuke = SimpleNamespace(
        get_antinuke_settings=lambda _guild_id: _async_value(_settings()),
        _actor_is_owner_or_bot=lambda _guild, _actor: False,
    )
    setattr(guardian, lockdown._GUARDIAN_PATCH_FLAG, False)  # noqa: SLF001

    assert lockdown._patch_guardian_surface(guardian, anti_nuke) is True  # noqa: SLF001
    actor = SimpleNamespace(id=55, bot=True)
    guild = SimpleNamespace(id=1)

    asyncio.run(guardian._rollback_untrusted_overwrite(guild, object(), actor, "overwrite_update"))
    asyncio.run(guardian._rollback_untrusted_automod(guild, object(), actor, "automod_rule_update"))

    assert seen == [55, 55]


def test_gateway_preserves_bot_target_and_thresholds_bot_actor() -> None:
    original_calls: list[str] = []
    process_calls: list[str] = []

    async def original_role_create(_guild, _entry):
        original_calls.append("role_create")

    async def original_role_update(_guild, _entry, _actor):
        original_calls.append("role_update")

    async def original_member_update(_guild, _entry, _actor):
        original_calls.append("member_update")

    async def resolve_target(_guild, entry):
        return entry.target

    gateway = SimpleNamespace(
        _handle_role_create=original_role_create,
        _handle_dangerous_role_update=original_role_update,
        _handle_member_role_update=original_member_update,
        _resolve_target_member=resolve_target,
        _role_diff=lambda entry: (list(getattr(entry, "added_roles", [])), []),
        _target_label=lambda _entry: "@role",
        _member_target_label=lambda _entry: "bot-target",
    )

    async def process(_guild, **kwargs):
        process_calls.append(str(kwargs["action_key"]))
        return True

    anti_nuke = SimpleNamespace(
        _process_claimed_destructive_event=process,
        dangerous_permissions_added=lambda _before, _after: ["manage_roles"],
        get_antinuke_settings=lambda _guild_id: _async_value(_settings()),
        _safe_id_list=lambda value: list(value or []),
        role_has_dangerous_permissions=lambda _role: True,
    )
    setattr(gateway, lockdown._GATEWAY_PATCH_FLAG, False)  # noqa: SLF001

    assert lockdown._patch_gateway_surface(gateway, anti_nuke) is True  # noqa: SLF001

    guild = SimpleNamespace(id=1)
    bot_actor = SimpleNamespace(id=55, bot=True)
    role_entry = SimpleNamespace(user=bot_actor, before=object(), after=object())

    asyncio.run(gateway._handle_role_create(guild, role_entry))
    asyncio.run(gateway._handle_dangerous_role_update(guild, role_entry, bot_actor))

    target_bot = SimpleNamespace(id=77, bot=True)
    member_entry = SimpleNamespace(
        user=SimpleNamespace(id=88, bot=False),
        target=target_bot,
        added_roles=[SimpleNamespace(id=99)],
    )
    asyncio.run(
        gateway._handle_member_role_update(
            guild,
            member_entry,
            member_entry.user,
        )
    )

    assert original_calls == []
    assert process_calls == ["role_create", "role_update"]


async def _async_value(value):
    return value
