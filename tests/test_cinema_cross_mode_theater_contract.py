from __future__ import annotations

"""Contract tests across the real shared standalone/private/watch-party renderer.

These tests prove routing, authorization, viewer-specific audio URLs and room
state isolation. They cannot simulate a device's native video decoder.
"""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from aiohttp import web

from stoney_verify import movie_night_web
from stoney_verify.movie_night import MovieNightManager


@pytest.mark.parametrize("mode", ["standalone", "private", "watch_party"])
def test_same_authenticated_theater_route_preserves_fullscreen_contract_for_all_modes(
    monkeypatch, mode: str,
) -> None:
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cross-mode-signed-test")
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=100, channel_id=200, host_id=10,
        stream_token="", mode=mode,
    )
    monkeypatch.setattr(movie_night_web, "get_movie_night_manager", lambda: manager)

    async def membership(guild_id, uid):
        assert (guild_id, uid) == (100, 10)
        return "present"

    monkeypatch.setattr(movie_night_web, "_watch_membership_state", membership)
    exp = int(time.time()) + 1800
    request = SimpleNamespace(
        match_info={"room_id": room.room_id},
        query={
            "uid": "10",
            "exp": str(exp),
            "sig": movie_night_web._signature(room.room_id, 10, exp),
        },
    )
    response = asyncio.run(movie_night_web.movie_night_watch(request))
    page = response.text
    assert response.status == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Content-Security-Policy"]
    assert 'id="videoStage"' in page
    assert 'id="compatAudio"' in page
    assert '.video-stage[data-cinema-layout="inline"]' in page
    assert "aspect-ratio:16/9!important;" in page
    assert 'document.addEventListener("fullscreenchange",handleFullscreenChange)' in page
    assert 'video.addEventListener("webkitendfullscreen"' in page
    assert 'function syncCompatAudio(force=false)' in page
    assert 'function recoverPlayerFromViewportChange()' in page
    assert 'videoStage.style.height=height+"px"' not in page
    # Fullscreen restore changes layout, not the video stream or room clock.
    restore = page.split("function recoverPlayerFromViewportChange()", 1)[1].split(
        "if(typeof ResizeObserver", 1,
    )[0]
    for forbidden in ("video.src=", "video.load(", "compatAudio.src=", "safeSeek(", "hostAction("):
        assert forbidden not in restore


@pytest.mark.parametrize(
    "mode,guest_invited,guest_allowed",
    [
        ("standalone", False, False),
        ("private", False, False),
        ("private", True, True),
        ("watch_party", False, True),
    ],
)
def test_guest_access_rechecked_before_shared_theater_page(
    monkeypatch, mode: str, guest_invited: bool, guest_allowed: bool,
) -> None:
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cross-mode-signed-test")
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=100, channel_id=200, host_id=10,
        stream_token="token", mode=mode,
    )
    if guest_invited:
        manager.invite_private_viewer(room.room_id, host_id=10, user_id=20)
    monkeypatch.setattr(movie_night_web, "get_movie_night_manager", lambda: manager)

    async def membership(guild_id, uid):
        assert (guild_id, uid) == (100, 20)
        return "present"

    monkeypatch.setattr(movie_night_web, "_watch_membership_state", membership)
    exp = int(time.time()) + 1800
    request = SimpleNamespace(
        match_info={"room_id": room.room_id},
        query={
            "uid": "20",
            "exp": str(exp),
            "sig": movie_night_web._signature(room.room_id, 20, exp),
        },
    )
    if guest_allowed:
        response = asyncio.run(movie_night_web.movie_night_watch(request))
        assert response.status == 200
        assert 'id="videoStage"' in response.text
    else:
        with pytest.raises(web.HTTPForbidden):
            asyncio.run(movie_night_web.movie_night_watch(request))


