"""Release gates for AntiNuke's routine-creation safety boundary.

Discord permission to create a resource is not independent proof of a raid.
Only explicit Strict Lockdown may classify creation alone as punitive.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_gateway_runtime as gateway
from stoney_verify import anti_nuke_guardian_runtime as guardian


class Resource:
    def __init__(self, resource_id: int) -> None:
        self.id = resource_id
        self.name = "ordinary-created-resource"
        self.deleted = False
        self.reason = None

    async def delete(self, *, reason=None) -> None:
        self.deleted = True
        self.reason = reason


class Guild:
    def __init__(self, resource: Resource) -> None:
        self.id = 94041
        self.owner_id = 999999
        self.resource = resource

    async def webhooks(self):
        return [self.resource]


def entry(guild: Guild, action: str, actor_id: int) -> SimpleNamespace:
    actor = SimpleNamespace(id=actor_id, roles=[], bot=False)
    return SimpleNamespace(
        id=100000 + actor_id,
        action=SimpleNamespace(name=action),
        guild=guild,
        user=actor,
        user_id=actor.id,
        target=guild.resource,
    )


def settings(*, mode: str = "contain", strict: bool = False) -> dict:
    configured = anti_nuke.normalize_antinuke_settings(
        {"antinuke_enabled": True, "antinuke_mode": mode}
    )
    configured["antinuke_strict_lockdown"] = strict
    return configured


def reset_state() -> None:
    anti_nuke._SEEN_AUDIT_ENTRY_IDS.clear()
    guardian._CREATION_ROLLBACK_DONE.clear()
    guardian._PANIC_EVENTS.clear()
    guardian._PANIC_UNTIL.clear()


@pytest.mark.parametrize("mode", ("contain", "alert"))
@pytest.mark.parametrize("action", ("channel_create", "webhook_create"))
def test_normal_or_alert_creation_never_rolls_back_or_contain(
    monkeypatch, mode: str, action: str
) -> None:
    reset_state()
    resource = Resource(92000)
    guild = Guild(resource)
    events = [entry(guild, action, user_id) for user_id in (7001, 7002, 7003, 7004)]

    async def get_settings(_guild_id: int):
        return settings(mode=mode)

    async def forbidden_process(*_args, **_kwargs):
        raise AssertionError("routine creation reached destructive engine")

    async def forbidden_post(*_args, **_kwargs):
        raise AssertionError("routine creation reached destructive incident")

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", get_settings)
    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", forbidden_process)
    monkeypatch.setattr(anti_nuke, "_post_incident", forbidden_post)

    for event in events:
        if action == "channel_create":
            asyncio.run(guardian._on_audit_log_entry_create(event))
        else:
            asyncio.run(gateway._on_audit_log_entry_create(event))
        assert anti_nuke._audit_entry_seen(event) is True

    assert resource.deleted is False
    assert not guardian._PANIC_EVENTS.get(guild.id)
    assert guild.id not in guardian._PANIC_UNTIL
    reset_state()


@pytest.mark.parametrize("action", ("channel_create", "webhook_create"))
def test_strict_lockdown_retains_creation_rollback_and_accounting(
    monkeypatch, action: str
) -> None:
    reset_state()
    resource = Resource(94000)
    guild = Guild(resource)
    event = entry(guild, action, 7050)
    counted: list[str] = []

    async def get_settings(_guild_id: int):
        return settings(strict=True)

    async def counted_process(_guild, **kwargs):
        counted.append(kwargs["action_key"])
        return True

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", get_settings)
    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", counted_process)

    if action == "channel_create":
        asyncio.run(guardian._on_audit_log_entry_create(event))
    else:
        asyncio.run(gateway._on_audit_log_entry_create(event))

    assert resource.deleted is True
    assert counted == [action]
    reset_state()


@pytest.mark.parametrize("strict, expected", ((False, False), (True, True)))
def test_native_fallback_cannot_bypass_creation_policy(monkeypatch, strict: bool, expected: bool) -> None:
    resource = Resource(95000)
    guild = Guild(resource)
    event = entry(guild, "channel_create", 7060)
    claimed: list[str] = []
    counted: list[str] = []

    async def get_settings(_guild_id: int):
        return settings(strict=strict)

    async def claim(_guild, audit_action, *, target_id=None, **_kwargs):
        claimed.append(audit_action)
        return event

    async def process(_guild, **kwargs):
        counted.append(kwargs["action_key"])
        return True

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", get_settings)
    monkeypatch.setattr(anti_nuke, "_claim_recent_audit_entry", claim)
    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", process)

    handled = asyncio.run(
        anti_nuke._handle_threshold_event(
            guild, audit_action="channel_create", action_key="channel_create",
            action_label="Channel creation", target_id=resource.id,
            target_label="#created", threshold_key="antinuke_channel_delete_threshold",
        )
    )
    assert handled is expected
    assert claimed == (["channel_create"] if expected else [])
    assert counted == (["channel_create"] if expected else [])


@pytest.mark.parametrize("action", ("channel_create", "webhook_create"))
def test_canonical_engine_rejects_normal_creation_even_if_called_directly(
    monkeypatch, action: str
) -> None:
    resource = Resource(96000)
    guild = Guild(resource)
    event = entry(guild, action, 7070)

    async def get_settings(_guild_id: int):
        return settings()

    def forbidden_record(*_args, **_kwargs):
        raise AssertionError("routine creation must not record destructive strike")

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", get_settings)
    monkeypatch.setattr(anti_nuke, "_record_action", forbidden_record)

    result = asyncio.run(
        anti_nuke._process_claimed_destructive_event(
            guild, entry=event, action_key=action,
            action_label="Resource creation", target_label="resource",
            threshold_key="antinuke_channel_delete_threshold",
        )
    )
    assert result is False


@pytest.mark.parametrize("action", ("channel_delete", "webhook_delete"))
def test_destructive_deletion_still_reaches_enforcement(monkeypatch, action: str) -> None:
    reset_state()
    guild = Guild(Resource(97000))
    event = entry(guild, action, 7080)
    observed: list[str] = []

    async def process(_guild, **kwargs):
        observed.append(kwargs["action_key"])
        return True

    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", process)
    asyncio.run(guardian._on_audit_log_entry_create(event))
    assert observed == [action]
    reset_state()


def test_creation_policy_fails_safe_for_incomplete_settings() -> None:
    assert anti_nuke._creation_containment_authorized(None) is False
    assert anti_nuke._creation_containment_authorized({}) is False
    assert anti_nuke._creation_containment_authorized({
        "antinuke_enabled": True, "antinuke_mode": "contain",
        "antinuke_strict_lockdown": "false"
    }) is False
    assert anti_nuke._creation_containment_authorized(settings(strict=True)) is True


@pytest.mark.parametrize("action", ("channel_create", "webhook_create"))
@pytest.mark.parametrize("missing", ("actor", "target"))
def test_strict_creation_rollback_requires_attributed_actor_and_target(
    monkeypatch, action: str, missing: str
) -> None:
    resource = Resource(98000)
    guild = Guild(resource)
    audit = entry(guild, action, 7090)
    if missing == "actor":
        audit.user = SimpleNamespace(id=0, roles=[], bot=False)
    else:
        audit.target = SimpleNamespace(id=None, name="unknown")

    async def get_settings(_guild_id: int):
        return settings(strict=True)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", get_settings)

    if action == "channel_create":
        result = asyncio.run(
            guardian._rollback_untrusted_creation(
                guild, audit, audit.user, action
            )
        )
    else:
        result = asyncio.run(
            gateway._rollback_untrusted_webhook_create(
                guild, audit, audit.user
            )
        )
    assert "missing" in result or "incomplete" in result
    assert resource.deleted is False
