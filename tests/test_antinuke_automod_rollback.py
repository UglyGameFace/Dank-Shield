from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_guardian_runtime as guardian


class FakeRule:
    def __init__(self, rule_id: int = 9101) -> None:
        self.id = rule_id
        self.name = "rule"
        self.deleted = False
        self.edit_calls: list[dict] = []

    async def delete(self, *, reason=None) -> None:
        self.deleted = True
        self.delete_reason = reason

    async def edit(self, **kwargs):
        self.edit_calls.append(dict(kwargs))
        return self


class FakeGuild:
    def __init__(self) -> None:
        self.id = 8801
        self.owner_id = 999999
        self.created_rules: list[dict] = []

    async def create_automod_rule(self, **kwargs):
        self.created_rules.append(dict(kwargs))
        return SimpleNamespace(id=9991, **kwargs)


def _actor(user_id: int = 7001):
    return SimpleNamespace(id=user_id, roles=[], mention=f"<@{user_id}>")


def _settings(**patch):
    return anti_nuke.normalize_antinuke_settings(
        {"antinuke_enabled": True, "antinuke_mode": "contain", **patch}
    )


def test_untrusted_automod_create_is_deleted(monkeypatch) -> None:
    guild = FakeGuild()
    actor = _actor()
    rule = FakeRule()
    entry = SimpleNamespace(target=rule, before=SimpleNamespace())

    async def fake_settings(_guild_id):
        return _settings()

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)

    result = asyncio.run(
        guardian._rollback_untrusted_automod(
            guild,
            entry,
            actor,
            "automod_rule_create",
        )
    )

    assert rule.deleted is True
    assert "deleted unauthorized" in result
    assert "AntiNuke rollback" in rule.delete_reason


def test_untrusted_automod_update_restores_audit_before_state(monkeypatch) -> None:
    guild = FakeGuild()
    actor = _actor()
    rule = FakeRule()
    old_actions = [SimpleNamespace(type="block_message")]
    old_trigger = SimpleNamespace(type="keyword")
    entry = SimpleNamespace(
        target=rule,
        before=SimpleNamespace(
            name="Protect Links",
            event_type=SimpleNamespace(value=1),
            actions=old_actions,
            trigger=old_trigger,
            enabled=True,
            exempt_roles=[],
            exempt_channels=[],
        ),
    )

    async def fake_settings(_guild_id):
        return _settings()

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)

    result = asyncio.run(
        guardian._rollback_untrusted_automod(
            guild,
            entry,
            actor,
            "automod_rule_update",
        )
    )

    assert "restored unauthorized" in result
    assert len(rule.edit_calls) == 1
    restored = rule.edit_calls[0]
    assert restored["name"] == "Protect Links"
    assert restored["actions"] is old_actions
    assert restored["trigger"] is old_trigger
    assert restored["enabled"] is True
    assert "reason" in restored


def test_untrusted_automod_delete_recreates_rule_from_audit_state(monkeypatch) -> None:
    guild = FakeGuild()
    actor = _actor()
    old_actions = [SimpleNamespace(type="block_message")]
    old_trigger = SimpleNamespace(type="keyword")
    event_type = SimpleNamespace(value=1)
    entry = SimpleNamespace(
        target=SimpleNamespace(id=9201, name="Deleted Rule"),
        before=SimpleNamespace(
            name="Deleted Rule",
            event_type=event_type,
            trigger=old_trigger,
            actions=old_actions,
            enabled=True,
            exempt_roles=[],
            exempt_channels=[],
        ),
    )

    async def fake_settings(_guild_id):
        return _settings()

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)

    result = asyncio.run(
        guardian._rollback_untrusted_automod(
            guild,
            entry,
            actor,
            "automod_rule_delete",
        )
    )

    assert "recreated unauthorizedly deleted" in result
    assert len(guild.created_rules) == 1
    recreated = guild.created_rules[0]
    assert recreated["name"] == "Deleted Rule"
    assert recreated["event_type"] is event_type
    assert recreated["trigger"] is old_trigger
    assert recreated["actions"] is old_actions
    assert recreated["enabled"] is True


def test_delegated_operator_automod_change_is_not_rolled_back(monkeypatch) -> None:
    guild = FakeGuild()
    actor = _actor(7002)
    rule = FakeRule()
    entry = SimpleNamespace(target=rule, before=SimpleNamespace())

    async def fake_settings(_guild_id):
        return _settings(antinuke_trusted_user_ids=[actor.id])

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)

    result = asyncio.run(
        guardian._rollback_untrusted_automod(
            guild,
            entry,
            actor,
            "automod_rule_create",
        )
    )

    assert result == ""
    assert rule.deleted is False


def test_alert_mode_never_mutates_automod(monkeypatch) -> None:
    guild = FakeGuild()
    actor = _actor(7003)
    rule = FakeRule()
    entry = SimpleNamespace(target=rule, before=SimpleNamespace())

    async def fake_settings(_guild_id):
        return anti_nuke.normalize_antinuke_settings(
            {"antinuke_enabled": True, "antinuke_mode": "alert"}
        )

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)

    result = asyncio.run(
        guardian._rollback_untrusted_automod(
            guild,
            entry,
            actor,
            "automod_rule_create",
        )
    )

    assert result == ""
    assert rule.deleted is False
