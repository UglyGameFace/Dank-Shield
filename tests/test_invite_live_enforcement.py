from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stoney_verify import invite_policy_engine as policy


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.id = 555
        self.content = content
        self.guild = SimpleNamespace(id=42)
        self.channel = SimpleNamespace(id=77, parent=None, category=None)
        self.author = SimpleNamespace(id=99, bot=False)
        self.embeds: list[Any] = []
        self.components: list[Any] = []
        self.attachments: list[Any] = []
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True


async def _stats_ok(message: Any, decision: Any) -> Any:
    _ = message, decision
    return SimpleNamespace(queued=False, event_hash="test", blocked_count=1)


async def _modlog_ok(message: Any, decision: Any) -> None:
    _ = message, decision


def test_live_human_external_invite_is_deleted_when_invite_shield_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_values: list[bool] = []

    async def fake_load(guild: Any, *, refresh: bool = False):
        assert int(guild.id) == 42
        refresh_values.append(bool(refresh))
        return (
            {"automod_block_invites": True, "automod_block_links": False},
            {"allow_server_invites": True},
        )

    async def fake_classify(guild: Any, code: str) -> tuple[str, str]:
        assert int(guild.id) == 42
        assert code == "outside"
        return "external", "999"

    monkeypatch.setattr(policy, "load_invite_policy", fake_load)
    monkeypatch.setattr(policy, "_invite_code_belongs_to_guild", fake_classify)
    monkeypatch.setattr(policy.durable_invite_stats, "record_deleted_invite_decision", _stats_ok)
    monkeypatch.setattr(policy, "send_invite_decision_modlog", _modlog_ok)

    message = FakeMessage("join this https://discord.gg/outside")
    decision = asyncio.run(
        policy.enforce_live_invite_message(
            message,
            source="globals_live_enforcer",
            refresh_policy=True,
        )
    )

    assert decision is not None
    assert decision.codes == ["outside"]
    assert decision.external_codes == ["outside"]
    assert decision.rule_id == "invite_shield_external_or_blocked"
    assert decision.should_delete is True
    assert decision.delete_attempted is True
    assert decision.delete_succeeded is True
    assert message.deleted is True
    assert refresh_values == [True]


def test_live_same_server_invite_remains_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_load(guild: Any, *, refresh: bool = False):
        _ = guild, refresh
        return (
            {"automod_block_invites": True, "automod_block_links": False},
            {"allow_server_invites": True},
        )

    async def fake_classify(guild: Any, code: str) -> tuple[str, str]:
        _ = guild
        assert code == "ours"
        return "internal", "42"

    monkeypatch.setattr(policy, "load_invite_policy", fake_load)
    monkeypatch.setattr(policy, "_invite_code_belongs_to_guild", fake_classify)
    monkeypatch.setattr(policy.durable_invite_stats, "record_deleted_invite_decision", _stats_ok)
    monkeypatch.setattr(policy, "send_invite_decision_modlog", _modlog_ok)

    message = FakeMessage("our server: https://discord.gg/ours")
    decision = asyncio.run(policy.enforce_live_invite_message(message))

    assert decision is not None
    assert decision.internal_codes == ["ours"]
    assert decision.should_delete is False
    assert decision.rule_id == "allowed_invite"
    assert message.deleted is False


def test_globals_listener_uses_canonical_live_enforcement_boundary() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "stoney_verify"
        / "globals.py"
    ).read_text(encoding="utf-8")

    assert "from stoney_verify.invite_policy_engine import enforce_live_invite_message" in source
    assert 'source="globals_live_enforcer"' in source
    assert "refresh_policy=True" in source
    assert "delete_message_if_allowed(message, decision)" not in source
