from __future__ import annotations

from stoney_verify.movie_night import MovieNightManager


def _room_with_three_viewers() -> tuple[MovieNightManager, str]:
    manager = MovieNightManager(
        host_grace_seconds=45,
        viewer_ttl_seconds=120,
        vote_ttl_seconds=60,
    )
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="torrent-token",
        now=100.0,
    )
    manager.heartbeat(
        room.room_id,
        user_id=20,
        position_seconds=0,
        byte_position=0,
        buffered_until_byte=8 * 1024 * 1024,
        paused=True,
        now=100.0,
    )
    manager.heartbeat(
        room.room_id,
        user_id=30,
        position_seconds=0,
        byte_position=0,
        buffered_until_byte=8 * 1024 * 1024,
        paused=True,
        now=100.0,
    )
    return manager, room.room_id


def test_programming_votes_are_available_while_host_is_present() -> None:
    manager, room_id = _room_with_three_viewers()
    vote = manager.propose_vote(
        room_id,
        proposer_id=20,
        action="search",
        payload={"query": "Blade Runner"},
        now=105.0,
    )
    assert not vote.resolved
    assert vote.action == "search"

    vote = manager.cast_vote(
        room_id,
        vote.vote_id,
        user_id=30,
        approve=True,
        now=106.0,
    )
    assert vote.resolved
    assert vote.passed
    assert manager.get(room_id).approved_search_query == "Blade Runner"  # type: ignore[union-attr]


def test_non_host_playback_vote_waits_until_host_is_away() -> None:
    manager, room_id = _room_with_three_viewers()

    try:
        manager.propose_vote(
            room_id,
            proposer_id=20,
            action="pause",
            now=110.0,
        )
    except PermissionError as exc:
        assert "host is away" in str(exc).lower()
    else:
        raise AssertionError("viewer playback vote unexpectedly opened while host was active")

    room = manager.get(room_id)
    assert room is not None
    room.viewers[10].last_seen = 100.0
    room.host_last_seen = 100.0
    manager.heartbeat(
        room_id,
        user_id=20,
        position_seconds=10,
        byte_position=10 * 1024 * 1024,
        buffered_until_byte=30 * 1024 * 1024,
        paused=False,
        now=160.0,
    )
    manager.heartbeat(
        room_id,
        user_id=30,
        position_seconds=10,
        byte_position=10 * 1024 * 1024,
        buffered_until_byte=30 * 1024 * 1024,
        paused=False,
        now=160.0,
    )

    vote = manager.propose_vote(
        room_id,
        proposer_id=20,
        action="pause",
        now=160.0,
    )
    assert not vote.resolved
    vote = manager.cast_vote(
        room_id,
        vote.vote_id,
        user_id=30,
        approve=True,
        now=161.0,
    )
    assert vote.resolved and vote.passed
    assert manager.get(room_id).playback_state == "paused"  # type: ignore[union-attr]


def test_returning_host_cancels_failover_playback_vote_but_not_search_vote() -> None:
    manager, room_id = _room_with_three_viewers()
    room = manager.get(room_id)
    assert room is not None

    room.viewers[10].last_seen = 100.0
    room.host_last_seen = 100.0
    manager.heartbeat(
        room_id,
        user_id=20,
        position_seconds=1,
        byte_position=1024,
        buffered_until_byte=4096,
        paused=False,
        now=160.0,
    )
    manager.heartbeat(
        room_id,
        user_id=30,
        position_seconds=1,
        byte_position=1024,
        buffered_until_byte=4096,
        paused=False,
        now=160.0,
    )

    playback_vote = manager.propose_vote(
        room_id,
        proposer_id=20,
        action="seek",
        payload={"seconds": 500},
        now=160.0,
    )
    search_vote = manager.propose_vote(
        room_id,
        proposer_id=20,
        action="search",
        payload={"query": "Alien"},
        now=160.0,
    )

    manager.heartbeat(
        room_id,
        user_id=10,
        position_seconds=1,
        byte_position=1024,
        buffered_until_byte=4096,
        paused=False,
        now=161.0,
    )

    assert playback_vote.resolved and not playback_vote.passed
    assert not search_vote.resolved


