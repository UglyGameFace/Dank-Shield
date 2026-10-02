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



def test_passed_external_vote_execution_is_claimed_exactly_once() -> None:
    manager, room_id = _room_with_three_viewers()
    vote = manager.propose_vote(
        room_id,
        proposer_id=20,
        action="search",
        payload={"query": "The Thing"},
        now=105.0,
    )
    vote = manager.cast_vote(
        room_id,
        vote.vote_id,
        user_id=30,
        approve=True,
        now=106.0,
    )
    assert vote.passed
    assert manager.claim_vote_execution(room_id, vote.vote_id)
    assert not manager.claim_vote_execution(room_id, vote.vote_id)


def test_movie_result_order_uses_best_live_seed_count_when_votes_tie() -> None:
    manager, room_id = _room_with_three_viewers()
    weak = manager.nominate(
        room_id,
        user_id=20,
        title="Weak Swarm",
        auto_vote=False,
        now=105.0,
    )
    strong = manager.nominate(
        room_id,
        user_id=20,
        title="Strong Swarm",
        auto_vote=False,
        now=106.0,
    )
    manager.add_variant(
        room_id,
        weak.candidate_id,
        user_id=20,
        source_ref="magnet:?xt=urn:btih:weak",
        seeds=2,
        leechers=8,
        peers=10,
        auto_vote=False,
        now=107.0,
    )
    manager.add_variant(
        room_id,
        strong.candidate_id,
        user_id=20,
        source_ref="magnet:?xt=urn:btih:strong",
        seeds=90,
        leechers=10,
        peers=100,
        auto_vote=False,
        now=108.0,
    )

    ranked = manager.ranked_candidates(room_id, now=109.0)
    assert ranked[0].candidate_id == strong.candidate_id
    assert ranked[1].candidate_id == weak.candidate_id


def test_group_buffer_hold_pauses_and_resumes_without_infinite_stall() -> None:
    manager = MovieNightManager(
        host_grace_seconds=45,
        viewer_ttl_seconds=120,
        vote_ttl_seconds=60,
        buffer_low_seconds=4,
        buffer_resume_seconds=10,
        buffer_max_hold_seconds=20,
    )
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="torrent-token",
        now=100.0,
    )
    manager.join_room(room.room_id, user_id=20, now=100.0)
    manager.apply_host_action(
        room.room_id,
        host_id=10,
        action="resume",
        now=100.0,
    )

    manager.heartbeat(
        room.room_id,
        user_id=10,
        position_seconds=10,
        byte_position=10_000,
        buffered_until_byte=30_000,
        paused=False,
        buffered_until_seconds=30,
        media_duration_seconds=600,
        now=101.0,
    )
    manager.heartbeat(
        room.room_id,
        user_id=20,
        position_seconds=10,
        byte_position=10_000,
        buffered_until_byte=12_000,
        paused=False,
        buffered_until_seconds=12,
        media_duration_seconds=600,
        now=101.0,
    )
    assert room.playback_state == "buffering"
    held_position = room.playback_position

    manager.heartbeat(
        room.room_id,
        user_id=10,
        position_seconds=held_position,
        byte_position=10_000,
        buffered_until_byte=40_000,
        paused=True,
        buffered_until_seconds=30,
        media_duration_seconds=600,
        now=105.0,
    )
    manager.heartbeat(
        room.room_id,
        user_id=20,
        position_seconds=held_position,
        byte_position=10_000,
        buffered_until_byte=40_000,
        paused=True,
        buffered_until_seconds=30,
        media_duration_seconds=600,
        now=105.0,
    )
    assert room.playback_state == "playing"
    assert room.buffering_since == 0.0


def test_group_buffer_hold_has_max_wait_and_cooldown() -> None:
    manager = MovieNightManager(
        viewer_ttl_seconds=120,
        buffer_low_seconds=4,
        buffer_resume_seconds=10,
        buffer_max_hold_seconds=5,
    )
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="torrent-token",
        now=100.0,
    )
    manager.join_room(room.room_id, user_id=20, now=100.0)
    manager.apply_host_action(room.room_id, host_id=10, action="resume", now=100.0)

    for uid in (10, 20):
        manager.heartbeat(
            room.room_id,
            user_id=uid,
            position_seconds=10,
            byte_position=10_000,
            buffered_until_byte=11_000,
            paused=False,
            buffered_until_seconds=11,
            media_duration_seconds=600,
            now=101.0,
        )
    assert room.playback_state == "buffering"

    for uid in (10, 20):
        manager.heartbeat(
            room.room_id,
            user_id=uid,
            position_seconds=room.playback_position,
            byte_position=10_000,
            buffered_until_byte=11_000,
            paused=True,
            buffered_until_seconds=11,
            media_duration_seconds=600,
            now=107.0,
        )
    assert room.playback_state == "playing"
    assert room.buffering_cooldown_until > 107.0



