from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import discord

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
    source = (ROOT / "stoney_verify/startup_guards/vc_per_guild_access_fix.py").read_text(encoding="utf-8")
    grant = source.split("async def _grant(", 1)[1].split("async def _revoke(", 1)[0]
    assert "ow.view_channel = True" in grant
    assert "ow.connect = True" in grant
    assert "ow.speak = True" in grant
    assert "ow.stream = True" in grant
    assert "ow.use_voice_activation = True" in grant
