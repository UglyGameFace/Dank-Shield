from __future__ import annotations

import time

import pytest

from stoney_verify.media_session_votes import (
    ACTION_END_STREAM,
    ACTION_PLAY_NEXT,
    ACTION_PLAY_RESULT,
    ACTION_QUEUE_RESULT,
    ACTION_SEARCH_MEDIA,
    ACTION_SEEK,
    MediaVoteManager,
    VotePolicy,
    normalize_media_action,
    normalize_search_query,
)


def test_media_action_normalization_and_search_query_cleanup() -> None:
    assert normalize_media_action("play next") == ACTION_PLAY_NEXT
    assert normalize_media_action("search-media") == ACTION_SEARCH_MEDIA
    assert normalize_search_query("   The    Matrix   ") == "The Matrix"
    with pytest.raises(ValueError):
        normalize_media_action("delete-server")
    with pytest.raises(ValueError):
        normalize_search_query("   ")


def test_host_active_blocks_room_vote_until_away() -> None:
    manager = MediaVoteManager(owner_id=10, away_seconds=120)

    with pytest.raises(PermissionError, match="host is active"):
        manager.start_vote(
            user_id=20,
            action=ACTION_SEARCH_MEDIA,
            payload={"query": "Arrival"},
        )

    assert manager.set_host_away(10, True)
    vote = manager.start_vote(
        user_id=20,
        action=ACTION_SEARCH_MEDIA,
        payload={"query": "Arrival"},
    )
    assert vote.action == ACTION_SEARCH_MEDIA
    assert vote.payload["query"] == "Arrival"
    assert vote.yes == {20}


def test_host_inactivity_enters_away_mode() -> None:
    manager = MediaVoteManager(owner_id=10, away_seconds=15)
    manager.lease.last_active_at = time.monotonic() - 16
    assert manager.host_away


def test_host_reclaim_clears_active_vote() -> None:
    manager = MediaVoteManager(owner_id=10)
    manager.set_host_away(10, True)
    manager.start_vote(
        user_id=20,
        action=ACTION_SEARCH_MEDIA,
        payload={"query": "Dune"},
    )

    assert manager.host_reclaim(10)
    assert not manager.host_away
    assert manager.active is None


def test_one_member_one_vote_and_vote_can_change() -> None:
    manager = MediaVoteManager(owner_id=10)
    manager.set_host_away(10, True)
    manager.start_vote(
        user_id=20,
        action=ACTION_SEEK,
        payload={"position": 120},
    )

    vote = manager.cast(user_id=21, approve=True)
    assert vote.yes == {20, 21}
    vote = manager.cast(user_id=21, approve=False)
    assert vote.yes == {20}
    assert vote.no == {21}


def test_quorum_scales_with_participants() -> None:
    manager = MediaVoteManager(
        owner_id=10,
        policy=VotePolicy(quorum_fraction=0.50, min_quorum=2),
    )
    assert manager.required_quorum(1) == 1
    assert manager.required_quorum(2) == 2
    assert manager.required_quorum(5) == 3
    assert manager.required_quorum(20) == 10


def test_normal_action_passes_at_configured_majority() -> None:
    manager = MediaVoteManager(
        owner_id=10,
        policy=VotePolicy(
            quorum_fraction=0.50,
            pass_fraction=0.60,
            destructive_pass_fraction=0.67,
            min_quorum=2,
        ),
    )
    manager.set_host_away(10, True)
    manager.start_vote(
        user_id=20,
        action=ACTION_SEARCH_MEDIA,
        payload={"query": "Interstellar"},
    )
    manager.cast(user_id=21, approve=True)
    manager.cast(user_id=22, approve=False)

    status, details = manager.evaluate(eligible_count=5)
    assert status == "passed"
    assert details["quorum"] == 3
    assert details["yes"] == 2
    assert details["approval"] == pytest.approx(2 / 3)


def test_destructive_action_requires_stronger_majority() -> None:
    manager = MediaVoteManager(
        owner_id=10,
        policy=VotePolicy(
            quorum_fraction=0.50,
            pass_fraction=0.60,
            destructive_pass_fraction=0.75,
            min_quorum=2,
        ),
    )
    manager.set_host_away(10, True)
    vote = manager.start_vote(
        user_id=20,
        action=ACTION_END_STREAM,
    )
    assert vote.destructive

    manager.cast(user_id=21, approve=True)
    manager.cast(user_id=22, approve=False)
    manager.cast(user_id=23, approve=False)

    status, details = manager.evaluate(eligible_count=6)
    assert status != "passed"
    assert details["pass_fraction"] == 0.75


def test_search_play_queue_and_next_actions_validate_result_ids() -> None:
    manager = MediaVoteManager(owner_id=10)
    manager.set_host_away(10, True)

    search = manager.start_vote(
        user_id=20,
        action=ACTION_SEARCH_MEDIA,
        payload={"query": "Blade Runner 2049"},
    )
    assert search.payload["query"] == "Blade Runner 2049"

    manager.active = None
    for action in (ACTION_PLAY_RESULT, ACTION_QUEUE_RESULT, ACTION_PLAY_NEXT):
        with pytest.raises(ValueError, match="result id"):
            manager.start_vote(user_id=20, action=action, payload={})
        vote = manager.start_vote(
            user_id=20,
            action=action,
            payload={"result_id": "resolver:movie:123"},
        )
        assert vote.payload["result_id"] == "resolver:movie:123"
        manager.active = None


def test_vote_expiry_removes_stale_action() -> None:
    manager = MediaVoteManager(
        owner_id=10,
        policy=VotePolicy(ttl_seconds=15),
    )
    manager.set_host_away(10, True)
    vote = manager.start_vote(
        user_id=20,
        action=ACTION_SEARCH_MEDIA,
        payload={"query": "Alien"},
    )
    vote.expires_at = time.monotonic() - 1
    manager.prune()
    assert manager.active is None


def test_consume_only_returns_passed_vote() -> None:
    manager = MediaVoteManager(
        owner_id=10,
        policy=VotePolicy(
            quorum_fraction=0.50,
            pass_fraction=0.60,
            min_quorum=2,
        ),
    )
    manager.set_host_away(10, True)
    manager.start_vote(
        user_id=20,
        action=ACTION_SEARCH_MEDIA,
        payload={"query": "Heat"},
    )
    assert manager.consume_if_passed(eligible_count=4) is None

    manager.cast(user_id=21, approve=True)
    passed = manager.consume_if_passed(eligible_count=4)
    assert passed is not None
    assert passed.action == ACTION_SEARCH_MEDIA
    assert manager.active is None
