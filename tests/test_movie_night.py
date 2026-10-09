from __future__ import annotations

from stoney_verify.movie_night import PRIVATE_VIEWER_LIMIT, MovieNightManager


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



def test_end_vote_is_collaborative_even_while_host_is_active() -> None:
    manager, room_id = _room_with_three_viewers()

    vote = manager.propose_vote(
        room_id,
        proposer_id=20,
        action="end",
        now=105.0,
    )
    assert not vote.resolved

    vote = manager.cast_vote(
        room_id,
        vote.vote_id,
        user_id=30,
        approve=True,
        now=106.0,
    )
    assert vote.resolved and vote.passed

    room = manager.get(room_id)
    assert room is not None
    assert room.ended
    assert room.playback_state == "ended"

    # External cleanup is claimed after the state transition marks the room ended.
    assert manager.claim_vote_execution(room_id, vote.vote_id)
    assert not manager.claim_vote_execution(room_id, vote.vote_id)


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


def test_auto_source_favors_verified_playable_video_before_quality_or_seeds() -> None:
    manager, room_id = _room_with_three_viewers()
    candidate = manager.nominate(
        room_id, user_id=20, title="Browser Video Compatibility", now=105.0,
    )
    risky = manager.add_variant(
        room_id, candidate.candidate_id, user_id=20,
        source_ref="authorized:high-seed-hevc-mkv",
        seeds=90, leechers=2, peers=92,
        metadata={"verified": {
            "filename": "release.mkv", "container": "matroska,webm",
            "video": {"codec": "hevc", "bit_depth": 10, "width": 3840, "height": 2160},
        }}, now=106.0,
    )
    safe = manager.add_variant(
        room_id, candidate.candidate_id, user_id=30,
        source_ref="authorized:seeded-h264-mp4",
        seeds=12, leechers=1, peers=13,
        metadata={"verified": {
            "filename": "release.mp4", "container": "mov,mp4,m4a,3gp,3g2,mj2",
            "video": {"codec": "h264", "bit_depth": 8, "width": 1920, "height": 1080},
        }}, now=107.0,
    )
    assert safe.browser_video_risk_key() == 0
    assert risky.browser_video_risk_key() == 2
    ranked = manager.ranked_variants(room_id, candidate.candidate_id, now=108.0)
    assert ranked[0].variant_id == safe.variant_id

    # Votes remain authoritative for multi-viewer rooms.
    manager.vote_variant(room_id, candidate.candidate_id, risky.variant_id, user_id=20, approve=True)
    ranked = manager.ranked_variants(room_id, candidate.candidate_id, now=108.0)
    assert ranked[0].variant_id == risky.variant_id


def test_video_risk_is_unknown_without_verified_codec_not_fake_safe() -> None:
    manager, room_id = _room_with_three_viewers()
    candidate = manager.nominate(
        room_id, user_id=20, title="Unprobed Codec", now=105.0,
    )
    unknown = manager.add_variant(
        room_id, candidate.candidate_id, user_id=20,
        source_ref="authorized:not-yet-probed", seeds=8,
        metadata={"release_name": {"resolution": "1080p"}}, now=106.0,
    )
    assert unknown.browser_video_risk_key() == 1
    unknown.metadata["verified"] = {
        "filename": "high-depth.mp4", "container": "mov,mp4",
        "video": {"codec": "h264", "bit_depth": 10},
    }
    assert unknown.browser_video_risk_key() == 1
    unknown.metadata["verified"]["video"]["bit_depth"] = "invalid"
    assert unknown.browser_video_risk_key() == 1


