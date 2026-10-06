from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from aiohttp import web

from stoney_verify import cinema_library_service, movie_night_web
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
    assert "/movie/{room_id}/invite-options" in rendered
    assert "/movie/{room_id}/promote" in rendered
    assert "/movie/{room_id}/queue-search" in rendered
    progress_methods = {
        method
        for method, resource in routes
        if "/movie/{room_id}/progress" in resource
    }
    assert {"GET", "POST"} <= progress_methods
    assert "/api/" not in rendered
    assert "/guild/" not in rendered





def test_cinema_library_runtime_defaults_and_episode_lookup(monkeypatch) -> None:
    defaults = cinema_library_service._normalize_preferences({})
    assert defaults == {
        "autoplay_next": True,
        "playback_speed": 1.0,
        "preferred_source": "",
        "default_audio_language": "",
        "default_subtitle_language": "",
        "visual_quality": "auto",
        "feed_notification_mode": "instant",
        "feed_playable_only": True,
        "feed_min_seeds": 0,
        "feed_preferred_resolutions": [],
        "feed_preferred_codecs": [],
        "feed_preferred_languages": [],
        "feed_queue_suggestions": True,
    }
    assert issubclass(cinema_library_service.InvalidCinemaState, ValueError)

    async def rows(_user_id: int, *, refresh: bool = False):
        assert refresh is False
        return [
            {
                "user_id": 42,
                "media_type": "episode",
                "tmdb_id": 9001,
                "season_number": 3,
                "episode_number": 7,
                "progress_seconds": 733.0,
            },
            {
                "user_id": 42,
                "media_type": "movie",
                "tmdb_id": 123,
                "season_number": 0,
                "episode_number": 0,
            },
        ]

    monkeypatch.setattr(cinema_library_service, "list_user_media", rows)
    state = asyncio.run(
        cinema_library_service.get_media_state(
            42,
            media_type="episode",
            tmdb_id=9001,
            season_number=3,
            episode_number=7,
        )
    )
    assert state is not None
    assert state["progress_seconds"] == 733.0


def test_movie_night_progress_get_uses_current_canonical_media(monkeypatch) -> None:
    candidate = SimpleNamespace(
        title="Example Show S03E07",
        metadata={
            "catalog": {
                "media_type": "episode",
                "tmdb_id": 9001,
                "series_id": 77,
                "series_title": "Example Show",
                "season_number": 3,
                "episode_number": 7,
                "title": "Example Show S03E07",
            }
        },
    )
    room = SimpleNamespace(
        current_candidate_id="candidate-1",
        candidates={"candidate-1": candidate},
    )

    async def room_and_user(_request):
        return room, 42

    async def media_state(user_id: int, **kwargs):
        assert user_id == 42
        assert kwargs == {
            "media_type": "episode",
            "tmdb_id": 9001,
            "season_number": 3,
            "episode_number": 7,
        }
        return {
            "media_type": "episode",
            "tmdb_id": 9001,
            "season_number": 3,
            "episode_number": 7,
            "progress_seconds": 733.0,
            "completed": False,
        }

    monkeypatch.setattr(movie_night_web, "_room_and_user", room_and_user)
    monkeypatch.setattr(movie_night_web, "get_media_state", media_state)
    response = asyncio.run(
        movie_night_web.movie_night_progress(SimpleNamespace(method="GET"))
    )
    payload = json.loads(response.text)

    assert payload["tracked"] is True
    assert payload["item"]["progress_seconds"] == 733.0


def test_movie_night_player_restores_progress_only_through_host_authority() -> None:
    html = movie_night_web._watch_html(
        "room-progress",
        42,
        "uid=42&exp=9999999999&sig=test",
    )

    assert "async function maybeRestoreWatchProgress(s)" in html
    assert "if(!s?.is_host || !s?.stream_url) return;" in html
    assert 'const saved=await jsonFetch("/movie/"+BOOT.roomId+"/progress")' in html
    assert 'await hostAction("seek",{seconds:target})' in html
    assert 'video.addEventListener("seeked",()=>{' in html
    assert "scheduleHostSeekCommit();" in html
    assert "hostSeekCommitTimer=setTimeout" in html
    assert "persistWatchProgress(true)" in html
    assert "if(key!==lastProgressMediaKey) lastProgressPersistAt=0;" in html


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
    assert "const ranges=video.buffered" in html
    assert "ranges.start(i)" in html
    assert "ranges.end(i)" in html
    assert "return video.buffered.end(video.buffered.length-1)" not in html
    assert "Tap to Sync" in html
    assert "syncButton.hidden=!!s.is_host" in html
    assert "Buffering the group for smoother playback" in html
    assert 'const joiningLabel=s.private?"Private Session":"Watch Party"' in html
    assert '"Joining "+joiningLabel+"… buffering around "' in html
    assert "Playback will stay put while the buffer catches up." in html
    assert 's.sync_status==="joining"' in html
    assert 'return "Synced"' in html
    assert "<summary>Advanced Stream Details</summary>" in html
    assert 'class="theater"' in html
    assert html.index("<video") < html.index("<summary>Advanced Stream Details</summary>")


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
    assert "canonicalPlaybackRate()*1.04" in html
    assert "canonicalPlaybackRate()*0.96" in html
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
    assert "Private Session is still active" in html
    assert "Watch Party is still active" in html
    assert "choose the release again" in html


