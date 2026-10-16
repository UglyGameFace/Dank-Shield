from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_gateway_runtime as gateway
from stoney_verify import anti_nuke_guardian_runtime as guardian


def _permissions(**overrides):
    values = {name: False for name in anti_nuke.DANGEROUS_PERMISSION_NAMES}
    values.update(overrides)
    return SimpleNamespace(**values)


def _entry(action_name: str, *, before=None, after=None):
    actor = SimpleNamespace(id=400, roles=[], mention="<@400>")
    return SimpleNamespace(
        id=900,
        action=SimpleNamespace(name=action_name),
        guild=SimpleNamespace(id=100, owner_id=999),
        user=actor,
        user_id=actor.id,
        target=SimpleNamespace(id=500, name="target"),
        before=before or SimpleNamespace(),
        after=after or SimpleNamespace(),
    )


def _reset() -> None:
    anti_nuke._SEEN_AUDIT_ENTRY_IDS.clear()
    anti_nuke._ACTION_WINDOWS.clear()
    anti_nuke._TRIGGER_COOLDOWNS.clear()
    guardian._PANIC_EVENTS.clear()
    guardian._PANIC_UNTIL.clear()


def test_dangerous_role_permission_addition_uses_gateway_handler(monkeypatch) -> None:
    _reset()
    entry = _entry(
        "role_update",
        before=SimpleNamespace(permissions=_permissions(administrator=False)),
        after=SimpleNamespace(permissions=_permissions(administrator=True)),
    )
    calls: list[int] = []

    async def fake_handler(_guild, found_entry, _actor):
        calls.append(int(found_entry.id))

    monkeypatch.setattr(gateway, "_handle_dangerous_role_update", fake_handler)
    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert calls == [900]
    assert anti_nuke._audit_entry_seen(entry) is True
    _reset()


def test_benign_role_update_stays_with_guardian(monkeypatch) -> None:
    _reset()
    entry = _entry(
        "role_update",
        before=SimpleNamespace(
            permissions=_permissions(),
            name="old",
        ),
        after=SimpleNamespace(
            permissions=_permissions(),
            name="new",
        ),
    )
    delegated: list[int] = []

    async def fake_guardian(found_entry):
        delegated.append(int(found_entry.id))

    monkeypatch.setattr(guardian, "_on_audit_log_entry_create", fake_guardian)
    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert delegated == [900]
    assert anti_nuke._audit_entry_seen(entry) is False
    _reset()


def test_sensitive_member_role_grant_uses_gateway_handler(monkeypatch) -> None:
    _reset()
    dangerous_role = SimpleNamespace(
        id=700,
        name="Admin",
        permissions=_permissions(administrator=True),
    )
    entry = _entry(
        "member_role_update",
        before=SimpleNamespace(roles=[]),
        after=SimpleNamespace(roles=[dangerous_role]),
    )
    calls: list[int] = []

    async def fake_handler(_guild, found_entry, _actor):
        calls.append(int(found_entry.id))

    monkeypatch.setattr(gateway, "_handle_member_role_update", fake_handler)
    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert calls == [900]
    assert anti_nuke._audit_entry_seen(entry) is True
    _reset()


def test_role_removal_also_uses_claimed_gateway_evidence(monkeypatch) -> None:
    _reset()
    role = SimpleNamespace(id=701, name="Moderator", permissions=_permissions())
    entry = _entry(
        "member_role_update",
        before=SimpleNamespace(roles=[role]),
        after=SimpleNamespace(roles=[]),
    )
    calls: list[int] = []

    async def fake_handler(_guild, found_entry, _actor):
        calls.append(int(found_entry.id))

    monkeypatch.setattr(gateway, "_handle_member_role_update", fake_handler)
    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert calls == [900]
    assert anti_nuke._audit_entry_seen(entry) is True
    _reset()


def test_timeout_extension_uses_gateway_handler(monkeypatch) -> None:
    _reset()
    now = datetime.now(timezone.utc)
    entry = _entry(
        "member_update",
        before=SimpleNamespace(timed_out_until=None),
        after=SimpleNamespace(timed_out_until=now + timedelta(minutes=30)),
    )
    calls: list[int] = []

    async def fake_handler(_guild, found_entry, _actor):
        calls.append(int(found_entry.id))

    monkeypatch.setattr(gateway, "_handle_member_timeout", fake_handler)
    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert calls == [900]
    assert anti_nuke._audit_entry_seen(entry) is True
    _reset()


def test_unresolved_gateway_actor_is_not_consumed(monkeypatch) -> None:
    _reset()
    entry = _entry(
        "role_update",
        before=SimpleNamespace(permissions=_permissions(administrator=False)),
        after=SimpleNamespace(permissions=_permissions(administrator=True)),
    )

    async def unresolved(_guild, _entry):
        return None

    monkeypatch.setattr(guardian, "_resolve_actor", unresolved)
    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert anti_nuke._audit_entry_seen(entry) is False
    _reset()


