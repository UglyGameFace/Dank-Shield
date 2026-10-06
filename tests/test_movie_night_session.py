from __future__ import annotations

import asyncio

from stoney_verify import movie_night_session
from stoney_verify.movie_night import MovieNightManager


class _FakeTorrentManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    async def release_lease(
        self,
        token: str,
        lease_key: str,
        *,
        remove_if_unused: bool = True,
    ) -> bool:
        self.calls.append((token, lease_key, remove_if_unused))
        return True


def test_terminate_movie_night_releases_room_lease_and_clears_media_state(monkeypatch) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
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
        title="Example",
        now=100.0,
    )
    room.queue.append(candidate.candidate_id)
    room.approved_search_query = "Example"

    torrents = _FakeTorrentManager()
    monkeypatch.setattr(movie_night_session, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(movie_night_session, "get_torrent_manager", lambda: torrents)

    result = asyncio.run(movie_night_session.terminate_movie_night_room(room))

    assert result.had_stream
    assert result.lease_released
    assert result.cleanup_error == ""
    assert torrents.calls == [("torrent-token", "movie:1:2", True)]
    assert room.ended
    assert room.playback_state == "ended"
    assert room.stream_token == ""
    assert room.current_candidate_id == ""
    assert room.current_variant_id == ""
    assert room.approved_search_query == ""
    assert room.queue == []
    assert room.candidates == {}
    assert manager.active_room_for_channel(1, 2) is None
    assert manager.get(room.room_id) is None


def test_terminate_movie_night_is_idempotent_without_a_stream(monkeypatch) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        now=100.0,
    )
    torrents = _FakeTorrentManager()
    monkeypatch.setattr(movie_night_session, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(movie_night_session, "get_torrent_manager", lambda: torrents)

    first = asyncio.run(movie_night_session.terminate_movie_night_room(room))
    second = asyncio.run(movie_night_session.terminate_movie_night_room(room))

    assert not first.had_stream
    assert first.lease_released
    assert not second.had_stream
    assert second.lease_released
    assert torrents.calls == []


def test_web_player_has_terminal_state_before_missing_room_fallback() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "stoney_verify"
        / "movie_night_web.py"
    ).read_text(encoding="utf-8")

    assert '<button class="host-action danger" id="end"' in source
    assert 'id="endLabel">End Session</span>' in source
    assert 'hostAction("end")' in source
    assert 'if(s.ended) {' in source
    assert 'terminated=true;' in source
    assert 'Dank Cinema session not found' in source
    assert 'if(terminated) return false;' in source
    assert 'if(terminated) return null;' in source


def test_inactive_room_cleanup_releases_media_and_keeps_fresh_room(monkeypatch) -> None:
    manager = MovieNightManager(
        viewer_ttl_seconds=35,
        empty_room_ttl_seconds=300,
    )
    stale = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="stale-token",
        now=-1_000_000.0,
    )
    fresh = manager.create_room(
        guild_id=1,
        channel_id=3,
        host_id=20,
        stream_token="fresh-token",
    )
    torrents = _FakeTorrentManager()
    monkeypatch.setattr(movie_night_session, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(movie_night_session, "get_torrent_manager", lambda: torrents)

    cleaned = asyncio.run(movie_night_session.cleanup_inactive_movie_night_rooms())

    assert cleaned == 1
    assert manager.get(stale.room_id) is None
    assert manager.get(fresh.room_id) is fresh
    assert torrents.calls == [("stale-token", "movie:1:2", True)]


def test_movie_night_cleanup_task_is_registered_with_public_routes() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "stoney_verify"
        / "movie_night_web.py"
    ).read_text(encoding="utf-8")

    assert "ensure_movie_night_cleanup_task()" in source
