from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_hostile_actor_runtime as hostile
from stoney_verify import anti_nuke_reentry_race_runtime as reentry


BENIGN_ACTIONS = {
    "invite_create",
    "invite_update",
    "emoji_create",
    "emoji_update",
    "sticker_create",
    "sticker_update",
    "scheduled_event_create",
    "scheduled_event_update",
}

LEGACY_REASONS = {
    "Dank Shield AntiNuke containment: Invite creation",
    "Dank Shield AntiNuke containment: Invite mutation",
    "Dank Shield AntiNuke containment: Emoji creation",
    "Dank Shield AntiNuke containment: Emoji mutation",
    "Dank Shield AntiNuke containment: Sticker creation",
    "Dank Shield AntiNuke containment: Sticker mutation",
    "Dank Shield AntiNuke containment: Scheduled-event creation",
    "Dank Shield AntiNuke containment: Scheduled-event mutation",
}


def _legacy_row(reason: str, *, user_id: int = 55) -> dict[str, object]:
    return {
        "guild_id": 10,
        "user_id": user_id,
        "active": True,
        "classification": "confirmed_destructive_actor",
        "source": "antinuke",
        "last_reason": reason,
        "is_bot": False,
    }


def _real_hostile(*, user_id: int = 55) -> dict[str, object]:
    return _legacy_row(
        "Dank Shield AntiNuke containment: Channel deletion",
        user_id=user_id,
    )


def _reset(guild_id: int = 10, user_id: int = 55) -> None:
    hostile._MEMORY.pop((guild_id, user_id), None)  # noqa: SLF001
    hostile._NEGATIVE_CACHE.pop((guild_id, user_id), None)  # noqa: SLF001
    reentry._LEGACY_REPUTATION_CLEARING.discard((guild_id, user_id))  # noqa: SLF001


def test_final_guardian_boundary_removes_all_benign_member_actions(monkeypatch) -> None:
    async def base_bot_add(*_args, **_kwargs):
        return None

    async def base_process(*_args, **_kwargs):
        return None

    actions = {
        name: (name, "threshold", name, None)
        for name in BENIGN_ACTIONS | {"channel_delete"}
    }
    fake_guardian = SimpleNamespace(
        _ACTIONS=actions,
        _PANIC_WEIGHTS={name: 2 for name in actions},
        _PANIC_ACTIONS=frozenset(actions),
        _PANIC_SEVERE_ACTIONS=frozenset(BENIGN_ACTIONS | {"channel_delete"}),
        _handle_bot_add=base_bot_add,
        _process=base_process,
    )
    monkeypatch.setattr(reentry, "guardian", fake_guardian)

    assert reentry._patch_guardian() is True  # noqa: SLF001

    for action_name in BENIGN_ACTIONS:
        assert action_name not in fake_guardian._ACTIONS
        assert action_name not in fake_guardian._PANIC_WEIGHTS
        assert action_name not in fake_guardian._PANIC_ACTIONS
        assert action_name not in fake_guardian._PANIC_SEVERE_ACTIONS

    assert "channel_delete" in fake_guardian._ACTIONS
    assert "channel_delete" in fake_guardian._PANIC_ACTIONS


def test_every_exact_legacy_benign_false_positive_is_masked_and_cleared(monkeypatch) -> None:
    clears: list[tuple[int, int, str]] = []

    async def clear(guild_id: int, user_id: int, *, cleared_by=None, reason: str = ""):
        _ = cleared_by
        clears.append((guild_id, user_id, reason))
        return {"active": False}

    monkeypatch.setattr(hostile, "clear_hostile_reputation", clear)

    async def scenario() -> None:
        for index, reason in enumerate(sorted(LEGACY_REASONS), start=1):
            user_id = 100 + index
            _reset(user_id=user_id)
            result = await reentry.sanitize_legacy_false_positive_reputation(
                10,
                user_id,
                _legacy_row(reason, user_id=user_id),
            )
            assert result is not None
            assert result["active"] is False
            assert hostile._MEMORY[(10, user_id)]["active"] is False  # noqa: SLF001
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert len(clears) == len(LEGACY_REASONS)
    assert all("benign-action false positive" in reason for _g, _u, reason in clears)
    for index in range(1, len(LEGACY_REASONS) + 1):
        _reset(user_id=100 + index)


def test_real_destructive_reputation_is_never_cleared(monkeypatch) -> None:
    _reset()
    clears: list[int] = []

    async def clear(*_args, **_kwargs):
        clears.append(1)
        return None

    monkeypatch.setattr(hostile, "clear_hostile_reputation", clear)

    result = asyncio.run(
        reentry.sanitize_legacy_false_positive_reputation(10, 55, _real_hostile())
    )

    assert result is not None
    assert result["active"] is True
    assert clears == []
    _reset()


def test_fast_reentry_cannot_reban_benign_action_false_positive(monkeypatch) -> None:
    _reset()
    hostile._MEMORY[(10, 55)] = _legacy_row(  # noqa: SLF001
        "Dank Shield AntiNuke containment: Emoji creation"
    )
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


def test_authoritative_reputation_lookup_sanitizes_before_callers_see_row(monkeypatch) -> None:
    _reset()
    had_flag = hasattr(hostile, reentry._REPUTATION_FLAG)  # noqa: SLF001
    old_flag = getattr(hostile, reentry._REPUTATION_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(hostile, reentry._REPUTATION_FLAG)  # noqa: SLF001

    async def base_get(_guild_id: int, _user_id: int, *, refresh: bool = False):
        _ = refresh
        return _legacy_row("Dank Shield AntiNuke containment: Sticker mutation")

    async def clear(*_args, **_kwargs):
        return {"active": False}

    monkeypatch.setattr(hostile, "get_actor_reputation", base_get)
    monkeypatch.setattr(hostile, "clear_hostile_reputation", clear)

    try:
        assert reentry._patch_reputation_lookup() is True  # noqa: SLF001

        async def scenario() -> None:
            row = await hostile.get_actor_reputation(10, 55)
            assert row is not None
            assert row["active"] is False
            await asyncio.sleep(0)

        asyncio.run(scenario())
    finally:
        if had_flag:
            setattr(hostile, reentry._REPUTATION_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(hostile, reentry._REPUTATION_FLAG):  # noqa: SLF001
            delattr(hostile, reentry._REPUTATION_FLAG)  # noqa: SLF001
        _reset()