def test_private_watch_room_allows_invited_identity_and_rejects_uninvited(monkeypatch) -> None:
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
        raise AssertionError("uninvited user unexpectedly accessed a private Watch room")

    manager.invite_private_viewer(room.room_id, host_id=10, user_id=20)
    resolved_room, resolved_uid = asyncio.run(
        movie_night_web._room_and_user(SimpleNamespace())
    )
    assert resolved_room is room
    assert resolved_uid == 20


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
    assert payload["title"] == "Private Session"
    assert payload["movie"]["title"] == "Private Session"
    assert payload["viewer_count"] == 1
    html = movie_night_web._watch_html(
        room.room_id,
        10,
        "uid=10&exp=9999999999&sig=test",
    )
    assert "Dank Cinema Private Session" in html
    assert "Hosted by You" in html
    assert "End this Private Session and release its media?" in html
    assert 'privateMode?"Private Room":"Watch Party"' in html
    assert 's.is_host?"Hosted by You"' in html
    assert 'privateMode?"End Private Session":"End Movie Night"' in html
    assert 'privateMode?"Private Session":"Watch Party"' in html
    assert 'pauseLabel.textContent=Number(s.viewer_count||0)>1?"Pause Private Room":"Pause"' in html
    assert 'pauseLabel.textContent="Pause for Everyone"' in html