def test_movie_candidate_supports_multiple_release_variants_and_votes() -> None:
    manager, room_id = _room_with_three_viewers()
    candidate = manager.nominate(
        room_id,
        user_id=20,
        title="Example Movie",
        metadata={"catalog_id": "movie:123"},
        now=105.0,
    )

    small_hevc = manager.add_variant(
        room_id,
        candidate.candidate_id,
        user_id=20,
        source_ref="authorized:variant:small-hevc",
        file_size=4 * 1024 * 1024 * 1024,
        seeds=40,
        leechers=15,
        peers=55,
        metadata={
            "release_name": {"source": "WEB-DL"},
            "verified": {
                "video": {
                    "codec": "hevc",
                    "width": 1920,
                    "height": 1080,
                    "hdr": [],
                },
                "audio_tracks": [{"channels": 6}],
            },
        },
        now=106.0,
    )
    huge_h264 = manager.add_variant(
        room_id,
        candidate.candidate_id,
        user_id=30,
        source_ref="authorized:variant:huge-h264",
        file_size=9 * 1024 * 1024 * 1024,
        seeds=15,
        leechers=5,
        peers=20,
        metadata={
            "release_name": {"source": "WEBRip"},
            "verified": {
                "video": {
                    "codec": "h264",
                    "width": 1920,
                    "height": 1080,
                    "hdr": [],
                },
                "audio_tracks": [{"channels": 6}],
            },
        },
        now=107.0,
    )

    ranked = manager.ranked_variants(room_id, candidate.candidate_id, now=108.0)
    assert ranked[0].variant_id == small_hevc.variant_id

    manager.vote_variant(
        room_id,
        candidate.candidate_id,
        huge_h264.variant_id,
        user_id=20,
        approve=True,
        now=109.0,
    )
    manager.vote_variant(
        room_id,
        candidate.candidate_id,
        huge_h264.variant_id,
        user_id=10,
        approve=True,
        now=109.0,
    )
    ranked = manager.ranked_variants(room_id, candidate.candidate_id, now=110.0)
    assert ranked[0].variant_id == huge_h264.variant_id


def test_default_variant_order_is_seed_first_and_zero_seed_is_last() -> None:
    manager, room_id = _room_with_three_viewers()
    candidate = manager.nominate(
        room_id,
        user_id=20,
        title="Seed Health Test",
        now=105.0,
    )

    healthy = manager.add_variant(
        room_id,
        candidate.candidate_id,
        user_id=20,
        source_ref="authorized:healthy",
        file_size=8_000_000_000,
        seeds=48,
        leechers=12,
        peers=60,
        metadata={
            "release_name": {"source": "WEB-DL"},
            "verified": {
                "video": {"codec": "h264", "width": 1920, "height": 1080, "hdr": []},
                "audio_tracks": [{"channels": 6}],
            },
        },
        now=106.0,
    )
    prettier_but_dead = manager.add_variant(
        room_id,
        candidate.candidate_id,
        user_id=30,
        source_ref="authorized:dead",
        file_size=14_000_000_000,
        seeds=0,
        leechers=18,
        peers=18,
        metadata={
            "release_name": {"source": "BluRay"},
            "verified": {
                "video": {"codec": "hevc", "width": 3840, "height": 2160, "hdr": ["HDR10"]},
                "audio_tracks": [{"channels": 8}],
            },
        },
        now=107.0,
    )

    ranked = manager.ranked_variants(room_id, candidate.candidate_id, now=108.0)
    assert ranked[0].variant_id == healthy.variant_id
    assert ranked[-1].variant_id == prettier_but_dead.variant_id
    assert healthy.swarm_health == {
        "seeds": 48,
        "leechers": 12,
        "peers": 60,
        "seed_leech_ratio": 4.0,
        "label": "strong",
    }
    assert prettier_but_dead.swarm_health["label"] == "dead"


