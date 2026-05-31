from __future__ import annotations

"""Prevent duplicate global+guild slash command surfaces in production.

Discord shows duplicate slash suggestions when the same app has both:
- global commands, and
- guild-scoped copies of those commands

For a public production bot, global commands should be the normal command surface.
Guild-scoped copies are only for intentional beta/test servers.

This guard:
1. Defaults STONEY_SYNC_BETA_GUILD_COMMANDS to false unless explicitly set.
2. After startup sync finishes, clears old guild-scoped command copies only for
   explicitly configured cleanup guild IDs such as GUILD_ID or
   DANK_GUILD_COMMAND_CLEANUP_IDS.

It does not clear global commands, does not touch random customer guilds, and
skips itself when beta guild command syncing is explicitly enabled.
"""

import asyncio
import os
from typing import Any

import discord

try:
    from ..globals import bot
except Exception:  # pragma: no cover - startup fallback
    bot = None  # type: ignore

_RAN = False


def _log(message: str) -> None:
    try:
        print(f"✅ command_scope_dedupe: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ command_scope_dedupe: {message}")
    except Exception:
        pass


def _env_raw(name: str) -> str | None:
    try:
        return os.getenv(name)
    except Exception:
        return None


def _env_str(name: str, default: str = "") -> str:
    raw = _env_raw(name)
    if raw is None:
        return default
    text = str(raw).strip()
    return text if text else default


def _env_true(name: str, default: bool = False) -> bool:
    raw = _env_raw(name)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_explicit(name: str) -> bool:
    raw = _env_raw(name)
    return raw is not None and str(raw).strip() != ""


def _env_int_set(name: str) -> set[int]:
    out: set[int] = set()
    raw = _env_str(name, "")
    if not raw:
        return out
    for item in raw.replace(";", ",").replace(" ", ",").split(","):
        text = str(item or "").strip()
        if not text:
            continue
        try:
            value = int(text)
            if value > 0:
                out.add(value)
        except Exception:
            continue
    return out


def _public_scope_enabled() -> bool:
    profile = _env_str("STONEY_COMMAND_PROFILE", "public").lower()
    deployment = _env_str("STONEY_DEPLOYMENT_MODE", "").lower()
    if not deployment:
        if _env_true("STONEY_PRODUCTION_MODE", False):
            deployment = "production"
        elif _env_true("STONEY_PUBLIC_MODE", False):
            deployment = "public"
        else:
            deployment = "development"
    return profile in {"public", "minimal"} or deployment in {"public", "prod", "production"}


def _beta_guild_sync_explicitly_enabled() -> bool:
    return _env_explicit("STONEY_SYNC_BETA_GUILD_COMMANDS") and _env_true("STONEY_SYNC_BETA_GUILD_COMMANDS", False)


def _cleanup_guild_ids() -> set[int]:
    ids: set[int] = set()
    ids |= _env_int_set("DANK_GUILD_COMMAND_CLEANUP_IDS")
    ids |= _env_int_set("STONEY_GUILD_COMMAND_CLEANUP_IDS")
    ids |= _env_int_set("GUILD_ID")
    ids |= _env_int_set("STONEY_BETA_GUILD_ID")
    ids |= _env_int_set("DANK_BETA_GUILD_ID")
    return {gid for gid in ids if gid > 0}


def _default_beta_sync_off() -> None:
    # Old behavior defaulted this on, which creates global+guild duplicates in
    # production. Keep explicit env values respected for beta testing.
    if not _env_explicit("STONEY_SYNC_BETA_GUILD_COMMANDS"):
        os.environ["STONEY_SYNC_BETA_GUILD_COMMANDS"] = "false"
        _log("defaulted STONEY_SYNC_BETA_GUILD_COMMANDS=false to avoid duplicate slash commands")


async def _clear_one_guild_copy(guild_id: int) -> bool:
    if bot is None:
        return False
    try:
        guild_obj = discord.Object(id=int(guild_id))
        bot.tree.clear_commands(guild=guild_obj)
        synced = await bot.tree.sync(guild=guild_obj)
        _log(f"cleared guild-scoped slash command copy guild={guild_id} remaining={len(synced)}")
        return True
    except Exception as e:
        _warn(f"failed clearing guild-scoped command copy guild={guild_id}: {type(e).__name__}: {e}")
        return False


async def _late_cleanup_task() -> None:
    global _RAN
    if _RAN:
        return
    _RAN = True

    try:
        await asyncio.sleep(25.0)
    except Exception:
        pass

    if not _public_scope_enabled():
        _log("skipped guild command copy cleanup outside public/production scope")
        return

    if _beta_guild_sync_explicitly_enabled():
        _log("skipped guild command copy cleanup because STONEY_SYNC_BETA_GUILD_COMMANDS=true")
        return

    if _env_true("DANK_DISABLE_GUILD_COMMAND_COPY_CLEANUP", False):
        _log("skipped guild command copy cleanup because DANK_DISABLE_GUILD_COMMAND_COPY_CLEANUP=true")
        return

    guild_ids = sorted(_cleanup_guild_ids())
    if not guild_ids:
        _log("no configured guild command cleanup IDs found")
        return

    ok = 0
    for guild_id in guild_ids:
        if await _clear_one_guild_copy(guild_id):
            ok += 1

    _log(f"guild command duplicate cleanup complete cleared={ok}/{len(guild_ids)}")


def _install_listener() -> None:
    if bot is None:
        _warn("bot unavailable; cannot install guild command cleanup listener")
        return

    if getattr(bot, "_COMMAND_SCOPE_DEDUPE_LISTENER_INSTALLED", False):
        return

    @bot.listen("on_ready")
    async def _command_scope_dedupe_on_ready() -> None:
        try:
            asyncio.create_task(_late_cleanup_task(), name="command_scope_dedupe_cleanup")
        except Exception as e:
            _warn(f"failed scheduling guild command cleanup: {e!r}")

    setattr(bot, "_COMMAND_SCOPE_DEDUPE_LISTENER_INSTALLED", True)
    _log("installed guild command duplicate cleanup listener")


def apply() -> bool:
    try:
        _default_beta_sync_off()
        _install_listener()
        return True
    except Exception as e:
        _warn(f"apply failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