def test_late_joiner_does_not_pause_room_until_synchronized() -> None:
    manager = MovieNightManager(
        viewer_ttl_seconds=120,
        buffer_low_seconds=4,
        buffer_resume_seconds=10,
        buffer_max_hold_seconds=20,
        late_join_min_buffer_seconds=8,
        late_join_max_buffer_seconds=15,
        late_join_sync_tolerance_seconds=2.5,
    )
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="torrent-token",
        now=100.0,
    )
    manager.apply_host_action(
        room.room_id,
        host_id=10,
        action="seek",
        payload={"seconds": 2700},
        now=100.0,
    )
    manager.apply_host_action(
        room.room_id,
        host_id=10,
        action="resume",
        now=100.0,
    )

    manager.heartbeat(
        room.room_id,
        user_id=10,
        position_seconds=2701,
        byte_position=2701_000,
        buffered_until_byte=2730_000,
        paused=False,
        buffered_until_seconds=2730,
        media_duration_seconds=7200,
        now=101.0,
    )

    manager.join_room(room.room_id, user_id=20, now=110.0)
    viewer = room.viewers[20]
    assert not viewer.sync_ready
    assert 20 in manager.active_viewers(room, now=110.0)
    assert 20 not in manager.buffer_quorum_viewers(room, now=110.0)

    # The late viewer has barely any data. Existing playback must continue.
    target = room.current_position(111.0)
    manager.heartbeat(
        room.room_id,
        user_id=20,
        position_seconds=target,
        byte_position=target.__int__() * 1000,
        buffered_until_byte=target.__int__() * 1000 + 1000,
        paused=True,
        buffered_until_seconds=target + 2,
        media_duration_seconds=7200,
        sync_buffer_target_seconds=12,
        now=111.0,
    )
    assert not viewer.sync_ready
    assert room.playback_state == "playing"
    assert 20 not in manager.buffer_quorum_viewers(room, now=111.0)

    # Once the viewer is at the live room position with enough adaptive buffer,
    # they graduate into the normal group-buffer quorum.
    target = room.current_position(113.0)
    manager.heartbeat(
        room.room_id,
        user_id=20,
        position_seconds=target,
        byte_position=target.__int__() * 1000,
        buffered_until_byte=target.__int__() * 1000 + 20_000,
        paused=True,
        buffered_until_seconds=target + 12,
        media_duration_seconds=7200,
        sync_buffer_target_seconds=12,
        now=113.0,
    )
    assert viewer.sync_ready
    assert viewer.sync_ready_at == 113.0
    assert 20 in manager.buffer_quorum_viewers(room, now=113.0)


def test_late_joiner_can_trigger_group_buffering_only_after_sync() -> None:
    manager = MovieNightManager(
        viewer_ttl_seconds=120,
        buffer_low_seconds=4,
        buffer_resume_seconds=10,
        buffer_max_hold_seconds=20,
        late_join_min_buffer_seconds=8,
        late_join_max_buffer_seconds=15,
    )
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="torrent-token",
        now=100.0,
    )
    manager.apply_host_action(room.room_id, host_id=10, action="resume", now=100.0)
    manager.heartbeat(
        room.room_id,
        user_id=10,
        position_seconds=1,
        byte_position=1000,
        buffered_until_byte=30_000,
        paused=False,
        buffered_until_seconds=30,
        media_duration_seconds=600,
        now=101.0,
    )

    manager.join_room(room.room_id, user_id=20, now=105.0)
    target = room.current_position(106.0)
    manager.heartbeat(
        room.room_id,
        user_id=20,
        position_seconds=target,
        byte_position=6000,
        buffered_until_byte=30_000,
        paused=True,
        buffered_until_seconds=target + 12,
        media_duration_seconds=600,
        sync_buffer_target_seconds=10,
        now=106.0,
    )
    assert room.viewers[20].sync_ready
    assert room.playback_state == "playing"

    # Once synchronized, a later low-buffer heartbeat may legitimately protect
    # the group with a bounded shared buffering hold.
    target = room.current_position(108.0)
    manager.heartbeat(
        room.room_id,
        user_id=10,
        position_seconds=target,
        byte_position=8000,
        buffered_until_byte=30_000,
        paused=False,
        buffered_until_seconds=target + 20,
        media_duration_seconds=600,
        now=108.0,
    )
    manager.heartbeat(
        room.room_id,
        user_id=20,
        position_seconds=target,
        byte_position=8000,
        buffered_until_byte=8500,
        paused=False,
        buffered_until_seconds=target + 1,
        media_duration_seconds=600,
        sync_buffer_target_seconds=10,
        now=108.0,
    )
    assert room.playback_state == "buffering"


def test_media_change_requalifies_non_host_viewers_for_new_stream() -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="old-token",
        now=100.0,
    )
    manager.join_room(room.room_id, user_id=20, now=100.0)
    assert room.viewers[20].sync_ready

    manager.set_room_media(
        room.room_id,
        host_id=10,
        stream_token="new-token",
    )
    assert room.viewers[10].sync_ready
    assert not room.viewers[20].sync_ready
    assert 20 not in manager.buffer_quorum_viewers(room, now=101.0)
