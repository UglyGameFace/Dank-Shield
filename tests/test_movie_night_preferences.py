from __future__ import annotations

from stoney_verify.movie_night_preferences import (
    MOVIE_NIGHT_PREFERENCES_KEY,
    MovieNightPreferences,
    parse_movie_night_preferences,
    set_adult_content_enabled,
)


def test_movie_night_preferences_default_adult_content_off() -> None:
    prefs = parse_movie_night_preferences({})
    assert prefs == MovieNightPreferences()
    assert prefs.adult_content_enabled is False


def test_movie_night_preferences_round_trip_and_revision() -> None:
    raw = {
        MOVIE_NIGHT_PREFERENCES_KEY: {
            "version": 1,
            "revision": 4,
            "adult_content_enabled": True,
        }
    }
    prefs = parse_movie_night_preferences(raw)
    assert prefs.revision == 4
    assert prefs.adult_content_enabled is True
    assert prefs.to_payload()["adult_content_enabled"] is True

    updated = set_adult_content_enabled(prefs, False)
    assert updated.revision == 5
    assert updated.adult_content_enabled is False


def test_invalid_movie_night_preferences_fall_back_safely() -> None:
    prefs = parse_movie_night_preferences(
        {MOVIE_NIGHT_PREFERENCES_KEY: {"revision": "not-an-int"}}
    )
    assert prefs.revision == 0
    assert prefs.adult_content_enabled is False
