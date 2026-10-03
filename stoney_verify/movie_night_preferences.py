from __future__ import annotations

"""Per-guild Dank Cinema viewer-experience preferences.

This module owns lightweight Movie Night product settings only. Provider
configuration stays in media_source_registry.py and Community & Pings stays in
community_pings_service.py.
"""

from dataclasses import dataclass
from typing import Any, Mapping

MOVIE_NIGHT_PREFERENCES_KEY = "movie_night_preferences_v1"
MOVIE_NIGHT_PREFERENCES_VERSION = 1


@dataclass(frozen=True)
class MovieNightPreferences:
    revision: int = 0
    adult_content_enabled: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": MOVIE_NIGHT_PREFERENCES_VERSION,
            "revision": int(self.revision),
            "adult_content_enabled": bool(self.adult_content_enabled),
        }


def parse_movie_night_preferences(raw_config: Mapping[str, Any]) -> MovieNightPreferences:
    blob = raw_config.get(MOVIE_NIGHT_PREFERENCES_KEY)
    if not isinstance(blob, Mapping):
        return MovieNightPreferences()
    try:
        revision = max(0, int(blob.get("revision") or 0))
    except Exception:
        revision = 0
    return MovieNightPreferences(
        revision=revision,
        adult_content_enabled=bool(blob.get("adult_content_enabled", False)),
    )


def set_adult_content_enabled(
    preferences: MovieNightPreferences,
    enabled: bool,
) -> MovieNightPreferences:
    return MovieNightPreferences(
        revision=int(preferences.revision) + 1,
        adult_content_enabled=bool(enabled),
    )


async def load_movie_night_preferences(
    guild_id: int,
    *,
    refresh: bool = False,
) -> tuple[Mapping[str, Any], MovieNightPreferences]:
    from stoney_verify.guild_config import get_guild_config

    raw = await get_guild_config(int(guild_id), refresh=bool(refresh))
    return raw, parse_movie_night_preferences(raw)


async def save_movie_night_preferences(
    guild_id: int,
    *,
    expected_config: Mapping[str, Any],
    updated: MovieNightPreferences,
) -> tuple[bool, Mapping[str, Any]]:
    from stoney_verify.guild_config import compare_and_swap_guild_config_key

    applied, saved = await compare_and_swap_guild_config_key(
        int(guild_id),
        MOVIE_NIGHT_PREFERENCES_KEY,
        expected=expected_config.get(MOVIE_NIGHT_PREFERENCES_KEY),
        value=updated.to_payload(),
        source="movie_night_preferences",
    )
    return bool(applied), saved


__all__ = [
    "MOVIE_NIGHT_PREFERENCES_KEY",
    "MOVIE_NIGHT_PREFERENCES_VERSION",
    "MovieNightPreferences",
    "load_movie_night_preferences",
    "parse_movie_night_preferences",
    "save_movie_night_preferences",
    "set_adult_content_enabled",
]
