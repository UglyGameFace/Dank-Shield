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
