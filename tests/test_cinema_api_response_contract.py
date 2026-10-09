from __future__ import annotations

"""Exercise the real Cinema website API helper against browser-shaped HTTP replies."""

from pathlib import Path
import shutil
import subprocess

import pytest


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "stoney_verify/assets/cinema_site.js"

_NODE_TEST = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");

const source = fs.readFileSync(process.argv[1], "utf8");
const marker = "  async function api(path, options = {}) {";
const start = source.indexOf(marker);
const end = source.indexOf("  function node(", start);
assert.ok(start > 0 && end > start, "Find the real shared Cinema API function");
const section = source.slice(start + marker.length, end);
const makeApi = new Function(
  "fetch", "authUrl", "API_BASE", "authDiagnostics", "authDiagnosticText",
  "window", "console",
  "return (async function api(path, options = {}) {" + section + ");",
);

let nextResponse;
let requestedUrl = "";
let requestOptions;
const errors = [];
const fetch = async (url, options) => {
  requestedUrl = url;
  requestOptions = options;
  return nextResponse;
};
const api = makeApi(
  fetch,
  (path) => path,
  "/cinema/123/api",
  async () => null,
  () => "",
  { location: { href: "https://cinema.example/cinema/123" } },
  { error: (...args) => errors.push(args) },
);

function response(status, type, body, { redirected = false, url = "https://cinema.example/cinema/123/api/play" } = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    redirected,
    url,
    headers: { get: (name) => name.toLowerCase() === "content-type" ? type : null },
    text: async () => body,
    json: async () => JSON.parse(body),
  };
}

async function expectMessage(value) {
  await assert.rejects(
    api("/play", { method: "POST", body: '{"media_type":"episode"}' }),
    (error) => error instanceof Error && error.message.includes(value),
  );
}

(async () => {
  nextResponse = response(200, "text/html; charset=utf-8", "<!doctype html><title>Redirect</title>");
  await expectMessage("instead of JSON for /play");
  assert.equal(errors.at(-1)[0], "cinema_api_non_json_success");

  nextResponse = response(200, "text/html", "<!doctype html>", {
    redirected: true,
    url: "https://cinema.example/cinema/login?uid=secret",
  });
  await expectMessage("redirected to /cinema/login");
  assert.ok(!errors.at(-1)[1].finalPath.includes("secret"), "Never log signed parameters");

  nextResponse = response(502, "text/html", "<!doctype html><title>Gateway error</title>");
  await expectMessage("HTML error page (HTTP 502)");

  nextResponse = response(409, "text/plain; charset=utf-8", "No playable source is available");
  await expectMessage("No playable source is available");

  nextResponse = response(409, "application/json", '{"error":"Exact episode unavailable"}');
  await expectMessage("Exact episode unavailable");

  nextResponse = response(200, "application/json; charset=utf-8", '{"ok":true,"watch_url":"/movie/demo/watch"}');
  const result = await api("/play", { method: "POST", body: "{}" });
  assert.equal(result.watch_url, "/movie/demo/watch");
  assert.equal(requestedUrl, "/cinema/123/api/play");
  assert.equal(requestOptions.credentials, "same-origin");
  assert.equal(requestOptions.headers.Accept, "application/json");

  nextResponse = response(200, "application/problem+json", '{"ok":true}');
  assert.equal((await api("/home")).ok, true);

  nextResponse = response(200, "application/json", "<!doctype html>");
  await expectMessage("invalid JSON (HTTP 200)");

  nextResponse = response(204, "", "");
  await expectMessage("instead of JSON for /play");

  console.log("Cinema API success, HTML, redirect, errors, and invalid JSON verified");
})().catch((error) => {
  process.stderr.write(String(error?.stack || error) + "\n");
  process.exitCode = 1;
});
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for browser-helper execution")
def test_cinema_site_api_never_parses_html_as_success_json() -> None:
    completed = subprocess.run(
        ["node", "-e", _NODE_TEST, str(_SCRIPT_PATH)],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