def test_variant_selection_can_use_room_vote_winner() -> None:
    manager, room_id = _room_with_three_viewers()
    candidate = manager.nominate(
        room_id,
        user_id=20,
        title="Another Movie",
        now=105.0,
    )
    a = manager.add_variant(
        room_id,
        candidate.candidate_id,
        user_id=20,
        source_ref="authorized:a",
        file_size=5_000_000_000,
        metadata={"release_name": {"source": "WEB-DL"}},
        now=106.0,
    )
    b = manager.add_variant(
        room_id,
        candidate.candidate_id,
        user_id=30,
        source_ref="authorized:b",
        file_size=6_000_000_000,
        metadata={"release_name": {"source": "BluRay"}},
        now=107.0,
    )
    manager.vote_variant(
        room_id,
        candidate.candidate_id,
        b.variant_id,
        user_id=10,
        approve=True,
        now=108.0,
    )

    winner = manager.select_variant(room_id, candidate.candidate_id, now=109.0)
    assert winner.variant_id == b.variant_id
    assert candidate.selected_variant_id == b.variant_id
    assert a.variant_id != candidate.selected_variant_id


def test_release_variants_keep_source_provenance() -> None:
    manager, room_id = _room_with_three_viewers()
    candidate = manager.nominate(
        room_id,
        user_id=20,
        title="Source Provenance",
        now=105.0,
    )
    variant = manager.add_variant(
        room_id,
        candidate.candidate_id,
        user_id=20,
        source_ref="authorized:source:item-1",
        source_id="family-library",
        source_label="Family Library",
        file_size=2_000_000_000,
        seeds=75,
        leechers=10,
        peers=85,
        now=106.0,
    )

    assert variant.source_id == "family-library"
    assert variant.source_label == "Family Library"
    assert variant.swarm_health["seeds"] == 75
    assert variant.swarm_health["leechers"] == 10
    assert variant.swarm_health["seed_leech_ratio"] == 7.5


def test_seed_count_wins_default_tie_before_quality() -> None:
    manager, room_id = _room_with_three_viewers()
    candidate = manager.nominate(
        room_id,
        user_id=20,
        title="Seed First",
        now=105.0,
    )
    high_seed_1080 = manager.add_variant(
        room_id,
        candidate.candidate_id,
        user_id=20,
        source_ref="authorized:1080",
        file_size=4_000_000_000,
        seeds=120,
        leechers=20,
        peers=140,
        metadata={
            "release_name": {"source": "WEB-DL"},
            "verified": {
                "video": {"codec": "hevc", "width": 1920, "height": 1080, "hdr": []},
                "audio_tracks": [{"channels": 6}],
            },
        },
        now=106.0,
    )
    low_seed_4k = manager.add_variant(
        room_id,
        candidate.candidate_id,
        user_id=30,
        source_ref="authorized:4k",
        file_size=10_000_000_000,
        seeds=8,
        leechers=2,
        peers=10,
        metadata={
            "release_name": {"source": "BluRay"},
            "verified": {
                "video": {"codec": "hevc", "width": 3840, "height": 2160, "hdr": ["HDR10"]},
                "audio_tracks": [{"channels": 8}],
            },
        },
        now=107.0,
    )

    ranked = manager.ranked_variants(room_id, candidate.candidate_id, now=108.0)
    assert ranked[0].variant_id == high_seed_1080.variant_id
    assert ranked[1].variant_id == low_seed_4k.variant_id


def test_group_buffer_corridor_tracks_shared_viewer_health() -> None:
    manager, room_id = _room_with_three_viewers()
    manager.heartbeat(
        room_id,
        user_id=10,
        position_seconds=100,
        byte_position=100 * 1024 * 1024,
        buffered_until_byte=140 * 1024 * 1024,
        paused=False,
        now=110.0,
    )
    manager.heartbeat(
        room_id,
        user_id=20,
        position_seconds=99,
        byte_position=98 * 1024 * 1024,
        buffered_until_byte=130 * 1024 * 1024,
        paused=False,
        now=110.0,
    )
    manager.heartbeat(
        room_id,
        user_id=30,
        position_seconds=101,
        byte_position=102 * 1024 * 1024,
        buffered_until_byte=150 * 1024 * 1024,
        paused=False,
        now=110.0,
    )

    corridor = manager.group_buffer_corridor(room_id, now=110.0)
    assert corridor is not None
    start, weakest_buffer_end, leader = corridor
    assert start == 98 * 1024 * 1024
    assert weakest_buffer_end == 130 * 1024 * 1024
    assert leader == 150 * 1024 * 1024
