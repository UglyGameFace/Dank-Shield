from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from stoney_verify.torrent_media_server import _validate_public_base_url
from stoney_verify.torrent_streaming import (
    TorrentMediaManager,
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

    def prioritize_files(self, priorities) -> None:
        self.file_priorities = list(priorities)

    def prioritize_pieces(self, updates) -> None:
        self.piece_updates.extend((int(a), int(b)) for a, b in updates)

    def have_piece(self, piece: int) -> bool:
        return int(piece) in self.available

    def status(self):
        return SimpleNamespace(
            error="",
            download_rate=1234,
            upload_rate=50,
            num_peers=3,
            num_seeds=1,
            state="downloading",
        )

    def file_progress(self):
        return [0, 1024, 2048]


def _manager(monkeypatch, tmp_path: Path) -> TorrentMediaManager:
    monkeypatch.setenv("DANK_TORRENT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DANK_TORRENT_MAX_SESSIONS", "1")
    monkeypatch.setenv("DANK_TORRENT_MAX_TOTAL_BYTES", str(8 * 1024 * 1024 * 1024))
    return TorrentMediaManager(lt_module=_FakeLT())


def test_magnet_and_torrent_source_detection() -> None:
    magnet = "magnet:?xt=urn:btih:ABC123&dn=Public+Domain"
    assert find_magnet(f"watch this {magnet}") == magnet
    assert magnet_identity(magnet) == "btih:abc123"
    assert magnet_identity("https://example.com/file") == ""
    assert is_torrent_filename("movie.torrent")
    assert is_torrent_filename("MOVIE.TORRENT")
    assert not is_torrent_filename("movie.mp4")


def test_base32_and_hex_btih_normalize_to_same_identity() -> None:
    zeros_hex = "0" * 40
    zeros_base32 = "A" * 32
    assert magnet_identity(
        f"magnet:?xt=urn:btih:{zeros_hex}"
    ) == f"btih:{zeros_hex}"
    assert magnet_identity(
        f"magnet:?xt=urn:btih:{zeros_base32}"
    ) == f"btih:{zeros_hex}"


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


def test_live_session_capacity_counts_existing_sessions(monkeypatch, tmp_path: Path) -> None:
    manager = _manager(monkeypatch, tmp_path)
    manager._sessions["existing"] = object()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="1-session capacity"):
        asyncio.run(manager._reserve_start())


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
    assert "get_torrent_manager().ensure_cleanup_task()" in routes
    assert "find_magnet(" in router
    assert "is_torrent_filename(" in router
    assert "manager.start_magnet(" in router
    assert "manager.start_torrent_bytes(" in router
    assert "manager.stream_url(session)" in router
    assert "DANK_MEDIA_PUBLIC_BASE_URL is required" in router
    assert "DANK_TORRENT_STREAM_SECRET is required" in router
    assert "media_server_ready()" in router
    assert "await manager.remove(session.token)" in router
