from __future__ import annotations

"""Browser-native video checks must not pretend a recognized torrent is playable."""

import json
import shutil
import subprocess

import pytest

from stoney_verify import movie_night_web


_NODE = r"""
const assert = require("node:assert/strict");
const vm = require("node:vm");
const script = process.argv[1];
const calls = [];
const element = {textContent:""};
const video = {
  canPlayType(type) {
    calls.push(type);
    if (type.startsWith('video/mp4; codecs="avc1.42E01E"')) return "probably";
    if (type.startsWith('video/webm; codecs="vp09.00.10.08"')) return "maybe";
    return "";
  },
};
const ctx = {
  video,
  document: {getElementById: () => element},
  String,
};
vm.createContext(ctx);
vm.runInContext(script, ctx);

const mp4 = ctx.browserVideoCapability({
  video_verified: true, video_codec: "h264", media_content_type: "video/mp4",
});
assert.equal(mp4.supported, true);
assert.match(mp4.label, /probably/);
assert.ok(calls.some(x => x.includes("avc1.42E01E")));
const webm = ctx.browserVideoCapability({
  video_verified: true, video_codec:"vp9", media_content_type:"video/webm",
});
assert.equal(webm.supported, true);
assert.match(webm.label, /maybe/);
const mkv = ctx.browserVideoCapability({
  video_verified: true, video_codec:"hevc", media_content_type:"video/x-matroska",
});
assert.equal(mkv.supported, false);
assert.match(mkv.label, /choose another release/);
const unknown = ctx.browserVideoCapability({
  video_verified:false, video_codec:"", media_content_type:"video/x-matroska",
});
assert.equal(unknown.supported, null, "Unprobed media must remain unknown");
const missing = ctx.browserVideoCapability({
  video_verified:true, video_codec:"unknown", media_content_type:"application/octet-stream",
});
assert.equal(missing.supported, null);
ctx.renderBrowserMediaSupport({video_verified:true, video_codec:"hevc", media_content_type:"video/x-matroska"});
assert.match(element.textContent, /choose another release/);
console.log("Cinema browser capability matrix verified");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node required for live JS capability logic")
def test_actual_theater_can_play_type_is_used_for_video_format_advice() -> None:
    html = movie_night_web._watch_html(
        "video-capability-test", 42, "uid=42&exp=9999999999&sig=test",
    )
    a = html.index("function browserVideoCapability(s) {")
    b = html.index("function streamHealthLabel(s)", a)
    code = html[a:b]
    result = subprocess.run(
        ["node", "-e", _NODE, code],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert 'id="browserMediaSupport"' in html
    assert "renderBrowserMediaSupport(lastState)" in html
    assert "Browser may not support this video codec" in html


def test_media_state_exposes_verified_hints_not_unsafe_unverified_guesses(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from stoney_verify.movie_night import MovieNightManager

    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=44, channel_id=45, host_id=46,
        stream_token="stub-token", mode="standalone",
    )
    candidate = manager.nominate(
        room.room_id, user_id=46, title="Known Movie", auto_vote=False,
    )
    variant = manager.add_variant(
        room.room_id, candidate.candidate_id, user_id=46,
        source_ref="magnet:?xt=urn:btih:verified-source", auto_vote=False,
    )
    room.current_candidate_id = candidate.candidate_id
    room.current_variant_id = variant.variant_id
    session = SimpleNamespace(
        token="stub-token", file_name="movie.mkv",
        release_metadata={"title": "Test"},
        verified_metadata={
            "available": True, "container": "matroska,webm",
            "video": {"codec": "hevc", "width": 1920, "height": 1080},
            "audio_tracks": [{"codec": "eac3", "language": "eng"}],
        },
    )

    class FakeTorrentManager:
        async def get(self, token):
            assert token == "stub-token"
            return session

        def session_usable(self, _session):
            return True

        def stream_url(self, _session, **kwargs):
            return "/media/torrent/stream/stub-token/movie.mkv?sig=test"

        def schedule_metadata_probe(self, _session):
            return False

        def browser_audio_compatibility(self, _session):
            return {"required": False, "codecs": ["eac3"]}

        def status(self, _session):
            return {}

        def consumer_startup_status(self, _session, _key):
            return {}

    monkeypatch.setattr(movie_night_web, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(movie_night_web, "get_torrent_manager", lambda: FakeTorrentManager())
    result = asyncio.run(movie_night_web._state_payload(room, 46))
    assert result["video_verified"] is True
    assert result["video_codec"] == "hevc"
    assert result["video_container"] == "matroska,webm"
    assert variant.metadata["verified"]["video"]["codec"] == "hevc"
    assert variant.browser_video_risk_key() == 2
    assert result["media_content_type"] == "video/x-matroska"
    assert "sig=" in result["stream_url"]

    session.verified_metadata = {"available": False}
    result = asyncio.run(movie_night_web._state_payload(room, 46))
    assert result["video_verified"] is False
    assert result["video_codec"] == ""
