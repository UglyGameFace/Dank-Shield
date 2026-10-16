"""Current guild trust must govern every irreversible known-hostile action.

A member's old hostile row cannot bypass the fresh trusted-role protection from
P0 #610 through a direct re-entry ban, fast path, or message deletion.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from stoney_verify import anti_nuke, anti_nuke_hostile_actor_runtime as hostile
from stoney_verify import anti_nuke_reentry_race_runtime as race


class Guild:
    def __init__(self, *, trusted: bool = True) -> None:
        self.id = 324
        self.owner_id = 999
        self.member = SimpleNamespace(
            id=55, guild=self, bot=False,
            roles=[SimpleNamespace(id=9001 if trusted else 8001)],
        )

    def get_member(self, user_id: int):
        return self.member if int(user_id) == self.member.id else None

    async def fetch_member(self, user_id: int):
        return self.get_member(user_id)


async def _settings(_guild_id: int, *, refresh: bool = False):
    return {
        "antinuke_enabled": True,
        "antinuke_mode": "contain",
        "antinuke_trusted_user_ids": [],
        "antinuke_trusted_role_ids": [9001],
    }


def _row():
    return {"active": True, "classification": "confirmed_destructive_actor"}


def _install_trust(monkeypatch):
    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", _settings)


def test_direct_reentry_hold_preserves_trusted_moderator(monkeypatch) -> None:
    guild = Guild(trusted=True)
    _install_trust(monkeypatch)
    incidents = []

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("trusted moderator must not be banned or kicked")

    async def incident(_anti_nuke, _guild, **kwargs):
        incidents.append((kwargs["title"], kwargs["response_label"]))

    monkeypatch.setattr(hostile, "_ban_identity", forbidden)
    monkeypatch.setattr(hostile, "_post_reputation_incident", incident)
    result = asyncio.run(hostile._enforce_reputation_member(
        guild.member, _row(), anti_nuke=anti_nuke, source="startup reconciliation"
    ))
    assert result is True
    assert len(incidents) == 1
    assert "Staff Review" in incidents[0][0]
    assert "trusted moderator role" in incidents[0][1]


def test_direct_reentry_still_bans_confirmed_untrusted(monkeypatch) -> None:
    guild = Guild(trusted=False)
    _install_trust(monkeypatch)
    banned = []
    incidents = []

    async def ban(_guild, user_id, **kwargs):
        banned.append(user_id)
        return True, "banned hostile identity"

    async def incident(_anti_nuke, _guild, **kwargs):
        incidents.append(kwargs["title"])

    monkeypatch.setattr(hostile, "_ban_identity", ban)
    monkeypatch.setattr(hostile, "_post_reputation_incident", incident)
    result = asyncio.run(hostile._enforce_reputation_member(
        guild.member, _row(), anti_nuke=anti_nuke, source="member join"
    ))
    assert result is True
    assert banned == [55]
    assert incidents == ["🛑 Known Hostile Identity Blocked"]


def test_message_backstop_does_not_delete_trusted_moderator_message(monkeypatch) -> None:
    guild = Guild(trusted=True)
    _install_trust(monkeypatch)
    monkeypatch.setattr(hostile, "_is_owner", lambda *_: False)
    monkeypatch.setattr(hostile, "_is_dank_bot", lambda *_: False)
    monkeypatch.setitem(hostile._MEMORY, (324, 55), _row())
    touched = []

    class Message:
        async def delete(self):
            touched.append("delete")

    message = Message()
    message.guild = guild
    message.author = guild.member

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("trusted staff message must not trigger hostile ban")

    monkeypatch.setattr(hostile, "_enforce_reputation_member", forbidden)
    try:
        asyncio.run(hostile._on_message_known_hostile(message))
        assert touched == []
    finally:
        hostile._MEMORY.pop((324, 55), None)


def test_fast_join_honors_current_trust_before_direct_ban(monkeypatch) -> None:
    guild = Guild(trusted=True)
    _install_trust(monkeypatch)
    monkeypatch.setattr(hostile, "_is_owner", lambda *_: False)
    monkeypatch.setattr(hostile, "_is_dank_bot", lambda *_: False)

    async def fast(_gid, _uid):
        return _row()

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("fast re-entry must not ban trusted staff")

    monkeypatch.setattr(race, "_fast_reputation", fast)
    monkeypatch.setattr(hostile, "_ban_identity", forbidden)
    asyncio.run(race._fast_member_join(guild.member))


def test_identity_link_cannot_create_new_hostile_record_for_trusted_staff(monkeypatch) -> None:
    guild = Guild(trusted=True)
    _install_trust(monkeypatch)
    monkeypatch.setattr(hostile, "_is_owner", lambda *_: False)
    monkeypatch.setattr(hostile, "_is_dank_bot", lambda *_: False)

    async def reputation(_gid, _uid, *, refresh=False):
        return None

    async def link(_member, _raidguard):
        return {"related_user_id": 88, "evidence": "hard identity link"}

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("trusted moderator must not receive inherited hostile row")

    monkeypatch.setattr(hostile, "get_actor_reputation", reputation)
    monkeypatch.setattr(hostile, "_confirmed_hostile_link", link)
    monkeypatch.setattr(hostile, "mark_confirmed_hostile", forbidden)
    asyncio.run(hostile._on_member_join(guild.member))


def test_fast_bot_add_guards_target_before_ban_and_integrations() -> None:
    source = Path(race.__file__).read_text(encoding="utf-8")
    fast = source.split("    async def fast_bot_add(", 1)[1].split(
        "    async def process(", 1
    )[0]
    assert fast.index("await hostile._automatic_containment_blocker") < fast.index(
        "_mark_hostile_readd"
    ) < fast.index("await hostile._ban_identity")
    assert fast.index("await hostile._automatic_containment_blocker") < fast.index(
        "_purge_pending_integrations"
    )


def test_regular_bot_add_guards_direct_ban_and_canonical_fallback() -> None:
    source = Path(hostile.__file__).read_text(encoding="utf-8")
    wrapped = source.split(
        "    async def wrapped(guild: discord.Guild, entry: Any, actor: Any) -> None:", 1
    )[1].split("    guardian._handle_bot_add = wrapped", 1)[0]
    assert wrapped.index("await _automatic_containment_blocker") < wrapped.index(
        "await _ban_identity"
    ) < wrapped.index("_canonical_contain(anti_nuke)")
