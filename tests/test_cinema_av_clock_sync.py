from __future__ import annotations

"""Regression-test AAC video-clock ownership using the rendered Theater JavaScript."""

import json
import shutil
import subprocess

import pytest

from stoney_verify import movie_night_web


def _between(html: str, first: str, last: str) -> str:
    start = html.index(first)
    end = html.index(last, start)
    assert start < end
    return html[start:end]


_NODE_CLOCK_TEST = r"""
const assert = require("node:assert/strict");
const vm = require("node:vm");
const parts = JSON.parse(process.argv[1]);
const listeners = new Map();
const video = {
  paused: false, seeking: false, readyState: 4,
  currentTime: 50, playbackRate: 1,
  addEventListener(name, cb) { listeners.set(name, cb); },
};
const audio = {
  paused: false, readyState: 4, currentTime: 50,
  playbackRate: 1,
  getAttribute(key) { return key === "src" ? "/audio" : null; },
  pause() { this.paused = true; this.pauses = (this.pauses || 0) + 1; },
  play() { this.paused = false; this.plays = (this.plays || 0) + 1; return Promise.resolve(); },
  pauses: 0, plays: 0,
};
let reloads = 0;
let seekRequests = [];
const notice = { textContent: "" };
const context = {
  video, compatAudio: audio, notice,
  compatAudioUrl: "/audio", compatAudioOffset: 0,
  compatAudioStartPending: false, compatAudioNeedsGesture: false,
  compatAudioLastSyncAt: 0, videoClockBuffering: false,
  userMuted: false, terminated: false,
  lastState: { stream_url: "/video" },
  compatAudioActive: () => true,
  compatAudioClock: () => audio.currentTime,
  restartCompatAudio: async () => { reloads++; return true; },
  scheduleHostSeekCommit: () => {},
  scheduleCompatAudioRestart: (time, playing) => seekRequests.push({time, playing}),
  refreshAudioPermissionControl: () => {},
  Date: {now: () => 50000},
  Math, Number, Promise,
};
vm.createContext(context);
for (const key of ["clockFns", "sync", "seeking", "waiting", "playing"]) {
  vm.runInContext(parts[key], context);
}
(async () => {
  assert.equal(context.videoClockAdvancing(), true);
  // The original implementation repeatedly destroyed audio on drift.
  // Clock corrections must not allocate fresh FFmpeg streams.
  video.currentTime = 54;
  await context.syncCompatAudio(true);
  assert.equal(reloads, 0);
  assert.ok(audio.playbackRate <= 1.12 + 1e-7);
  assert.ok(audio.playbackRate >= 1);

  video.currentTime = 50.01;
  await context.syncCompatAudio(true);
  assert.equal(audio.playbackRate, 1);

  // Native video "waiting" can fire with video.paused still false.
  listeners.get("waiting")();
  assert.equal(video.paused, false);
  assert.equal(context.videoClockAdvancing(), false);
  assert.equal(audio.paused, true);
  const playsBeforeWait = audio.plays;
  await context.syncCompatAudio(true);
  assert.equal(audio.plays, playsBeforeWait);
  assert.equal(reloads, 0);

  // Real video recovery restarts audio once without changing the source.
  listeners.get("playing")();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(context.videoClockAdvancing(), true);
  assert.equal(audio.paused, false);
  assert.equal(audio.plays, playsBeforeWait + 1);
  assert.equal(reloads, 0);

  listeners.get("stalled")();
  assert.equal(audio.paused, true);
  await context.syncCompatAudio(true);
  assert.equal(reloads, 0);

  // A genuine seek is the one event that requests sidecar re-anchoring.
  video.currentTime = 340;
  video.seeking = true;
  listeners.get("seeking")();
  assert.equal(audio.paused, true);
  assert.equal(context.videoClockAdvancing(), false);
  video.seeking = false;
  listeners.get("seeked")();
  assert.equal(seekRequests.length, 1);
  assert.equal(seekRequests[0].time, 340);
  assert.equal(seekRequests[0].playing, true);

  // Muted or gesture-blocked audio must not restart behind user consent.
  context.compatAudioNeedsGesture = true;
  const playCount = audio.plays;
  await context.syncCompatAudio(true);
  assert.equal(audio.plays, playCount);
  assert.equal(reloads, 0);

  console.log("AAC video buffering, smooth drift, seek, and permission gate passed");
})().catch(error => {
  process.stderr.write(String(error.stack || error) + "\n");
  process.exitCode = 1;
});
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required for browser-state regression")
def test_aac_clock_stops_during_video_stall_and_resumes_without_reload() -> None:
    html = movie_night_web._watch_html(
        "clock-sync-test", 42, "uid=42&exp=9999999999&sig=test",
    )
    sections = {
        "clockFns": _between(
            html, "function videoClockAdvancing() {",
            "function scheduleCompatAudioRestart(",
        ),
        "sync": _between(
            html, "async function syncCompatAudio(force=false) {",
            "function normalizedAudioLanguage(value)",
        ),
        "seeking": _between(
            html, 'video.addEventListener("seeking",()=>{',
            'video.addEventListener("loadedmetadata",',
        ),
        "waiting": _between(
            html, 'video.addEventListener("waiting",()=>{',
            'video.addEventListener("error",',
        ),
        "playing": _between(
            html, 'video.addEventListener("playing",()=>{',
            'video.addEventListener("pause",()=>{',
        ),
    }
    result = subprocess.run(
        ["node", "-e", _NODE_CLOCK_TEST, json.dumps(sections)],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
