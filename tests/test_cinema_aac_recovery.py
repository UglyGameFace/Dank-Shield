from __future__ import annotations

"""Regression coverage for cross-browser AAC recovery and persistent controls."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from stoney_verify import movie_night_web


def _player_script() -> str:
    html = movie_night_web._watch_html(
        "aac-recovery-test",
        42,
        "uid=42&exp=9999999999&sig=test",
    )
    start = html.index("function refreshAudioPermissionControl() {")
    end = html.index("function refreshNativePlayerCapabilities()", start)
    refresh = html[start:end]
    start = html.index("function videoClockAdvancing() {")
    end = html.index("function scheduleCompatAudioRestart(", start)
    clock = html[start:end]
    start = html.index("function startCompatAudioFromGesture(")
    end = html.index("function scheduleCompatAudioRestart(", start)
    gesture = html[start:end]
    start = html.index("async function syncCompatAudio(force=false) {")
    end = html.index("function normalizedAudioLanguage(value)", start)
    sync = html[start:end]
    return json.dumps({"refresh": refresh, "clock": clock, "gesture": gesture, "sync": sync})


_NODE_CONTRACT = r"""
const assert = require("node:assert/strict");
const vm = require("node:vm");

const snippets = JSON.parse(process.argv[1]);
const control = {hidden: true};
const button = {textContent: "", disabled: false};
let active = true;
let restarts = 0;
let loads = 0;
let playCalls = 0;
let refused = false;

const audio = {
  paused: true,
  readyState: 0,
  currentTime: 0,
  volume: 1,
  muted: false,
  getAttribute: (key) => key === "src" ? "/existing-audio" : null,
  src: "/existing-audio",
  play: () => {
    playCalls++;
    if (refused) return Promise.reject(Object.assign(new Error("permission"), { name:"NotAllowedError" }));
    audio.paused = false;
    return Promise.resolve();
  },
  pause: () => { audio.paused = true; },
  load: () => { loads++; },
};
const video = { paused: false, seeking: false, readyState: 4, currentTime: 12, playbackRate: 1 };
const notice = {textContent: ""};
const ctx = {
  document: {getElementById: id => id === "enableAudioControl" ? control : button},
  video,
  compatAudio: audio,
  compatAudioActive: () => active,
  compatAudioUrl: "/audio",
  compatAudioClock: () => audio.currentTime,
  compatAudioTargetUrl: () => "/audio",
  compatAudioNeedsGesture: false,
  compatAudioStartPending: false,
  compatAudioStartSequence: 0,
  compatAudioRestartAt: 0,
  compatAudioLastSyncAt: 0,
  compatAudioOffset: 0,
  videoClockBuffering: false,
  userMuted: false,
  applyUserAudioState: () => {},
  restartCompatAudio: async () => { restarts++; return true; },
  notice,
  Date: { now: () => 20000 },
  URL,
  Math,
  Number,
  Promise,
};
vm.createContext(ctx);
vm.runInContext(snippets.refresh, ctx);
ctx.refreshAudioPermissionControl();

// A transient `paused` flip is not a reason for the recovery button to vanish.
assert.equal(control.hidden, true, "Normal playback must not demand audio permission");
assert.equal(button.textContent, "Restore audio");
audio.paused = false;
ctx.refreshAudioPermissionControl();
assert.equal(control.hidden, true, "Playing audio has no permission prompt");
assert.equal(button.textContent, "Restore audio");
ctx.compatAudioNeedsGesture = true;
ctx.refreshAudioPermissionControl();
assert.equal(control.hidden, false, "Show recovery only after actual gesture failure");
assert.equal(button.textContent, "Restore audio");
ctx.userMuted = true;
ctx.refreshAudioPermissionControl();
assert.equal(control.hidden, true);
ctx.userMuted = false;
active = false;
ctx.refreshAudioPermissionControl();
assert.equal(control.hidden, true);
active = true;

