from __future__ import annotations

import asyncio
from types import SimpleNamespace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from aiohttp import web

from stoney_verify import movie_night_web
from stoney_verify.movie_night import MovieNightManager


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
    assert "Playback will stay put while the buffer catches up." in html
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



def test_movie_night_viewer_sync_is_explicit_and_drift_safe() -> None:
    html = movie_night_web._watch_html(
        "room-sync",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert "CLIENT_SESSION_ID" in html
    assert "client_session_id:CLIENT_SESSION_ID" in html
    assert "sync_requested:!!(syncRequested||forceSync)" in html
    assert "syncButton.onclick=async()" in html
    assert "heartbeat(true)" in html
    assert "syncGestureGranted=true" in html
    assert "safeSeek(joinTarget)" in html
    assert "SOFT_DRIFT_START=0.35" in html
    assert "HARD_DRIFT_SECONDS=5.0" in html
    assert "video.playbackRate=signed<0?1.04:0.96" in html
    assert "if(drift>1.75" not in html
    assert "if(Math.abs((video.currentTime||0)-Number(lastState.position_seconds||0))>0.5)" not in html


def test_movie_night_native_viewer_play_counts_as_sync_gesture() -> None:
    html = movie_night_web._watch_html(
        "room-sync",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert 'video.addEventListener("play",()=>{' in html
    assert "if(remoteApply) return;" in html
    assert "syncGestureGranted=true;" in html
    assert "syncRequested=true;" in html
    assert "heartbeat(true);" in html


def test_movie_night_joining_viewer_is_not_poll_seeked_every_two_seconds() -> None:
    html = movie_night_web._watch_html(
        "room-sync",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert 'else if(s.sync_status==="joining")' in html
    assert "JOIN_RETARGET_SECONDS=12.0" in html
    assert "JOIN_RETARGET_COOLDOWN_MS=10000" in html
    assert "HARD_SEEK_COOLDOWN_MS=8000" in html
    assert "correctSyncedDrift(target)" in html
    assert "if(drift>1.75" not in html



def test_movie_night_stream_reloads_when_signed_consumer_changes() -> None:
    html = movie_night_web._watch_html(
        "room-sync",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert 'let lastStreamConsumer=""' in html
    assert 'const streamConsumer=String(s.stream_consumer||"")' in html
    assert 'streamConsumer!==lastStreamConsumer' in html
    assert 'lastStreamConsumer=streamConsumer' in html


def test_movie_night_refresh_keeps_same_client_session_and_backoff_retry() -> None:
    html = movie_night_web._watch_html(
        "room-refresh",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert "window.sessionStorage.getItem(key)" in html
    assert "window.sessionStorage.setItem(key,created)" in html
    assert 'const CLIENT_SESSION_ID=loadClientSessionId();' in html
    assert "function scheduleStreamRetry()" in html
    assert "streamRetryTimer!==null" in html
    assert "Math.min(15000,2500*Math.pow(1.6,step))" in html
    assert 'video.addEventListener("waiting"' in html
    assert 'video.addEventListener("stalled"' in html
    assert 'setTimeout(()=>{{ video.src=lastState.stream_url; video.load(); }},2500);' not in html


def test_swarm_display_falls_back_to_selected_release_when_live_swarm_is_transiently_zero() -> None:
    class _Variant:
        swarm_health = {
            "seeds": 153,
            "leechers": 6,
            "peers": 159,
        }

    fallback = movie_night_web._swarm_display(
        {"seeds": 0, "leechers": 0, "peers": 0},
        _Variant(),
    )
    assert fallback == {
        "seeds": 153,
        "leechers": 6,
        "peers": 159,
        "source": "provider",
    }

    live = movie_night_web._swarm_display(
        {"seeds": 11, "leechers": 3, "peers": 14},
        _Variant(),
    )
    assert live == {
        "seeds": 11,
        "leechers": 3,
        "peers": 14,
        "source": "live",
    }


def test_movie_night_player_labels_seed_leech_source() -> None:
    html = movie_night_web._watch_html(
        "room-swarm",
        456,
        "uid=456&exp=9999999999&sig=test",
    )
    assert "Seeds / Leechers" in html
    assert "t.swarm_source" in html
    assert "t.leechers" in html


def test_same_session_refresh_warmup_preserves_existing_viewer_telemetry() -> None:
    viewer = type(
        "Viewer",
        (),
        {
            "client_session_id": "same-tab",
            "position_seconds": 321.5,
            "buffered_until_seconds": 339.0,
            "media_duration_seconds": 7200.0,
            "paused": False,
        },
    )()

    result = movie_night_web._preserve_refresh_telemetry(
        viewer,
        position=0.0,
        duration=0.0,
        buffered=0.0,
        paused=True,
        client_session_id="same-tab",
        has_stream=True,
    )
    assert result == (321.5, 7200.0, 339.0, False, True)


def test_new_session_refresh_warmup_does_not_inherit_old_viewer_telemetry() -> None:
    viewer = type(
        "Viewer",
        (),
        {
            "client_session_id": "old-tab",
            "position_seconds": 321.5,
            "buffered_until_seconds": 339.0,
            "media_duration_seconds": 7200.0,
            "paused": False,
        },
    )()

    result = movie_night_web._preserve_refresh_telemetry(
        viewer,
        position=0.0,
        duration=0.0,
        buffered=0.0,
        paused=True,
        client_session_id="new-tab",
        has_stream=True,
    )
    assert result == (0.0, 0.0, 0.0, True, False)


def test_state_marks_reclaimed_media_without_ending_room(monkeypatch) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="expired-media-token",
    )

    class _MissingTorrentManager:
        async def get(self, token: str):
            assert token == "expired-media-token"
            return None

    monkeypatch.setattr(movie_night_web, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(
        movie_night_web,
        "get_torrent_manager",
        lambda: _MissingTorrentManager(),
    )

    payload = asyncio.run(movie_night_web._state_payload(room, 10))

    assert payload["ended"] is False
    assert payload["media_missing"] is True
    assert payload["stream_url"] == ""


def test_player_explains_reclaimed_media_instead_of_saying_no_movie_chosen() -> None:
    html = movie_night_web._watch_html(
        "room-expired-media",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert "if(s.media_missing)" in html
    assert "media session expired or was reclaimed" in html
    assert "room is still active" in html
    assert "choose the release again" in html


def test_private_watch_room_rejects_non_owner_identity(monkeypatch) -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        mode="private",
    )
    monkeypatch.setattr(movie_night_web, "get_movie_night_manager", lambda: manager)

    monkeypatch.setattr(
        movie_night_web,
        "_request_identity",
        lambda request: (room.room_id, 20),
    )
    try:
        asyncio.run(movie_night_web._room_and_user(SimpleNamespace()))
    except web.HTTPForbidden as exc:
        assert "private" in exc.text.lower()
    else:
        raise AssertionError("non-owner unexpectedly accessed a private Watch room")

    monkeypatch.setattr(
        movie_night_web,
        "_request_identity",
        lambda request: (room.room_id, 10),
    )
    resolved_room, resolved_uid = asyncio.run(
        movie_night_web._room_and_user(SimpleNamespace())
    )
    assert resolved_room is room
    assert resolved_uid == 10


def test_movie_night_state_exposes_private_room_mode(monkeypatch) -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        mode="private",
    )

    class _TorrentManager:
        async def get(self, token: str):
            _ = token
            return None

    monkeypatch.setattr(movie_night_web, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(movie_night_web, "get_torrent_manager", lambda: _TorrentManager())

    payload = asyncio.run(movie_night_web._state_payload(room, 10))

    assert payload["mode"] == "private"
    assert payload["private"] is True
    assert payload["viewer_count"] == 1