def test_watch_state_flips_host_authority_without_new_room(monkeypatch) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
    )
    manager.join_room(room.room_id, user_id=20)
    monkeypatch.setattr(movie_night_web, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(
        movie_night_web,
        "get_torrent_manager",
        lambda: SimpleNamespace(),
    )

    before_old = asyncio.run(movie_night_web._state_payload(room, 10))
    before_new = asyncio.run(movie_night_web._state_payload(room, 20))
    assert before_old["is_host"] is True
    assert before_new["is_host"] is False

    manager.transfer_host(
        room.room_id,
        current_host_id=10,
        new_host_id=20,
    )

    after_old = asyncio.run(movie_night_web._state_payload(room, 10))
    after_new = asyncio.run(movie_night_web._state_payload(room, 20))
    assert after_old["is_host"] is False
    assert after_new["is_host"] is True
    assert after_old["room_id"] == after_new["room_id"] == room.room_id



def test_dank_cinema_player_matches_mobile_theater_contract() -> None:
    html = movie_night_web._watch_html(
        "room-design",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert "Dank Cinema • The 420 Lobby" in html
    assert 'class="brand-art"' in html
    assert 'class="brand-lockup-art"' in html
    assert '/movie/assets/dank-cinema-brand.webp?v=art-system-v5' in html
    assert '<link rel="icon" type="image/webp" href="/movie/assets/dank-cinema-brand-mark.webp?v=art-system-v5">' in html
    assert '<img src="/movie/assets/dank-cinema-brand-mark.webp?v=' not in html
    assert '/movie/assets/dank-cinema-brand-wordmark.webp?v=' not in html
    assert 'alt="Dank Cinema — A feature of The 420 Lobby"' in html
    assert 'class="wordmark"' not in html
    assert "family=Lacquer" not in html
    assert 'id="videoStage"' in html
    assert 'screen.orientation.lock("landscape")' in html
    assert 'screen.orientation.unlock()' in html
    assert 'id="cast"' in html
    assert "cast_sender.js?loadCastFramework=1" in html
    assert "history.replaceState" not in html
    assert "DEFAULT_MEDIA_RECEIVER_APP_ID" in html
    assert "requestSession()" in html
    assert "session.loadMedia(request)" in html
    assert "lastState.cast_stream_url" in html
    assert "CAST_STATE_CHANGED" in html
    assert "NO_DEVICES_AVAILABLE" in html
    assert "video.remote.watchAvailability" in html
    assert "remotePlaybackAvailable" in html
    assert 'castButton.classList.toggle("unavailable"' in html
    assert "No compatible casting device is currently available in this browser." in html
    assert 'controlslist="nodownload"' in html
    assert '<video id="video" controls' not in html
    assert '<video id="video" playsinline preload="metadata" controls>' not in html
    assert 'event=>event.preventDefault()' in html
    assert "Stream Health:" in html
    assert "<summary>Advanced Stream Details</summary>" in html
    assert "Fullscreen" in html
    assert "Pause for Everyone" in html
    assert "End Private Session" in html
    assert "Pass Host" in html
    assert "Manage Queue" in html
    assert "Cinema notifications are managed" not in html
    assert "Movie discovery and your saved Cinema controls" not in html


def test_candidate_web_metadata_allows_only_tmdb_artwork() -> None:
    candidate = SimpleNamespace(
        title="Example Movie",
        metadata={
            "year": 2026,
            "overview": "Example overview",
            "poster_url": "https://image.tmdb.org/t/p/w342/example.jpg",
        },
    )
    metadata = movie_night_web._candidate_web_metadata(candidate)
    assert metadata == {
        "title": "Example Movie",
        "year": 2026,
        "overview": "Example overview",
        "poster_url": "https://image.tmdb.org/t/p/w342/example.jpg",
        "backdrop_url": "",
        "media_type": "",
        "tmdb_id": 0,
        "series_id": 0,
        "series_title": "",
        "season_number": 0,
        "episode_number": 0,
        "episode_title": "",
        "adult": False,
        "runtime_minutes": 0,
    }

    candidate.metadata["poster_url"] = "https://example.invalid/poster.jpg"
    assert movie_night_web._candidate_web_metadata(candidate)["poster_url"] == ""


def test_candidate_web_metadata_reads_canonical_catalog_envelope() -> None:
    candidate = SimpleNamespace(
        title="Release-ish fallback title",
        metadata={
            "search_query": "Terrifier 3",
            "catalog": {
                "catalog_provider": "tmdb",
                "catalog_id": "1034541",
                "title": "Terrifier 3",
                "year": 2024,
                "overview": "Art the Clown returns.",
                "poster_url": "https://image.tmdb.org/t/p/w342/terrifier3.jpg",
                "backdrop_url": "https://image.tmdb.org/t/p/w780/terrifier3-bg.jpg",
            },
        },
    )

    assert movie_night_web._candidate_web_metadata(candidate) == {
        "title": "Terrifier 3",
        "year": 2024,
        "overview": "Art the Clown returns.",
        "poster_url": "https://image.tmdb.org/t/p/w342/terrifier3.jpg",
        "backdrop_url": "https://image.tmdb.org/t/p/w780/terrifier3-bg.jpg",
        "media_type": "movie",
        "tmdb_id": 1034541,
        "series_id": 0,
        "series_title": "",
        "season_number": 0,
        "episode_number": 0,
        "episode_title": "",
        "adult": False,
        "runtime_minutes": 0,
    }


def test_candidate_web_metadata_catalog_artwork_stays_tmdb_only() -> None:
    candidate = SimpleNamespace(
        title="Example",
        metadata={
            "catalog": {
                "title": "Example",
                "year": 2026,
                "overview": "Catalog metadata.",
                "poster_url": "https://example.invalid/not-tmdb.jpg",
                "backdrop_url": "https://example.invalid/not-tmdb-bg.jpg",
            },
        },
    )

    metadata = movie_night_web._candidate_web_metadata(candidate)
    assert metadata["poster_url"] == ""
    assert metadata["backdrop_url"] == ""


def test_movie_night_watch_csp_allows_only_tmdb_remote_images(monkeypatch) -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
    )
    monkeypatch.setattr(movie_night_web, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(
        movie_night_web,
        "_request_identity",
        lambda request: (room.room_id, 10),
    )

    request = SimpleNamespace(
        match_info={"room_id": room.room_id},
        query={"uid": "10", "exp": "9999999999", "sig": "test"},
    )
    response = asyncio.run(movie_night_web.movie_night_watch(request))
    csp = response.headers["Content-Security-Policy"]
    assert "img-src 'self' https://image.tmdb.org https://cdn.discordapp.com https://media.discordapp.net" in csp
    assert "script-src 'unsafe-inline' https://www.gstatic.com" in csp
    assert "connect-src 'self' https://www.gstatic.com https://*.googleapis.com" in csp
    assert "img-src *" not in csp



def test_dank_cinema_navigation_only_exposes_real_actions() -> None:
    html = movie_night_web._watch_html(
        "room-real-actions",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert 'data-nav="theater"' in html
    assert 'data-nav="queue"' in html
    assert 'data-nav="details"' in html
    assert 'data-nav="discord"' in html
    assert 'data-nav="browse"' not in html
    assert 'data-nav="my-stuff"' not in html
    assert "openDiscordRoom()" in html
    assert 'id="sessionPanel"' in html
    assert 'data-panel="chat">💬 Discord' in html
    assert 'id="discordLive"' in html
    assert 'document.getElementById("discordLive").onclick=openDiscordRoom' in html


def test_dank_cinema_hides_placeholder_art_and_fake_avatars() -> None:
    html = movie_night_web._watch_html(
        "room-real-ui",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert 'id="posterWrap" hidden' in html
    assert "posterFallback" not in html
    assert "fake-avatars" not in html
    assert "renderAvatars" not in html
    assert "A synchronized Dank Cinema session in The 420 Lobby." not in html



def test_cast_stream_uses_separate_consumer_identity() -> None:
    source = Path(movie_night_web.__file__).read_text(encoding="utf-8")
    assert 'cast_consumer_key = f"cast:' in source
    assert '"cast_stream_url": cast_stream_url' in source
    assert "consumer_key=cast_consumer_key" in source



def test_dank_cinema_player_uses_real_tmdb_backdrop_and_landscape_fullscreen() -> None:
    html = movie_night_web._watch_html(
        "room-mockup-parity",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert "--backdrop-image" in html
    assert "movie.backdrop_url||movie.poster_url" in html
    assert "video.poster=backdrop" in html
    assert 'document.getElementById("videoStage")' in html
    assert 'requestFullscreen({navigationUI:"hide"})' in html
    assert 'screen.orientation.lock("landscape")' in html
    assert "document.fullscreenElement" in html


def test_dank_cinema_cast_button_is_truthful_and_device_backed() -> None:
    html = movie_night_web._watch_html(
        "room-real-cast",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert "castContext.getCastState()" in html
    assert "CastContextEventType.CAST_STATE_CHANGED" in html
    assert "CastState.NO_DEVICES_AVAILABLE" in html
    assert "typeof castContext.requestSession" in html
    assert "video.remote.watchAvailability" in html
    assert "video.remote.prompt" in html
    assert "remotePlaybackAvailable" in html
    assert 'castButton.classList.toggle("unavailable"' in html
    assert 'castButton.setAttribute("aria-label",status.available?"Cast":"Cast unavailable")' in html
    assert "No compatible casting device is currently available in this browser." in html
    assert "webkitShowPlaybackTargetPicker" not in html



def test_dank_cinema_polling_does_not_show_broken_sync_on_one_transient_fetch() -> None:
    html = movie_night_web._watch_html(
        "room-transient-fetch",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert "stateFetchFailures+=1" in html
    assert "stateFetchFailures>=3" in html
    assert "Sync connection lost. Reconnecting…" in html
    assert 'notice.textContent="Sync error: "+message' not in html



def test_dank_cinema_approved_brand_asset_is_real_webp() -> None:
    movie_night_web._dank_cinema_brand_source_bytes.cache_clear()
    movie_night_web._dank_cinema_brand_rgba.cache_clear()
    movie_night_web._dank_cinema_brand_variant.cache_clear()
    payload = movie_night_web._dank_cinema_brand_variant("full")

    assert len(payload) > 5_000
    assert payload[:4] == b"RIFF"
    assert payload[8:12] == b"WEBP"
    assert b"ALPH" in payload
    assert payload != movie_night_web._dank_cinema_brand_source_bytes()

    response = asyncio.run(
        movie_night_web.dank_cinema_brand_asset(
            SimpleNamespace(match_info={})
        )
    )
    assert response.content_type == "image/webp"
    assert response.body == payload
    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_dank_cinema_brand_asset_route_is_registered() -> None:
    source = Path(movie_night_web.__file__).read_text(encoding="utf-8")
    assert '"/movie/assets/dank-cinema-brand.webp"' in source
    assert '"/movie/assets/dank-cinema-brand-{variant}.webp"' in source
    assert "dank_cinema_brand_asset" in source



def test_dank_cinema_player_chrome_auto_hides_and_empty_stage_toggles_it() -> None:
    html = movie_night_web._watch_html(
        "room-player-chrome",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert ".video-stage.controls-hidden .player-chrome" in html
    assert ".video-stage.controls-hidden .center-play" in html
    assert ".video-stage.controls-hidden .stage-top" in html
    assert 'videoStage.addEventListener("pointerup"' in html
    assert 'videoStage.classList.contains("controls-hidden")' in html
    assert "hidePlayerControls()" in html
    assert "schedulePlayerControlsHide(2200)" in html
    assert "cursor:none" in html


def test_dank_cinema_private_double_tap_skip_is_host_only() -> None:
    html = movie_night_web._watch_html(
        "room-private-tap-skip",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert 'id="tapSkipLeft"' in html
    assert 'id="tapSkipRight"' in html
    assert "now-lastStageTapAt<=340" in html
    assert "lastState?.private && lastState?.is_host" in html
    assert 'privateTapSkip(side==="left"?-10:10)' in html
    assert "if(!lastState?.private || !lastState?.is_host || !lastState?.stream_url)" in html


def test_discord_viewer_summaries_use_real_member_identity(monkeypatch) -> None:
    from stoney_verify import globals as globals_module

    class Avatar:
        url = "https://cdn.discordapp.com/avatars/20/example.webp"

    host = SimpleNamespace(
        id=10,
        display_name="Host Person",
        display_avatar=Avatar(),
    )
    viewer = SimpleNamespace(
        id=20,
        display_name="Viewer Person",
        display_avatar=Avatar(),
    )
    members = {10: host, 20: viewer}
    guild = SimpleNamespace(get_member=lambda uid: members.get(int(uid)))
    fake_bot = SimpleNamespace(
        get_guild=lambda guild_id: guild if int(guild_id) == 123 else None,
        get_user=lambda uid: None,
    )
    monkeypatch.setattr(globals_module, "bot", fake_bot)

    room = SimpleNamespace(guild_id=123, host_id=10)
    rows = movie_night_web._discord_viewer_summaries(room, {20, 10})

    assert [row["user_id"] for row in rows] == [10, 20]
    assert rows[0]["display_name"] == "Host Person"
    assert rows[0]["is_host"] is True
    assert rows[1]["display_name"] == "Viewer Person"
    assert rows[1]["avatar_url"].startswith("https://cdn.discordapp.com/")


def test_dank_cinema_web_host_transfer_endpoint_uses_canonical_manager(monkeypatch) -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=123,
        channel_id=456,
        host_id=10,
        stream_token="",
    )
    manager.join_room(room.room_id, user_id=20)

    async def room_and_user(_request):
        return room, 10

    async def state_payload(current_room, user_id):
        return {
            "ok": True,
            "room_id": current_room.room_id,
            "is_host": int(user_id) == int(current_room.host_id),
            "host_id": int(current_room.host_id),
        }

    async def request_json():
        return {"new_host_id": 20}

    monkeypatch.setattr(movie_night_web, "_room_and_user", room_and_user)
    monkeypatch.setattr(movie_night_web, "_state_payload", state_payload)
    monkeypatch.setattr(movie_night_web, "get_movie_night_manager", lambda: manager)

    response = asyncio.run(
        movie_night_web.movie_night_transfer_host(
            SimpleNamespace(json=request_json)
        )
    )
    payload = json.loads(response.text)

    assert room.host_id == 20
    assert payload["host_id"] == 20
    assert payload["is_host"] is False



def test_dank_cinema_web_queue_endpoint_uses_canonical_manager(monkeypatch) -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=123,
        channel_id=456,
        host_id=10,
        stream_token="",
    )
    first = manager.nominate(
        room.room_id,
        user_id=10,
        title="First",
        auto_vote=False,
    )
    second = manager.nominate(
        room.room_id,
        user_id=10,
        title="Second",
        auto_vote=False,
    )
    room.queue[:] = [first.candidate_id, second.candidate_id]

    async def room_and_user(_request):
        return room, 10

    async def state_payload(current_room, user_id):
        return {
            "ok": True,
            "queue": list(current_room.queue),
            "is_host": int(user_id) == int(current_room.host_id),
        }

    async def request_json():
        return {
            "action": "move_up",
            "candidate_id": second.candidate_id,
        }

    monkeypatch.setattr(movie_night_web, "_room_and_user", room_and_user)
    monkeypatch.setattr(movie_night_web, "_state_payload", state_payload)
    monkeypatch.setattr(movie_night_web, "get_movie_night_manager", lambda: manager)

    response = asyncio.run(
        movie_night_web.movie_night_queue_action(
            SimpleNamespace(json=request_json)
        )
    )
    payload = json.loads(response.text)

    assert payload["queue"] == [second.candidate_id, first.candidate_id]


def test_dank_cinema_queue_ui_has_real_host_management_actions() -> None:
    html = movie_night_web._watch_html(
        "room-queue-manager",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert 'id="manageQueue"' in html
    assert 'id="clearQueue"' in html
    assert '"/movie/"+BOOT.roomId+"/queue"' in html
    assert '"move_up"' in html
    assert '"move_down"' in html
    assert '"remove"' in html
    assert 'queueAction("clear")' in html



def test_dank_cinema_host_controls_can_be_reopened_after_close() -> None:
    html = movie_night_web._watch_html(
        "room-host-reopen",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert 'id="hostLauncher"' in html
    assert "let hostSheetDismissed=true" in html
    assert "let previousHostState=null" in html
    assert "previousHostState===false" in html
    assert "function openHostControls()" in html
    assert "function closeHostControls()" in html
    assert "launcher.hidden=!hostSheetDismissed" in html
    assert 'document.getElementById("hostLauncher").onclick=openHostControls' in html
    assert 'contextAction.dataset.panel="host"' in html
    assert 'else if(tab.dataset.panel==="host")' in html


def test_dank_cinema_mobile_layout_wraps_controls_instead_of_overflowing() -> None:
    html = movie_night_web._watch_html(
        "room-responsive",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert ".shell { width:min(1480px,100%); margin:0 auto; padding:0 22px 160px; overflow-x:hidden; }" in html
    assert "@media (min-width:641px) and (max-width:1079px)" in html
    assert "@media (min-width:1080px)" in html
    assert ".tab {" in html
    assert "white-space:normal;overflow-wrap:anywhere" in html
    assert ".host-actions { grid-template-columns:repeat(2,minmax(0,1fr));overflow:visible; }" in html
    assert ".quick-tabs { grid-template-columns:repeat(2,minmax(0,1fr)); }" in html
    assert ".queue-item.manageable .queue-actions { grid-column:1 / -1;justify-content:flex-end; }" in html


def test_dank_cinema_brand_is_recreated_as_transparent_header_art() -> None:
    html = movie_night_web._watch_html(
        "room-brand-flush",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert "mix-blend-mode:screen" not in html
    assert "ImageDraw.floodfill" not in html
    assert "drop-shadow(0 10px 28px rgba(0,0,0,.42))" in html
    assert "rgba(2,7,6,.88)" in html
    assert 'class="brand-art"' in html
    assert 'class="brand-lockup-art"' in html
    assert '/movie/assets/dank-cinema-brand.webp?v=art-system-v5' in html
    assert '<link rel="icon" type="image/webp" href="/movie/assets/dank-cinema-brand-mark.webp?v=art-system-v5">' in html
    assert '<img src="/movie/assets/dank-cinema-brand-mark.webp?v=' not in html
    assert '/movie/assets/dank-cinema-brand-wordmark.webp?v=' not in html

    source = Path(movie_night_web.__file__).read_text(encoding="utf-8")
    assert "ImageDraw.floodfill" in source
    assert "source.putalpha(alpha)" in source
    assert "target_width = 320" in source
    assert "target_width = 1040" in source
    assert "target_width = 1200" in source


def test_dank_cinema_center_play_uses_canonical_host_action() -> None:
    html = movie_night_web._watch_html(
        "room-center-play",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert "async function togglePlayerPlayback()" in html
    assert 'await hostAction(shouldResume?"resume":"pause")' in html
    assert 'document.getElementById("centerPlay").onclick=togglePlayerPlayback' in html
    assert 'document.getElementById("playerToggle").onclick=togglePlayerPlayback' in html
    assert "if(!lastState || !lastState.is_host) return;" in html
    assert "if(!lastState || !lastState.is_host || remoteApply) return;" not in html


def test_discord_room_context_exposes_cached_guild_channel_and_identity(monkeypatch) -> None:
    from stoney_verify import globals as globals_module

    class Avatar:
        url = "https://cdn.discordapp.com/avatars/10/example.webp"

    member = SimpleNamespace(
        id=10,
        display_name="Cinema Host",
        display_avatar=Avatar(),
    )
    channel = SimpleNamespace(id=456, name="movie-night")
    guild = SimpleNamespace(
        id=123,
        name="The 420 Lobby",
        get_member=lambda uid: member if int(uid) == 10 else None,
        get_channel=lambda cid: channel if int(cid) == 456 else None,
    )
    fake_bot = SimpleNamespace(
        get_guild=lambda gid: guild if int(gid) == 123 else None,
        get_user=lambda uid: None,
    )
    monkeypatch.setattr(globals_module, "bot", fake_bot)

    room = SimpleNamespace(guild_id=123, channel_id=456)
    context = movie_night_web._discord_room_context(room, 10)

    assert context == {
        "connected": True,
        "guild_name": "The 420 Lobby",
        "channel_name": "movie-night",
        "user_id": 10,
        "user_name": "Cinema Host",
        "avatar_url": "https://cdn.discordapp.com/avatars/10/example.webp",
    }


def test_dank_cinema_discord_integration_is_visible_on_theater_page() -> None:
    html = movie_night_web._watch_html(
        "room-discord-context",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert 'id="discordContext"' in html
    assert 'id="discordIdentityTitle"' in html
    assert 'id="discordIdentitySub"' in html
    assert "Discord linked as " in html
    assert 'guild+" • #"+channel' in html
    assert "renderDiscordContext(s)" in html
    assert 'id="discordLive"' in html
    assert 'id="discordLiveText"' in html
    assert 'document.getElementById("discordLive").onclick=openDiscordRoom' in html
    assert '"Discord • "+guild+" • #"+channel' in html



def test_watch_page_initial_placeholders_do_not_assume_watch_party_mode() -> None:
    html = movie_night_web._watch_html(
        "room-neutral-first-paint",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert 'id="roomMode">Cinema Session</span>' in html
    assert 'id="title">Cinema</h2>' in html
    assert 'id="sessionMode">Connecting…</span>' in html
    assert 'id="roomMode">Movie Night</span>' not in html


def test_public_room_fallback_title_is_watch_party(monkeypatch) -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        mode="watch_party",
    )

    class _TorrentManager:
        async def get(self, token: str):
            _ = token
            return None

    monkeypatch.setattr(movie_night_web, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(movie_night_web, "get_torrent_manager", lambda: _TorrentManager())

    payload = asyncio.run(movie_night_web._state_payload(room, 10))

    assert payload["private"] is False
    assert payload["mode"] == "watch_party"
    assert payload["title"] == "Watch Party"
    assert payload["movie"]["title"] == "Watch Party"



def test_private_watch_page_has_real_invite_to_watch_party_flow() -> None:
    html = movie_night_web._watch_html(
        "room-promote",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    assert 'id="inviteWatchParty"' in html
    assert 'id="inviteModal"' in html
    assert '"/movie/"+BOOT.roomId+"/invite-options?q="' in html
    assert '"/movie/"+BOOT.roomId+"/promote"' in html
    assert "turn this Private Session into a Watch Party" in html
    assert 'inviteWatchParty.hidden=!(s.is_host && privateMode)' in html
    assert "privateTapSkip" in html


def test_discord_invite_options_are_real_cached_non_bot_members(monkeypatch) -> None:
    from stoney_verify import globals as globals_module

    class Avatar:
        def __init__(self, url: str) -> None:
            self.url = url

    host = SimpleNamespace(
        id=10,
        bot=False,
        display_name="Host",
        name="host",
        display_avatar=Avatar("https://cdn.discordapp.com/avatars/10/host.webp"),
    )
    alice = SimpleNamespace(
        id=20,
        bot=False,
        display_name="Alice Moviefan",
        name="alice",
        display_avatar=Avatar("https://cdn.discordapp.com/avatars/20/alice.webp"),
    )
    bot_member = SimpleNamespace(
        id=30,
        bot=True,
        display_name="Movie Bot",
        name="moviebot",
        display_avatar=Avatar(""),
    )
    guild = SimpleNamespace(members=[host, alice, bot_member])
    monkeypatch.setattr(
        globals_module,
        "bot",
        SimpleNamespace(get_guild=lambda guild_id: guild if int(guild_id) == 123 else None),
    )
    room = SimpleNamespace(guild_id=123, host_id=10)

    rows = movie_night_web._discord_invite_options(room, "alice")

    assert [row["user_id"] for row in rows] == [20]
    assert rows[0]["display_name"] == "Alice Moviefan"
    assert rows[0]["avatar_url"].startswith("https://cdn.discordapp.com/")


def test_web_promote_endpoint_keeps_room_and_returns_target_watch_link(monkeypatch) -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=123,
        channel_id=456,
        host_id=10,
        stream_token="stream-token",
        mode="private",
    )
    room.playback_position = 42.0

    async def room_and_user(_request):
        return room, 10

    async def request_json():
        return {"user_id": 20}

    async def state_payload(current_room, user_id):
        return {
            "room_id": current_room.room_id,
            "mode": current_room.mode,
            "private": current_room.mode == "private",
            "is_host": int(user_id) == int(current_room.host_id),
            "position_seconds": current_room.current_position(),
        }

    async def dm_invite(_room, *, user_id, watch_url):
        assert user_id == 20
        assert "uid=20" in watch_url
        return True

    monkeypatch.setattr(movie_night_web, "_room_and_user", room_and_user)
    monkeypatch.setattr(movie_night_web, "_state_payload", state_payload)
    monkeypatch.setattr(movie_night_web, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(
        movie_night_web,
        "_discord_invite_target",
        lambda _room, user_id: (
            {
                "user_id": 20,
                "display_name": "Alice",
                "username": "alice",
                "avatar_url": "",
            }
            if int(user_id) == 20
            else None
        ),
    )
    monkeypatch.setattr(movie_night_web, "_dm_watch_party_invite", dm_invite)

    async def announce(_room, *, invitee_id):
        assert invitee_id == 20
        return True

    monkeypatch.setattr(movie_night_web, "_announce_watch_party_promotion", announce)
    monkeypatch.setattr(
        movie_night_web,
        "movie_night_watch_url",
        lambda room_id, user_id: f"https://watch.example/{room_id}?uid={user_id}",
    )

    response = asyncio.run(
        movie_night_web.movie_night_promote_watch_party(
            SimpleNamespace(json=request_json)
        )
    )
    payload = json.loads(response.text)

    assert room.mode == "watch_party"
    assert room.stream_token == "stream-token"
    assert room.playback_position == 42.0
    assert payload["private"] is False
    assert payload["invite"]["dm_sent"] is True
    assert payload["invite"]["announced"] is True
    assert payload["invite"]["display_name"] == "Alice"
    assert payload["invite"]["watch_url"].endswith("uid=20")


def test_player_layout_recovers_from_mobile_desktop_mode_resizes() -> None:
    html = movie_night_web._watch_html(
        "room-layout",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    assert "position:absolute; inset:0; z-index:0" in html
    assert "object-fit:contain" in html
    assert "contain:layout paint" in html
    assert "new ResizeObserver(()=>stabilizePlayerLayout())" in html
    assert 'window.addEventListener("resize",stabilizePlayerLayout' in html
    assert 'window.visualViewport?.addEventListener("resize",stabilizePlayerLayout' in html
    assert 'window.addEventListener("orientationchange",recoverPlayerFromViewportChange' in html
    assert 'video.addEventListener(eventName,()=>stabilizePlayerLayout())' in html
    assert "function recoverPlayerFromViewportChange()" in html
    assert 'video.addEventListener("webkitendfullscreen"' in html
    assert 'document.addEventListener("webkitfullscreenchange",handleFullscreenChange)' in html
    assert 'video.style.pointerEvents="none"' in html
    assert "videoStage.style.maxHeight=height+\"px\"" in html



def test_dank_cinema_uses_ffmpeg_aac_sidecar_without_replacing_video_clock() -> None:
    html = movie_night_web._watch_html(
        "room-audio-compat",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    assert 'id="compatAudio"' in html
    assert "function compatAudioActive()" in html
    assert "function restartCompatAudio(seconds, shouldPlay=false)" in html
    assert "function syncCompatAudio(force=false)" in html
    assert "function startCompatAudioFromGesture(seconds, keepPlaying)" in html
    assert "s?.audio_compat_required && s?.audio_compat_url" in html
    assert "video.muted=true" in html
    assert "compatAudio.volume=target" in html
    assert 'url.searchParams.set("start"' in html
    assert 'video.addEventListener("seeked",()=>{' in html
    assert "scheduleCompatAudioRestart(Number(video.currentTime||0)" in html
    assert "position_seconds:video.currentTime||0" in html


def test_viewer_sync_starts_aac_compatibility_inside_user_gesture() -> None:
    html = movie_night_web._watch_html(
        "room-aac-sync",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    sync_start = html.index("syncButton.onclick=async()=>")
    sync_end = html.index('document.getElementById("play").onclick', sync_start)
    sync_block = html[sync_start:sync_end]

    assert "const compatGesturePromise=compatAudioActive()" in sync_block
    assert "startCompatAudioFromGesture(joinTarget,keepPlaying)" in sync_block
    assert "const started=video.play();" in sync_block
    assert "Promise.all([" in sync_block
    assert sync_block.index("startCompatAudioFromGesture") < sync_block.index("await Promise.all")
    assert sync_block.index("video.play()") < sync_block.index("await Promise.all")
    assert sync_block.index("await Promise.all") < sync_block.index("heartbeat(true)")
    assert "Your browser blocked AAC compatibility audio" in sync_block


def test_dank_cinema_has_real_visual_quality_tiers_and_reduced_motion() -> None:
    html = movie_night_web._watch_html(
        "room-quality",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    assert 'id="qualityMode"' in html
    assert 'value="high"' in html
    assert 'value="standard"' in html
    assert 'value="lite"' in html
    assert 'data-quality="high"' in html
    assert 'data-quality="standard"' in html
    assert 'data-quality="lite"' in html
    assert "@media (prefers-reduced-motion:reduce)" in html
    assert "navigator.connection||navigator.mozConnection||navigator.webkitConnection" in html
    assert 'connection?.effectiveType' in html
    assert 'network==="2g"' in html
    assert 'network==="3g"' in html
    assert "navigator.deviceMemory" in html
    assert "navigator.hardwareConcurrency" in html
    assert "Playback features stay identical" in html


def test_dank_cinema_player_capability_controls_are_not_placebos() -> None:
    html = movie_night_web._watch_html(
        "room-capabilities",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    assert 'id="pip"' in html
    assert 'id="captions"' in html
    assert "document.pictureInPictureEnabled" in html
    assert "video.requestPictureInPicture" in html
    assert "document.exitPictureInPicture" in html
    assert "video.textTracks" in html
    assert 'tracks[i].mode=(i===preferredIndex && !anyShowing)?"showing":"disabled"' in html
    assert 'document.addEventListener("keydown"' in html
    assert 'key==="arrowleft"' in html
    assert 'key==="arrowright"' in html
    assert 'key==="f"' in html
    assert 'key==="m"' in html
    assert 'video.addEventListener("volumechange",syncVolumeControls)' in html
    assert 'muteControl.setAttribute("aria-pressed"' in html
    assert 'const AUDIO_STORAGE_KEY="dank-cinema-audio:"+BOOT.uid' in html
    assert "function applyUserAudioState(forceAudible=false)" in html
    assert "async function primeAudiblePlaybackGesture()" in html
    assert "if(shouldResume) await primeAudiblePlaybackGesture();" in html
    assert "applyUserAudioState(true);\n  syncRequested=true;" in html
    assert "applyUserAudioState(false);\n      showPlayerControls(false);" in html
    assert "video.load();\n  applyUserAudioState(false);" in html
    assert "pointer-events:none;" in html
    assert "touch-action:manipulation;" in html


def test_dank_cinema_tmdb_art_is_responsive_instead_of_one_size_for_every_device() -> None:
    html = movie_night_web._watch_html(
        "room-art",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    assert 'loading="lazy" decoding="async"' in html
    assert 'poster.srcset=w185+" 185w, "+w342+" 342w, "+w500+" 500w"' in html
    assert 'poster.sizes="(max-width:640px) 78px, (max-width:1079px) 108px, 120px"' in html
    assert 'return tmdbVariant(url,"w780")||url' in html
    assert 'return tmdbVariant(url,"w1280")||url' in html
    assert 'return tmdbVariant(url,"original")||url' in html
    assert "artworkResizeTimer" in html


def test_dank_cinema_desktop_tablet_and_mobile_have_distinct_compositions() -> None:
    html = movie_night_web._watch_html(
        "room-responsive",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    assert 'class="theater-grid"' in html
    assert 'class="theater-sidecar"' in html
    assert "@media (min-width:1080px)" in html
    assert "grid-template-columns:minmax(0,1fr) minmax(320px,360px)" in html
    assert "@media (min-width:641px) and (max-width:1079px)" in html
    assert "@media (max-width:640px)" in html
    assert "@media (max-width:380px)" in html


def test_watch_party_invite_dialog_restores_focus_and_supports_escape() -> None:
    html = movie_night_web._watch_html(
        "room-invite-a11y",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'aria-labelledby="inviteTitle"' in html
    assert "inviteTrigger=event.currentTarget" in html
    assert 'if(event.key==="Escape")' in html
    assert "inviteTrigger.focus()" in html


def test_quality_mode_boot_does_not_touch_room_state_before_it_is_declared() -> None:
    html = movie_night_web._watch_html(
        "room-quality-boot",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    quality_fn = html.split("function applyQualityMode(preference)", 1)[1].split("let storedQuality", 1)[0]
    assert "lastState" not in quality_fn
    assert html.index("applyQualityMode(storedQuality);") < html.index("let lastState=null;")



def test_feed_center_state_groups_real_sources_and_hides_urls_from_viewers(monkeypatch) -> None:
    async def shared_state(_guild_id, *, can_manage, refresh=False):
        _ = refresh
        enabled = {
            "source_id": "anime-feed",
            "label": "Anime Feed",
            "provider_type": "feed",
            "category": "anime",
            "enabled": True,
            "search_capable": False,
            "discovery_capable": True,
            "playback_capable": True,
            "supported_media_types": ["anime"],
            "health_state": "unchecked",
        }
        disabled = {
            "source_id": "private-json",
            "label": "Private JSON",
            "provider_type": "json",
            "category": "movies",
            "enabled": False,
            "search_capable": True,
            "discovery_capable": True,
            "playback_capable": True,
            "supported_media_types": ["movies"],
            "health_state": "disabled",
        }
        if can_manage:
            enabled["endpoint_url"] = "https://feeds.example.org/anime.xml"
            disabled["endpoint_url"] = "https://feeds.example.org/private.json"
        return {
            "revision": 7,
            "can_manage": can_manage,
            "sources": [enabled, disabled] if can_manage else [enabled],
            "categories": ["movies", "tv", "anime", "documentaries", "custom"],
        }

    monkeypatch.setattr(movie_night_web, "cinema_feed_state", shared_state)
    room = SimpleNamespace(guild_id=123, host_id=10)

    viewer_state = asyncio.run(movie_night_web._media_source_state(room, 20))
    assert viewer_state["is_host"] is False
    assert [item["source_id"] for item in viewer_state["sources"]] == ["anime-feed"]
    assert "endpoint_url" not in viewer_state["sources"][0]
    assert viewer_state["sources"][0]["category"] == "anime"
    assert viewer_state["sources"][0]["discovery_capable"] is True
    assert viewer_state["sources"][0]["search_capable"] is False
    assert viewer_state["sources"][0]["playback_capable"] is True
    assert viewer_state["sources"][0]["health_state"] == "unchecked"
    assert viewer_state["sources"][0]["supported_media_types"] == ["anime"]

    host_state = asyncio.run(movie_night_web._media_source_state(room, 10))
    assert host_state["is_host"] is True
    assert {item["source_id"] for item in host_state["sources"]} == {
        "anime-feed",
        "private-json",
    }
    assert all("endpoint_url" in item for item in host_state["sources"])
    assert host_state["categories"] == [
        "movies",
        "tv",
        "anime",
        "documentaries",
        "custom",
    ]


def test_non_host_cannot_mutate_feed_center(monkeypatch) -> None:
    room = SimpleNamespace(guild_id=123, host_id=10)

    async def room_and_user(_request):
        return room, 20

    async def request_json():
        return {"action": "remove", "source_id": "anime-feed"}

    monkeypatch.setattr(movie_night_web, "_room_and_user", room_and_user)

    try:
        asyncio.run(
            movie_night_web.movie_night_source_action(
                SimpleNamespace(json=request_json)
            )
        )
    except web.HTTPForbidden as exc:
        assert "host" in exc.text.lower()
    else:
        raise AssertionError("non-host unexpectedly mutated the Feed Center")


def test_feed_center_ui_exposes_real_source_management_without_fake_catalog_cards() -> None:
    html = movie_night_web._watch_html(
        "room-feed-center",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    assert 'id="feedPanel"' in html
    assert 'id="feedAddToggle"' in html
    assert 'id="feedForm"' in html
    assert 'id="feedCategory"' in html
    assert ">Movies<" in html
    assert ">TV Shows<" in html
    assert ">Anime<" in html
    assert ">Documentaries<" in html
    assert ">Custom<" in html
    assert '"/movie/"+BOOT.roomId+"/sources"' in html
    assert 'refresh.textContent="Refresh"' in html
    assert 'edit.textContent="Edit"' in html
    assert 'toggle.textContent=source.enabled?"Disable":"Enable"' in html
    assert 'remove.textContent="Delete"' in html



def test_brand_art_reserves_composed_lockup_ratio_to_avoid_header_cls() -> None:
    html = movie_night_web._watch_html(
        "room-brand-ratio",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    assert 'class="brand-lockup-art"' in html
    assert 'width="1200"' in html
    assert 'height="278"' in html
    assert 'brand-mark-art' not in html
    assert 'brand-wordmark-art' not in html



def test_google_cast_sdk_is_deferred_until_after_initial_render() -> None:
    html = movie_night_web._watch_html(
        "room-cast-perf",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    assert "https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1" in html
    assert 'data-dank-cast-sdk="1"' in html
    assert 'typeof window.requestIdleCallback==="function"' in html
    assert "requestIdleCallback(()=>loadGoogleCastSdk()" in html
    assert "setTimeout(()=>loadGoogleCastSdk(),900)" in html



def test_desktop_host_controls_use_compact_floating_panel_instead_of_full_width_sheet() -> None:
    html = movie_night_web._watch_html(
        "room-desktop-host",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    assert "left:auto;right:24px;bottom:24px;transform:none" in html
    assert "width:390px;max-height:min(76vh,620px)" in html
    assert ".host-actions { grid-template-columns:repeat(2,minmax(0,1fr)); }" in html



def test_queue_empty_state_keeps_queue_management_inside_theater() -> None:
    html = movie_night_web._watch_html(
        "room-empty-queue",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    assert "Your Queue Is Empty" in html
    assert 'id="queueAddToggle"' in html
    assert 'id="queueAddForm"' in html
    assert 'id="queueSearchInput"' in html
    assert "without leaving the Theater" in html
    assert "Add a title from Discord Cinema" not in html
    assert 'action.textContent="Open Discord"' not in html
    assert "Nothing queued yet." not in html


def test_queue_manager_exposes_real_add_play_next_reorder_and_remove_controls() -> None:
    html = movie_night_web._watch_html(
        "room-queue-controls",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    assert '"/movie/"+BOOT.roomId+"/queue-search?q="+encodeURIComponent(query)' in html
    assert 'action:"add"' in html
    assert '["Play Next","play_next"' in html
    assert '["↑","move_up"' in html
    assert '["↓","move_down"' in html
    assert '["×","remove"' in html
    assert 'document.getElementById("clearQueue").onclick=()=>queueAction("clear")' in html
    assert '"Added by "+String(item.added_by)' in html
    assert "Movie title or exact episode, e.g. Show S3E7" in html



def test_theater_uses_human_session_connection_states() -> None:
    html = movie_night_web._watch_html(
        "room-human-status",
        10,
        "uid=10&exp=9999999999&sig=test",
    )

    assert 'function humanSessionStatus(s)' in html
    assert 'return "Host Away"' in html
    assert 'return "Buffering"' in html
    assert 'return "Connecting"' in html
    assert 'return "Synced"' in html
    assert 'sync.textContent="Reconnecting"' in html
    assert 'role.textContent="Reconnecting"' in html