// Run both the actual production video-clock gate and the AAC sync handler.
vm.runInContext(snippets.clock, ctx);
// A loading stream may lag the video; polling must not keep recreating FFmpeg.
vm.runInContext(snippets.sync, ctx);
(async () => {
  ctx.compatAudioStartPending = true;
  await ctx.syncCompatAudio(true);
  assert.equal(restarts, 0);
  ctx.compatAudioStartPending = false;
  ctx.compatAudioNeedsGesture = true;
  await ctx.syncCompatAudio(true);
  assert.equal(restarts, 0);
  ctx.compatAudioNeedsGesture = false;
  audio.paused = false;
  audio.readyState = 0;
  await ctx.syncCompatAudio(true);
  assert.equal(restarts, 0);
  audio.readyState = 3;
  await ctx.syncCompatAudio(true);
  assert.equal(restarts, 0, "Normal A/V drift must not recreate FFmpeg audio");
  assert.ok(audio.playbackRate > 1, "Lagging AAC should converge smoothly");

  // A rejected autoplay must surface a persistent user gesture requirement.
  video.currentTime = 0;
  audio.currentTime = 0;
  audio.paused = true;
  ctx.compatAudioNeedsGesture = false;
  refused = true;
  await ctx.syncCompatAudio(true);
  assert.equal(ctx.compatAudioNeedsGesture, true);
  assert.match(notice.textContent, /Enable audio/);

  // An explicit click reuses the existing audio source instead of restarting it.
  vm.runInContext(snippets.gesture, ctx);
  refused = false;
  video.currentTime = 3;
  audio.currentTime = 3;
  const priorLoads = loads;
  const started = await ctx.startCompatAudioFromGesture(3, true);
  assert.equal(started, true);
  assert.equal(loads, priorLoads, "Click must not reload an already aligned audio source");
  assert.equal(ctx.compatAudioNeedsGesture, false);
  assert.equal(ctx.compatAudioStartPending, false);
  assert.ok(playCalls >= 2);
  const priorExplicitRestart = loads;
  assert.equal(await ctx.startCompatAudioFromGesture(3, true, true), true);
  assert.equal(loads, priorExplicitRestart + 1, "Restart audio must reload AAC");
  assert.equal(control.hidden, true, "Successful AAC playback needs no recovery prompt");
  assert.equal(button.textContent, "Restore audio");
  process.stdout.write("AAC recovery behavior verified\n");
})().catch(error => {
  process.stderr.write(String(error.stack || error) + "\n");
  process.exitCode = 1;
});
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for runtime browser-state checks")
def test_aac_permission_control_and_playback_recovery_behavior() -> None:
    result = subprocess.run(
        ["node", "-e", _NODE_CONTRACT, _player_script()],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_original_sidecar_error_awaits_user_retry_instead_of_rearming_on_poll() -> None:
    html = movie_night_web._watch_html(
        "aac-error-retry",
        42,
        "uid=42&exp=9999999999&sig=test",
    )
    start = html.index('compatAudio.addEventListener("error",()=>{')
    end = html.index('applyUserAudioState(false);', start)
    handler = html[start:end]
    assert 'if(wasAlternate) {' in handler
    assert 'compatAudioUrl="";' in handler.split('if(wasAlternate) {', 1)[1]
    assert 'compatAudioNeedsGesture=true;' in handler
    assert 'refreshAudioPermissionControl();' in handler
    assert 'Preserve the signed URL and mode for an explicit retry.' in handler
    assert 'Browser blocked AAC audio. Tap Enable audio' in html
    assert 'control.hidden=!(compatAudioActive() && !userMuted && compatAudioNeedsGesture)' in html


def test_volume_and_unmute_are_direct_audio_permission_gestures() -> None:
    html = movie_night_web._watch_html(
        "aac-unmute-gesture", 42, "uid=42&exp=9999999999&sig=test",
    )
    volume_start = html.index('volumeControl.addEventListener("input",event=>{')
    mute_start = html.index('muteControl.onclick=()=>{', volume_start)
    volume = html[volume_start:mute_start]
    mute = html[mute_start:html.index('video.addEventListener("volumechange"', mute_start)]
    direct_call = "startCompatAudioFromGesture(Number(video.currentTime||0),true)"
    assert direct_call in volume
    assert direct_call in mute
    assert "void syncCompatAudio(true)" not in mute
    assert 'compatAudio.addEventListener("playing",()=>{' in html
    assert "compatAudioNeedsGesture=false;" in html.split(
        'compatAudio.addEventListener("playing",()=>{', 1
    )[1].split("});", 1)[0]
