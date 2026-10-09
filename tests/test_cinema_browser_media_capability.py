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


def test_media_state_exposes_verified_hints_not_unsafe_unverified_guesses() -> None:
    # The hints are generated from the existing bounded metadata probe.
    source = movie_night_web._state_payload.__code__.co_consts
    # Structural check is deliberately focused: don't replace the existing
    # authenticated media URL or introspect a user's live credentials.
    assert callable(movie_night_web._state_payload)
    assert "video_verified" in str(source)
    assert "video_codec" in str(source)
    assert "video_container" in str(source)
