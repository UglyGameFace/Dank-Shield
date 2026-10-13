from __future__ import annotations

"""Browser media errors must never trigger endless legacy reload cycles."""

import json
import shutil
import subprocess

import pytest

from stoney_verify import movie_night_web


_NODE = r"""
const assert=require("node:assert/strict");
const vm=require("node:vm");
const code=JSON.parse(process.argv[1]);
const video={error:{code:4}};
const notice={textContent:""};
const timers=[];
const attachments=[];
const ctx={
  video,notice,terminated:false,streamRetryTimer:null,streamRetryAttempt:0,
  STREAM_RETRY_LIMIT:3,
  lastState:{stream_url:"/signed-original",stream_token:"movie-original"},
  setTimeout(fn,delay){const timer={fn,delay};timers.push(timer);return timer;},
  jsonFetch:async()=>({stream_url:ctx.lastState.stream_url}),
  applyState:async()=>{},
  attachStream(url,force){attachments.push({url,force});},
  Math,Number,String,
};
vm.createContext(ctx);
vm.runInContext(code,ctx);
ctx.scheduleStreamRetry();
assert.equal(ctx.streamRetryAttempt,0,"unsupported formats are not retried");
assert.match(notice.textContent,/cannot play the selected source/);
video.error.code=3;
ctx.scheduleStreamRetry();
assert.equal(ctx.streamRetryAttempt,0,"decoder failure is not endlessly reloaded");
assert.match(notice.textContent,/could not decode/);

video.error.code=2;
async function fire() {
  const item=timers.shift();
  assert.ok(item);
  await item.fn();
}
(async()=>{
  for(let i=0;i<3;i++){
    ctx.scheduleStreamRetry();
    assert.equal(ctx.streamRetryAttempt,i+1);
    await fire();
  }
  assert.equal(attachments.length,3);
  ctx.scheduleStreamRetry();
  assert.equal(timers.length,0,"bounded after three stream reloads");
  assert.match(notice.textContent,/Automatic reload stopped/);

  // Changing movies during an earlier retry must never reload the new source.
  ctx.streamRetryAttempt=0;
  ctx.scheduleStreamRetry();
  ctx.lastState={stream_url:"/signed-new",stream_token:"movie-new"};
  const before=attachments.length;
  await fire();
  assert.equal(attachments.length,before);
  console.log("Decoder failures, bounded network retries, and source switch OK");
})().catch(err=>{console.error(err);process.exitCode=1;});
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node required for rendered Theater JS")
def test_decode_errors_stop_retry_and_network_retries_have_budget() -> None:
    html=movie_night_web._watch_html("room-retry",42,"uid=42&exp=9999999999&sig=test")
    a=html.index("function scheduleStreamRetry() {")
    b=html.index("function safeSeek(target) {",a)
    code=html[a:b]
    result=subprocess.run(
        ["node","-e",_NODE,json.dumps(code)],
        capture_output=True,text=True,check=False,timeout=20,
    )
    assert result.returncode==0,result.stderr or result.stdout


def test_loaded_metadata_does_not_unbind_retry_error_budget() -> None:
    html=movie_night_web._watch_html("room-retry-events",42,"uid=42&exp=9999999999&sig=test")
    a=html.index('video.addEventListener("loadedmetadata",()=>{')
    b=html.index('video.addEventListener("durationchange"',a)
    assert "streamRetryAttempt=0;" not in html[a:b]
    c=html.index('video.addEventListener("canplay",()=>{')
    d=html.index('video.addEventListener("waiting"',c)
    assert "streamRetryAttempt=0;" not in html[c:d]
    assert "if(attachedStreamUrl!==clean) streamRetryAttempt=0;" in html
    assert "const retryStreamToken=String(lastState.stream_token||\"\")" in html
