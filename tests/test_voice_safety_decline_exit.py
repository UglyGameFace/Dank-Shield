"""Voice #517: opt out means exit, with a bounded cancel-and-reagree grace."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from stoney_verify.verification_new import decline_exit


class Role:
    def __init__(self, rid: int, pos: int, *, managed=False, default=False):
        self.id, self.position = rid, pos
        self.managed, self._default = managed, default

    def __ge__(self, other):
        return self.position >= other.position

    def __lt__(self, other):
        return self.position < other.position

    def is_default(self):
        return self._default


class Member:
    def __init__(self, gid, roles, *, mid=24):
        self.id = mid
        self.guild = gid
        self.bot = False
        self.roles = list(roles)
        self.edits = []
        self.kicks = []

    async def edit(self, *, roles, reason):
        self.roles = list(roles)
        self.edits.append(tuple(role.id for role in roles))

    async def kick(self, *, reason):
        self.kicks.append(reason)


class Guild:
    def __init__(self, *, owner_id=1, can_kick=True, can_roles=True):
        self.id = 71
        self.owner_id = owner_id
        self.default = Role(71, 0, default=True)
        self.verified = Role(101, 8)
        self.special = Role(102, 11)
        self.unverified = Role(103, 3)
        self.roles = [self.default, self.verified, self.special, self.unverified]
        self.member = Member(self, [self.default, self.verified, self.special])
        self.me = SimpleNamespace(
            top_role=Role(500, 100),
            guild_permissions=SimpleNamespace(
                manage_roles=can_roles, kick_members=can_kick,
            ),
        )

    def get_role(self, rid):
        return next((role for role in self.roles if role.id == rid), None)

    async def fetch_member(self, uid):
        assert uid == self.member.id
        return self.member


class Interaction:
    def __init__(self, guild):
        self.guild = guild
        self.user = guild.member
        self.client = object()
        self.message = SimpleNamespace(
            embeds=[SimpleNamespace(fields=[
                SimpleNamespace(name="🎙️ Dank Shield Voice Safety Agreement v1"),
            ])],
        )
        self.response = SimpleNamespace(
            defer=AsyncMock(), send_message=AsyncMock(),
        )
        self.followup = SimpleNamespace(send=AsyncMock())


@pytest.fixture(autouse=True)
def reset_pending():
    decline_exit._PENDING.clear()
    yield
    for entry in decline_exit._PENDING.values():
        if entry.task:
            entry.task.cancel()
    decline_exit._PENDING.clear()


@pytest.fixture
def environment(monkeypatch):
    from stoney_verify import community_voice_caption_runtime as runtime
    from stoney_verify import guild_config
    from stoney_verify import setup_engine
    from stoney_verify import voice_safety_consent
    from stoney_verify.setup_engine import loader

    monkeypatch.setattr(decline_exit.discord, "Guild", Guild)
    monkeypatch.setattr(decline_exit.discord, "Member", Member)
    monkeypatch.setattr(decline_exit.discord, "Role", Role)
    recorded = {"value": True, "updates": []}

    class Manager:
        async def set_voice_safety_consent(self, gid, uid, enabled, *, source="voice_safety_notice_v1"):
            recorded["value"] = enabled
            recorded["updates"].append((gid, uid, enabled, source))
            return enabled

    monkeypatch.setattr(runtime, "ensure_community_voice_caption_manager", lambda bot: Manager())
    monkeypatch.setattr(guild_config, "get_guild_config", AsyncMock(return_value={"voice_safety_enabled": True}))
    monkeypatch.setattr(loader, "snapshot_from_config", lambda gid, cfg: SimpleNamespace(unverified_role_id=103))
    async def fresh_agreement(*_args):
        return recorded["value"]
    monkeypatch.setattr(voice_safety_consent, "read_voice_safety_agreement", fresh_agreement)
    return recorded


@pytest.mark.asyncio
async def test_decline_revokes_before_stripping_and_kicks_after_grace(environment):
    guild = Guild()
    interaction = Interaction(guild)
    await decline_exit.decline_and_exit(interaction, from_verify=True)
    assert environment["updates"][0][:3] == (71, 24, False)
    assert guild.member.edits == [(103,)]
    assert [r.id for r in guild.member.roles] == [103]
    entry = decline_exit._PENDING[(71, 24)]
    assert entry.original_role_ids == (101, 102)
    assert entry.unverified_role_id == 103
    assert isinstance(interaction.followup.send.await_args.kwargs["view"], decline_exit.ExitGraceView)
    assert "10 seconds" in interaction.followup.send.await_args.args[0]
    entry.task.cancel()  # Test drives expiry explicitly; no wall-clock sleep.
    entry.expires = time.monotonic() - 1
    await decline_exit._kick_later(entry)
    assert len(guild.member.kicks) == 1
    assert not decline_exit.exit_pending(71, 24)
    assert environment["value"] is False


@pytest.mark.asyncio
async def test_member_can_reaccept_within_grace_and_original_roles_return(environment):
    guild = Guild()
    interaction = Interaction(guild)
    await decline_exit.decline_and_exit(interaction, from_verify=True)
    entry = decline_exit._PENDING[(71, 24)]
    button_view = interaction.followup.send.await_args.kwargs["view"]
    await button_view.children[0].callback(Interaction(guild))
    assert environment["updates"][-1][:3] == (71, 24, True)
    assert {r.id for r in guild.member.roles} == {101, 102}
    assert not decline_exit.exit_pending(71, 24)
    await decline_exit._kick_later(entry)
    assert not guild.member.kicks


@pytest.mark.asyncio
async def test_grace_rejects_other_members_and_expired_entries(environment):
    guild = Guild()
    await decline_exit.decline_and_exit(Interaction(guild), from_verify=True)
    entry = decline_exit._PENDING[(71, 24)]
    view = decline_exit.ExitGraceView(entry)
    stranger = Interaction(guild)
    stranger.user = Member(guild, [], mid=99)
    assert not await view.interaction_check(stranger)
    stranger.response.send_message.assert_awaited_once()
    entry.expires = time.monotonic() - 1
    own = Interaction(guild)
    await view.children[0].callback(own)
    own.response.send_message.assert_awaited_once()
    assert environment["value"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("opts", [{"owner_id":24}, {"can_kick":False}, {"can_roles":False}])
async def test_permission_blockers_do_not_claim_roles_or_exit(environment, opts):
    guild = Guild(**opts)
    i = Interaction(guild)
    await decline_exit.decline_and_exit(i, from_verify=False)
    assert environment["value"] is False
    assert guild.member.edits == []
    assert guild.member.kicks == []
    assert not decline_exit.exit_pending(71, 24)
    assert "Leave Server" in i.followup.send.await_args.args[0]


def test_new_verify_panel_only_offers_exit_when_disclosed():
    from stoney_verify.verification_new.basic_verify import BasicVerifyView
    from stoney_verify.commands_ext.public_voice_safety import VoiceSafetyConsentView

    ordinary = BasicVerifyView(voice_safety_agreement=False)
    disclosed = BasicVerifyView(voice_safety_agreement=True)
    assert [child.label for child in ordinary.children] == ["Verify"]
    assert {child.label for child in disclosed.children} == {
        "Agree & Verify", "Decline & Exit Server",
    }
    assert "Decline & Exit Server" in {child.label for child in VoiceSafetyConsentView().children}
    assert any(c.custom_id == decline_exit.DECLINE_CUSTOM_ID for c in disclosed.children)
