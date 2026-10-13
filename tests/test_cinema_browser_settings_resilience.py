from __future__ import annotations

"""Test real generated Cinema JS against browser settings and page lifecycle."""

import json
import shutil
import subprocess

import pytest

from stoney_verify import movie_night_web


_NODE_TEST = r"""
const assert = require("node:assert/strict");
const vm = require("node:vm");
const parts = JSON.parse(process.argv[1]);

const listeners = {};
const mq = {
  "(forced-colors: active)": {matches:false},
  "(prefers-contrast: more)": {matches:false},
  "(prefers-reduced-motion: reduce)": {matches:false},
  "(prefers-color-scheme: dark)": {matches:true},
};
const window = {
  matchMedia(query) {
    const m = mq[query];
    return m
      ? {get matches() {return m.matches;}, addEventListener(type, fn) {
          listeners["media:"+query+":"+type] = fn;
        }}
      : null;
  },
  addEventListener(name, fn) {listeners["window:"+name] = fn;},
};
const elements = {browserEnvironment:{textContent:""}};
const document = {
  hidden:false,
  documentElement:{dataset:{qualityPreference:"auto"}},
  getElementById(name) { return elements[name] ?? null; },
  addEventListener(name, fn) {listeners["document:"+name] = fn;},
};
let onLine = true;
const connection = {
  saveData:false,
  addEventListener(name, fn) {listeners["connection:"+name]=fn;},
};
const navigator = {
  connection,
  get onLine() {return onLine;},
};
let autoCalls = [];
const ctx = {
  window, document, navigator, Number, String, Date, Math,
  applyQualityMode: preference => autoCalls.push(preference),
};
vm.createContext(ctx);
vm.runInContext(parts.browserSettings, ctx);
ctx.installBrowserSettingsListeners();
assert.match(elements.browserEnvironment.textContent, /Default contrast/);
assert.match(elements.browserEnvironment.textContent, /Data Saver not reported/);
assert.equal(autoCalls.length, 0, "Initializing diagnostics should not change video quality");

mq["(forced-colors: active)"].matches=true;
mq["(prefers-reduced-motion: reduce)"].matches=true;
connection.saveData=true;
listeners["media:(forced-colors: active):change"]();
assert.match(elements.browserEnvironment.textContent, /Forced colors/);
assert.match(elements.browserEnvironment.textContent, /Reduced motion/);
assert.match(elements.browserEnvironment.textContent, /Data Saver on/);
assert.deepEqual(autoCalls, ["auto"]);

document.documentElement.dataset.qualityPreference="high";
listeners["connection:change"]();
assert.deepEqual(autoCalls, ["auto"], "Manual visual quality must stay manual");

onLine=false;
listeners["window:offline"]();
assert.match(elements.browserEnvironment.textContent, /Offline/);
document.hidden=true;
listeners["document:visibilitychange"]();
assert.match(elements.browserEnvironment.textContent, /Background tab/);
onLine=true;
document.hidden=false;

let now=50000;
let recoveryCalls=[];
let layoutCalls=0;
const wakeCtx={
  document, navigator,
  get terminated() {return false;},
  pageHiddenAt:40000,
  lastPageWakeAt:0,
  BACKGROUND_MEDIA_REFRESH_MS:30000,
  Date:{now:()=>now},
  recoverPlayerFromViewportChange:()=>{layoutCalls++;},
  recoverSessionConnection:(full)=>{recoveryCalls.push(full);return Promise.resolve(true);},
};
vm.createContext(wakeCtx);
vm.runInContext(parts.wake, wakeCtx);
wakeCtx.handlePageWake();
assert.equal(recoveryCalls.length,1);
assert.equal(recoveryCalls[0],false, "Short background visits do not force media reload");
now+=150;
wakeCtx.handlePageWake();
assert.equal(recoveryCalls.length,1, "pageshow + visibilitychange must not double-recover");

now+=1400;
document.hidden=true;
wakeCtx.handlePageWake();
assert.equal(recoveryCalls.length,1);
document.hidden=false;
onLine=false;
wakeCtx.handlePageWake();
assert.equal(recoveryCalls.length,1);
onLine=true;
wakeCtx.pageHiddenAt=10000;
wakeCtx.handlePageWake();
assert.equal(recoveryCalls.length,2);
assert.equal(recoveryCalls[1],true, "Long suspension invokes one media recovery");
assert.equal(layoutCalls,2);
console.log("Cinema forced colors, data saver, quality preference and mobile wake behavior OK");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node required for rendered browser state")
def test_browser_settings_and_page_wake_use_live_theater_handlers() -> None:
    html = movie_night_web._watch_html(
        "browser-settings-test", 42, "uid=42&exp=9999999999&sig=test",
    )
    settings_start = html.index("function browserEnvironmentSummary() {")
    settings_end = html.index("function applyQualityMode(preference)", settings_start)
    wake_start = html.index("function handlePageWake() {")
    wake_end = html.index('window.addEventListener("pageshow"', wake_start)
    parts = {
        "browserSettings":html[settings_start:settings_end],
        "wake":html[wake_start:wake_end],
    }
    result = subprocess.run(
        ["node","-e",_NODE_TEST,json.dumps(parts)],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode==0, result.stderr or result.stdout


def test_appearance_diagnostics_do_not_touch_media_or_expose_user_agent() -> None:
    html = movie_night_web._watch_html(
        "appearance-settings-test", 42, "uid=42&exp=9999999999&sig=test",
    )
    assert "@media (forced-colors:active)" in html
    assert "@media (prefers-contrast:more)" in html
    assert "@media (prefers-reduced-motion:reduce)" in html
    assert "forced-color-adjust:none;filter:none !important" in html
    assert 'id="browserEnvironment"' in html
    assert "browserEnvironmentSummary()" in html
    assert "installBrowserSettingsListeners();" in html
    start = html.index("function browserEnvironmentSummary() {")
    end = html.index("function applyQualityMode(preference)", start)
    snippet = html[start:end]
    assert "userAgent" not in snippet
    assert "localStorage" not in snippet
    assert "video.src=" not in snippet
    assert "fetch(" not in snippet


def test_browser_cache_and_media_protection_remain_intact() -> None:
    from inspect import getsource

    source = getsource(movie_night_web.movie_night_watch)
    assert '"Cache-Control": "private, no-store"' in source
    assert '"Referrer-Policy": "no-referrer"' in source
    assert '"Content-Security-Policy": (' in source


def test_catalog_and_theater_share_system_accessibility_contract() -> None:
    from pathlib import Path

    css=(
        Path(movie_night_web.__file__).resolve().parent
        / "assets" / "cinema_site.css"
    ).read_text(encoding="utf-8")
    assert "@media(forced-colors:active)" in css
    assert "@media(prefers-contrast:more)" in css
    assert "@media(prefers-reduced-motion:reduce)" in css
    assert "-webkit-text-size-adjust:auto;text-size-adjust:auto" in css
    assert "forced-color-adjust:none;filter:none!important" in css
    assert ":focus-visible" in css
