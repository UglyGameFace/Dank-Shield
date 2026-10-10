from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from stoney_verify import cinema_playback_service as playback
from stoney_verify.media_metadata import parse_release_name
from stoney_verify.movie_night import MovieSourceVariant


def release(
    name: str,
    *,
    seeds: int = 10,
    file_size: int = 0,
    provider: str = "ordinary",
    verified=None,
    observed=None,
) -> MovieSourceVariant:
    metadata = {"release_name": parse_release_name(name)}
    if verified is not None:
        metadata["verified"] = verified
    if observed is not None:
        metadata["observed_swarm"] = observed
    return MovieSourceVariant(
        variant_id=name,
        source_ref="magnet:?xt=urn:btih:" + "a" * 40,
        source_id=provider, source_label=provider,
        created_at=1.0, seeds=seeds,
        leechers=2, peers=seeds + 2, file_size=file_size,
        metadata=metadata,
    )


def test_verified_hevc_does_not_override_unverified_h264_in_automatic_mode():
    h265 = release(
        "Film.2026.1080p.x264.mp4", seeds=300,
        verified={
            "available": True,
            "container": "mov,mp4,m4a,3gp,3g2,mj2",
            "video": {"codec": "hevc", "height": 1080, "bit_depth": 10},
        },
    )
    h264 = release("Film.2026.1080p.x264.mp4", seeds=7)
    assert h265.browser_video_risk_key() == 2
    assert playback.ranked_automatic_variants([h265, h264]) == [h264]


def test_known_h264_mp4_verified_wins_before_unverified_seed_count():
    known = release(
        "Movie.2026.1080p.x264.mp4", seeds=4,
        verified={
            "available": True, "filename": "movie.mp4",
            "container": "mov,mp4,m4a,3gp,3g2,mj2",
            "video": {"codec": "h264", "height": 1080, "bit_depth": 8},
        },
    )
    unknown = release("Movie.2026.1080p.x264.mp4", seeds=400)
    assert playback.ranked_automatic_variants([unknown, known])[0] is known


def test_automatic_quality_size_and_preference_are_soft_not_absolute():
    huge = release("Movie.2026.2160p.x264.mp4", file_size=18 * 1024 ** 3,
                   seeds=400, provider="favorite")
    moderate = release("Movie.2026.1080p.x264.mp4", file_size=3 * 1024 ** 3,
                       seeds=30, provider="other")
    small = release("Movie.2026.720p.x264.mp4", file_size=1 * 1024 ** 3,
                    seeds=70, provider="other")
    assert playback.ranked_automatic_variants(
        [huge, small, moderate], preferred_source="favorite",
    )[0] is moderate
    assert playback.ranked_automatic_variants(
        [huge, small, moderate], max_file_bytes=8 * 1024 ** 3,
    ) == [moderate, small]
    a = release("Movie.2026.1080p.x264.mp4", provider="a", seeds=9)
    b = release("Movie.2026.1080p.x264.mp4", provider="b", seeds=9)
    assert playback.ranked_automatic_variants(
        [a, b], preferred_source="b",
    )[0] is b


def test_measured_throughput_is_distinct_from_reported_seeds_and_finished_download():
    slow = release("Movie.2026.1080p.x264.mp4", seeds=800, observed={
        "at": time.monotonic(),
        "download_rate": 0, "connected_peers": 0, "progress": 0.1,
    })
    unknown = release("Movie.2026.1080p.x264.mp4", seeds=100)
    fast = release("Movie.2026.1080p.x264.mp4", seeds=5, observed={
        "at": time.monotonic(),
        "download_rate": 3 * 1024 ** 2,
        "connected_peers": 4, "progress": 0.5,
    })
    completed = release("Movie.2026.1080p.x264.mp4", seeds=1, observed={
        "at": time.monotonic(),
        "download_rate": 0, "connected_peers": 0, "progress": 1.0,
    })
    assert playback.ranked_automatic_variants([slow, unknown, fast, completed]) == [
        completed, fast, unknown, slow,
    ]


def test_torrent_file_chooser_avoids_mkv_and_wrong_episode_in_same_pack():
    session = SimpleNamespace(
        candidates=[
            SimpleNamespace(index=0, path="Example.Show.S01E02.1080p.x264.mp4", size=2_000_000_000),
            SimpleNamespace(index=1, path="Example.Show.S01E01.1080p.HEVC.mkv", size=2_000_000_000),
            SimpleNamespace(index=2, path="Example.Show.S01E01.720p.x264.mp4", size=1_000_000_000),
        ],
    )
    item = playback.choose_automatic_torrent_file(
        session, {"media_type": "episode", "season_number": 1, "episode_number": 1},
    )
    assert item.index == 2
    assert playback.choose_automatic_torrent_file(
        session, {"media_type": "episode", "season_number": 9, "episode_number": 1},
    ) is None


