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


def filter_discovery_games(
    rows: list[dict[str, Any]],
    query: str,
    *,
    external: bool = False,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Filter *only* supplied public/native-authorized source rows by game.

    External titles, descriptions and member-authored notes are never searched:
    search uses the structured provider game field only. Native/partner sessions
    use canonical game_name, and filtering never expands the source permission
    boundary. Results retain the upstream order (normally newest first).
    """
    safe_limit = max(1, min(int(limit), 25))
    key = game_search_key(query)
    if not key:
        return [dict(row) for row in rows[:safe_limit] if isinstance(row, dict)]
    matches: list[dict[str, Any]] = []
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        name = row.get("game") if external else row.get("game_name")
        candidate = game_search_key(name)
        if not candidate:
            continue
        # A game family query may match a specific title; an unrelated
        # title containing letters inside a word must not match.
        if candidate == key or candidate.startswith(key + " ") or (" " + key + " ") in (" " + candidate + " "):
            matches.append(dict(row))
        if len(matches) >= safe_limit:
            break
    return matches
