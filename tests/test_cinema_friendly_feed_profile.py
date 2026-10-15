from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from stoney_verify import cinema_site


def _assets() -> tuple[str, str]:
    base = Path(cinema_site.__file__).resolve().parent / "assets"
    return (
        (base / "cinema_site.js").read_text(encoding="utf-8"),
        (base / "cinema_site.css").read_text(encoding="utf-8"),
    )


def test_unauthorized_numeric_identity_not_used_as_display_name(monkeypatch) -> None:
    from stoney_verify import globals as bot_globals

    class DummyGuild:
        name = "Example Discord"
        def get_member(self, user_id: int):
            return None

    class DummyBot:
        def get_guild(self, guild_id: int):
            return DummyGuild()
        def get_user(self, user_id: int):
            return None

    monkeypatch.setattr(bot_globals, "bot", DummyBot(), raising=False)
    context = cinema_site._safe_discord_context(123456789012345678, 629459300854661120)
    assert context["user_name"] == "Cinema Member"
    assert context["user_name"] != str(context["user_id"])
    assert context["guild_name"] == "Example Discord"
    assert context["avatar_url"] == ""
    assert context["user_id"] == 629459300854661120


def test_known_discord_identity_preserved(monkeypatch) -> None:
    from stoney_verify import globals as bot_globals
    class User:
        display_name = "Movie Fan"
        global_name = "Movie Fan"
        name = "Movie Fan"
        display_avatar = type("Avatar", (), {
            "url": "https://cdn.discordapp.com/avatars/123/example.png"
        })()
    class Guild:
        name = "Community"
        def get_member(self, _user_id):
            return User()
    class Bot:
        def get_guild(self, _guild_id):
            return Guild()
    monkeypatch.setattr(bot_globals, "bot", Bot(), raising=False)
    context = cinema_site._safe_discord_context(123456789012345678, 629459300854661120)
    assert context["user_name"] == "Movie Fan"
    assert context["avatar_url"].startswith("https://cdn.discordapp.com/")
    assert context["guild_name"] == "Community"


def test_feed_actions_do_not_pretend_unmatched_release_is_playable_movie() -> None:
    script, styles = _assets()
    assert 'const linkedMovie = result.media_type === "movie" && Number(result.tmdb_id || 0) > 0;' in script
    assert 'if (linkedMovie && result.playable !== false)' in script
    assert '"▶ Play Movie"' in script
    assert 'playOnSite(result, play, "", status)' in script
    assert '"Find Movie or Show"' in script
    assert '"Search by Movie Title"' in script
    assert '"View matching titles"' not in script
    assert 'input.value.trim().toLowerCase() === cleaned.toLowerCase()' in script
    assert '"A release was found, but Cinema hasn\'t matched it to a movie or show yet.' in script
    assert 'node("p", "feed-action-status")' in script
    assert 'status.setAttribute("role", "alert")' in script
    assert 'errorTarget.textContent = message;' in script
    assert '.feed-action-status:not([hidden])' in styles


def test_profile_remains_customizable_with_advanced_controls() -> None:
    script, styles = _assets()
    assert 'const advanced = node("details", "advanced-prefs");' in script
    assert 'settings.append(form, autoplay, feedPlayable, queueSuggestions, advanced, save)' in script
    # Preserve all previously saved preferences through the same data-pref
    # serializing loop, including those now inside the collapsed section.
    assert 'settings.querySelectorAll("[data-pref]")' in script
    for key in (
        "visual_quality",
        "playback_speed",
        "feed_notification_mode",
        "autoplay_next",
        "feed_playable_only",
        "feed_queue_suggestions",
        "preferred_source",
        "default_audio_language",
        "default_subtitle_language",
        "feed_min_seeds",
        "feed_preferred_resolutions",
        "feed_preferred_codecs",
        "feed_preferred_languages",
    ):
        assert f'"{key}"' in script
    assert 'api("/profile", { method: "POST", body: JSON.stringify(payload) })' in script
    assert '.advanced-prefs summary' in styles


def test_missing_artwork_is_intentional_and_not_a_blank_card() -> None:
    script, styles = _assets()
    assert '"card-art-fallback"' in script
    assert '"Artwork unavailable"' in script
    assert 'image.addEventListener("error"' in script
    assert '.card-art-fallback' in styles


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_cinema_browser_javascript_parses() -> None:
    script_path = Path(cinema_site.__file__).resolve().parent / "assets" / "cinema_site.js"
    result = subprocess.run(
        ["node", "--check", str(script_path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_feed_title_cleanup_preserves_real_names_without_release_tags() -> None:
    script, _styles = _assets()
    first = script.index("  function feedSearchTitle(result) {")
    ending = script.index("\n  }", first) + len("\n  }")
    js_function = script[first:ending]
    assertions = r'''
const cases = [
  ["Scary Movie (2026) [1080p] [WEBRip] [5.1]", "Scary Movie"],
  ["Scary.Movie.2026.1080p.WEBRip.x265", "Scary Movie"],
  ["The Matrix [1080p]", "The Matrix"],
  ["A 2020 Vision (2025)", "A 2020 Vision"],
  ["2026", "2026"],
  ["1923 S02E02", "1923 S02E02"]
];
for (const [title, expected] of cases) {
  const actual = feedSearchTitle({ title });
  if (actual !== expected) {
    throw new Error(JSON.stringify({ title, expected, actual }));
  }
}
'''
    result = subprocess.run(
        ["node", "-e", js_function + "\n" + assertions],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
