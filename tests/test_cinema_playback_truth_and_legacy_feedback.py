from __future__ import annotations

"""Test the real generated Theater player for browser media-state regressions."""

import json
import shutil
import subprocess

import pytest

from stoney_verify import movie_night_web


_NODE = r"""
const assert=require("node:assert/strict");
const vm=require("node:vm");
const parts=JSON.parse(process.argv[1]);

async function main() {
  const video={
    paused:true,seeking:false,readyState:4,error:null,
    playRequests:0,
    requestVideoFrameCallback(){},
    play(){
      this.playRequests++;
      // Simulates an indefinite media startup; Play must not multiply on polls.
      return new Promise((resolve,reject)=>{
        this.lastResolve=resolve;this.lastReject=reject;
      });
    },
  };
  const notice={textContent:""};
  const state={
    video, notice, terminated:false, lastState:{is_host:true,state:"playing"},
    hostAutoPlayPending:false, hostAutoPlayAttempt:0, hostAutoPlayBlocked:false,
    refreshed:0, refreshStreamHealth(){this.refreshed++;},
  };
  vm.createContext(state);
  vm.runInContext(parts.requestPlayback,state);
  state.requestHostVideoPlayFromState();
  state.requestHostVideoPlayFromState();
  state.requestHostVideoPlayFromState();
  assert.equal(video.playRequests,1,"Three polls must yield exactly one media play request");
  assert.equal(state.hostAutoPlayPending,true);
  video.paused=false;video.lastResolve();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(state.hostAutoPlayPending,false);
  state.requestHostVideoPlayFromState();
  assert.equal(video.playRequests,1,"Already playing must not start a second attempt");

  video.paused=true;
  state.requestHostVideoPlayFromState();
  assert.equal(video.playRequests,2);
  video.lastReject({name:"NotAllowedError"});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(state.hostAutoPlayBlocked,true);
  state.requestHostVideoPlayFromState();
  assert.equal(video.playRequests,2,"Autoplay rejection requires explicit user gesture");
  assert.match(notice.textContent,/Play tap/);

  const frame={
    video, startupTrace:{events:{}}, fmtClock:()=>"",
    videoClockBuffering:false, compatAudioActive:()=>false,
    browserVideoCapability:()=>({supported:true}), compatAudio:{},
  };
  vm.createContext(frame);
  vm.runInContext(parts.health,frame);
  const room={media_missing:false,stream_url:"/signed",state:"playing"};
  assert.equal(frame.streamHealthLabel(room),"Playback requested; waiting for browser video",
    "Room wants to play but browser remains paused");
  video.paused=false;
  assert.equal(frame.streamHealthLabel(room),"Waiting for first decoded video frame");
  frame.startupTrace.events.first_frame=1234;
  assert.equal(frame.streamHealthLabel(room),"Video frames rendering");
  video.paused=true;
  assert.equal(frame.streamHealthLabel(room),"Playback requested; waiting for browser video");
  video.readyState=1;
  assert.equal(frame.streamHealthLabel(room),"Preparing playable video");

  // Event callbacks from media may be involuntary. Native PiP controls are
  // explicitly allowed to control the room; normal buffer events are not.
  const events={},actions=[],document={
    pictureInPictureElement:null,fullscreenElement:null
  };
  const bridge={
    video:{...video,readyState:4,webkitDisplayingFullscreen:false,
      addEventListener(name,cb){events[name]=cb;} },
    document,lastState:{is_host:true,state:"playing"},
    terminated:false, remoteApply:false,hostPlayGesturePending:false,
    videoClockBuffering:false,
    schedulePlayerControlsHide(){},compatAudioActive:()=>false,
    syncCompatAudio(){},holdCompatAudioForVideo(){},
    showPlayerControls(){},safeSeek(){return true;},
    heartbeat(){},nativeEventSource:null,
    hostAction(action){actions.push(action);return Promise.resolve(true);},
    syncGestureGranted:false,syncRequested:false,
    joinTarget:null,lastJoinRetargetAt:0,
  };
  vm.createContext(bridge);
  vm.runInContext(parts.events,bridge);
  events.pause();
  events.play();
  assert.deepEqual(actions,[],"Unsolicited pause/play must not mutate host state");
  document.pictureInPictureElement=bridge.video;
  events.pause();
  assert.deepEqual(actions,["pause"],"Explicit PiP pause still reaches host authority");
  bridge.videoClockBuffering=true;
  events.pause();
  assert.deepEqual(actions,["pause"],"Buffering must not emit a duplicate host pause");
  console.log("Cinema host Promise dedupe, true video health and PiP feedback OK");
}
main().catch(err=>{console.error(err);process.exitCode=1;});
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node required to exercise Theater JavaScript")
def test_cinema_host_media_playback_uses_real_state_not_old_pause_feedback() -> None:
    html=movie_night_web._watch_html("room-legacy-media",42,"uid=42&exp=9999999999&sig=test")
    def chunk(a: str,b: str) -> str:
        i=html.index(a)
        j=html.index(b,i)
        return html[i:j]
    parts={
        "requestPlayback":chunk(
            "function requestHostVideoPlayFromState() {",
            "async function applyState(s) {",
        ),
        "health":chunk("function streamHealthLabel(s) {",
            "function renderAudioClockDiagnostics() {"),
        "events":chunk("function nativeVideoControlsActive() {",
            'video.addEventListener("seeking"',
        ),
    }
    run=subprocess.run(
        ["node","-e",_NODE,json.dumps(parts)],
        capture_output=True,text=True,check=False,timeout=20,
    )
    assert run.returncode==0,run.stderr or run.stdout


def test_browser_frame_probe_must_not_forge_a_rendered_frame_from_media_time() -> None:
    html=movie_night_web._watch_html("room-video-evidence",42,"uid=42&exp=9999999999&sig=test")
    assert 'video.requestVideoFrameCallback(()=>{' in html
    assert 'watchDecodedFrames(activeMediaGeneration);' in html
    assert 'latestDecodedFrameAt=performance.now();' in html
    assert 'markStartupEvent("clock_advanced")' in html
    assert 'Number(video.currentTime||0)>0' in html
    assert 'markStartupEvent("first_frame");' not in html
    assert 'return "Video frames rendering"' in html
    assert 'return "Video stalled; no fresh decoded frames"' in html
    assert 'return "Video clock advancing; frames unverified"' in html
    assert 'frameSeen?"; decoded video frame confirmed"' in html
    assert 'video.addEventListener("pause",()=>{' in html
    assert "refreshStreamHealth();" in html
