import pytest

from stoney_verify.cinema_subtitles import srt_to_vtt


def test_srt_to_vtt_safe_and_timed():
    value = b"1\n00:00:01,000 --> 00:00:03,250\nHello <script>alert(1)</script> & friends\n\n2\n00:00:04,000 --> 00:00:05,000\nBonjour\n"
    result = srt_to_vtt(value)
    assert result.startswith("WEBVTT\n\n")
    assert "00:00:01.000 --> 00:00:03.250" in result
    assert "&lt;script&gt;" in result
    assert "<script>" not in result
    assert "Bonjour" in result


@pytest.mark.parametrize("data", [
    b"", b"1\n00:99:00,000 --> 00:00:01,000\nNope",
    b"1\n00:00:03,000 --> 00:00:01,000\nNope",
    b"1\n00:00:00,000 --> 00:00:01,000\n\x00",
])
def test_reject_invalid_subtitle(data):
    with pytest.raises(ValueError):
        srt_to_vtt(data)


def test_reject_oversized_subtitle():
    with pytest.raises(ValueError, match="too large"):
        srt_to_vtt(b"x" * 20, max_bytes=10)



def test_verified_embedded_tracks_reject_bitmap_subtitles():
    from stoney_verify.cinema_subtitles import embedded_text_tracks

    options = embedded_text_tracks({
        "available": True,
        "subtitle_tracks": [
            {"codec": "hdmv_pgs_subtitle", "language": "eng"},
            {"codec": "subrip", "language": "eng", "title": "English SDH"},
            {"codec": "ass", "language": "jpn", "title": "Signs"},
            {"codec": "dvd_subtitle", "language": "spa"},
        ],
    })
    assert [item["index"] for item in options] == [1, 2]
    assert [item["language"] for item in options] == ["eng", "jpn"]
    assert "English SDH" in options[0]["label"]
    assert embedded_text_tracks({"available": False, "subtitle_tracks": options}) == []


def test_embedded_subtitles_deduplicate_verified_session_track(monkeypatch):
    import asyncio
    from stoney_verify import cinema_subtitles

    cinema_subtitles._VTT_CACHE.clear()
    calls = []

    async def ffmpeg_stub(input_url, track):
        calls.append((input_url, track))
        return b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"

    monkeypatch.setattr(cinema_subtitles, "_run_ffmpeg_srt", ffmpeg_stub)
    monkeypatch.setenv("DANK_MEDIA_PORT", "8123")
    url = "https://cinema.example/media/torrent/stream/token-a/sample.mkv?exp=123&sig=test"
    one = asyncio.run(cinema_subtitles.embedded_vtt_for_session(
        token="token-a", track=0, signed_stream_url=url,
    ))
    two = asyncio.run(cinema_subtitles.embedded_vtt_for_session(
        token="token-a", track=0, signed_stream_url=url,
    ))
    assert one == two
    assert one.startswith(b"WEBVTT")
    assert calls == [(
        "http://127.0.0.1:8123/media/torrent/stream/token-a/sample.mkv?exp=123&sig=test", 0,
    )]
    cinema_subtitles._VTT_CACHE.clear()


def test_embedded_subtitle_rejects_untrusted_stream_origins():
    import asyncio
    from stoney_verify import cinema_subtitles

    with pytest.raises(ValueError):
        asyncio.run(cinema_subtitles.embedded_vtt_for_session(
            token="token-a", track=2,
            signed_stream_url="https://evil.example/media/whatever?token=1",
        ))


def test_signed_viewer_subtitle_route_is_available_in_shared_theater():
    from stoney_verify import movie_night_web

    html = movie_night_web._watch_html(
        "subtitle-room", 15, "uid=15&exp=9999999999&sig=test",
    )
    assert 'id="subtitleMenu" hidden' in html
    assert 'id="subtitleTrack"' in html
    assert "function syncCinemaSubtitleTracks(s)" in html
    assert 'BOOT.query+"&track="+encodeURIComponent(String(index))' in html
    assert 'video.appendChild(track);' in html
    assert 'track.track.mode="showing";' in html
    assert 'guild_subtitle_language:preferredSubtitleLanguage' in html


def test_subtitle_preferences_are_normalized_and_guild_scoped():
    from stoney_verify.cinema_library_service import _normalize_preferences

    preferences = _normalize_preferences({
        "default_subtitle_language": "eng",
        "subtitle_language_by_guild": {
            "100": "jpn", "200": "off", "-2": "eng", "bad": "spa",
        },
    })
    assert preferences["default_subtitle_language"] == "eng"
    assert preferences["subtitle_language_by_guild"] == {
        "100": "jpn", "200": "off",
    }



