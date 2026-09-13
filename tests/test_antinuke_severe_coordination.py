from __future__ import annotations

from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_guardian_runtime as guardian


class FakeGuild:
    def __init__(self) -> None:
        self.id = 8811
        self.owner_id = 999999


def _actor(user_id: int):
    return SimpleNamespace(id=user_id, roles=[], mention=f"<@{user_id}>")


def _reset() -> None:
    anti_nuke._SEEN_AUDIT_ENTRY_IDS.clear()
    guardian._PANIC_EVENTS.clear()
    guardian._PANIC_UNTIL.clear()


def test_two_structural_deletions_by_two_actors_trigger_immediately() -> None:
    _reset()
    guild = FakeGuild()
    first = _actor(101)
    second = _actor(102)

    active, triggered, _ = guardian._panic_state(guild, first, "channel_delete")
    assert active is False
    assert triggered is False

    active, triggered, observed = guardian._panic_state(guild, second, "role_delete")
    assert active is True
    assert triggered is True
    assert {actor.id for actor in observed} == {101, 102}
    _reset()


def test_two_channel_deletions_by_two_actors_close_score_six_gap() -> None:
    _reset()
    guild = FakeGuild()
    first = _actor(201)
    second = _actor(202)

    guardian._panic_state(guild, first, "channel_delete")
    active, triggered, _ = guardian._panic_state(guild, second, "channel_delete")

    assert active is True
    assert triggered is True
    _reset()


def test_two_moderation_actions_do_not_use_severe_fast_path() -> None:
    _reset()
    guild = FakeGuild()
    first = _actor(301)
    second = _actor(302)

    guardian._panic_state(guild, first, "ban")
    active, triggered, _ = guardian._panic_state(guild, second, "kick")

    assert active is False
    assert triggered is False
    assert guild.id not in guardian._PANIC_UNTIL
    _reset()


def test_two_creation_actions_do_not_use_severe_fast_path() -> None:
    _reset()
    guild = FakeGuild()
    first = _actor(401)
    second = _actor(402)

    guardian._panic_state(guild, first, "channel_create")
    active, triggered, _ = guardian._panic_state(guild, second, "role_create")

    assert active is False
    assert triggered is False
    assert guild.id not in guardian._PANIC_UNTIL
    _reset()


def test_single_actor_cannot_trigger_multi_actor_fast_path() -> None:
    _reset()
    guild = FakeGuild()
    actor = _actor(501)

    guardian._panic_state(guild, actor, "channel_delete")
    active, triggered, _ = guardian._panic_state(guild, actor, "role_delete")

    assert active is False
    assert triggered is False
    _reset()


def test_severe_set_is_structural_not_routine_moderation() -> None:
    expected = {
        "channel_delete",
        "overwrite_update",
        "role_delete",
        "webhook_delete",
        "automod_rule_delete",
    }
    assert expected.issubset(guardian._PANIC_SEVERE_ACTIONS)
    assert "ban" not in guardian._PANIC_SEVERE_ACTIONS
    assert "kick" not in guardian._PANIC_SEVERE_ACTIONS
    assert "channel_create" not in guardian._PANIC_SEVERE_ACTIONS
    assert "role_create" not in guardian._PANIC_SEVERE_ACTIONS