def test_gateway_trusted_operator_uses_canonical_threshold_not_first_strike(
    monkeypatch,
) -> None:
    _reset()
    guild = SimpleNamespace(id=100, owner_id=999)
    actor = SimpleNamespace(id=400, bot=False, roles=[])
    entry = SimpleNamespace(user=actor)
    recorded: list[tuple[str, object]] = []
    punished: list[int] = []
    settings = {
        "antinuke_enabled": True, "antinuke_mode": "contain",
        "antinuke_trusted_user_ids": [400], "antinuke_trusted_role_ids": [],
        "antinuke_strict_lockdown": False,
    }

    async def canonical(_guild, **kwargs):
        recorded.append((str(kwargs["action_key"]), kwargs.get("threshold_override")))
        return True

    async def direct(_guild, member, *, reason: str):
        punished.append(int(member.id))
        return ["kicked"], []

    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", canonical)
    monkeypatch.setattr(gateway, "_contain_actor", direct)
    monkeypatch.setattr(
        anti_nuke, "_actor_is_configured_trusted",
        lambda subject, config: int(subject.id) in config.get("antinuke_trusted_user_ids", []),
    )

    removed, blocked, counted = asyncio.run(
        gateway._contain_or_count_role_mutation(
            guild, entry, actor, settings=settings, action_key="role_update",
            action_label="Permission escalation", target_label="Admin",
            reason="security-sensitive change",
        )
    )
    assert (removed, blocked, counted) == ([], [], True)
    assert recorded == [("role_update", None)]
    assert punished == []

    settings["antinuke_strict_lockdown"] = True
    removed, blocked, counted = asyncio.run(
        gateway._contain_or_count_role_mutation(
            guild, entry, actor, settings=settings, action_key="role_update",
            action_label="Permission escalation", target_label="Admin",
            reason="security-sensitive change",
        )
    )
    assert (removed, blocked, counted) == (["kicked"], [], False)
    assert punished == [400]

    settings["antinuke_strict_lockdown"] = False
    settings["antinuke_trusted_user_ids"] = []
    removed, blocked, counted = asyncio.run(
        gateway._contain_or_count_role_mutation(
            guild, entry, actor, settings=settings, action_key="role_update",
            action_label="Permission escalation", target_label="Admin",
            reason="security-sensitive change",
        )
    )
    assert (removed, blocked, counted) == (["kicked"], [], False)
    assert punished == [400, 400]
    assert recorded == [("role_update", None)]
    _reset()


def test_three_gateway_role_authority_paths_count_trusted_operator(
    monkeypatch,
) -> None:
    _reset()
    guild = SimpleNamespace(id=100, owner_id=999, me=None)
    actor = SimpleNamespace(id=400, bot=False, roles=[])
    role = SimpleNamespace(id=700, name="Admin", managed=False)
    observed: list[str] = []

    async def settings(_guild_id: int):
        return {
            "antinuke_enabled": True, "antinuke_mode": "contain",
            "antinuke_protect_role_escalation": True,
            "antinuke_trusted_user_ids": [400],
            "antinuke_trusted_role_ids": [],
            "antinuke_strict_lockdown": False,
        }

    async def canonical(_guild, **kwargs):
        observed.append(str(kwargs["action_key"]))
        return True

    async def unexpected_containment(*_args, **_kwargs):
        raise AssertionError("trusted staff should not be directly punished")

    async def noop_incident(*_args, **_kwargs):
        pass

    async def resolve_target(_guild, _entry):
        return SimpleNamespace(id=800)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", canonical)
    monkeypatch.setattr(gateway, "_contain_actor", unexpected_containment)
    monkeypatch.setattr(anti_nuke, "_post_incident", noop_incident)
    monkeypatch.setattr(anti_nuke, "role_has_dangerous_permissions", lambda _role: True)
    monkeypatch.setattr(
        anti_nuke, "dangerous_permissions_added",
        lambda _before, _after: ["administrator"],
    )
    monkeypatch.setattr(
        anti_nuke, "_actor_is_configured_trusted",
        lambda subject, _config: int(subject.id) == 400,
    )
    monkeypatch.setattr(guardian, "_panic_state", lambda *_args, **_kwargs: (False, False, []))
    monkeypatch.setattr(gateway, "_resolve_target_member", resolve_target)
    monkeypatch.setattr(gateway, "_role_diff", lambda _entry: ([role], []))
    monkeypatch.setattr(anti_nuke, "_member_is_manageable_by_bot", lambda *_args: False)

    entry = SimpleNamespace(
        user=actor, target=role,
        before=SimpleNamespace(permissions=SimpleNamespace(administrator=False)),
        after=SimpleNamespace(permissions=SimpleNamespace(administrator=True)),
    )
    asyncio.run(gateway._handle_role_create(guild, entry))
    asyncio.run(gateway._handle_dangerous_role_update(guild, entry, actor))
    asyncio.run(gateway._handle_member_role_update(guild, entry, actor))

    assert observed == ["role_create", "role_update", "role_update"]
    _reset()