def test_bounded_auto_start_retries_only_prior_to_room_commit(monkeypatch):
    room = SimpleNamespace(
        room_id="room", host_id=42, ended=False,
        stream_token="", current_candidate_id="", current_variant_id="",
    )
    manager = SimpleNamespace(get=lambda _id: room)
    monkeypatch.setattr(playback, "get_movie_night_manager", lambda: manager)
    rows = [release(f"Film.2026.1080p.x264.{n}.mp4", seeds=50 - n)
            for n in range(6)]
    calls = []

    async def start(room_id, *, actor_id, candidate_id, variant_id, automatic=False):
        assert automatic is True and actor_id == 42 and candidate_id == "candidate"
        calls.append(variant_id)
        if len(calls) < 3:
            raise TimeoutError("unavailable metadata")
        return SimpleNamespace(room=room)

    result, selected, failures = asyncio.run(playback.start_automatic_variant(
        "room", actor_id=42, candidate_id="candidate",
        selected=rows[0], ranked=rows, start_variant=start,
    ))
    assert result.room is room
    assert selected.variant_id == rows[2].variant_id
    assert failures == 2
    assert len(calls) == 3


def test_auto_start_never_retries_after_room_changes(monkeypatch):
    room = SimpleNamespace(
        room_id="room", host_id=42, ended=False,
        stream_token="before", current_candidate_id="previous", current_variant_id="old",
    )
    monkeypatch.setattr(
        playback, "get_movie_night_manager", lambda: SimpleNamespace(get=lambda _id: room),
    )
    rows = [release(f"Film.2026.1080p.x264.{n}.mp4") for n in range(4)]
    calls = []

    async def unsafe_start(_room_id, *, actor_id, candidate_id, variant_id, automatic=False):
        calls.append(variant_id)
        room.stream_token = "committed"
        raise RuntimeError("failure after commit")

    with pytest.raises(RuntimeError, match="after commit"):
        asyncio.run(playback.start_automatic_variant(
            "room", actor_id=42, candidate_id="candidate",
            selected=rows[0], ranked=rows, start_variant=unsafe_start,
        ))
    assert len(calls) == 1


def test_auto_start_does_not_retry_manual_authority_changes(monkeypatch):
    room = SimpleNamespace(
        room_id="room", host_id=99, ended=False,
        stream_token="", current_candidate_id="", current_variant_id="",
    )
    monkeypatch.setattr(
        playback, "get_movie_night_manager", lambda: SimpleNamespace(get=lambda _id: room),
    )
    with pytest.raises(PermissionError):
        asyncio.run(playback.start_automatic_variant(
            "room", actor_id=42, candidate_id="candidate",
            selected=None, ranked=[],
        ))


def test_explicit_active_watch_party_votes_override_automatic_ranking():
    crowd_choice = release("Film.2026.720p.x264.mp4", seeds=5)
    fast_candidate = release("Film.2026.1080p.x264.mp4", seeds=300)
    crowd_choice.votes.update({11, 12})
    fast_candidate.votes.update({13})
    assert playback.ranked_automatic_variants(
        [fast_candidate, crowd_choice], active_voters={11, 12, 13},
    )[0] is crowd_choice
    # Stale/disconnected voters do not outweigh active room members.
    assert playback.ranked_automatic_variants(
        [fast_candidate, crowd_choice], active_voters={13},
    )[0] is fast_candidate


def test_verified_source_evidence_survives_same_source_refresh():
    from stoney_verify.movie_night import MovieNightManager

    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=100, channel_id=200, host_id=42,
        stream_token="", mode="standalone",
    )
    candidate = manager.nominate(
        room.room_id, user_id=42, title="Film",
        auto_vote=False,
    )
    kw = dict(
        room_id=room.room_id, candidate_id=candidate.candidate_id, user_id=42,
        source_ref="magnet:?xt=urn:btih:" + "b" * 40,
        auto_vote=False,
    )
    original = manager.add_variant(
        **kw, seeds=12, metadata={"release_name": {"video_tags": []}},
    )
    original.metadata["verified"] = {
        "available": True, "filename": "film.mp4",
        "container": "mov,mp4,m4a,3gp,3g2,mj2",
        "video": {"codec": "h264", "height": 1080, "bit_depth": 8},
    }
    original.metadata["observed_swarm"] = {
        "at": time.monotonic(), "download_rate": 3 * 1024 ** 2,
        "connected_peers": 8, "progress": 0.3,
    }
    refreshed = manager.add_variant(
        **kw, seeds=8, metadata={"release_name": {"video_tags": ["H.264"]}},
    )
    assert refreshed is original
    assert refreshed.browser_video_risk_key() == 0
    assert refreshed.metadata["observed_swarm"]["connected_peers"] == 8


def test_healthy_lower_resolution_beats_unseeded_1080p_and_weak_swarm():
    unseeded = release("Film.2026.1080p.x264.mp4", seeds=0,
                       file_size=2 * 1024 ** 3)
    weak = release("Film.2026.1080p.x264.mp4", seeds=1,
                   file_size=2 * 1024 ** 3)
    healthy = release("Film.2026.720p.x264.mp4", seeds=40,
                      file_size=1 * 1024 ** 3)
    assert playback.ranked_automatic_variants(
        [unseeded, weak, healthy],
    )[0] is healthy


def test_all_explicitly_risky_candidates_fail_closed():
    hevc = release("Film.2026.1080p.HEVC.mkv", seeds=400)
    avi = release("Film.2026.720p.x264.avi", seeds=20)
    assert playback.ranked_automatic_variants([hevc, avi]) == []