def test_browser_safe_audio_beats_known_risky_audio_when_both_are_seeded() -> None:
    manager, room_id = _room_with_three_viewers()
    candidate = manager.nominate(
        room_id,
        user_id=20,
        title="Audio Compatibility",
        now=105.0,
    )
    risky = manager.add_variant(
        room_id,
        candidate.candidate_id,
        user_id=20,
        source_ref="authorized:eac3",
        file_size=4_000_000_000,
        seeds=80,
        leechers=10,
        peers=90,
        metadata={
            "release_name": {
                "source": "WEB-DL",
                "audio_tags": ["DDP 5.1"],
            },
        },
        now=106.0,
    )
    safe = manager.add_variant(
        room_id,
        candidate.candidate_id,
        user_id=30,
        source_ref="authorized:aac",
        file_size=4_000_000_000,
        seeds=12,
        leechers=3,
        peers=15,
        metadata={
            "release_name": {
                "source": "WEB-DL",
                "audio_tags": ["AAC"],
            },
        },
        now=107.0,
    )

    ranked = manager.ranked_variants(room_id, candidate.candidate_id, now=108.0)

    assert safe.browser_audio_risk_key() == 0
    assert risky.browser_audio_risk_key() == 2
    assert ranked[0].variant_id == safe.variant_id


def test_mixed_aac_and_dts_release_is_not_misranked_browser_safe() -> None:
    manager, room_id = _room_with_three_viewers()
    candidate = manager.nominate(
        room_id, user_id=20, title="Multi-track Audio", now=105.0,
    )
    mixed = manager.add_variant(
        room_id, candidate.candidate_id, user_id=20,
        source_ref="authorized:mixed",
        seeds=90, leechers=3, peers=93,
        metadata={
            "verified": {
                "audio_tracks": [
                    {"codec": "dts", "language": "por"},
                    {"codec": "aac", "language": "eng"},
                ],
            },
        }, now=106.0,
    )
    native = manager.add_variant(
        room_id, candidate.candidate_id, user_id=30,
        source_ref="authorized:native",
        seeds=20, leechers=1, peers=21,
        metadata={"verified": {
            "audio_tracks": [{"codec": "aac", "language": "eng"}],
        }}, now=107.0,
    )
    assert mixed.browser_audio_risk_key() == 2
    assert native.browser_audio_risk_key() == 0
    assert manager.ranked_variants(
        room_id, candidate.candidate_id, now=108.0,
    )[0].variant_id == native.variant_id


def test_zero_seed_browser_safe_audio_still_loses_to_seeded_release() -> None:
    manager, room_id = _room_with_three_viewers()
    candidate = manager.nominate(
        room_id,
        user_id=20,
        title="Audio Availability",
        now=105.0,
    )
    seeded = manager.add_variant(
        room_id,
        candidate.candidate_id,
        user_id=20,
        source_ref="authorized:seeded",
        seeds=2,
        leechers=1,
        peers=3,
        metadata={"release_name": {"audio_tags": ["DDP 5.1"]}},
        now=106.0,
    )
    dead_safe = manager.add_variant(
        room_id,
        candidate.candidate_id,
        user_id=30,
        source_ref="authorized:dead-aac",
        seeds=0,
        leechers=0,
        peers=0,
        metadata={"release_name": {"audio_tags": ["AAC"]}},
        now=107.0,
    )

    ranked = manager.ranked_variants(room_id, candidate.candidate_id, now=108.0)

    assert ranked[0].variant_id == seeded.variant_id
    assert ranked[-1].variant_id == dead_safe.variant_id


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
    room.viewers[20].sync_requested = True
    room.viewers[20].sync_requested_at = 100.0

    manager.set_room_media(
        room.room_id,
        host_id=10,
        stream_token="new-token",
    )
    assert room.viewers[10].sync_ready
    assert not room.viewers[20].sync_ready
    assert not room.viewers[20].sync_requested
    assert room.viewers[20].sync_requested_at == 0.0
    assert 20 not in manager.buffer_quorum_viewers(room, now=101.0)



