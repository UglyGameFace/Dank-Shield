from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from stoney_verify.services import vc_verification_permissions as vc_permissions
from stoney_verify.services.setup_permission_policy import vc_verification_overwrites


ROOT = Path(__file__).resolve().parents[1]


class FakeRole:
    def __init__(self, role_id: int, name: str, *, default: bool = False):
        self.id = role_id
        self.name = name
        self.mention = f"<@&{role_id}>"
        self._default = default

    def is_default(self) -> bool:
        return self._default

    def __hash__(self) -> int:
        return hash(self.id)


class FakeVoiceChannel:
    def __init__(self, overwrites=None):
        self.overwrites = dict(overwrites or {})
        self.permission_calls: list[tuple[object, object, str]] = []

    def overwrites_for(self, target):
        return self.overwrites.get(target, discord.PermissionOverwrite())

    async def set_permissions(self, target, *, overwrite=None, reason=None):
        self.permission_calls.append((target, overwrite, str(reason or "")))
        if overwrite is None:
            self.overwrites.pop(target, None)
        else:
            self.overwrites[target] = overwrite


def _value(overwrite: discord.PermissionOverwrite, key: str):
    return getattr(overwrite, key)


def test_unverified_baseline_denies_voice_and_video_until_session_grant():
    everyone = FakeRole(1, "@everyone", default=True)
    unverified = FakeRole(2, "Unverified")
    verified = FakeRole(3, "Verified")
    staff = FakeRole(4, "Staff")
    guild = SimpleNamespace(default_role=everyone, me=None)
    overwrites = vc_verification_overwrites(
        guild,
        staff_role=staff,
        control_role=None,
        unverified_role=unverified,
        verified_role=verified,
        resident_role=None,
    )
    waiting = overwrites[unverified]
    assert _value(waiting, "view_channel") is True
    assert _value(waiting, "connect") is False
    assert _value(waiting, "speak") is False
    assert _value(waiting, "stream") is False
    assert _value(waiting, "use_voice_activation") is False
    staff_base = overwrites[staff]
    assert _value(staff_base, "connect") is False
    assert _value(staff_base, "stream") is False


def test_runtime_session_grant_enables_video_and_voice():
    source = (
        ROOT / "stoney_verify/startup_guards/vc_per_guild_access_fix.py"
    ).read_text(encoding="utf-8")
    grant = source.split("async def _grant(", 1)[1].split(
        "async def _revoke(", 1
    )[0]
    assert "ow.view_channel = True" in grant
    assert "ow.connect = True" in grant
    assert "ow.speak = True" in grant
    assert "ow.stream = True" in grant
    assert "ow.use_voice_activation = True" in grant


def test_reconciler_fetches_configured_voice_channel_when_cache_misses(
    monkeypatch,
):
    monkeypatch.setattr(vc_permissions.discord, "Role", FakeRole)
    monkeypatch.setattr(vc_permissions.discord, "VoiceChannel", FakeVoiceChannel)

    everyone = FakeRole(1, "@everyone", default=True)
    channel = FakeVoiceChannel()
    fetched_ids: list[int] = []

    async def fetch_channel(channel_id: int):
        fetched_ids.append(channel_id)
        return channel

    guild = SimpleNamespace(
        id=123,
        default_role=everyone,
        me=None,
        get_channel=lambda _channel_id: None,
        fetch_channel=fetch_channel,
        get_role=lambda _role_id: None,
    )

    async def scenario():
        return await vc_permissions.reconcile_vc_verification_channel(
            guild,
            cfg={"vc_verify_channel_id": 99},
        )

    result = asyncio.run(scenario())

    assert result.ok is True
    assert fetched_ids == [99]
    assert everyone in channel.overwrites


def test_reconciler_removes_stale_role_grants_but_keeps_member_sessions(
    monkeypatch,
):
    monkeypatch.setattr(vc_permissions.discord, "Role", FakeRole)
    monkeypatch.setattr(vc_permissions.discord, "VoiceChannel", FakeVoiceChannel)

    everyone = FakeRole(1, "@everyone", default=True)
    stale_role = FakeRole(2, "Old Verify Staff")
    active_member = object()
    stale_grant = discord.PermissionOverwrite(
        view_channel=True,
        connect=True,
        speak=True,
        stream=True,
    )
    session_grant = discord.PermissionOverwrite(
        view_channel=True,
        connect=True,
        speak=True,
        stream=True,
    )
    channel = FakeVoiceChannel(
        {
            stale_role: stale_grant,
            active_member: session_grant,
        }
    )
    guild = SimpleNamespace(
        id=123,
        default_role=everyone,
        me=None,
        get_channel=lambda channel_id: channel if channel_id == 99 else None,
        get_role=lambda _role_id: None,
    )

    async def scenario():
        return await vc_permissions.reconcile_vc_verification_channel(
            guild,
            cfg={"vc_verify_channel_id": 99},
        )

    result = asyncio.run(scenario())

    assert result.ok is True
    assert stale_role not in channel.overwrites
    assert active_member in channel.overwrites
    assert any(
        target is stale_role and overwrite is None
        for target, overwrite, _reason in channel.permission_calls
    )
    assert not any(
        target is active_member and overwrite is None
        for target, overwrite, _reason in channel.permission_calls
    )
