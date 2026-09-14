from __future__ import annotations

"""Native Discord command/runtime ownership for Dank Shield.

This module contains reusable command-surface and bot-construction behavior, but
it never monkey-patches discord.py. ``globals.py`` owns bot construction and
``app.py`` owns when command sync/cleanup runs.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from .command_surface_contract import (
    PUBLIC_DANK_CHILDREN,
    PUBLIC_GLOBAL_COMMAND_NAMES,
)

GLOBAL_COMMAND_HARD_LIMIT = 100
GLOBAL_COMMAND_WARN_AT = 90
COMMAND_SYNC_EPOCH = "2026-09-14-menu-first-native-command-owner-v1"


def _env_str(name: str, default: str = "") -> str:
    try:
        raw = os.getenv(name)
        if raw is None:
            return default
        value = str(raw).strip()
        return value if value else default
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env_str(name, "").lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int = 0) -> int:
    raw = _env_str(name, "")
    if not raw:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


def _env_int_set(name: str) -> set[int]:
    out: set[int] = set()
    raw = _env_str(name, "")
    if not raw:
        return out
    for part in raw.replace(";", ",").replace(" ", ",").split(","):
        text = part.strip()
        if not text:
            continue
        try:
            value = int(text)
        except Exception:
            continue
        if value > 0:
            out.add(value)
    return out


def public_command_scope_enabled() -> bool:
    profile = _env_str("DANK_COMMAND_PROFILE", "public").lower()
    deployment = _env_str("DANK_DEPLOYMENT_MODE", "").lower()
    if not deployment:
        if _env_bool("DANK_PRODUCTION_MODE", False):
            deployment = "production"
        elif _env_bool("DANK_PUBLIC_MODE", False):
            deployment = "public"
        else:
            deployment = "development"
    return profile in {"public", "minimal"} or deployment in {"public", "prod", "production"}


def auto_shard_enabled() -> bool:
    return _env_bool("DISCORD_AUTO_SHARD", False)


def configured_shard_count() -> Optional[int]:
    value = _env_int("DISCORD_SHARD_COUNT", 0)
    return value if value > 0 else None


def create_discord_bot(
    *,
    command_prefix: str,
    intents: discord.Intents,
    help_command: Any = None,
) -> commands.Bot:
    """Create the one shared bot without replacing ``commands.Bot`` globally."""

    use_auto_shard = auto_shard_enabled()
    bot_cls: type[commands.Bot] = commands.AutoShardedBot if use_auto_shard else commands.Bot
    kwargs: dict[str, Any] = {
        "command_prefix": command_prefix,
        "intents": intents,
        "help_command": help_command,
    }
    shard_count = configured_shard_count() if use_auto_shard else None
    if shard_count is not None:
        kwargs["shard_count"] = shard_count

    instance = bot_cls(**kwargs)
    try:
        print(
            "🧭 command_runtime bot created "
            f"class={instance.__class__.__name__} "
            f"auto_shard={use_auto_shard} "
            f"configured_shard_count={shard_count or 'auto'}"
        )
    except Exception:
        pass
    return instance


def _command_name(command: Any) -> str:
    try:
        return str(getattr(command, "name", "") or getattr(command, "qualified_name", "") or repr(command))
    except Exception:
        return "unknown"


def _global_commands(tree: Any) -> list[Any]:
    try:
        return list(tree.get_commands(guild=None) or [])
    except Exception:
        commands_map = getattr(tree, "_global_commands", {}) or {}
        if isinstance(commands_map, dict):
            return list(commands_map.values())
        return []


def _guild_command_count(tree: Any) -> int:
    total = 0
    try:
        for value in (getattr(tree, "_guild_commands", {}) or {}).values():
            total += len(value or {})
    except Exception:
        pass
    return int(total)


def command_budget_snapshot(tree: Any) -> dict[str, Any]:
    commands_now = _global_commands(tree)
    names = [_command_name(command) for command in commands_now]
    count = len(names)
    return {
        "global_count": count,
        "global_limit": GLOBAL_COMMAND_HARD_LIMIT,
        "global_remaining": max(0, GLOBAL_COMMAND_HARD_LIMIT - count),
        "guild_command_count": _guild_command_count(tree),
        "global_names": names,
    }


def _dank_child_names(tree: Any) -> tuple[str, ...]:
    try:
        dank = tree.get_command("dank", guild=None)
    except TypeError:
        dank = tree.get_command("dank")
    except Exception:
        dank = None
    if dank is None:
        return ()
    try:
        return tuple(
            sorted(
                str(getattr(child, "name", ""))
                for child in list(getattr(dank, "commands", []) or [])
                if str(getattr(child, "name", "")).strip()
            )
        )
    except Exception:
        return ()


def validate_public_command_surface(tree: Any) -> dict[str, Any]:
    """Fail closed if the menu-first public surface drifted before Discord login."""

    snapshot = command_budget_snapshot(tree)
    actual_names = tuple(sorted(str(name) for name in snapshot["global_names"]))
    expected_names = tuple(sorted(PUBLIC_GLOBAL_COMMAND_NAMES))
    if actual_names != expected_names:
        missing = sorted(set(expected_names) - set(actual_names))
        unexpected = sorted(set(actual_names) - set(expected_names))
        raise RuntimeError(
            "Dank Shield public command surface drifted before login: "
            f"missing={missing} unexpected={unexpected} actual={list(actual_names)}"
        )

    actual_children = _dank_child_names(tree)
    expected_children = tuple(sorted(PUBLIC_DANK_CHILDREN))
    if actual_children != expected_children:
        missing = sorted(set(expected_children) - set(actual_children))
        unexpected = sorted(set(actual_children) - set(expected_children))
        raise RuntimeError(
            "Dank Shield /dank menu-first surface drifted before login: "
            f"missing={missing} unexpected={unexpected} actual={list(actual_children)}"
        )

    return snapshot


def validate_global_sync_budget(tree: Any, *, public_scope: bool) -> dict[str, Any]:
    snapshot = validate_public_command_surface(tree) if public_scope else command_budget_snapshot(tree)
    count = int(snapshot.get("global_count", 0) or 0)
    limit = max(1, _env_int("DANK_GLOBAL_COMMAND_SYNC_LIMIT", 25))
    allow_large = _env_bool("DANK_ALLOW_LARGE_GLOBAL_COMMAND_SYNC", False)

    if count > GLOBAL_COMMAND_HARD_LIMIT:
        raise RuntimeError(
            f"Discord global command hard limit exceeded: commands={count} hard_limit={GLOBAL_COMMAND_HARD_LIMIT}"
        )
    if count > limit and not allow_large:
        raise RuntimeError(
            "Dank Shield blocked global command sync: "
            f"commands={count} configured_limit={limit}. "
            "Consolidate the public surface or explicitly set DANK_ALLOW_LARGE_GLOBAL_COMMAND_SYNC=true."
        )

    if count >= GLOBAL_COMMAND_WARN_AT:
        print(
            "⚠️ command_runtime global command budget high "
            f"commands={count}/{GLOBAL_COMMAND_HARD_LIMIT}"
        )
    else:
        print(
            "🧭 command_runtime command budget "
            f"commands={count}/{GLOBAL_COMMAND_HARD_LIMIT} configured_sync_limit={limit} "
            f"allow_large={allow_large}"
        )
    return snapshot


def _command_payload(command: Any) -> Any:
    try:
        payload = command.to_dict()
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    children: list[Any] = []
    try:
        children = [_command_payload(child) for child in list(getattr(command, "commands", []) or [])]
    except Exception:
        pass
    return {
        "name": _command_name(command),
        "description": str(getattr(command, "description", "") or ""),
        "children": children,
    }


def command_surface_hash(tree: Any) -> str:
    commands_now = sorted(_global_commands(tree), key=_command_name)
    payload = [_command_payload(command) for command in commands_now]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()


def _sync_state_path() -> Path:
    return Path(_env_str("DANK_COMMAND_SYNC_STATE_FILE", ".dank_command_sync_state.json"))


def _read_sync_state() -> dict[str, Any]:
    path = _sync_state_path()
    try:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_sync_state(state: dict[str, Any]) -> None:
    path = _sync_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, sort_keys=True, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"⚠️ command_runtime could not write command sync state: {type(exc).__name__}: {exc}")


def should_skip_unchanged_global_sync(tree: Any, *, public_scope: bool) -> tuple[bool, str]:
    surface_hash = command_surface_hash(tree)
    if not public_scope:
        return False, surface_hash
    if _env_bool("DANK_FORCE_COMMAND_SYNC_ON_BOOT", False):
        return False, surface_hash
    if not _env_bool("DANK_SKIP_UNCHANGED_GLOBAL_SYNC", True):
        return False, surface_hash
    state = _read_sync_state()
    skip = (
        str(state.get("global", "")) == surface_hash
        and str(state.get("sync_epoch", "")) == COMMAND_SYNC_EPOCH
    )
    return skip, surface_hash


def remember_global_sync(surface_hash: str, *, public_scope: bool) -> None:
    if not public_scope:
        return
    state = _read_sync_state()
    state["global"] = str(surface_hash)
    state["sync_epoch"] = COMMAND_SYNC_EPOCH
    _write_sync_state(state)


@dataclass(frozen=True)
class CommandSyncResult:
    commands: list[Any]
    skipped: bool
    scope: str
    surface_hash: str = ""


async def sync_command_tree(
    tree: app_commands.CommandTree[Any],
    *,
    guild: Optional[discord.abc.Snowflake] = None,
    public_scope: bool,
    reason: str,
    force: bool = False,
) -> CommandSyncResult:
    """Synchronize through one explicit owner without replacing CommandTree.sync."""

    if guild is None:
        validate_global_sync_budget(tree, public_scope=public_scope)
        skip, surface_hash = should_skip_unchanged_global_sync(tree, public_scope=public_scope)
        if skip and not force:
            print(
                "🧭 command_runtime skipped unchanged global command sync "
                f"hash={surface_hash[:12]} epoch={COMMAND_SYNC_EPOCH} reason={reason}"
            )
            return CommandSyncResult([], True, "global", surface_hash)

        synced = list(await tree.sync())
        remember_global_sync(surface_hash, public_scope=public_scope)
        print(
            "🌐 command_runtime global slash sync complete "
            f"commands={len(synced)} hash={surface_hash[:12]} reason={reason} force={force}"
        )
        return CommandSyncResult(synced, False, "global", surface_hash)

    guild_id = int(getattr(guild, "id", 0) or 0)
    synced = list(await tree.sync(guild=guild))
    print(
        "🌐 command_runtime guild slash sync complete "
        f"guild={guild_id} commands={len(synced)} reason={reason}"
    )
    return CommandSyncResult(synced, False, f"guild:{guild_id}")


def configured_guild_cleanup_ids() -> set[int]:
    ids = _env_int_set("DANK_GUILD_COMMAND_CLEANUP_IDS")
    ids |= _env_int_set("GUILD_ID")
    ids |= _env_int_set("DANK_BETA_GUILD_ID")
    return {guild_id for guild_id in ids if guild_id > 0}


async def clear_stale_guild_command_copies(
    tree: app_commands.CommandTree[Any],
    *,
    public_scope: bool,
) -> dict[str, Any]:
    """Clear only explicitly configured stale guild-scoped copies in public mode."""

    if not public_scope:
        return {"status": "skipped_non_public", "cleared": [], "failed": []}
    if _env_bool("DANK_SYNC_BETA_GUILD_COMMANDS", False):
        return {"status": "skipped_beta_sync_enabled", "cleared": [], "failed": []}
    if not _env_bool("DANK_CLEAR_BETA_GUILD_COMMANDS_ON_BOOT", True):
        return {"status": "disabled", "cleared": [], "failed": []}

    cleared: list[int] = []
    failed: list[dict[str, Any]] = []
    for guild_id in sorted(configured_guild_cleanup_ids()):
        guild_obj = discord.Object(id=guild_id)
        try:
            tree.clear_commands(guild=guild_obj)
            result = await sync_command_tree(
                tree,
                guild=guild_obj,
                public_scope=public_scope,
                reason="stale_guild_copy_cleanup",
            )
            if result.commands:
                raise RuntimeError(
                    f"guild cleanup sync returned {len(result.commands)} command(s) instead of zero"
                )
            cleared.append(guild_id)
        except Exception as exc:
            failed.append({"guild_id": guild_id, "error": f"{type(exc).__name__}: {exc}"})
            print(
                "⚠️ command_runtime failed clearing stale guild command copy "
                f"guild={guild_id}: {type(exc).__name__}: {exc}"
            )

    status = "ok" if not failed else "partial_failure"
    print(
        "🧹 command_runtime stale guild command cleanup "
        f"status={status} cleared={cleared} failed={[item['guild_id'] for item in failed]}"
    )
    return {"status": status, "cleared": cleared, "failed": failed}


__all__ = [
    "COMMAND_SYNC_EPOCH",
    "CommandSyncResult",
    "auto_shard_enabled",
    "clear_stale_guild_command_copies",
    "command_budget_snapshot",
    "command_surface_hash",
    "configured_guild_cleanup_ids",
    "configured_shard_count",
    "create_discord_bot",
    "public_command_scope_enabled",
    "remember_global_sync",
    "should_skip_unchanged_global_sync",
    "sync_command_tree",
    "validate_global_sync_budget",
    "validate_public_command_surface",
]
