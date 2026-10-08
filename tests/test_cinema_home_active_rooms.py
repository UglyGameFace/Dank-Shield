from __future__ import annotations

from stoney_verify import cinema_site
from stoney_verify.movie_night import MovieCandidate, MovieNightManager


def _manager_with_room(monkeypatch, *, mode: str = "watch_party"):
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=101,
        channel_id=202,
        host_id=303,
        stream_token="",
        mode=mode,
    )
    monkeypatch.setattr(cinema_site, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(
        cinema_site,
        "movie_night_watch_url",
        lambda room_id, user_id: f"https://watch.example.test/{room_id}/{user_id}",
    )
    return manager, room


def test_home_active_room_without_selected_candidate_does_not_raise(monkeypatch) -> None:
    _manager, room = _manager_with_room(monkeypatch)

    rows = cinema_site._active_rooms_payload(101, 303)

    assert len(rows) == 1
    assert rows[0]["room_id"] == room.room_id
    assert rows[0]["title"] == "Cinema session"
    assert rows[0]["tmdb_id"] == 0
    assert rows[0]["mode"] == "watch_party"


def test_home_active_room_resolves_canonical_candidate_metadata(monkeypatch) -> None:
    _manager, room = _manager_with_room(monkeypatch, mode="private")
    candidate = MovieCandidate(
        candidate_id="candidate-1",
        title="Diversity Day",
        proposer_id=303,
        created_at=1.0,
        metadata={
            "catalog": {
                "title": "The Office: Diversity Day",
                "media_type": "episode",
                "tmdb_id": 9002,
                "series_id": 2316,
                "series_title": "The Office",
                "season_number": 1,
                "episode_number": 2,
                "poster_url": "https://image.tmdb.org/t/p/w500/show.jpg",
            }
        },
    )
    room.candidates[candidate.candidate_id] = candidate
    room.current_candidate_id = candidate.candidate_id

    rows = cinema_site._active_rooms_payload(101, 303)

    assert len(rows) == 1
    assert rows[0]["title"] == "The Office: Diversity Day"
    assert rows[0]["media_type"] == "episode"
    assert rows[0]["tmdb_id"] == 9002
    assert rows[0]["series_id"] == 2316
    assert rows[0]["season_number"] == 1
    assert rows[0]["episode_number"] == 2
    assert rows[0]["mode"] == "private"
    assert rows[0]["is_host"] is True


def test_home_active_room_missing_candidate_id_falls_back_without_crashing(monkeypatch) -> None:
    _manager, room = _manager_with_room(monkeypatch)
    room.current_candidate_id = "stale-candidate"

    rows = cinema_site._active_rooms_payload(101, 303)

    assert len(rows) == 1
    assert rows[0]["title"] == "Cinema session"


def test_home_does_not_expose_inaccessible_private_room(monkeypatch) -> None:
    _manager, _room = _manager_with_room(monkeypatch, mode="private")

    assert cinema_site._active_rooms_payload(101, 999) == []