def test_new_client_session_invalidates_stale_viewer_sync_until_requested() -> None:
    manager = MovieNightManager(
        viewer_ttl_seconds=120,
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
        action="resume",
        now=100.0,
    )

    manager.join_room(room.room_id, user_id=20, now=101.0)
    viewer = room.viewers[20]
    viewer.sync_ready = True
    viewer.sync_ready_at = 101.0
    viewer.client_session_id = "old-page"
    viewer.sync_requested = True

    target = room.current_position(110.0)
    manager.heartbeat(
        room.room_id,
        user_id=20,
        position_seconds=target,
        byte_position=1000,
        buffered_until_byte=20_000,
        paused=False,
        buffered_until_seconds=target + 20,
        media_duration_seconds=7200,
        sync_buffer_target_seconds=8,
        client_session_id="new-page",
        sync_requested=False,
        now=110.0,
    )

    assert viewer.client_session_id == "new-page"
    assert viewer.sync_requested is False
    assert viewer.sync_ready is False
    assert viewer.sync_ready_at == 0.0
    assert 20 not in manager.buffer_quorum_viewers(room, now=110.0)


def test_same_client_session_refresh_preserves_viewer_sync_state() -> None:
    manager = MovieNightManager(
        viewer_ttl_seconds=120,
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
        action="resume",
        now=100.0,
    )
    manager.join_room(room.room_id, user_id=20, now=101.0)

    viewer = room.viewers[20]
    viewer.client_session_id = "persisted-page-session"
    viewer.sync_requested = True
    viewer.sync_requested_at = 101.0
    viewer.sync_ready = True
    viewer.sync_ready_at = 101.0

    target = room.current_position(110.0)
    manager.heartbeat(
        room.room_id,
        user_id=20,
        position_seconds=target,
        byte_position=1000,
        buffered_until_byte=20_000,
        paused=False,
        buffered_until_seconds=target + 20,
        media_duration_seconds=7200,
        sync_buffer_target_seconds=8,
        client_session_id="persisted-page-session",
        sync_requested=True,
        now=110.0,
    )

    assert viewer.client_session_id == "persisted-page-session"
    assert viewer.sync_requested is True
    assert viewer.sync_ready is True
    assert viewer.sync_ready_at == 101.0
    assert 20 in manager.buffer_quorum_viewers(room, now=110.0)


def test_session_aware_viewer_requires_sync_request_before_graduating() -> None:
    manager = MovieNightManager(
        viewer_ttl_seconds=120,
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
        action="resume",
        now=100.0,
    )
    manager.join_room(room.room_id, user_id=20, now=101.0)

    target = room.current_position(110.0)
    manager.heartbeat(
        room.room_id,
        user_id=20,
        position_seconds=target,
        byte_position=1000,
        buffered_until_byte=20_000,
        paused=False,
        buffered_until_seconds=target + 20,
        media_duration_seconds=7200,
        sync_buffer_target_seconds=8,
        client_session_id="viewer-page",
        sync_requested=False,
        now=110.0,
    )
    viewer = room.viewers[20]
    assert viewer.sync_ready is False
    assert viewer.sync_requested is False

    target = room.current_position(111.0)
    manager.heartbeat(
        room.room_id,
        user_id=20,
        position_seconds=target,
        byte_position=2000,
        buffered_until_byte=22_000,
        paused=False,
        buffered_until_seconds=target + 20,
        media_duration_seconds=7200,
        sync_buffer_target_seconds=8,
        client_session_id="viewer-page",
        sync_requested=True,
        now=111.0,
    )

    assert viewer.sync_requested is True
    assert viewer.sync_requested_at == 111.0
    assert viewer.sync_ready is True
    assert viewer.sync_ready_at == 111.0
    assert 20 in manager.buffer_quorum_viewers(room, now=111.0)


def test_host_session_id_never_demotes_host_sync_authority() -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="torrent-token",
        now=100.0,
    )

    manager.heartbeat(
        room.room_id,
        user_id=10,
        position_seconds=0,
        byte_position=0,
        buffered_until_byte=0,
        paused=True,
        client_session_id="host-page-a",
        sync_requested=False,
        now=101.0,
    )
    manager.heartbeat(
        room.room_id,
        user_id=10,
        position_seconds=0,
        byte_position=0,
        buffered_until_byte=0,
        paused=True,
        client_session_id="host-page-b",
        sync_requested=False,
        now=102.0,
    )

    host = room.viewers[10]
    assert host.sync_ready is True
    assert host.client_session_id == "host-page-b"
    assert 10 in manager.buffer_quorum_viewers(room, now=102.0)


