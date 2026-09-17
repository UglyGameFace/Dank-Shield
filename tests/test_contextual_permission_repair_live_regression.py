from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import contextual_permission_repair as contextual
from stoney_verify import permission_repair_core as core


class _FakeTarget:
    def __init__(self, channel_id: int, *, manage_roles: bool, fresh: bool = False) -> None:
        self.id = channel_id
        self.name = f"target-{channel_id}"
        self.mention = f"<#{channel_id}>"
        self.manage_roles = manage_roles
        self.fresh = fresh

    def permissions_for(self, _member):
        return SimpleNamespace(
            view_channel=False,
            send_messages=False,
            embed_links=False,
            attach_files=False,
            read_message_history=False,
            manage_roles=self.manage_roles,
            manage_channels=False,
        )

    def overwrites_for(self, _member):
        return discord.PermissionOverwrite()


class _FakeGuild:
    def __init__(self, stale: _FakeTarget, fresh: _FakeTarget | None = None) -> None:
        self.id = 777
        self._stale = stale
        self._fresh = fresh or stale
        self.fetches: list[int] = []
        self.default_role = object()

    def get_channel(self, channel_id: int):
        return self._stale if channel_id == self._stale.id else None

    async def fetch_channel(self, channel_id: int):
        self.fetches.append(channel_id)
        return self._fresh if channel_id == self._fresh.id else None


def _audit_for(channel: _FakeTarget):
    missing = [] if channel.fresh else ["send_messages", "embed_links"]
    return SimpleNamespace(
        target_id=channel.id,
        missing=missing,
        blockers=[],
        repairable_missing=list(missing),
    )


def test_overwrite_repair_requires_manage_roles_not_manage_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _FakeTarget(10, manage_roles=True)
    guild = _FakeGuild(target)
    member = SimpleNamespace(top_role=object())
    monkeypatch.setattr(core, "_target_supported", lambda _target: True)
    monkeypatch.setattr(core, "_bot_member", lambda _guild: member)

    report = core.audit_target(guild, target, feature="logs", mode="minimum")

    assert report.missing
    assert report.blockers == []


def test_missing_manage_roles_is_the_manual_overwrite_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _FakeTarget(10, manage_roles=False)
    guild = _FakeGuild(target)
    member = SimpleNamespace(top_role=object())
    monkeypatch.setattr(core, "_target_supported", lambda _target: True)
    monkeypatch.setattr(core, "_bot_member", lambda _guild: member)

    report = core.audit_target(guild, target, feature="logs", mode="minimum")

    assert report.blockers
    blocker = " ".join(report.blockers)
    assert "Manage Roles" in blocker
    assert "does not have Manage Channels" not in blocker


def test_contextual_repair_reaudits_fresh_discord_channel_after_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = _FakeTarget(10, manage_roles=True, fresh=False)
    fresh = _FakeTarget(10, manage_roles=True, fresh=True)
    guild = _FakeGuild(stale, fresh)

    monkeypatch.setattr(contextual.discord.abc, "GuildChannel", _FakeTarget)
    monkeypatch.setattr(contextual.core, "audit_target", lambda _guild, channel, **_kwargs: _audit_for(channel))

    async def fake_apply(*_args, **_kwargs):
        return SimpleNamespace(
            ok=True,
            changed_targets=["<#10> — send_messages, embed_links"],
            failed_targets=[],
            notes=[],
        )

    monkeypatch.setattr(contextual.core, "apply_target_repair", fake_apply)

    async def run():
        return await contextual.repair_context(
            guild,
            [contextual.ContextualRepairTarget(10, "logs", "Log channel")],
            actor_id=99,
        )

    result = asyncio.run(run())

    assert guild.fetches == [10]
    assert result.ok is True
    assert result.remaining_issues == []
    assert result.after is not None and result.after.healthy is True
    assert "Access re-check passed" in result.summary()


def test_fresh_reaudit_falls_back_to_cache_when_http_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = _FakeTarget(10, manage_roles=True, fresh=False)
    guild = _FakeGuild(stale)

    async def broken_fetch(_channel_id: int):
        raise RuntimeError("temporary Discord fetch failure")

    guild.fetch_channel = broken_fetch  # type: ignore[method-assign]
    monkeypatch.setattr(contextual.discord.abc, "GuildChannel", _FakeTarget)
    monkeypatch.setattr(contextual.core, "audit_target", lambda _guild, channel, **_kwargs: _audit_for(channel))

    audit = asyncio.run(
        contextual._audit_context_fresh(
            guild,
            [contextual.ContextualRepairTarget(10, "logs", "Log channel")],
        )
    )

    assert audit.healthy is False
    assert contextual.remaining_issue_lines(audit) == [
        "Log channel: still missing send_messages, embed_links"
    ]
