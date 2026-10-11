from __future__ import annotations

"""Small per-guild durable store for active /toke meetup cards.

The store persists identifiers and choices, never member messages or media.
Discloud deployments must preserve DANK_DATA_DIR between restarts.
"""

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

SESSIONS_FILE = Path(os.getenv("DANK_TOKE_SESSIONS_FILE", str(Path(os.getenv("DANK_DATA_DIR", "data")) / "toke_sessions.json")))
_LOCK = asyncio.Lock()
_RETENTION_SECONDS = 86400


def _read() -> dict[str, dict[str, Any]]:
    if not SESSIONS_FILE.exists():
        return {}
    data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Toke session storage must be a JSON object.")
    return {str(k): dict(v) for k, v in data.items() if isinstance(v, Mapping)}


def _write(data: dict[str, dict[str, Any]]) -> None:
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".toke-", suffix=".json", dir=str(SESSIONS_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, separators=(",", ":"), sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, SESSIONS_FILE)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _id(guild_id: int, message_id: int) -> str:
    return f"{int(guild_id)}:{int(message_id)}"


def _expire(data: dict[str, dict[str, Any]], now: float) -> bool:
    changed = False
    for key, session in list(data.items()):
        until = float(session.get("expires_at", 0) or 0)
        if session.get("active", False) and until <= now:
            session["active"] = False
            changed = True
        if until + _RETENTION_SECONDS < now:
            data.pop(key, None)
            changed = True
    return changed


async def save_new(session: Mapping[str, Any]) -> dict[str, Any]:
    gid, mid = int(session["guild_id"]), int(session["message_id"])
    if gid <= 0 or mid <= 0:
        raise ValueError("Toke session identifiers are invalid.")
    value = dict(session)
    value["guild_id"], value["message_id"] = gid, mid
    value["cheers_ids"], value["going_ids"] = [], []
    value["active"] = True
    async with _LOCK:
        data = _read()
        _expire(data, time.time())
        data[_id(gid, mid)] = value
        _write(data)
        return dict(value)


async def get(guild_id: int, message_id: int) -> dict[str, Any] | None:
    async with _LOCK:
        data = _read()
        changed = _expire(data, time.time())
        if changed:
            _write(data)
        result = data.get(_id(guild_id, message_id))
        return dict(result) if result else None


async def all_active(guild_id: int | None = None) -> list[dict[str, Any]]:
    async with _LOCK:
        data = _read()
        if _expire(data, time.time()):
            _write(data)
        return [
            dict(session) for session in data.values()
            if session.get("active", False)
            and (guild_id is None or int(session.get("guild_id", 0)) == int(guild_id))
        ]


async def react(
    guild_id: int,
    message_id: int,
    actor_id: int,
    action: str,
    *,
    max_interested: int = 100,
) -> tuple[dict[str, Any] | None, str]:
    if actor_id <= 0 or action not in {"cheers", "going", "withdraw", "end"}:
        return None, "Invalid response."
    async with _LOCK:
        data = _read()
        changed = _expire(data, time.time())
        key = _id(guild_id, message_id)
        session = data.get(key)
        if not session or not session.get("active", False):
            if changed:
                _write(data)
            return None, "This sesh has ended or expired."
        cheers = list(dict.fromkeys(int(x) for x in session.get("cheers_ids", []) if int(x) > 0))
        going = list(dict.fromkeys(int(x) for x in session.get("going_ids", []) if int(x) > 0))
        if action == "end":
            session["active"] = False
        elif action == "withdraw":
            cheers = [x for x in cheers if x != actor_id]
            going = [x for x in going if x != actor_id]
        else:
            if actor_id not in cheers and len(cheers) >= max_interested:
                return dict(session), "This sesh has reached its participant limit."
            if actor_id not in cheers:
                cheers.append(actor_id)
            if action == "going" and actor_id not in going:
                going.append(actor_id)
        session["cheers_ids"], session["going_ids"] = cheers, going
        _write(data)
        return dict(session), ""


async def end_expired() -> list[dict[str, Any]]:
    """Return active cards just expired; caller edits their Discord buttons."""
    async with _LOCK:
        data = _read()
        now = time.time()
        expired = [
            dict(s) for s in data.values()
            if s.get("active", False) and float(s.get("expires_at", 0) or 0) <= now
        ]
        if _expire(data, now):
            _write(data)
        return expired