def test_subtitle_endpoint_requires_authorized_viewer_and_verified_text_track(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from aiohttp import web
    from stoney_verify import movie_night_web

    class TorrentStub:
        def __init__(self):
            self.session = SimpleNamespace(
                token="valid-token",
                verified_metadata={
                    "available": True,
                    "subtitle_tracks": [
                        {"codec": "subrip", "language": "en"},
                        {"codec": "hdmv_pgs_subtitle", "language": "fr"},
                    ],
                },
            )

        async def get(self, token):
            assert token == "valid-token"
            return self.session

        def session_usable(self, session):
            return session is self.session

        def stream_url(self, session, *, ttl_seconds, consumer_key):
            assert session is self.session and ttl_seconds == 180
            assert consumer_key == "movie:20:subtitle"
            return "https://example.invalid/media/torrent/stream/valid-token/test.mkv?sig=ok"

    room = SimpleNamespace(stream_token="valid-token", room_id="r", guild_id=100)
    async def authorized(_request):
        return room, 20

    async def extracted(*, token, track, signed_stream_url):
        assert token == "valid-token" and track == 0
        assert signed_stream_url.startswith("https://example.invalid/")
        return b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n"

    monkeypatch.setattr(movie_night_web, "_room_and_user", authorized)
    monkeypatch.setattr(movie_night_web, "get_torrent_manager", TorrentStub)
    monkeypatch.setattr(movie_night_web, "embedded_vtt_for_session", extracted)
    response = asyncio.run(movie_night_web.movie_night_subtitle(
        SimpleNamespace(query={"track": "0"}),
    ))
    assert response.status == 200
    assert response.content_type == "text/vtt"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.body.startswith(b"WEBVTT")
    for selected in ("1", "99", "../0", "no"):
        with pytest.raises((web.HTTPNotFound, web.HTTPBadRequest)):
            asyncio.run(movie_night_web.movie_night_subtitle(
                SimpleNamespace(query={"track": selected}),
            ))



def test_subdl_off_by_default_and_paid_operator_opt_in(monkeypatch):
    from stoney_verify import cinema_subtitle_providers as provider

    monkeypatch.delenv("DANK_CINEMA_SUBDL_COMMERCIAL_OK", raising=False)
    monkeypatch.setenv("DANK_CINEMA_SUBDL_API_KEY", "example-private-api-key")
    assert not provider.provider_configured()
    monkeypatch.setenv("DANK_CINEMA_SUBDL_COMMERCIAL_OK", "true")
    assert provider.provider_configured()
    monkeypatch.delenv("DANK_CINEMA_SUBDL_API_KEY")
    assert not provider.provider_configured()


def test_subdl_uses_series_id_for_exact_episode_and_rejects_unverified_matches():
    from stoney_verify import cinema_subtitle_providers as provider

    assert provider._media_query({
        "media_type": "episode", "tmdb_id": 555, "series_id": 77,
        "season_number": 3, "episode_number": 9,
    }) == ("tv", 77, 3, 9)
    assert provider._media_query({
        "media_type": "episode", "tmdb_id": 555, "series_id": 77,
        "season_number": 3, "episode_number": 0,
    }) is None
    assert provider._media_query({
        "media_type": "movie", "tmdb_id": 1234,
    }) == ("movie", 1234, 0, 0)
    rows = provider._parse_results({
        "subtitles": [
            {"n_id": "real-one", "lang": "en", "release_name": "Film.WEB"},
            {"n_id": "../../../etc/passwd", "lang": "en"},
            {"n_id": "other-language", "lang": "fr"},
        ],
    }, language="en")
    assert rows == [{
        "id": "real-one", "language": "en",
        "label": "SubDL • Film.WEB", "source": "subdl",
    }]


def test_subdl_download_requires_recent_exact_media_search_selection():
    from stoney_verify import cinema_subtitle_providers as provider

    provider._ELIGIBLE_IDS.clear()
    assert not provider.allowed_subdl_id("film-token", "not-seen")
    provider._ELIGIBLE_IDS[("film-token", "matching-one")] = 10**15
    assert provider.allowed_subdl_id("film-token", "matching-one")
    assert not provider.allowed_subdl_id("different-film", "matching-one")
    assert not provider.allowed_subdl_id("film-token", "../matching-one")
    provider._ELIGIBLE_IDS.clear()


def test_subdl_exact_episode_search_never_sends_key_to_browser(monkeypatch):
    import asyncio
    from stoney_verify import cinema_subtitle_providers as provider

    monkeypatch.delenv("DANK_CINEMA_SUBDL_API_KEY", raising=False)
    monkeypatch.delenv("DANK_CINEMA_SUBDL_COMMERCIAL_OK", raising=False)
    result = asyncio.run(provider.search_subdl({
        "media_type": "episode", "series_id": 500,
        "season_number": 2, "episode_number": 6,
    }, "en", stream_token="movie-token"))
    assert result == []



def test_subdl_direct_download_urls_are_allowlisted():
    from stoney_verify import cinema_subtitle_providers as provider

    good = "https://dl.subdl.com/subtitle/abc123/file.srt"
    assert provider._safe_download_url(good) == good
    for unsafe in (
        "http://dl.subdl.com/subtitle/abc/file.srt",
        "https://dl.subdl.com.evil.test/subtitle/abc/file.srt",
        "https://other.test/subtitle/abc/file.srt",
        "https://dl.subdl.com@evil.test/subtitle/abc/file.srt",
        "https://dl.subdl.com/admin",
    ):
        with pytest.raises(provider.SubtitleProviderUnavailable):
            provider._safe_download_url(unsafe)