def test_private_room_is_invite_only_and_revocation_removes_access() -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        mode="private",
        now=100.0,
    )

    assert room.mode == "private"
    assert room.private_allowed_viewers == {10}
    assert manager.user_can_access(room, 10)
    assert not manager.user_can_access(room, 20)
    assert manager.active_room_for_user(1, 2, 20) is None

    manager.invite_private_viewer(room.room_id, host_id=10, user_id=20)
    assert manager.user_can_access(room, 20)
    assert manager.active_room_for_user(1, 2, 20) is room

    manager.join_room(room.room_id, user_id=20, now=101.0)
    assert set(room.viewers) == {10, 20}
    assert manager.active_viewers(room, now=101.0) == {10, 20}

    manager.remove_private_viewer(room.room_id, host_id=10, user_id=20)
    assert not manager.user_can_access(room, 20)
    assert manager.active_room_for_user(1, 2, 20) is None
    assert set(room.viewers) == {10}


def test_private_room_enforces_twenty_authorized_viewer_limit() -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        mode="private",
        now=100.0,
    )

    for user_id in range(20, 20 + PRIVATE_VIEWER_LIMIT - 1):
        manager.invite_private_viewer(
            room.room_id,
            host_id=10,
            user_id=user_id,
        )

    assert len(room.private_allowed_viewers) == PRIVATE_VIEWER_LIMIT

    try:
        manager.invite_private_viewer(
            room.room_id,
            host_id=10,
            user_id=999,
        )
    except RuntimeError as exc:
        assert str(PRIVATE_VIEWER_LIMIT) in str(exc)
    else:
        raise AssertionError("private viewing accepted more than 20 authorized viewers")


def test_private_owner_votes_resolve_without_waiting_for_other_viewers() -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        mode="private",
        now=100.0,
    )

    manager.invite_private_viewer(room.room_id, host_id=10, user_id=20)
    manager.join_room(room.room_id, user_id=20, now=101.0)

    vote = manager.propose_vote(
        room.room_id,
        proposer_id=10,
        action="search",
        payload={"query": "Blade Runner"},
        now=102.0,
    )

    assert vote.resolved
    assert vote.passed
    assert manager.required_yes_votes(room, now=102.0) == 1
    assert manager.active_viewers(room, now=102.0) == {10, 20}
    assert room.approved_search_query == "Blade Runner"


def test_public_watch_party_join_behavior_is_unchanged() -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        now=100.0,
    )

    manager.join_room(room.room_id, user_id=20, now=101.0)

    assert room.mode == "watch_party"
    assert manager.user_can_access(room, 20)
    assert set(room.viewers) == {10, 20}

def test_private_room_accepts_invited_heartbeat_and_rejects_uninvited_user() -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="torrent-token",
        mode="private",
        now=100.0,
    )
    manager.invite_private_viewer(room.room_id, host_id=10, user_id=20)
    manager.join_room(room.room_id, user_id=20, now=101.0)
    manager.heartbeat(
        room.room_id,
        user_id=20,
        position_seconds=0,
        byte_position=0,
        buffered_until_byte=0,
        paused=True,
        now=102.0,
    )
    assert 20 in room.viewers

    try:
        manager.heartbeat(
            room.room_id,
            user_id=30,
            position_seconds=0,
            byte_position=0,
            buffered_until_byte=0,
            paused=True,
            now=103.0,
        )
    except PermissionError as exc:
        assert "private" in str(exc).lower()
    else:
        raise AssertionError("uninvited user heartbeat unexpectedly entered private viewing")

    assert set(room.viewers) == {10, 20}


