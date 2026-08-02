from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from stoney_verify.commands_ext import public_setup_compact as compact


def interaction(*, guild_id: int = 10, user_id: int = 20, message_id: int = 30) -> Any:
    return SimpleNamespace(
        guild=SimpleNamespace(id=guild_id),
        user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(id=message_id),
    )


def test_confirmations_survive_navigation_in_the_same_setup_message() -> None:
    compact._TEST_SESSIONS.clear()
    state = {"tickets": True, "logs": True}
    first = interaction()

    saved = compact._save_test_session(first, state, {"tickets"})
    reopened = compact._load_test_session(interaction(), state)

    assert saved == frozenset({"tickets"})
    assert reopened == frozenset({"tickets"})


def test_confirmation_cache_isolated_by_ephemeral_message() -> None:
    compact._TEST_SESSIONS.clear()
    state = {"tickets": True}
    compact._save_test_session(interaction(message_id=100), state, {"tickets"})

    assert compact._load_test_session(
        interaction(message_id=101),
        state,
    ) == frozenset()


def test_enabled_feature_change_invalidates_stale_confirmations() -> None:
    compact._TEST_SESSIONS.clear()
    current = interaction()
    compact._save_test_session(current, {"tickets": True}, {"tickets"})

    changed = {"tickets": True, "logs": True}
    assert compact._load_test_session(current, changed) == frozenset()
    assert compact._test_session_key(current) not in compact._TEST_SESSIONS


def test_finish_or_close_can_clear_current_session() -> None:
    compact._TEST_SESSIONS.clear()
    current = interaction()
    compact._save_test_session(current, {"tickets": True}, {"tickets"})

    compact._clear_test_session(current)

    assert compact._load_test_session(current, {"tickets": True}) == frozenset()


def test_stale_sessions_are_purged() -> None:
    compact._TEST_SESSIONS.clear()
    key = compact._test_session_key(interaction())
    compact._TEST_SESSIONS[key] = (
        0.0,
        ("tickets",),
        frozenset({"tickets"}),
    )

    compact._purge_test_sessions(
        now=float(compact._TEST_SESSION_TTL_SECONDS + 1),
    )

    assert key not in compact._TEST_SESSIONS