@pytest.mark.parametrize("mode", ["private", "watch_party"])
def test_distinct_viewers_get_distinct_audio_consumers_without_changing_shared_clock(
    monkeypatch, mode: str,
) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=100, channel_id=200, host_id=10,
        stream_token="torrent-token", mode=mode,
    )
    if mode == "private":
        manager.invite_private_viewer(room.room_id, host_id=10, user_id=20)
    current = time.monotonic()

    manager.heartbeat(
        room.room_id, user_id=10, position_seconds=55,
        byte_position=10, buffered_until_byte=100,
        paused=False, client_session_id="host-phone", now=current,
    )
    manager.heartbeat(
        room.room_id, user_id=20, position_seconds=0,
        byte_position=0, buffered_until_byte=0, paused=True,
        client_session_id="viewer-laptop", sync_requested=True, now=current,
    )
    before_host = room.host_id
    before_media = room.stream_token
    before_state = room.playback_state

    session = SimpleNamespace(
        file_name="Example.Film.1080p.mp4", file_size=10_000_000,
        release_metadata={"title": "Example Film"},
        verified_metadata={
            "available": True, "container": "mp4",
            "video": {"codec": "h264"},
            "audio_tracks": [
                {"language": "eng", "codec": "aac"},
                {"language": "jpn", "codec": "aac"},
            ],
        },
    )

    class TorrentStub:
        async def get(self, token):
            assert token == "torrent-token"
            return session

        def session_usable(self, _session):
            return True

        def schedule_metadata_probe(self, _session):
            return None

        def stream_url(self, _session, *, ttl_seconds, consumer_key):
            assert ttl_seconds == 21600
            return "https://example.invalid/stream?consumer=" + consumer_key

        def compat_audio_url(self, _session, *, ttl_seconds, consumer_key):
            return "https://example.invalid/audio?consumer=" + consumer_key

        def browser_audio_compatibility(self, _session):
            return {"required": False, "reason": "", "codecs": ["aac"]}

        def status(self, _session):
            return {}

        def consumer_startup_status(self, _session, consumer_key):
            return {"consumer": consumer_key}

    monkeypatch.setattr(movie_night_web, "get_torrent_manager", lambda: TorrentStub())
    monkeypatch.setattr(movie_night_web, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(movie_night_web.shutil, "which", lambda executable: "/usr/bin/ffmpeg" if executable == "ffmpeg" else None)
    monkeypatch.setattr(movie_night_web, "_discord_viewer_summaries", lambda *_args: [])
    monkeypatch.setattr(movie_night_web, "_discord_room_context", lambda *_args: {})

    host = asyncio.run(movie_night_web._state_payload(room, 10))
    viewer = asyncio.run(movie_night_web._state_payload(room, 20))

    assert host["mode"] == viewer["mode"] == mode
    assert host["viewer_count"] == viewer["viewer_count"] == 2
    assert host["is_host"] and not viewer["is_host"]
    assert host["stream_token"] == viewer["stream_token"] == "torrent-token"
    assert host["stream_consumer"] == "movie:10:host-phone"
    assert viewer["stream_consumer"] == "movie:20:viewer-laptop"
    assert host["stream_url"] != viewer["stream_url"]
    assert host["audio_track_url"] != viewer["audio_track_url"]
    assert host["audio_track_options"] == viewer["audio_track_options"]
    assert [row["language"] for row in host["audio_track_options"]] == ["eng", "jpn"]
    assert viewer["sync_requested"] is True
    assert host["sync_status"] == "host"
    assert host["position_seconds"] == viewer["position_seconds"]
    assert room.host_id == before_host
    assert room.stream_token == before_media
    assert room.playback_state == before_state


@pytest.mark.parametrize(
    "mode,uid,language",
    [
        ("standalone", 10, "jpn"),
        ("private", 10, "jpn"),
        ("private", 20, "eng"),
        ("watch_party", 10, "jpn"),
        ("watch_party", 20, "eng"),
    ],
)
def test_authenticated_audio_language_preferences_are_per_member_and_guild(
    monkeypatch, mode: str, uid: int, language: str,
) -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=100, channel_id=200, host_id=10,
        stream_token="", mode=mode,
    )
    if mode == "private":
        manager.invite_private_viewer(room.room_id, host_id=10, user_id=20)
    assert manager.user_can_access(room, uid)
    monkeypatch.setattr(movie_night_web, "_room_and_user", lambda _request: async_room(room, uid))

    async def user_profile(user_id):
        return {
            "preferences": {
                "default_audio_language": "en",
                "audio_language_by_guild": {
                    "100": "jpn" if user_id == 10 else "eng",
                    "200": "spa",
                },
            },
        }

    monkeypatch.setattr(movie_night_web, "get_cinema_user", user_profile)
    response = asyncio.run(
        movie_night_web.movie_night_preferences(SimpleNamespace(method="GET"))
    )
    payload = json.loads(response.text)
    assert payload["guild_id"] == 100
    assert payload["guild_audio_language"] == language
    assert payload["preferences"]["audio_language_by_guild"]["200"] == "spa"


async def async_room(room, uid):
    return room, uid