def test_private_invited_viewer_cannot_take_programming_control() -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        mode="private",
        now=100.0,
    )
    manager.invite_private_viewer(room.room_id, host_id=10, user_id=20)
    manager.join_room(room.room_id, user_id=20, now=101.0)

    try:
        manager.propose_vote(
            room.room_id,
            proposer_id=20,
            action="search",
            payload={"query": "Alien"},
            now=102.0,
        )
    except PermissionError as exc:
        assert "host" in str(exc).lower()
    else:
        raise AssertionError("invited private viewer unexpectedly gained room programming control")


def test_host_handoff_preserves_playback_and_moves_authority() -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="torrent-token",
        now=100.0,
    )
    manager.join_room(room.room_id, user_id=20, now=101.0)
    manager.apply_host_action(
        room.room_id,
        host_id=10,
        action="seek",
        payload={"seconds": 120.0},
        now=102.0,
    )
    manager.apply_host_action(
        room.room_id,
        host_id=10,
        action="resume",
        now=102.0,
    )
    vote = manager.propose_vote(
        room.room_id,
        proposer_id=10,
        action="pause",
        now=103.0,
    )
    assert not vote.resolved

    transferred = manager.transfer_host(
        room.room_id,
        current_host_id=10,
        new_host_id=20,
        now=112.0,
    )

    assert transferred is room
    assert room.host_id == 20
    assert room.playback_state == "playing"
    assert room.playback_position == 130.0
    assert room.current_position(now=117.0) == 135.0
    assert room.viewers[20].sync_ready is True
    assert room.viewers[20].position_seconds == 130.0
    assert vote.resolved is True
    assert vote.passed is False

    manager.apply_host_action(room.room_id, host_id=20, action="pause", now=117.0)
    assert room.playback_state == "paused"
    assert room.playback_position == 135.0

    try:
        manager.apply_host_action(room.room_id, host_id=10, action="resume", now=118.0)
    except PermissionError:
        pass
    else:
        raise AssertionError("previous host unexpectedly kept playback authority")


def test_host_handoff_requires_active_viewer_and_private_handoff_stays_authorized() -> None:
    manager = MovieNightManager(viewer_ttl_seconds=30)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        now=100.0,
    )
    manager.join_room(room.room_id, user_id=20, now=100.0)

    try:
        manager.transfer_host(
            room.room_id,
            current_host_id=10,
            new_host_id=20,
            now=200.0,
        )
    except PermissionError as exc:
        assert "active" in str(exc).lower()
    else:
        raise AssertionError("inactive viewer unexpectedly became host")

    room.ended = True
    private_room = manager.create_room(
        guild_id=1,
        channel_id=3,
        host_id=10,
        stream_token="",
        mode="private",
        now=201.0,
    )
    manager.invite_private_viewer(
        private_room.room_id,
        host_id=10,
        user_id=20,
    )
    manager.join_room(private_room.room_id, user_id=20, now=202.0)

    transferred = manager.transfer_host(
        private_room.room_id,
        current_host_id=10,
        new_host_id=20,
        now=203.0,
    )

    assert transferred is private_room
    assert private_room.host_id == 20
    assert manager.user_can_access(private_room, 10)
    assert manager.user_can_access(private_room, 20)
    assert private_room.viewers[20].sync_ready is True


def test_rejoin_restores_active_presence_without_losing_room_state() -> None:
    manager = MovieNightManager(viewer_ttl_seconds=35)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="torrent-token",
        now=100.0,
    )
    candidate = manager.nominate(
        room.room_id,
        user_id=10,
        title="Queued Movie",
        auto_vote=False,
        now=100.0,
    )
    room.queue.append(candidate.candidate_id)
    room.viewers[10].last_seen = 100.0
    room.host_last_seen = 100.0

    assert manager.active_viewers(room, now=140.0) == set()

    manager.join_room(room.room_id, user_id=10, now=140.0)

    assert manager.active_viewers(room, now=140.0) == {10}
    assert room.queue == [candidate.candidate_id]
    assert room.stream_token == "torrent-token"
    assert room.host_id == 10


