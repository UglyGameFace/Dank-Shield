from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

import stoney_verify.torrent_streaming as torrent_streaming
from stoney_verify.api_new.torrent_stream_routes import (
    _bounded_partial_response_end,
    _cast_cors_headers,
)
from stoney_verify.torrent_media_server import _validate_public_base_url
from stoney_verify.torrent_streaming import (
    TorrentMediaManager,
    TorrentSessionUnavailableError,
    TorrentStreamSession,
    find_magnet,
    is_playable_filename,
    is_torrent_filename,
    magnet_identity,
    media_content_type,
    parse_http_range,
)


class _FakeLTSession:
    def __init__(self) -> None:
        self.removed: list[object] = []

    def remove_torrent(self, handle, *args) -> None:
        self.removed.append(handle)


class _FakeLT:
    class options_t:
        delete_files = 1

    def __init__(self) -> None:
        self.settings = None
        self.session_obj = _FakeLTSession()

    def session(self, settings):
        self.settings = dict(settings)
        return self.session_obj


class _FakeFiles:
    def __init__(self, rows):
        self.rows = list(rows)

    def num_files(self) -> int:
        return len(self.rows)

    def file_size(self, index: int) -> int:
        return int(self.rows[index][1])

    def file_path(self, index: int) -> str:
        return str(self.rows[index][0])

    def file_offset(self, index: int) -> int:
        return int(self.rows[index][2])


class _FakeInfo:
    def __init__(self, rows, piece_length: int = 1024 * 1024) -> None:
        self._files = _FakeFiles(rows)
        self._piece_length = piece_length

    def files(self):
        return self._files

    def piece_length(self) -> int:
        return self._piece_length

    def info_hashes(self):
        return SimpleNamespace(v1="ABCDEF")


class _FakeHandle:
    def __init__(self) -> None:
        self.file_priorities = None
        self.piece_updates: list[tuple[int, int]] = []
        self.available: set[int] = set()
        self.download_rate = 1234
        self.upload_rate = 50

    def prioritize_files(self, priorities) -> None:
        self.file_priorities = list(priorities)

    def prioritize_pieces(self, updates) -> None:
        self.piece_updates.extend((int(a), int(b)) for a, b in updates)

    def have_piece(self, piece: int) -> bool:
        return int(piece) in self.available

    def status(self):
        return SimpleNamespace(
            error="",
            download_rate=self.download_rate,
            upload_rate=self.upload_rate,
            num_peers=3,
            num_seeds=1,
            state="downloading",
        )

    def file_progress(self):
        return [0, 1024, 2048]


def _manager(monkeypatch, tmp_path: Path) -> TorrentMediaManager:
    monkeypatch.setenv("DANK_TORRENT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DANK_TORRENT_MAX_SESSIONS", "1")
    monkeypatch.setenv("DANK_TORRENT_SOFT_SESSION_LIMIT", "1")
    monkeypatch.setenv("DANK_TORRENT_ALLOW_BURST", "false")
    monkeypatch.setenv("DANK_PROCESS_MEMORY_LIMIT_MB", "8192")
    monkeypatch.setenv("DANK_MOVIE_NIGHT_MEMORY_RESERVE_MB", "128")
    monkeypatch.setenv("DANK_TORRENT_ESTIMATED_SESSION_MB", "32")
    monkeypatch.setenv("DANK_TORRENT_DISK_RESERVE_BYTES", str(2 * 1024 * 1024 * 1024))
    monkeypatch.setenv("DANK_TORRENT_MAX_TOTAL_BYTES", str(8 * 1024 * 1024 * 1024))
    return TorrentMediaManager(lt_module=_FakeLT())


