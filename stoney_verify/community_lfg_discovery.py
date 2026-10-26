from __future__ import annotations

"""Pure, source-neutral game-name filtering for Community Hub discovery.

The upstream services own authorization and visibility. This module only
filters already-authorized, bounded result snapshots; it never copies an
external record into Dank Shield's native session store.
"""

import re
import unicodedata
from typing import Any


# Conservative game-name aliases, not a fuzzy cross-game recommendation engine.
_GAME_ALIASES = {
    "mc": "minecraft",
    "minecraft java": "minecraft",
    "minecraft bedrock": "minecraft",
    "gta5": "grand theft auto v",
    "gta 5": "grand theft auto v",
    "gta v": "grand theft auto v",
    "gtav": "grand theft auto v",
    "grand theft auto 5": "grand theft auto v",
}


def game_search_key(value: Any) -> str:
    """Normalize casing and Unicode punctuation for comparable game titles."""
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    compact = " ".join(re.sub(r"[^a-z0-9]+", " ", normalized).split())[:100]
    return _GAME_ALIASES.get(compact, compact)


PLAY_STYLE_FILTERS = frozenset({"casual", "competitive", "beginner", "any"})
MIC_PREFERENCE_FILTERS = frozenset({"optional", "preferred", "required", "no_mic"})


def filter_discovery_games(
    rows: list[dict[str, Any]],
    query: str,
    *,
    external: bool = False,
    limit: int = 25,
    play_style: str = "",
    mic_preference: str = "",
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Filter *only* supplied public/native-authorized source rows by game.

    External titles, descriptions and member-authored notes are never searched:
    search uses the structured provider game field only. Native/partner sessions
    use canonical game_name, and filtering never expands the source permission
    boundary. Results retain the upstream order (normally newest first).
    """
    style = str(play_style or "").strip()
    mic = str(mic_preference or "").strip()
    if style and style not in PLAY_STYLE_FILTERS:
        raise ValueError("Unknown Community Hub play-style filter")
    if mic and mic not in MIC_PREFERENCE_FILTERS:
        raise ValueError("Unknown Community Hub microphone filter")
    if external and (style or mic):
        # The100.io's normalized public adapter has not verified these fields.
        # Never silently claim to apply a filter the provider cannot support.
        raise ValueError("External provider does not support session preference filters")

    safe_limit = max(1, min(int(limit), 25))
    safe_offset = max(0, min(int(offset), 100))
    key = game_search_key(query)
    matches: list[dict[str, Any]] = []
    skipped_matches = 0
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        if key:
            name = row.get("game") if external else row.get("game_name")
            candidate = game_search_key(name)
            if not candidate:
                continue
            # A game-family query may match a specific title; unrelated
            # descriptions and free-text member notes are not searchable.
            if not (
                candidate == key
                or candidate.startswith(key)
                or f" {key}" in f" {candidate}"
            ):
                continue
        # Exact-match against real, durable session metadata. Unknown values
        # are deliberately excluded when a preference filter is active.
        if style and row.get("play_style") != style:
            continue
        if mic and row.get("mic_preference") != mic:
            continue
        if skipped_matches < safe_offset:
            skipped_matches += 1
            continue
        matches.append(dict(row))
        if len(matches) >= safe_limit:
            break
    return matches
