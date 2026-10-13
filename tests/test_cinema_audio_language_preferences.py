from __future__ import annotations

"""Cinema audio language choice must be viewer-specific and guild-isolated."""

import asyncio
import json
import shutil
import subprocess

import pytest

from stoney_verify import cinema_library_service as library
from stoney_verify import movie_night_web


def test_audio_language_preferences_are_bounded_and_guild_scoped() -> None:
    preferences = library._normalize_preferences({
        "default_audio_language": "English",
        "audio_language_by_guild": {
            "1234": "en",
            "5678": "pt",
            "-2": "bad",
            "text": "es",
            "1000": "not a real language preference!!",
        },
    })
    assert preferences["default_audio_language"] == "English"
    assert preferences["audio_language_by_guild"] == {
        "1234": "en", "5678": "pt",
    }


def test_an_authenticated_guild_updates_only_its_own_language(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "user_id": 42,
        "preferences": {
            **library.DEFAULT_PREFERENCES,
            "audio_language_by_guild": {"22": "pt"},
        },
    }
    writes: list[dict] = []

    class FakeQuery:
        def upsert(self, payload, **_kwargs):
            writes.append(payload)
            state["preferences"] = payload["preferences"]
            return self

        def execute(self):
            return {"data": [state]}

    class FakeClient:
        def table(self, name):
            assert name == library.USER_TABLE
            return FakeQuery()

    async def fake_get(user_id, refresh=False):
        assert user_id == 42
        return state.copy()

    async def fake_execute(_label, callback):
        return callback(FakeClient())

    monkeypatch.setattr(library, "get_cinema_user", fake_get)
    monkeypatch.setattr(library, "_execute", fake_execute)
    monkeypatch.setattr(library, "invalidate_cinema_user_cache", lambda *_: None)

    async def check():
        await library.update_cinema_preferences(
            42, {"guild_audio_language": "en"}, guild_id=11,
        )
        assert state["preferences"]["audio_language_by_guild"] == {
            "11": "en", "22": "pt",
        }

        # The caller cannot replace every guild's settings wholesale.
        await library.update_cinema_preferences(
            42, {"audio_language_by_guild": {"22": "de"}}, guild_id=11,
        )
        assert state["preferences"]["audio_language_by_guild"]["22"] == "pt"

        # Auto is a per-guild setting; no global preference changes.
        await library.update_cinema_preferences(
            42, {"guild_audio_language": "auto"}, guild_id=11,
        )
        assert state["preferences"]["audio_language_by_guild"]["11"] == "auto"
        assert state["preferences"]["audio_language_by_guild"]["22"] == "pt"

        with pytest.raises(library.InvalidCinemaState):
            await library.update_cinema_preferences(
                42, {"guild_audio_language": "en"}, guild_id=None,
            )
        with pytest.raises(library.InvalidCinemaState):
            await library.update_cinema_preferences(
                42, {"guild_audio_language": "not_a_language!"}, guild_id=11,
            )

    asyncio.run(check())
    assert len(writes) == 3


_NODE_LANGUAGE = r"""
const assert = require("node:assert/strict");
const vm = require("node:vm");

const code = process.argv[1];
const ctx = {
  preferredAudioLanguage: "en",
  document: {getElementById: () => null},
  Array,
  String,
};
vm.createContext(ctx);
vm.runInContext(code, ctx);

const tracks = {audio_track_options: [
  {index:0,language:"por",label:"por • Portuguese 5.1"},
  {index:1,language:"eng",label:"eng • English 7.1"},
  {index:2,language:"jpn",label:"jpn • Japanese 2.0"},
]};
assert.equal(ctx.normalizedAudioLanguage("eng • English 7.1"), "en");
assert.equal(ctx.normalizedAudioLanguage("por"), "pt");
assert.equal(ctx.normalizedAudioLanguage("en-US"), "en");
assert.equal(ctx.preferredTrackForLanguage(tracks), "sidecar:1");
ctx.preferredAudioLanguage = "pt";
assert.equal(ctx.preferredTrackForLanguage(tracks), "sidecar:0");
ctx.preferredAudioLanguage = "de";
assert.equal(ctx.preferredTrackForLanguage(tracks), "original");
ctx.preferredAudioLanguage = "";
assert.equal(ctx.preferredTrackForLanguage(tracks), "original");
process.stdout.write("Cinema audio track language matching OK\\n");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node needed to test the actual Theater browser JS")
def test_rendered_theater_matches_audio_language_to_verified_tracks() -> None:
    html = movie_night_web._watch_html(
        "guild-audio-pref", 42, "uid=42&exp=9999999999&sig=test",
    )
    start = html.index("function normalizedAudioLanguage(value) {")
    end = html.index("function applyCompatAudioState(", start)
    result = subprocess.run(
        ["node", "-e", _NODE_LANGUAGE, html[start:end]],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_watch_page_uses_authenticated_preference_endpoint_and_canonical_title() -> None:
    html = movie_night_web._watch_html(
        "guild-audio-ui", 42, "uid=42&exp=9999999999&sig=test",
    )
    assert 'id="audioLanguage"' in html
    assert 'guild_audio_language:preferredAudioLanguage||"auto"' in html
    assert 'applyCompatAudioState(lastState,true)' in html
    assert 'control.hidden=!(compatAudioActive() && !userMuted && compatAudioNeedsGesture)' in html
    assert 's.movie?.title' in html or 'movie_metadata.get("title")' in open(
        movie_night_web.__file__, encoding="utf-8",
    ).read()
