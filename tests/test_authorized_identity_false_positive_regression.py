from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_hostile_actor_runtime as hostile
from stoney_verify import anti_nuke_product_policy_runtime as policy
from stoney_verify import anti_nuke_reentry_race_runtime as reentry


def _invite_false_positive(*, user_id: int = 55) -> dict[str, object]:
    return {
        "guild_id": 10,
        "user_id": user_id,
        "active": True,
        "classification": "confirmed_destructive_actor",
        "source": "antinuke",
        "last_reason": "Dank Shield AntiNuke containment: Invite creation",
        "is_bot": False,
    }


def _real_hostile(*, user_id: int = 55) -> dict[str, object]:
    return {
        "guild_id": 10,
        "user_id": user_id,
        "active": True,
        "classification": "confirmed_destructive_actor",
        "source": "antinuke",
        "last_reason": "Dank Shield AntiNuke containment: Channel deletion",
        "is_bot": False,
    }


def _reset(guild_id: int = 10, user_id: int = 55) -> None:
    hostile._MEMORY.pop((guild_id, user_id), None)  # noqa: SLF001
    hostile._NEGATIVE_CACHE.pop((guild_id, user_id), None)  # noqa: SLF001
    policy._LEGACY_REPUTATION_CLEARING.discard((guild_id, user_id))  # noqa: SLF001


def test_invite_false_positive_is_masked_before_durable_clear(monkeypatch) -> None:
    _reset()
    clears: list[tuple[int, int, str]] = []

    async def clear(guild_id: int, user_id: int, *, cleared_by=None, reason: str = ""):
        _ = cleared_by
        clears.append((guild_id, user_id, reason))
        return {"active": False}

    monkeypatch.setattr(hostile, "clear_hostile_reputation", clear)

    async def scenario() -> None:
        result = await policy.sanitize_legacy_false_positive_reputation(
            10,
            55,
            _invite_false_positive(),
        )
        assert result is not None
        assert result["active"] is False
        assert hostile._MEMORY[(10, 55)]["active"] is False  # noqa: SLF001
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert len(clears) == 1
    assert clears[0][0:2] == (10, 55)
    assert "invite-creation false positive" in clears[0][2]
    _reset()


def test_real_destructive_reputation_is_never_cleared(monkeypatch) -> None:
    _reset()
    clears: list[int] = []

    async def clear(*_args, **_kwargs):
        clears.append(1)
        return None

    monkeypatch.setattr(hostile, "clear_hostile_reputation", clear)

    result = asyncio.run(
        policy.sanitize_legacy_false_positive_reputation(
            10,
            55,
            _real_hostile(),
        )
    )

    assert result is not None
    assert result["active"] is True
    assert clears == []
    _reset()


def test_fast_reentry_cannot_reban_legacy_invite_false_positive(monkeypatch) -> None:
    _reset()
    hostile._MEMORY[(10, 55)] = _invite_false_positive()  # noqa: SLF001
    clears: list[int] = []
    bans: list[int] = []

    async def clear(guild_id: int, user_id: int, *, cleared_by=None, reason: str = ""):
        _ = guild_id, cleared_by, reason
        clears.append(user_id)
        return {"active": False}

    async def ban_identity(_guild, user_id: int, *, member=None, reason: str):
        _ = member, reason
        bans.append(user_id)
        return True, "banned"

    monkeypatch.setattr(hostile, "clear_hostile_reputation", clear)
    monkeypatch.setattr(hostile, "_ban_identity", ban_identity)
    monkeypatch.setattr(hostile, "_is_owner", lambda _guild, _uid: False)
    monkeypatch.setattr(hostile, "_is_dank_bot", lambda _anti_nuke, _uid: False)

    guild = SimpleNamespace(id=10, owner_id=999)
    member = SimpleNamespace(id=55, guild=guild)

    async def scenario() -> None:
        await reentry._fast_member_join(member)  # noqa: SLF001
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert bans == []
    assert clears == [55]
    assert hostile._MEMORY[(10, 55)]["active"] is False  # noqa: SLF001
    _reset()


def test_fast_reentry_still_blocks_real_hostile_identity(monkeypatch) -> None:
    _reset()
    hostile._MEMORY[(10, 55)] = _real_hostile()  # noqa: SLF001
    bans: list[int] = []
    incidents: list[str] = []

    async def settings(_guild_id: int):
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}

    async def ban_identity(_guild, user_id: int, *, member=None, reason: str):
        _ = member, reason
        bans.append(user_id)
        return True, "banned hostile identity"

    async def incident(_anti_nuke, _guild, **kwargs):
        incidents.append(str(kwargs["title"]))

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(hostile, "_ban_identity", ban_identity)
    monkeypatch.setattr(hostile, "_post_reputation_incident", incident)
    monkeypatch.setattr(hostile, "_is_owner", lambda _guild, _uid: False)
    monkeypatch.setattr(hostile, "_is_dank_bot", lambda _anti_nuke, _uid: False)

    guild = SimpleNamespace(id=10, owner_id=999)
    member = SimpleNamespace(id=55, guild=guild)

    asyncio.run(reentry._fast_member_join(member))  # noqa: SLF001

    assert bans == [55]
    assert incidents == ["🛑 Known Hostile Re-entry Fast Block"]
    _reset()