def test_live_torrent_status_exposes_seed_and_leech_counts(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    handle = _FakeHandle()
    session = TorrentStreamSession(
        token="swarm",
        secret="secret",
        owner_id=2,
        guild_id=1,
        source_kind="magnet",
        source_identity="btih:swarm",
        save_root=tmp_path,
        handle=handle,
        info=object(),
        file_index=0,
        file_path="movie.mp4",
        file_name="movie.mp4",
        file_size=4096,
        file_offset=0,
        piece_length=1024,
        first_piece=0,
        last_piece=3,
        created_at=0.0,
        last_access=0.0,
    )

    status = manager.status(session)
    assert status["peers"] == 3
    assert status["seeds"] == 1
    assert status["leechers"] == 2
    assert status["seed_leech_ratio"] == 0.5


def test_torrent_session_keeps_dht_enabled_for_hash_only_magnets(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    assert manager.lt.settings["enable_dht"] is True
    assert manager.lt.settings["enable_lsd"] is False


def test_magnet_and_torrent_source_detection() -> None:
    btih = "0123456789abcdef0123456789abcdef01234567"
    magnet = f"magnet:?xt=urn:btih:{btih}&dn=Public+Domain"
    assert find_magnet(f"watch this {magnet}") == magnet
    assert magnet_identity(magnet) == f"btih:{btih}"
    assert magnet_identity("magnet:?xt=urn:btih:ABC123") == ""
    assert magnet_identity("https://example.com/file") == ""
    assert is_torrent_filename("movie.torrent")
    assert is_torrent_filename("MOVIE.TORRENT")
    assert not is_torrent_filename("movie.mp4")


def test_base32_and_hex_btih_normalize_to_same_identity() -> None:
    expected_hex = "0123456789abcdef0123456789abcdef01234567"
    equivalent_base32 = "AERUKZ4JVPG66AJDIVTYTK6N54ASGRLH"
    assert magnet_identity(
        f"magnet:?xt=urn:btih:{expected_hex}"
    ) == f"btih:{expected_hex}"
    assert magnet_identity(
        f"magnet:?XT=urn:btih:{equivalent_base32}"
    ) == f"btih:{expected_hex}"
    assert magnet_identity(f"magnet:?xt=urn:btih:{'0' * 40}") == ""
    assert magnet_identity(f"magnet:?xt=urn:btih:{'A' * 32}") == ""



def test_btmh_v2_magnet_identity_and_hybrid_v1_preference() -> None:
    v2 = "0123456789abcdef" * 4
    btmh = f"1220{v2}"
    assert magnet_identity(
        f"magnet:?xt=urn:btmh:{btmh}"
    ) == f"btmh:{btmh}"

    v1 = "abcdef0123456789abcdef0123456789abcdef01"
    hybrid = (
        f"magnet:?xt=urn:btmh:{btmh}"
        f"&xt=urn:btih:{v1}"
    )
    assert magnet_identity(hybrid) == f"btih:{v1}"


def test_info_identity_falls_back_to_v2_for_v2_only_torrent(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    v2 = "0123456789abcdef" * 4

    class _V2Info:
        def info_hashes(self):
            return SimpleNamespace(v1="0" * 40, v2=v2)

    assert manager._info_identity(_V2Info()) == f"btmh:1220{v2}"


def test_playable_media_detection_and_content_types() -> None:
    for name in ("movie.mp4", "clip.webm", "film.mkv", "old.avi", "scene.mov"):
        assert is_playable_filename(name)
    assert not is_playable_filename("readme.txt")
    assert media_content_type("movie.mp4") == "video/mp4"
    assert media_content_type("film.mkv") == "video/x-matroska"


def test_http_range_parser_covers_seek_and_suffix_ranges() -> None:
    assert parse_http_range("", 1000) == (0, 999, False)
    assert parse_http_range("bytes=100-199", 1000) == (100, 199, True)
    assert parse_http_range("bytes=900-", 1000) == (900, 999, True)
    assert parse_http_range("bytes=-100", 1000) == (900, 999, True)

    for invalid in (
        "items=1-2",
        "bytes=100-50",
        "bytes=1000-",
        "bytes=1-2,4-5",
    ):
        with pytest.raises(ValueError):
            parse_http_range(invalid, 1000)


def test_playable_selection_uses_largest_supported_file(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    info = _FakeInfo(
        [
            ("readme.txt", 100, 0),
            ("extras/trailer.mp4", 10_000_000, 100),
            ("movie/main.mkv", 700_000_000, 10_000_100),
        ]
    )
    candidates = manager._playable_candidates(info)
    assert [item.path for item in candidates] == [
        "movie/main.mkv",
        "extras/trailer.mp4",
    ]


def test_finalize_prioritizes_only_selected_file_and_bootstrap(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.bootstrap_bytes = 2 * 1024 * 1024
    manager.tail_probe_bytes = 1024 * 1024
    info = _FakeInfo(
        [
            ("extras.txt", 1024, 0),
            ("movie.mp4", 8 * 1024 * 1024, 1024 * 1024),
        ],
        piece_length=1024 * 1024,
    )
    handle = _FakeHandle()

    session = asyncio.run(
        manager._finalize_session(
            token="tok",
            save_root=tmp_path,
            handle=handle,
            info=info,
            guild_id=1,
            owner_id=2,
            source_kind="torrent",
            source_identity="btih:test",
        )
    )

    assert session.file_index == 1
    assert handle.file_priorities == [0, 1]
    assert any(priority == 7 for _, priority in handle.piece_updates)


def test_seek_reprioritizes_requested_and_upcoming_pieces(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.readahead_bytes = 2 * 1024 * 1024
    handle = _FakeHandle()
    session = TorrentStreamSession(
        token="tok",
        secret="secret",
        owner_id=2,
        guild_id=1,
        source_kind="magnet",
        source_identity="btih:test",
        save_root=tmp_path,
        handle=handle,
        info=object(),
        file_index=0,
        file_path="movie.mp4",
        file_name="movie.mp4",
        file_size=20 * 1024 * 1024,
        file_offset=2 * 1024 * 1024,
        piece_length=1024 * 1024,
        first_piece=2,
        last_piece=21,
        created_at=0.0,
        last_access=0.0,
    )

    manager.prioritize_range(
        session,
        5 * 1024 * 1024,
        6 * 1024 * 1024 - 1,
    )

    priorities = dict(handle.piece_updates)
    assert priorities[7] == 7
    assert priorities[8] == 6
    assert priorities[9] == 6


def test_full_video_seek_can_reprioritize_near_end_not_just_bootstrap(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.readahead_bytes = 4 * 1024 * 1024
    handle = _FakeHandle()
    full_size = 90 * 1024 * 1024
    piece_length = 1024 * 1024
    session = TorrentStreamSession(
        token="full-video",
        secret="secret",
        owner_id=2,
        guild_id=1,
        source_kind="magnet",
        source_identity="btih:full",
        save_root=tmp_path,
        handle=handle,
        info=object(),
        file_index=0,
        file_path="movie.mp4",
        file_name="movie.mp4",
        file_size=full_size,
        file_offset=0,
        piece_length=piece_length,
        first_piece=0,
        last_piece=(full_size - 1) // piece_length,
        created_at=0.0,
        last_access=0.0,
    )

    seek_start = 82 * 1024 * 1024
    seek_end = 83 * 1024 * 1024 - 1
    manager.prioritize_range(session, seek_start, seek_end)

    priorities = dict(handle.piece_updates)
    assert priorities[82] == 7
    assert priorities[83] == 6
    assert priorities[86] == 6
    assert session.file_size == full_size
    assert session.last_piece == 89


def test_http_ranges_can_address_first_middle_and_final_bytes_of_full_video() -> None:
    size = 90 * 1024 * 1024
    assert parse_http_range("bytes=0-1048575", size) == (0, 1048575, True)
    assert parse_http_range("bytes=47185920-48234495", size) == (
        47185920,
        48234495,
        True,
    )
    assert parse_http_range("bytes=-1048576", size) == (
        size - 1048576,
        size - 1,
        True,
    )


def test_adaptive_buffer_expands_on_slow_swarm_and_shrinks_on_fast_swarm(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.min_readahead_bytes = 4 * 1024 * 1024
    manager.max_readahead_bytes = 96 * 1024 * 1024
    manager.buffer_target_seconds = 30.0
    manager.buffer_max_seconds = 75.0
    handle = _FakeHandle()
    session = TorrentStreamSession(
        token="adaptive",
        secret="secret",
        owner_id=2,
        guild_id=1,
        source_kind="magnet",
        source_identity="btih:adaptive",
        save_root=tmp_path,
        handle=handle,
        info=object(),
        file_index=0,
        file_path="movie.mp4",
        file_name="movie.mp4",
        file_size=400 * 1024 * 1024,
        file_offset=0,
        piece_length=1024 * 1024,
        first_piece=0,
        last_piece=399,
        created_at=0.0,
        last_access=0.0,
        smoothed_consume_rate=2 * 1024 * 1024,
    )

    handle.download_rate = 1024 * 1024
    slow = manager.prepare_playback_request(
        session,
        10 * 1024 * 1024,
        11 * 1024 * 1024 - 1,
    )

    session.smoothed_download_rate = 0.0
    handle.download_rate = 10 * 1024 * 1024
    fast = manager.prepare_playback_request(
        session,
        11 * 1024 * 1024,
        12 * 1024 * 1024 - 1,
    )

    assert slow.target_seconds > fast.target_seconds
    assert slow.target_bytes > fast.target_bytes
    assert slow.target_bytes <= manager.max_readahead_bytes


def test_adaptive_buffer_detects_seek_and_rebuilds_near_new_position(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.min_readahead_bytes = 4 * 1024 * 1024
    manager.max_readahead_bytes = 64 * 1024 * 1024
    handle = _FakeHandle()
    handle.download_rate = 8 * 1024 * 1024
    session = TorrentStreamSession(
        token="seek-adaptive",
        secret="secret",
        owner_id=2,
        guild_id=1,
        source_kind="magnet",
        source_identity="btih:seek",
        save_root=tmp_path,
        handle=handle,
        info=object(),
        file_index=0,
        file_path="movie.mp4",
        file_name="movie.mp4",
        file_size=300 * 1024 * 1024,
        file_offset=0,
        piece_length=1024 * 1024,
        first_piece=0,
        last_piece=299,
        created_at=0.0,
        last_access=0.0,
        last_request_at=1.0,
        last_request_bytes=1024 * 1024,
        last_request_end=5 * 1024 * 1024 - 1,
        smoothed_consume_rate=1024 * 1024,
    )

    plan = manager.prepare_playback_request(
        session,
        200 * 1024 * 1024,
        201 * 1024 * 1024 - 1,
    )

    assert plan.seek
    assert plan.startup_wait_end > 201 * 1024 * 1024 - 1
    priorities = dict(handle.piece_updates)
    assert priorities[200] == 7
    assert priorities[201] == 6


def test_startup_buffer_is_bounded_even_when_client_requests_whole_file(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.bootstrap_bytes = 8 * 1024 * 1024
    handle = _FakeHandle()
    handle.download_rate = 4 * 1024 * 1024
    session = TorrentStreamSession(
        token="startup",
        secret="secret",
        owner_id=2,
        guild_id=1,
        source_kind="magnet",
        source_identity="btih:startup",
        save_root=tmp_path,
        handle=handle,
        info=object(),
        file_index=0,
        file_path="movie.mp4",
        file_name="movie.mp4",
        file_size=500 * 1024 * 1024,
        file_offset=0,
        piece_length=1024 * 1024,
        first_piece=0,
        last_piece=499,
        created_at=0.0,
        last_access=0.0,
    )

    first_chunk_end = 1024 * 1024 - 1
    plan = manager.prepare_playback_request(session, 0, first_chunk_end)

    assert plan.startup_wait_end > first_chunk_end
    assert plan.startup_wait_end <= first_chunk_end + manager.bootstrap_bytes
    assert plan.startup_wait_end < session.file_size - 1


def test_stall_history_increases_adaptive_buffer_margin(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    handle = _FakeHandle()
    handle.download_rate = 3 * 1024 * 1024
    session = TorrentStreamSession(
        token="stall",
        secret="secret",
        owner_id=2,
        guild_id=1,
        source_kind="magnet",
        source_identity="btih:stall",
        save_root=tmp_path,
        handle=handle,
        info=object(),
        file_index=0,
        file_path="movie.mp4",
        file_name="movie.mp4",
        file_size=400 * 1024 * 1024,
        file_offset=0,
        piece_length=1024 * 1024,
        first_piece=0,
        last_piece=399,
        created_at=0.0,
        last_access=0.0,
        smoothed_consume_rate=2 * 1024 * 1024,
    )

    normal = manager.prepare_playback_request(
        session,
        20 * 1024 * 1024,
        21 * 1024 * 1024 - 1,
    )
    session.stall_count = 3
    session.smoothed_download_rate = 0.0
    buffered = manager.prepare_playback_request(
        session,
        21 * 1024 * 1024,
        22 * 1024 * 1024 - 1,
    )

    assert buffered.target_seconds > normal.target_seconds
    assert buffered.target_bytes >= normal.target_bytes


def test_wait_range_requires_all_requested_pieces(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    handle = _FakeHandle()
    handle.available.update({0, 1})
    session = TorrentStreamSession(
        token="tok",
        secret="secret",
        owner_id=2,
        guild_id=1,
        source_kind="magnet",
        source_identity="btih:test",
        save_root=tmp_path,
        handle=handle,
        info=object(),
        file_index=0,
        file_path="movie.mp4",
        file_name="movie.mp4",
        file_size=3 * 1024 * 1024,
        file_offset=0,
        piece_length=1024 * 1024,
        first_piece=0,
        last_piece=2,
        created_at=0.0,
        last_access=0.0,
    )

    assert asyncio.run(manager.wait_range(session, 0, 2 * 1024 * 1024 - 1, timeout=0.6))
    assert not asyncio.run(
        manager.wait_range(session, 0, 3 * 1024 * 1024 - 1, timeout=0.6)
    )


def test_one_for_one_replacement_can_start_at_capacity(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager._sessions["old-token"] = object()  # type: ignore[assignment]

    asyncio.run(manager._reserve_start(replace_token="old-token"))
    assert manager._starting == 1
    assert "old-token" in manager._replacements_in_flight

    try:
        asyncio.run(manager._reserve_start(replace_token="old-token"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("duplicate replacement reservation bypassed session capacity")

    asyncio.run(manager._release_start(replace_token="old-token"))
    assert manager._starting == 0
    assert "old-token" not in manager._replacements_in_flight


def test_live_session_capacity_counts_existing_sessions(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager._sessions["existing"] = object()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="1-unique-torrent"):
        asyncio.run(manager._reserve_start())


def _cleanup_session(
    tmp_path: Path,
    *,
    token: str,
    handle=None,
) -> TorrentStreamSession:
    root = tmp_path / token
    root.mkdir(parents=True, exist_ok=True)
    return TorrentStreamSession(
        token=token,
        secret="secret",
        owner_id=2,
        guild_id=1,
        source_kind="magnet",
        source_identity=f"btih:{token}",
        save_root=root,
        handle=handle or _FakeHandle(),
        info=object(),
        file_index=0,
        file_path="movie.mp4",
        file_name="movie.mp4",
        file_size=4096,
        file_offset=0,
        piece_length=1024,
        first_piece=0,
        last_piece=3,
        created_at=0.0,
        last_access=time.monotonic() - 10_000.0,
    )


def test_idle_cleanup_never_reclaims_leased_movie_night_session(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.idle_ttl_seconds = 120.0
    session = _cleanup_session(tmp_path, token="leased")
    session.leases.add("movie:1:2")
    manager._sessions[session.token] = session
    manager._identity_index[session.source_identity] = session.token

    removed = asyncio.run(manager.cleanup_expired())

    assert removed == 0
    assert manager._sessions[session.token] is session
    assert manager.lt.session_obj.removed == []


def test_idle_cleanup_rechecks_refresh_before_reclaim(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.idle_ttl_seconds = 120.0
    session = _cleanup_session(tmp_path, token="refreshed")
    manager._sessions[session.token] = session
    manager._identity_index[session.source_identity] = session.token

    fetched = asyncio.run(manager.get(session.token))
    assert fetched is session
    removed = asyncio.run(manager.cleanup_expired())

    assert removed == 0
    assert session.token in manager._sessions


def test_invalid_libtorrent_handle_becomes_controlled_session_error(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)

    class _DeadHandle(_FakeHandle):
        def is_valid(self) -> bool:
            return False

        def prioritize_pieces(self, updates) -> None:
            _ = updates
            raise RuntimeError("invalid torrent handle used [libtorrent:20]")

    session = _cleanup_session(
        tmp_path,
        token="dead",
        handle=_DeadHandle(),
    )
    manager._sessions[session.token] = session
    manager._identity_index[session.source_identity] = session.token

    assert manager.session_usable(session) is False
    with pytest.raises(TorrentSessionUnavailableError):
        manager.prioritize_range(session, 0, 1023)

    discarded = asyncio.run(manager.discard_unusable_session(session.token))
    assert discarded is True
    assert session.token not in manager._sessions


def test_stream_url_is_signed_and_expiring(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DANK_MEDIA_PUBLIC_BASE_URL", "https://media.example")
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "separate-media-secret")
    manager = _manager(monkeypatch, tmp_path)
    handle = _FakeHandle()
    session = TorrentStreamSession(
        token="abc",
        secret="per-session",
        owner_id=2,
        guild_id=1,
        source_kind="torrent",
        source_identity="btih:test",
        save_root=tmp_path,
        handle=handle,
        info=object(),
        file_index=0,
        file_path="movie.mp4",
        file_name="movie.mp4",
        file_size=1024,
        file_offset=0,
        piece_length=256,
        first_piece=0,
        last_piece=3,
        created_at=0.0,
        last_access=0.0,
    )
    manager._sessions[session.token] = session

    url = manager.stream_url(session, ttl_seconds=600)
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.path.endswith("/abc/movie.mp4")
    assert asyncio.run(
        manager.validate_stream_access(
            "abc",
            query["exp"][0],
            query["sig"][0],
        )
    )
    assert not asyncio.run(
        manager.validate_stream_access(
            "abc",
            query["exp"][0],
            "bad-signature",
        )
    )


def test_public_media_url_requires_https_outside_localhost(monkeypatch) -> None:
    monkeypatch.setenv("DANK_MEDIA_PUBLIC_BASE_URL", "https://media.example")
    _validate_public_base_url()

    monkeypatch.setenv("DANK_MEDIA_PUBLIC_BASE_URL", "http://127.0.0.1:8080")
    _validate_public_base_url()

    monkeypatch.setenv("DANK_MEDIA_PUBLIC_BASE_URL", "http://media.example")
    with pytest.raises(RuntimeError, match="must use HTTPS"):
        _validate_public_base_url()


def test_partial_stream_response_never_advertises_unbuffered_tail() -> None:
    requested_end = 128 * 1024 * 1024 - 1
    buffered_end = 8 * 1024 * 1024 - 1

    assert _bounded_partial_response_end(
        0,
        requested_end,
        buffered_end,
        partial=True,
    ) == buffered_end
    assert _bounded_partial_response_end(
        4 * 1024 * 1024,
        requested_end,
        12 * 1024 * 1024 - 1,
        partial=True,
    ) == 12 * 1024 * 1024 - 1
    assert _bounded_partial_response_end(
        0,
        requested_end,
        buffered_end,
        partial=False,
    ) == requested_end


def test_torrent_runtime_static_contract_keeps_public_stream_isolated() -> None:
    root = Path(__file__).resolve().parents[1]
    server = (root / "stoney_verify/api_new/server.py").read_text(encoding="utf-8")
    routes = (root / "stoney_verify/api_new/torrent_stream_routes.py").read_text(encoding="utf-8")
    media_server = (root / "stoney_verify/torrent_media_server.py").read_text(encoding="utf-8")
    app = (root / "stoney_verify/app.py").read_text(encoding="utf-8")
    router = (root / "stoney_verify/share_router_runtime.py").read_text(encoding="utf-8")

    assert "register_torrent_admin_routes(app" in server
    assert "/media/torrent/stream/" not in server
    assert "register_torrent_public_routes(app)" in media_server
    assert 'DANK_MEDIA_BIND_HOST' in media_server
    assert 'DANK_MEDIA_PORT' in media_server
    assert "start_torrent_media_server" in app
    assert "parse_http_range(" in routes
    assert '"Accept-Ranges": "bytes"' in routes
    assert "await manager.wait_range(" in routes
    assert "except (ConnectionError, asyncio.CancelledError):" in routes
    assert "client_disconnected = True" in routes
    assert "consumer_key = str(request.query.get(\"cid\", \"\")" in routes
    assert "consumer_key=consumer_key" in routes
    assert "plan.target_bytes" in routes
    assert "_bounded_partial_response_end(" in routes
    assert "requested_end" in routes
    assert "startup_wait_end" in routes
    assert "get_torrent_manager().ensure_cleanup_task()" in routes
    assert "find_magnet(" in router
    assert "is_torrent_filename(" in router
    assert "manager.start_magnet(" in router
    assert "manager.start_torrent_bytes(" in router
    assert "manager.stream_url(session)" in router
    assert "DANK_MEDIA_PUBLIC_BASE_URL is required" in router
    assert "DANK_TORRENT_STREAM_SECRET is required" in router
    assert "media_server_ready()" in router
    assert "lease_key=lease_key" in router
    assert "await manager.release_lease(" in router


def _shared_session(tmp_path: Path, *, token: str = "shared") -> TorrentStreamSession:
    root = tmp_path / token
    root.mkdir(parents=True, exist_ok=True)
    return TorrentStreamSession(
        token=token,
        secret="secret",
        owner_id=1,
        guild_id=1,
        source_kind="magnet",
        source_identity="btih:shared",
        save_root=root,
        handle=_FakeHandle(),
        info=object(),
        file_index=0,
        file_path="movie.mp4",
        file_name="movie.mp4",
        file_size=1024 * 1024 * 1024,
        file_offset=0,
        piece_length=1024 * 1024,
        first_piece=0,
        last_piece=1023,
        created_at=0.0,
        last_access=0.0,
    )


def test_dynamic_capacity_uses_current_rss_and_protected_reserve(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.max_sessions = 4
    manager.soft_session_limit = 2
    manager.process_memory_limit_mb = 1495
    manager.protected_memory_reserve_mb = 350
    manager.estimated_session_memory_mb = 96
    manager.disk_reserve_bytes = 0
    monkeypatch.setattr(torrent_streaming, "current_rss_mb", lambda: 390.0)

    snap = manager.capacity_snapshot()
    assert snap.admission_allowed
    assert snap.current_rss_mb == 390.0
    assert snap.process_limit_mb == 1495
    assert snap.protected_reserve_mb == 350
    assert snap.memory_headroom_mb == pytest.approx(755.0)
    assert snap.memory_slots_available == 7
    assert snap.session_slots_available == 2


def test_dynamic_capacity_rejects_before_core_bot_reserve_is_crossed(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.max_sessions = 4
    manager.soft_session_limit = 2
    manager.process_memory_limit_mb = 1495
    manager.protected_memory_reserve_mb = 350
    manager.estimated_session_memory_mb = 96
    manager.disk_reserve_bytes = 0
    monkeypatch.setattr(torrent_streaming, "current_rss_mb", lambda: 1100.0)

    snap = manager.capacity_snapshot()
    assert not snap.admission_allowed
    assert snap.memory_slots_available == 0
    assert "protected memory reserve" in snap.blocker.lower()

    with pytest.raises(RuntimeError, match="protected memory reserve"):
        asyncio.run(manager._reserve_start())


def test_disk_admission_reserves_space_for_existing_and_new_media(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.max_sessions = 4
    manager.soft_session_limit = 4
    manager.disk_reserve_bytes = 2 * 1024 * 1024 * 1024
    monkeypatch.setattr(torrent_streaming, "current_rss_mb", lambda: 200.0)
    monkeypatch.setattr(
        torrent_streaming.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(
            total=20 * 1024 * 1024 * 1024,
            used=17 * 1024 * 1024 * 1024,
            free=3 * 1024 * 1024 * 1024,
        ),
    )

    with pytest.raises(RuntimeError, match="disk admission rejected"):
        manager._assert_disk_capacity(2 * 1024 * 1024 * 1024)


def test_identical_torrent_reuses_one_session_and_adds_room_lease(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    session = _shared_session(tmp_path)
    session.leases.add("movie:1:10")
    manager._sessions[session.token] = session
    manager._identity_index[session.source_identity] = session.token

    reused = asyncio.run(
        manager._reuse_session(
            "btih:shared",
            lease_key="movie:2:20",
        )
    )
    assert reused is session
    assert session.leases == {"movie:1:10", "movie:2:20"}
    assert session.reuse_hits == 1

    status = manager.capacity_status()
    assert status["active_unique_sessions"] == 1
    assert status["total_leases"] == 2
    assert status["shared_sessions"] == 1


def test_releasing_one_room_does_not_delete_shared_torrent(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    session = _shared_session(tmp_path)
    session.leases.update({"movie:1:10", "movie:2:20"})
    manager._sessions[session.token] = session
    manager._identity_index[session.source_identity] = session.token

    released = asyncio.run(
        manager.release_lease(
            session.token,
            "movie:1:10",
            remove_if_unused=True,
        )
    )
    assert released
    assert session.token in manager._sessions
    assert session.leases == {"movie:2:20"}

    released = asyncio.run(
        manager.release_lease(
            session.token,
            "movie:2:20",
            remove_if_unused=True,
        )
    )
    assert released
    assert session.token not in manager._sessions
    assert session.source_identity not in manager._identity_index


def test_untracked_share_router_consumer_prevents_movie_release_from_deleting_session(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    session = _shared_session(tmp_path)
    session.leases.add("movie:1:10")
    session.unleased_hold = True
    manager._sessions[session.token] = session
    manager._identity_index[session.source_identity] = session.token

    released = asyncio.run(
        manager.release_lease(
            session.token,
            "movie:1:10",
            remove_if_unused=True,
        )
    )
    assert released
    assert session.token in manager._sessions
    assert session.leases == set()
    assert session.unleased_hold



def test_in_flight_start_reserves_estimated_memory_before_rss_moves(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.max_sessions = 4
    manager.soft_session_limit = 4
    manager.process_memory_limit_mb = 700
    manager.protected_memory_reserve_mb = 350
    manager.estimated_session_memory_mb = 96
    manager.disk_reserve_bytes = 0
    manager._starting = 1
    monkeypatch.setattr(torrent_streaming, "current_rss_mb", lambda: 250.0)

    # Raw headroom is 100 MiB, which looks like one slot until the already
    # admitted in-flight start reserves its estimated 96 MiB.
    snap = manager.capacity_snapshot()
    assert snap.memory_headroom_mb == pytest.approx(100.0)
    assert snap.memory_slots_available == 0
    assert not snap.admission_allowed



def test_per_guild_unique_limit_prevents_one_server_consuming_all_slots(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.max_sessions = 4
    manager.soft_session_limit = 4
    manager.max_unique_per_guild = 1
    manager.disk_reserve_bytes = 0
    monkeypatch.setattr(torrent_streaming, "current_rss_mb", lambda: 200.0)

    session = _shared_session(tmp_path, token="guild-one")
    session.source_identity = "btih:guild-one"
    session.leases.add("movie:1:10")
    session.lease_guild_ids["movie:1:10"] = 1
    manager._sessions[session.token] = session
    manager._identity_index[session.source_identity] = session.token

    guild_one = manager.capacity_snapshot(guild_id=1)
    assert not guild_one.admission_allowed
    assert guild_one.guild_unique_sessions == 1
    assert guild_one.guild_slots_available == 0
    assert "this server reached" in guild_one.blocker.lower()

    guild_two = manager.capacity_snapshot(guild_id=2)
    assert guild_two.admission_allowed
    assert guild_two.guild_unique_sessions == 0
    assert guild_two.guild_slots_available == 1


def test_existing_identical_torrent_can_be_shared_even_when_guild_unique_limit_is_full(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.max_sessions = 4
    manager.soft_session_limit = 4
    manager.max_unique_per_guild = 1
    manager.disk_reserve_bytes = 0
    monkeypatch.setattr(torrent_streaming, "current_rss_mb", lambda: 200.0)

    existing = _shared_session(tmp_path, token="existing")
    existing.leases.add("movie:1:10")
    existing.lease_guild_ids["movie:1:10"] = 1
    manager._sessions[existing.token] = existing
    manager._identity_index[existing.source_identity] = existing.token

    reused = asyncio.run(
        manager._reuse_session(
            existing.source_identity,
            lease_key="movie:1:20",
            guild_id=1,
        )
    )
    assert reused is existing
    assert existing.leases == {"movie:1:10", "movie:1:20"}
    assert existing.lease_guild_ids["movie:1:20"] == 1
    assert len(manager._sessions) == 1


def test_configured_session_memory_estimate_is_admission_floor(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.estimated_session_memory_mb = 96
    manager._adaptive_session_memory_mb = 32.0
    manager._session_memory_samples = 3

    assert manager._effective_session_memory_mb() == 96


def test_session_memory_estimate_learns_from_clean_rss_delta(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.max_sessions = 4
    manager.soft_session_limit = 4
    manager._adaptive_session_memory_mb = 96.0
    manager._session_memory_samples = 0
    manager._starting = 1
    monkeypatch.setattr(torrent_streaming, "current_rss_mb", lambda: 520.0)

    manager._record_session_memory_observation(400.0)

    assert manager._session_memory_samples == 1
    # 75% of the 96 MiB prior + 25% of the observed 120 MiB delta.
    assert manager._effective_session_memory_mb() == 102

    snap = manager.capacity_snapshot()
    assert snap.estimated_session_mb == 102
    assert snap.memory_samples == 1


def test_overlapping_starts_do_not_pollute_adaptive_memory_estimate(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager._adaptive_session_memory_mb = 96.0
    manager._session_memory_samples = 0
    manager._starting = 2
    monkeypatch.setattr(torrent_streaming, "current_rss_mb", lambda: 700.0)

    manager._record_session_memory_observation(400.0)

    assert manager._session_memory_samples == 0
    assert manager._effective_session_memory_mb() == 96



def test_per_guild_in_flight_start_reserves_the_guild_slot(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.max_sessions = 4
    manager.soft_session_limit = 4
    manager.max_unique_per_guild = 1
    manager.disk_reserve_bytes = 0
    monkeypatch.setattr(torrent_streaming, "current_rss_mb", lambda: 200.0)

    asyncio.run(manager._reserve_start(guild_id=77))
    assert manager._starting_by_guild[77] == 1

    snap = manager.capacity_snapshot(guild_id=77)
    assert not snap.admission_allowed
    assert snap.guild_unique_sessions == 0
    assert snap.guild_slots_available == 0
    assert "this server reached" in snap.blocker.lower()

    with pytest.raises(RuntimeError, match="This server reached"):
        asyncio.run(manager._reserve_start(guild_id=77))

    other = manager.capacity_snapshot(guild_id=88)
    assert other.guild_slots_available == 1

    asyncio.run(manager._release_start(guild_id=77))
    assert 77 not in manager._starting_by_guild



def test_adaptive_playback_state_is_isolated_per_viewer(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.bootstrap_bytes = 8 * 1024 * 1024
    handle = _FakeHandle()
    handle.download_rate = 8 * 1024 * 1024
    session = _shared_session(tmp_path, token="viewer-isolation")
    session.handle = handle

    first_end = 1024 * 1024 - 1
    viewer_a_first = manager.prepare_playback_request(
        session,
        0,
        first_end,
        consumer_key="viewer-a",
    )
    viewer_b_first = manager.prepare_playback_request(
        session,
        200 * 1024 * 1024,
        201 * 1024 * 1024 - 1,
        consumer_key="viewer-b",
    )
    viewer_a_seek = manager.prepare_playback_request(
        session,
        200 * 1024 * 1024,
        201 * 1024 * 1024 - 1,
        consumer_key="viewer-a",
    )

    assert viewer_a_first.seek is False
    assert viewer_b_first.seek is False
    assert viewer_b_first.startup_wait_end > 201 * 1024 * 1024 - 1
    assert viewer_a_seek.seek is True
    assert set(session.consumer_playback) == {"viewer-a", "viewer-b"}


def test_viewer_consumer_id_is_signed_into_stream_url(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager.public_base_url = "https://media.example.com"
    manager.stream_secret = "stream-secret"
    session = _shared_session(tmp_path, token="signed-viewer")
    manager._sessions[session.token] = session

    url = manager.stream_url(
        session,
        ttl_seconds=600,
        consumer_key="movie:20:page-abc",
    )
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)

    assert query["cid"] == ["movie:20:page-abc"]
    assert asyncio.run(
        manager.validate_stream_access(
            session.token,
            query["exp"][0],
            query["sig"][0],
            query["cid"][0],
        )
    )
    assert not asyncio.run(
        manager.validate_stream_access(
            session.token,
            query["exp"][0],
            query["sig"][0],
            "movie:99:other-page",
        )
    )


def test_cast_cors_headers_allow_only_google_receiver_origins() -> None:
    allowed = SimpleNamespace(headers={"Origin": "https://www.gstatic.com"})
    nested = SimpleNamespace(headers={"Origin": "https://cast.gstatic.com"})
    denied = SimpleNamespace(headers={"Origin": "https://evil.example.com"})
    missing = SimpleNamespace(headers={})

    assert _cast_cors_headers(allowed)["Access-Control-Allow-Origin"] == "https://www.gstatic.com"
    assert _cast_cors_headers(nested)["Access-Control-Allow-Origin"] == "https://cast.gstatic.com"
    assert _cast_cors_headers(denied) == {}
    assert _cast_cors_headers(missing) == {}
    assert "Range" in _cast_cors_headers(allowed)["Access-Control-Allow-Headers"]
