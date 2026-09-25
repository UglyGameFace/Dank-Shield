from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stoney_verify import invite_policy_engine as policy


class FakeMessage:
    def __init__(
        self,
        content: str,
        *,
        bot: bool = False,
        components: list[Any] | None = None,
        author_id: int = 99,
        application_id: int | None = None,
        interaction_metadata: Any = None,
        author_name: str = "sender",
    ) -> None:
        self.id = 555
        self.content = content
        self.guild = SimpleNamespace(id=42)
        self.channel = SimpleNamespace(id=77, parent=None, category=None)
        self.author = SimpleNamespace(
            id=author_id,
            bot=bot,
            name=author_name,
            display_name=author_name,
            global_name=author_name,
        )
        self.application_id = application_id
        self.application = None
        self.interaction_metadata = interaction_metadata
        self.embeds: list[Any] = []
        self.components: list[Any] = list(components or [])
        self.attachments: list[Any] = []
        self.poll = None
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


def test_live_bot_components_v2_external_invite_is_deleted(
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
        assert code == "appcard"
        return "external", "999"

    monkeypatch.setattr(policy, "load_invite_policy", fake_load)
    monkeypatch.setattr(policy, "_invite_code_belongs_to_guild", fake_classify)
    monkeypatch.setattr(policy.durable_invite_stats, "record_deleted_invite_decision", _stats_ok)
    monkeypatch.setattr(policy, "send_invite_decision_modlog", _modlog_ok)

    text_display = SimpleNamespace(
        content="Bumped server: https://discord.gg/appcard",
        url=None,
        children=[],
        accessory=None,
    )
    container = SimpleNamespace(
        content=None,
        url=None,
        children=[text_display],
        accessory=None,
    )
    message = FakeMessage("", bot=True, components=[container])
    decision = asyncio.run(
        policy.enforce_live_invite_message(
            message,
            source="globals_live_enforcer",
            refresh_policy=True,
        )
    )

    assert decision is not None
    assert decision.codes == ["appcard"]
    assert decision.rule_id == "invite_shield_external_or_blocked"
    assert decision.should_delete is True
    assert decision.delete_succeeded is True
    assert message.deleted is True


def _protected_onebump_settings() -> dict[str, Any]:
    return {
        "allow_server_invites": True,
        policy.INVITE_PROTECTED_POSTER_RULE_KEY: True,
        policy.INVITE_TARGET_ALL_BOTS_KEY: True,
        policy.INVITE_TARGET_CHANNEL_IDS_KEY: ["77"],
    }


def test_content_redacted_onebump_ad_is_deleted_only_when_explicitly_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_load(guild: Any, *, refresh: bool = False):
        assert int(guild.id) == 42
        assert refresh is True
        return (
            {"automod_block_invites": True, "automod_block_links": False},
            _protected_onebump_settings(),
        )

    monkeypatch.setattr(policy, "load_invite_policy", fake_load)
    monkeypatch.setattr(policy.durable_invite_stats, "record_deleted_invite_decision", _stats_ok)
    monkeypatch.setattr(policy, "send_invite_decision_modlog", _modlog_ok)

    message = FakeMessage(
        "",
        bot=True,
        author_id=1028956609382199346,
        application_id=1028956609382199346,
        author_name="OneBump",
    )
    decision = asyncio.run(
        policy.enforce_live_invite_message(
            message,
            source="globals_live_enforcer",
            refresh_policy=True,
        )
    )

    assert decision is not None
    assert decision.codes == []
    assert decision.content_unavailable is True
    assert decision.trusted_advertiser == "OneBump"
    assert decision.rule_id == "protected_contentless_known_advertiser"
    assert decision.feature_owner == "Protected Bot/Channel Invite Rule"
    assert decision.should_delete is True
    assert decision.delete_succeeded is True
    assert message.deleted is True


def test_content_redacted_onebump_is_not_guess_deleted_without_explicit_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_load(guild: Any, *, refresh: bool = False):
        _ = guild, refresh
        return (
            {"automod_block_invites": True, "automod_block_links": False},
            {
                "allow_server_invites": True,
                policy.INVITE_PROTECTED_POSTER_RULE_KEY: False,
                policy.INVITE_TARGET_ALL_BOTS_KEY: False,
                policy.INVITE_TARGET_CHANNEL_IDS_KEY: [],
            },
        )

    monkeypatch.setattr(policy, "load_invite_policy", fake_load)

    message = FakeMessage(
        "",
        bot=True,
        author_id=1028956609382199346,
        application_id=1028956609382199346,
        author_name="OneBump",
    )
    decision = asyncio.run(
        policy.enforce_live_invite_message(
            message,
            source="globals_live_enforcer",
            refresh_policy=True,
        )
    )

    assert decision is not None
    assert decision.rule_id == "known_advertiser_content_unavailable_not_targeted"
    assert decision.action == "log_only"
    assert decision.should_delete is False
    assert message.deleted is False


def test_spoofed_onebump_display_name_cannot_trigger_contentless_delete() -> None:
    message = FakeMessage(
        "",
        bot=True,
        author_id=888888888888888888,
        application_id=777777777777777777,
        author_name="OneBump",
    )

    assert policy.known_advertising_integration_name(message) == ""
    assert policy.is_contentless_trusted_advertiser_candidate(message) is False
    assert asyncio.run(policy.enforce_live_invite_message(message)) is None
    assert message.deleted is False


def test_contentless_onebump_interaction_receipt_is_not_treated_as_ad() -> None:
    message = FakeMessage(
        "",
        bot=True,
        author_id=1028956609382199346,
        application_id=1028956609382199346,
        interaction_metadata=SimpleNamespace(id=123),
        author_name="OneBump",
    )

    assert policy.known_advertising_integration_name(message) == "OneBump"
    assert policy.is_contentless_trusted_advertiser_candidate(message) is False
    assert asyncio.run(policy.enforce_live_invite_message(message)) is None
    assert message.deleted is False


def test_onebump_application_owned_webhook_identity_is_enough_for_exact_match() -> None:
    message = FakeMessage(
        "",
        bot=True,
        author_id=555555555555555555,
        application_id=1028956609382199346,
        author_name="not-a-trusted-name",
    )

    assert policy.known_advertising_integration_name(message) == "OneBump"
    assert policy.is_contentless_trusted_advertiser_candidate(message) is True


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