def test_empty_room_timeout_starts_from_last_presence_and_keeps_live_room() -> None:
    manager = MovieNightManager(
        viewer_ttl_seconds=35,
        empty_room_ttl_seconds=1800,
    )
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="torrent-token",
        now=100.0,
    )

    assert not manager.room_empty_expired(room, now=1_899.0)
    assert manager.room_empty_expired(room, now=1_935.0)

    manager.join_room(room.room_id, user_id=20, now=1_930.0)
    assert not manager.room_empty_expired(room, now=1_940.0)
    assert manager.inactive_room_candidates(now=1_940.0) == ()


def test_empty_room_timeout_uses_latest_discord_or_watch_presence() -> None:
    manager = MovieNightManager(
        viewer_ttl_seconds=35,
        empty_room_ttl_seconds=300,
    )
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        now=100.0,
    )
    manager.join_room(room.room_id, user_id=20, now=350.0)

    assert manager.room_last_presence(room) == 350.0
    assert not manager.room_empty_expired(room, now=649.0)
    assert manager.room_empty_expired(room, now=650.0)



def test_host_can_remove_reorder_and_clear_cinema_queue() -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        now=100.0,
    )
    first = manager.nominate(
        room.room_id,
        user_id=10,
        title="First",
        auto_vote=False,
        now=100.0,
    )
    second = manager.nominate(
        room.room_id,
        user_id=10,
        title="Second",
        auto_vote=False,
        now=101.0,
    )
    third = manager.nominate(
        room.room_id,
        user_id=10,
        title="Third",
        auto_vote=False,
        now=102.0,
    )
    room.queue[:] = [first.candidate_id, second.candidate_id, third.candidate_id]

    manager.move_queued(
        room.room_id,
        host_id=10,
        candidate_id=third.candidate_id,
        offset=-1,
    )
    assert room.queue == [first.candidate_id, third.candidate_id, second.candidate_id]

    manager.remove_queued(
        room.room_id,
        host_id=10,
        candidate_id=third.candidate_id,
    )
    assert room.queue == [first.candidate_id, second.candidate_id]

    manager.clear_queue(room.room_id, host_id=10)
    assert room.queue == []


def test_non_host_cannot_manage_cinema_queue() -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        now=100.0,
    )
    manager.join_room(room.room_id, user_id=20, now=101.0)
    candidate = manager.nominate(
        room.room_id,
        user_id=10,
        title="Queued",
        auto_vote=False,
        now=102.0,
    )
    room.queue.append(candidate.candidate_id)

    try:
        manager.remove_queued(
            room.room_id,
            host_id=20,
            candidate_id=candidate.candidate_id,
        )
    except PermissionError as exc:
        assert "host" in str(exc).lower()
    else:
        raise AssertionError("non-host unexpectedly managed the Cinema queue")



def test_private_session_promotes_to_watch_party_without_restarting_room() -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="stream-token",
        mode="private",
        now=100.0,
    )
    manager.invite_private_viewer(room.room_id, host_id=10, user_id=20)
    manager.join_room(room.room_id, user_id=20, now=101.0)
    room.playback_state = "playing"
    room.playback_position = 37.5
    room.playback_anchor_monotonic = 101.0
    room.queue[:] = ["queued-a"]

    promoted = manager.promote_private_to_watch_party(
        room.room_id,
        host_id=10,
    )

    assert promoted is room
    assert room.mode == "watch_party"
    assert room.room_id == promoted.room_id
    assert room.stream_token == "stream-token"
    assert room.host_id == 10
    assert 20 in room.viewers
    assert room.queue == ["queued-a"]
    assert room.private_allowed_viewers == set()
    assert manager.user_can_access(room, 9999) is True


def test_only_private_host_can_promote_session_to_watch_party() -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        mode="private",
    )

    try:
        manager.promote_private_to_watch_party(room.room_id, host_id=20)
    except PermissionError as exc:
        assert "host" in str(exc).lower()
    else:
        raise AssertionError("non-host unexpectedly promoted a Private Session")

    assert room.mode == "private"
