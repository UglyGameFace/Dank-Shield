from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stoney_verify import contextual_permission_repair as repair


class FakeChannel:
    def __init__(self, channel_id: int, name: str) -> None:
        self.id = channel_id
        self.name = name
        self.mention = f"<#{channel_id}>"


class FakeGuild:
    def __init__(self, channels: list[FakeChannel]) -> None:
        self.id = 777
        self._channels = {channel.id: channel for channel in channels}

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)


def _audit(
    channel: FakeChannel,
    *,
    missing: list[str] | None = None,
    blockers: list[str] | None = None,
):
    missing = list(missing or [])
    blockers = list(blockers or [])
    return SimpleNamespace(
        target_id=channel.id,
        missing=missing,
        blockers=blockers,
        repairable_missing=list(missing) if not blockers else [],
    )


def test_same_screen_button_is_disabled_only_when_context_is_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repair.discord.abc, "GuildChannel", FakeChannel)
    channel = FakeChannel(10, "welcome")
    guild = FakeGuild([channel])

    monkeypatch.setattr(
        repair.core,
        "audit_target",
        lambda *_args, **_kwargs: _audit(channel),
    )
    healthy = repair.audit_context(
        guild,
        [repair.ContextualRepairTarget(10, "welcome", "Join channel")],
    )
    label, emoji, _style, disabled = repair.repair_button_state(healthy)
    assert (label, emoji, disabled) == ("Access Healthy", "✅", True)

    monkeypatch.setattr(
        repair.core,
        "audit_target",
        lambda *_args, **_kwargs: _audit(
            channel,
            missing=["view_channel", "send_messages"],
        ),
    )
    unhealthy = repair.audit_context(
        guild,
        [repair.ContextualRepairTarget(10, "welcome", "Join channel")],
    )
    label, emoji, _style, disabled = repair.repair_button_state(unhealthy)
    assert (label, emoji, disabled) == ("Fix Issues", "🛠️", False)


def test_repair_context_repairs_every_safe_target_then_reaudits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repair.discord.abc, "GuildChannel", FakeChannel)
    join = FakeChannel(10, "welcome")
    leave = FakeChannel(20, "join-leave-log")
    guild = FakeGuild([join, leave])
    repaired: set[int] = set()
    calls: list[tuple[int, str]] = []

    def fake_audit(_guild, channel, *, feature: str, mode: str):
        assert mode == "minimum"
        if channel.id in repaired:
            return _audit(channel)
        return _audit(channel, missing=["view_channel", "send_messages"])

    async def fake_apply(
        _guild,
        channel,
        *,
        actor_id: int,
        feature: str,
        mode: str,
        include_children: bool,
        clear_explicit_denies: bool,
    ):
        assert actor_id == 99
        assert mode == "minimum"
        assert include_children is False
        assert clear_explicit_denies is False
        calls.append((channel.id, feature))
        repaired.add(channel.id)
        return SimpleNamespace(
            ok=True,
            changed_targets=[f"{channel.mention} — repaired"],
            failed_targets=[],
            notes=[],
        )

    monkeypatch.setattr(repair.core, "audit_target", fake_audit)
    monkeypatch.setattr(repair.core, "apply_target_repair", fake_apply)

    async def run():
        return await repair.repair_context(
            guild,
            [
                repair.ContextualRepairTarget(10, "welcome", "Join channel"),
                repair.ContextualRepairTarget(20, "logs", "Leave log"),
            ],
            actor_id=99,
        )

    result = asyncio.run(run())
    assert result.ok is True
    assert result.remaining_issues == []
    assert calls == [(10, "welcome"), (20, "logs")]
    assert len(result.changed_targets) == 2
    assert "Access re-check passed" in result.summary()


def test_manual_issue_is_never_erased_by_bot_permission_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repair.discord.abc, "GuildChannel", FakeChannel)
    channel = FakeChannel(10, "private-log")
    guild = FakeGuild([channel])
    monkeypatch.setattr(
        repair.core,
        "audit_target",
        lambda *_args, **_kwargs: _audit(channel),
    )

    audit = repair.audit_context(
        guild,
        [repair.ContextualRepairTarget(10, "welcome", "Join channel")],
        manual_issues=[
            "Join audience: selected channel is private; choose a public Join channel."
        ],
    )
    assert audit.healthy is False
    assert repair.repair_button_state(audit)[0] == "Fix Issues"
    assert repair.remaining_issue_lines(audit) == [
        "Join audience: selected channel is private; choose a public Join channel."
    ]


def test_duplicate_target_feature_pairs_are_repaired_once() -> None:
    normalized = repair.normalize_targets(
        [
            repair.ContextualRepairTarget(10, "welcome", "Join"),
            repair.ContextualRepairTarget(10, "welcome", "Join duplicate"),
            repair.ContextualRepairTarget(10, "logs", "Same channel, different contract"),
        ]
    )
    assert [(item.channel_id, item.feature) for item in normalized] == [
        (10, "welcome"),
        (10, "logs"),
    ]
