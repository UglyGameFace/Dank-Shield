from __future__ import annotations

import asyncio
import json
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
    assert "const ranges=video.buffered" in html
    assert "ranges.start(i)" in html
    assert "ranges.end(i)" in html
    assert "return video.buffered.end(video.buffered.length-1)" not in html
    assert "Tap to Sync" in html
    assert "syncButton.hidden=!!s.is_host" in html
    assert "Buffering the group for smoother playback" in html
    assert "Joining Movie Night" in html
    assert "Playback will stay put while the buffer catches up." in html
    assert 's.sync_status==="joining"' in html
    assert "Synced Viewer" in html
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
    assert 'class="brand-banner"' in html
    assert 'src="/movie/assets/dank-cinema-brand.webp"' in html
    assert 'alt="Dank Cinema — A feature of The 420 Lobby"' in html
    assert 'class="wordmark"' not in html
    assert 'class="brand-mark"' not in html
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
    assert 'data-panel="chat">💬 Open Discord' in html


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
    movie_night_web._dank_cinema_brand_bytes.cache_clear()
    payload = movie_night_web._dank_cinema_brand_bytes()

    assert len(payload) > 5_000
    assert payload[:4] == b"RIFF"
    assert payload[8:12] == b"WEBP"

    response = asyncio.run(
        movie_night_web.dank_cinema_brand_asset(SimpleNamespace())
    )
    assert response.content_type == "image/webp"
    assert response.body == payload
    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_dank_cinema_brand_asset_route_is_registered() -> None:
    source = Path(movie_night_web.__file__).read_text(encoding="utf-8")
    assert '"/movie/assets/dank-cinema-brand.webp"' in source
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
    assert "function openHostControls()" in html
    assert "function closeHostControls()" in html
    assert "hostLauncher.hidden=!hostSheetDismissed" in html
    assert 'contextAction.dataset.panel="host"' in html
    assert 'else if(tab.dataset.panel==="host")' in html


def test_dank_cinema_mobile_layout_wraps_controls_instead_of_overflowing() -> None:
    html = movie_night_web._watch_html(
        "room-responsive",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert ".shell { width:min(1120px,100%); margin:0 auto; padding:0 18px 150px; overflow-x:hidden; }" in html
    assert ".tab {" in html
    assert "white-space:normal;overflow-wrap:anywhere" in html
    assert ".host-actions { grid-template-columns:repeat(2,minmax(0,1fr));overflow:visible; }" in html
    assert ".quick-tabs { grid-template-columns:repeat(2,minmax(0,1fr)); }" in html
    assert ".queue-item.manageable .queue-actions { grid-column:1 / -1;justify-content:flex-end; }" in html


def test_dank_cinema_brand_blends_into_theater_header() -> None:
    html = movie_night_web._watch_html(
        "room-brand-flush",
        456,
        "uid=456&exp=9999999999&sig=test",
    )

    assert "mix-blend-mode:screen" in html
    assert "linear-gradient(180deg,#020706 0%,#06110e 72%,transparent 100%)" in html
    assert 'src="/movie/assets/dank-cinema-brand.webp"' in html


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
