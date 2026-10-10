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
