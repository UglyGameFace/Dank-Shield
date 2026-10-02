from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from aiohttp import web

from stoney_verify import movie_night_web


def test_movie_night_watch_url_is_signed_to_room_user_and_expiry(monkeypatch) -> None:
    monkeypatch.setenv("DANK_MEDIA_PUBLIC_BASE_URL", "https://media.example.com")
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "movie-secret")

    url = movie_night_web.movie_night_watch_url("room-123", 456)
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.path == "/movie/room-123/watch"
    assert query["uid"] == ["456"]
    assert "exp" in query
    assert "sig" in query

    uid = movie_night_web._validate_access(
        "room-123",
        query["uid"][0],
        query["exp"][0],
        query["sig"][0],
    )
    assert uid == 456
    assert (
        movie_night_web._validate_access(
            "room-123",
            "457",
            query["exp"][0],
            query["sig"][0],
        )
        is None
    )


def test_movie_night_public_routes_are_media_only() -> None:
    app = web.Application()
    movie_night_web.register_movie_night_public_routes(app)
    routes = {
        (route.method, str(route.resource))
        for route in app.router.routes()
    }

    rendered = "\n".join(f"{method} {resource}" for method, resource in routes)
    assert "/movie/{room_id}/watch" in rendered
    assert "/movie/{room_id}/state" in rendered
    assert "/movie/{room_id}/heartbeat" in rendered
    assert "/movie/{room_id}/action" in rendered
    assert "/api/" not in rendered
    assert "/guild/" not in rendered


def test_movie_night_player_contains_sync_heartbeat_and_host_controls() -> None:
    html = movie_night_web._watch_html(
        "room-123",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert "<video" in html
    assert "playsinline" in html
    assert "/heartbeat" in html
    assert "/state" in html
    assert "/action" in html
    assert 'hostAction("resume")' in html
    assert 'hostAction("pause")' in html
    assert "bufferedEnd()" in html
    assert "Tap to Sync" in html
    assert "Buffering the group for smoother playback" in html
    assert "Joining Movie Night" in html
    assert "without pausing the room" in html
    assert 's.sync_status==="joining"' in html
    assert "Synced Viewer" in html


def test_public_media_server_registers_movie_night_without_admin_api() -> None:
    root = Path(__file__).resolve().parents[1]
    server = (root / "stoney_verify" / "torrent_media_server.py").read_text(
        encoding="utf-8"
    )
    player = (root / "stoney_verify" / "movie_night_web.py").read_text(
        encoding="utf-8"
    )

    assert "register_movie_night_public_routes(app)" in server
    assert "register_torrent_public_routes(app)" in server
    assert "register_torrent_admin_routes" not in server
    assert 'ttl_seconds=21600' in player
    assert "Content-Security-Policy" in player
    assert "frame-ancestors 'none'" in player
