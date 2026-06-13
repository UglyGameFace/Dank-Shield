from __future__ import annotations

"""Serialize Discord command sync operations through the shared queue.

Slash command cleanup already prunes stale local commands before sync. This guard
adds the bot-wide operation queue around CommandTree.sync so boot/redeploy paths,
manual force-syncs, and guild/global sync attempts cannot overlap or create a
Discord API storm.
"""

import hashlib
import json
from typing import Any, Optional

from discord import app_commands

_PATCHED = False
_ORIGINAL_SYNC = None


def _log(message: str) -> None:
    try:
        print(f"🧱 command_sync_operation_queue_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ command_sync_operation_queue_guard {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _guild_from_sync_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Optional[Any]:
    try:
        if "guild" in kwargs:
            return kwargs.get("guild")
        if args:
            return args[0]
    except Exception:
        pass
    return None


def _scope_label(guild: Optional[Any]) -> str:
    if guild is None:
        return "global"
    gid = _safe_int(getattr(guild, "id", guild), 0)
    return f"guild:{gid or 'unknown'}"


def _command_names(tree: app_commands.CommandTree[Any], *, guild: Optional[Any]) -> list[str]:
    try:
        return sorted(str(getattr(cmd, "name", "")) for cmd in tree.get_commands(guild=guild) if str(getattr(cmd, "name", "")).strip())
    except Exception:
        try:
            return sorted(str(getattr(cmd, "name", "")) for cmd in tree.get_commands() if str(getattr(cmd, "name", "")).strip())
        except Exception:
            return []


def _surface_hash(names: list[str]) -> str:
    raw = json.dumps(list(names or []), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:16]


async def _run_original_sync(tree: app_commands.CommandTree[Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if _ORIGINAL_SYNC is None:
        raise RuntimeError("original CommandTree.sync is not available")
    return await _ORIGINAL_SYNC(tree, *args, **kwargs)  # type: ignore[misc]


def install() -> bool:
    global _PATCHED, _ORIGINAL_SYNC
    if _PATCHED:
        return True

    _ORIGINAL_SYNC = app_commands.CommandTree.sync

    async def queued_sync(self: app_commands.CommandTree[Any], *args: Any, **kwargs: Any):
        guild = _guild_from_sync_args(args, kwargs)
        gid = _safe_int(getattr(guild, "id", guild), 0) if guild is not None else 0
        scope = _scope_label(guild)
        names = _command_names(self, guild=guild)
        surface_hash = _surface_hash(names)
        try:
            from ..operation_queue import run_exclusive

            state, result, job = await run_exclusive(
                guild_id=gid or "global",
                actor_id=None,
                operation_type="commands_sync",
                risk_level="dangerous",
                source="startup",
                payload={"scope": scope, "names": names, "surface_hash": surface_hash},
                concurrency_class="commands_sync",
                concurrency_key=scope,
                timeout_seconds=240.0,
                reject_if_busy=False,
                factory=lambda: _run_original_sync(self, args, kwargs),
            )
            if state in {"succeeded", "partial", "failed"} and result is not None:
                return result
            _warn(f"command sync returned no result scope={scope} state={state} job={job}")
            return []
        except Exception as e:
            _warn(f"queue unavailable for command sync scope={scope}; running original: {e!r}")
            return await _run_original_sync(self, args, kwargs)

    app_commands.CommandTree.sync = queued_sync  # type: ignore[assignment]
    _PATCHED = True
    _log("loaded; CommandTree.sync is serialized through operation queue")
    return True


install()

__all__ = ["install"]
