from __future__ import annotations

"""Public status / uptime reporting for Dank Shield.

This module owns the user-facing status report and the optional heartbeat row.
The important production rule here is simple: health checks must never freeze
Discord's event loop and must never spam users with scary transient Supabase
messages just because a single probe was slow.

Fixes included here:
- Supabase probes are cached.
- Only one Supabase probe can run at a time per process.
- Slow probes return the last known state instead of blocking setup/status UI.
- Periodic heartbeat writes use a short timeout and fail quietly.
- /dank setup-status still works and posts a clear status report.
"""

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

import discord

from .common import safe_defer


_REGISTERED = False
_REPORT_TASK: asyncio.Task | None = None
_HEARTBEAT_TASK: asyncio.Task | None = None
_LAST_REPORT_BY_GUILD: dict[int, float] = {}

_STATUS_TABLE = "bot_status_heartbeats"

_STATUS_CHANNEL_KEYS: tuple[str, ...] = (
    "status_channel_id",
    "bot_status_channel_id",
    "uptime_channel_id",
    "health_channel_id",
    "modlog_channel_id",
    "raidlog_channel_id",
    "join_log_channel_id",
)

_SUPABASE_PROBE_LOCK: asyncio.Lock | None = None
_SUPABASE_PROBE_CACHE: dict[str, Any] = {
    "checked_at": 0.0,
    "ok": None,
    "detail": "not checked yet",
    "inflight": False,
}


# ============================================================
# Env helpers
# ============================================================

def _env_bool(name: str, default: bool = False) -> bool:
    try:
        raw = os.getenv(name, "")
        if raw is None or str(raw).strip() == "":
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _env_int(name: str, default: int = 0) -> int:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        return int(raw) if raw else int(default)
    except Exception:
        return int(default)


def _env_str(name: str, default: str = "") -> str:
    try:
        raw = os.getenv(name)
        if raw is None:
            return default
        text = str(raw).strip()
        return text if text else default
    except Exception:
        return default


def _enabled() -> bool:
    return _env_bool("DANK_STATUS_REPORT_ENABLED", True)


def _heartbeat_enabled() -> bool:
    return _env_bool("DANK_STATUS_HEARTBEAT_ENABLED", True)


def _report_on_ready_enabled() -> bool:
    return _env_bool("DANK_STATUS_REPORT_ON_READY", True)


def _heartbeat_interval_seconds() -> int:
    return max(30, _env_int("DANK_STATUS_HEARTBEAT_SECONDS", 120))


def _startup_report_delay_seconds() -> int:
    return max(8, _env_int("DANK_STATUS_STARTUP_REPORT_DELAY_SECONDS", 20))


def _report_cooldown_seconds() -> int:
    return max(60, _env_int("DANK_STATUS_REPORT_COOLDOWN_SECONDS", 600))


def _auto_save_discovered_config_enabled() -> bool:
    return _env_bool("DANK_STATUS_AUTO_SAVE_DISCOVERED_CONFIG", True)


def _treat_env_fallback_as_ok() -> bool:
    return _env_bool("DANK_STATUS_TREAT_ENV_FALLBACK_OK", False)


def _supabase_probe_cache_seconds() -> int:
    return max(60, _env_int("DANK_SUPABASE_PROBE_CACHE_SECONDS", 300))


def _supabase_probe_timeout_seconds() -> float:
    # This is intentionally short. A status card should not wait on a slow DB.
    try:
        raw = float(_env_str("DANK_SUPABASE_PROBE_TIMEOUT_SECONDS", "2.5") or "2.5")
        return max(0.75, min(raw, 5.0))
    except Exception:
        return 2.5


def _heartbeat_write_timeout_seconds() -> float:
    try:
        raw = float(_env_str("DANK_STATUS_HEARTBEAT_WRITE_TIMEOUT_SECONDS", "3.0") or "3.0")
        return max(0.75, min(raw, 6.0))
    except Exception:
        return 3.0


def _bot_status_id(bot: Any) -> str:
    explicit = _env_str("DANK_STATUS_BOT_ID", "")
    if explicit:
        return explicit

    try:
        user = getattr(bot, "user", None)
        if user is not None and getattr(user, "id", None):
            return str(int(user.id))
    except Exception:
        pass

    return "dank-shield-helper"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _cfg_get(cfg: Any, key: str, default: Any = None) -> Any:
    if isinstance(cfg, Mapping):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _primary_config_table_name() -> str:
    return _env_str("DANK_GUILD_CONFIG_TABLE", "guild_config") or "guild_config"


def _config_table_names() -> tuple[str, ...]:
    primary = _primary_config_table_name()
    names: list[str] = []

    for name in (primary, "guild_config", "guild_configs"):
        clean = _safe_str(name)
        if clean and clean not in names:
            names.append(clean)

    return tuple(names)


def _nested_settings(row: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}

    try:
        for key in ("settings", "config", "metadata", "meta", "raw"):
            value = row.get(key)
            if isinstance(value, Mapping):
                merged.update(dict(value))
        merged.update(dict(row))
    except Exception:
        try:
            merged.update(dict(row))
        except Exception:
            pass

    return merged


# ============================================================
# Guild config / status channel lookup
# ============================================================

def _fetch_config_row_from_table_sync(table_name: str, guild_id: int) -> Optional[dict[str, Any]]:
    try:
        from ..globals import get_supabase

        sb = get_supabase()
        if sb is None:
            return None

        response = (
            sb.table(table_name)
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .limit(1)
            .execute()
        )
        rows = getattr(response, "data", None) or []
        if not rows:
            return None

        first = rows[0]
        if isinstance(first, Mapping):
            row = dict(first)
            row["_source_table"] = table_name
            return row

        return None
    except Exception:
        return None


def _fetch_config_row_sync(guild_id: int) -> Optional[dict[str, Any]]:
    for table_name in _config_table_names():
        row = _fetch_config_row_from_table_sync(table_name, guild_id)
        if row:
            return row
    return None


async def _fetch_config_row(guild_id: int) -> Optional[dict[str, Any]]:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_fetch_config_row_sync, int(guild_id)), timeout=4.0)
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None


def _extract_status_channel_id(row: Optional[Mapping[str, Any]]) -> int:
    if not row:
        return 0

    data = _nested_settings(row)

    for key in _STATUS_CHANNEL_KEYS:
        cid = _safe_int(data.get(key), 0)
        if cid > 0:
            return cid

    return 0


async def _resolve_status_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    channel_id = 0

    try:
        row = await _fetch_config_row(int(guild.id))
        channel_id = _extract_status_channel_id(row)
    except Exception:
        channel_id = 0

    if channel_id <= 0:
        try:
            from ..guild_config import get_guild_config

            cfg = await asyncio.wait_for(
                get_guild_config(int(guild.id), refresh=False),
                timeout=4.0,
            )

            channel_id = int(
                _cfg_get(cfg, "status_channel_id", 0)
                or _cfg_get(cfg, "bot_status_channel_id", 0)
                or _cfg_get(cfg, "uptime_channel_id", 0)
                or _cfg_get(cfg, "health_channel_id", 0)
                or _cfg_get(cfg, "modlog_channel_id", 0)
                or _cfg_get(cfg, "raidlog_channel_id", 0)
                or _cfg_get(cfg, "join_log_channel_id", 0)
                or 0
            )
        except Exception:
            channel_id = 0

    if channel_id <= 0:
        return None

    channel = guild.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await guild.fetch_channel(int(channel_id))
        except Exception:
            channel = None

    if not isinstance(channel, discord.TextChannel):
        return None

    try:
        me = guild.me
        if me is not None:
            perms = channel.permissions_for(me)
            if not (perms.view_channel and perms.send_messages and perms.embed_links):
                return None
    except Exception:
        pass

    return channel


# ============================================================
# Probes
# ============================================================

def _service_line(name: str, ok: bool, detail: str = "") -> str:
    icon = "✅" if ok else "⚠️"
    return f"{icon} **{name}:** {detail or ('available' if ok else 'degraded')}"


def _probe_supabase_sync_once() -> tuple[bool, str]:
    try:
        from ..globals import get_supabase, supabase_diagnostics

        sb = get_supabase()
        if sb is None:
            diag = supabase_diagnostics(ensure_client=False)
            state = _safe_str(diag.get("client_state"), "unavailable") if isinstance(diag, Mapping) else "unavailable"
            return False, f"client unavailable (`{state}`)"

        last_error = ""
        for table_name in _config_table_names():
            try:
                (
                    sb.table(table_name)
                    .select("guild_id")
                    .limit(1)
                    .execute()
                )
                return True, f"database reachable (`{table_name}`)"
            except Exception as e:
                last_error = f"probe failed on `{table_name}`: {type(e).__name__}"
                continue

        return False, last_error or "database probe failed"
    except Exception as e:
        return False, f"database probe failed: {type(e).__name__}"


def _probe_cache_age() -> float:
    try:
        return max(0.0, time.monotonic() - float(_SUPABASE_PROBE_CACHE.get("checked_at") or 0.0))
    except Exception:
        return 999999.0


def _cached_supabase_probe_detail(*, pending_label: bool = True) -> tuple[bool, str]:
    ok = _SUPABASE_PROBE_CACHE.get("ok")
    detail = _safe_str(_SUPABASE_PROBE_CACHE.get("detail"), "not checked yet")
    age = _probe_cache_age()

    if ok is True:
        return True, f"{detail}; last checked {int(age)}s ago"
    if ok is False:
        return False, f"{detail}; last checked {int(age)}s ago"

    if pending_label:
        return True, "probe pending; client initialized, not blocking setup"
    return False, detail


async def _probe_supabase() -> tuple[bool, str]:
    """Non-blocking cached Supabase status probe.

    This intentionally does not wait forever. If the database is slow, the user
    should see a stable cached health state instead of a repeated timeout error.
    """
    global _SUPABASE_PROBE_LOCK

    if _SUPABASE_PROBE_LOCK is None:
        _SUPABASE_PROBE_LOCK = asyncio.Lock()

    age = _probe_cache_age()
    cache_ttl = float(_supabase_probe_cache_seconds())

    if _SUPABASE_PROBE_CACHE.get("ok") is not None and age < cache_ttl:
        return _cached_supabase_probe_detail()

    if _SUPABASE_PROBE_LOCK.locked():
        return _cached_supabase_probe_detail()

    async with _SUPABASE_PROBE_LOCK:
        # Another waiter may have refreshed it while this coroutine waited.
        age = _probe_cache_age()
        if _SUPABASE_PROBE_CACHE.get("ok") is not None and age < cache_ttl:
            return _cached_supabase_probe_detail()

        _SUPABASE_PROBE_CACHE["inflight"] = True
        try:
            timeout = _supabase_probe_timeout_seconds()
            try:
                ok, detail = await asyncio.wait_for(
                    asyncio.to_thread(_probe_supabase_sync_once),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                previous_ok = _SUPABASE_PROBE_CACHE.get("ok")
                previous_detail = _safe_str(_SUPABASE_PROBE_CACHE.get("detail"), "previous state unavailable")

                # Do not convert a single slow probe into a blocker when we have
                # a recent successful state. This was the noisy false-negative.
                if previous_ok is True:
                    return True, f"last successful DB probe still valid; current probe exceeded {timeout:.1f}s"

                ok = False
                detail = f"database probe slow; retrying in background instead of blocking setup"
                if previous_detail and previous_detail != "not checked yet":
                    detail += f" (previous: {previous_detail})"

            _SUPABASE_PROBE_CACHE.update(
                {
                    "checked_at": time.monotonic(),
                    "ok": bool(ok),
                    "detail": str(detail),
                    "inflight": False,
                }
            )
            return _cached_supabase_probe_detail()
        finally:
            _SUPABASE_PROBE_CACHE["inflight"] = False


async def _maybe_auto_save_discovered_config(guild: discord.Guild) -> None:
    if not _auto_save_discovered_config_enabled():
        return

    try:
        from ..guild_config import save_runtime_discovered_config

        await asyncio.wait_for(
            save_runtime_discovered_config(guild),
            timeout=8.0,
        )
    except Exception:
        pass


async def _probe_guild_config(guild: discord.Guild) -> tuple[bool, str]:
    try:
        from ..guild_config import get_guild_config

        await _maybe_auto_save_discovered_config(guild)

        cfg = await asyncio.wait_for(
            get_guild_config(int(guild.id), refresh=True),
            timeout=6.0,
        )

        source = _safe_str(_cfg_get(cfg, "source", "unknown"), "unknown")
        use_env_fallbacks = _safe_bool(_cfg_get(cfg, "use_env_fallbacks", True), True)
        allow_runtime_discovery = _safe_bool(_cfg_get(cfg, "allow_runtime_discovery", True), True)

        if source.startswith("supabase:"):
            return True, f"loaded from `{source}`"

        if source.startswith("unconfigured:"):
            return False, f"server setup incomplete (`{source}`)"

        if source.startswith("env_fallback"):
            if _treat_env_fallback_as_ok():
                return True, f"using configured fallback (`{source}`)"
            return False, f"using fallback config (`{source}`); run setup/discovery to save DB config"

        if "runtime_discovery" in source:
            return False, f"using runtime discovery (`{source}`); save discovery to DB for stable config"

        detail = f"`{source}`"
        if use_env_fallbacks:
            detail += ", env fallback enabled"
        if allow_runtime_discovery:
            detail += ", runtime discovery enabled"

        return False, f"config source unclear ({detail})"
    except asyncio.TimeoutError:
        return False, "config load timed out"
    except Exception as e:
        return False, f"config load failed: {type(e).__name__}"


def _probe_permissions(guild: discord.Guild) -> tuple[bool, str]:
    try:
        me = guild.me
        if me is None:
            return False, "bot member not resolved"

        perms = me.guild_permissions
        missing: list[str] = []

        for attr, label in (
            ("view_channel", "View Channels"),
            ("send_messages", "Send Messages"),
            ("embed_links", "Embed Links"),
            ("manage_channels", "Manage Channels"),
            ("manage_roles", "Manage Roles"),
            ("read_message_history", "Read Message History"),
        ):
            if not bool(getattr(perms, attr, False)):
                missing.append(label)

        if missing:
            return False, "missing " + ", ".join(missing[:6])

        return True, "required baseline permissions present"
    except Exception as e:
        return False, f"permission probe failed: {type(e).__name__}"


def _probe_gateway(bot: Any) -> tuple[bool, str]:
    try:
        latency = float(getattr(bot, "latency", 0.0) or 0.0)
        latency_ms = max(0, int(round(latency * 1000)))
        if latency_ms <= 0:
            return True, "connected"
        return True, f"connected, latency `{latency_ms}ms`"
    except Exception:
        return True, "connected"


async def _build_service_status_lines(bot: Any, guild: discord.Guild) -> tuple[list[str], bool]:
    gateway_ok, gateway_detail = _probe_gateway(bot)
    db_ok, db_detail = await _probe_supabase()
    cfg_ok, cfg_detail = await _probe_guild_config(guild)
    perm_ok, perm_detail = _probe_permissions(guild)

    watchdog_configured = False
    watchdog_ok = False
    watchdog_detail = "not configured — set `DANK_HEALTHCHECKS_PING_URL`"
    try:
        from ..startup_guards.process_health import external_watchdog_status

        watchdog_configured, watchdog_ok, watchdog_detail = external_watchdog_status()
    except Exception:
        pass

    lines = [
        _service_line("Discord gateway", gateway_ok, gateway_detail),
        _service_line("Supabase", db_ok, db_detail),
        _service_line("Guild config", cfg_ok, cfg_detail),
        _service_line("Bot permissions", perm_ok, perm_detail),
        _service_line("Slash commands", True, "registered with Discord if this message posted"),
        _service_line("Internal DB heartbeat", _heartbeat_enabled(), "enabled" if _heartbeat_enabled() else "disabled by env"),
        _service_line("External uptime watchdog", watchdog_ok, watchdog_detail),
    ]

    return lines, bool(gateway_ok and db_ok and cfg_ok and perm_ok and watchdog_configured and watchdog_ok)


# ============================================================
# Status reports
# ============================================================

async def _send_status_report(bot: Any, guild: discord.Guild, *, event: str, force: bool = False) -> bool:
    if not _enabled():
        return False

    now = time.monotonic()
    gid = int(guild.id)

    if not force:
        last = _LAST_REPORT_BY_GUILD.get(gid, 0.0)
        if now - last < _report_cooldown_seconds():
            return False

    channel = await _resolve_status_channel(guild)
    if channel is None:
        return False

    lines, all_ok = await _build_service_status_lines(bot, guild)

    title = "🟢 Dank Shield is back online" if event == "startup" else "🟡 Dank Shield gateway restored"
    description = (
        "The bot process is online again. Service checks are below."
        if all_ok
        else "The bot process is online, but one or more services need attention."
    )

    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.green() if all_ok else discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )

    try:
        embed.add_field(name="Server", value=f"`{guild.name}` (`{guild.id}`)", inline=False)
    except Exception:
        pass

    embed.add_field(name="Service Availability", value="\n".join(lines)[:1024], inline=False)
    embed.add_field(
        name="Important",
        value="True bot-down alerts require a separate watchdog because the bot cannot send Discord messages while its own process is offline.",
        inline=False,
    )
    embed.set_footer(text="Dank Shield status reporter")

    try:
        await channel.send(embed=embed)
        _LAST_REPORT_BY_GUILD[gid] = time.monotonic()
        return True
    except Exception:
        return False


# ============================================================
# Heartbeat
# ============================================================

def _write_heartbeat_sync(bot_id: str, guild_count: int) -> bool:
    try:
        from ..globals import get_supabase

        sb = get_supabase()
        if sb is None:
            return False

        payload = {
            "bot_id": str(bot_id),
            "status": "online",
            "last_seen_at": _utc_iso(),
            "guild_count": int(guild_count),
            "updated_at": _utc_iso(),
        }

        try:
            sb.table(_STATUS_TABLE).upsert(payload, on_conflict="bot_id").execute()
        except TypeError:
            sb.table(_STATUS_TABLE).upsert(payload).execute()

        return True
    except Exception:
        return False


async def _heartbeat_loop(bot: Any) -> None:
    if not _heartbeat_enabled():
        return

    bot_id = _bot_status_id(bot)

    while True:
        try:
            guild_count = len(list(getattr(bot, "guilds", []) or []))
            await asyncio.wait_for(
                asyncio.to_thread(_write_heartbeat_sync, bot_id, guild_count),
                timeout=_heartbeat_write_timeout_seconds(),
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            # Deliberately quiet. A slow heartbeat write should not make the bot
            # look unhealthy or block the gateway.
            pass
        except Exception:
            pass

        try:
            await asyncio.sleep(float(_heartbeat_interval_seconds()))
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(60.0)


async def _startup_report_all_guilds(bot: Any) -> None:
    try:
        await asyncio.sleep(float(_startup_report_delay_seconds()))
    except Exception:
        pass

    if not _report_on_ready_enabled():
        return

    sent = 0
    skipped = 0

    for guild in list(getattr(bot, "guilds", []) or []):
        try:
            ok = await _send_status_report(bot, guild, event="startup", force=False)
            if ok:
                sent += 1
            else:
                skipped += 1
            await asyncio.sleep(0.35)
        except Exception:
            skipped += 1
            continue

    try:
        print(f"📡 status_reporter startup reports complete sent={sent} skipped={skipped}")
    except Exception:
        pass


# ============================================================
# /dank setup-status command
# ============================================================

async def _require_status_setup_permission(interaction: discord.Interaction) -> bool:
    try:
        from .public_setup_group import _require_setup_permission

        return bool(await _require_setup_permission(interaction))
    except Exception:
        pass

    try:
        user = interaction.user
        if isinstance(user, discord.Member):
            perms = user.guild_permissions
            return bool(perms.administrator or perms.manage_guild)
    except Exception:
        pass

    try:
        if interaction.response.is_done():
            await interaction.followup.send("❌ You need Manage Server to use this.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ You need Manage Server to use this.", ephemeral=True)
    except Exception:
        pass

    return False


async def _save_status_channel(guild_id: int, status_channel_id: int, interaction: discord.Interaction) -> None:
    updates = {
        "status_channel_id": str(int(status_channel_id)),
        "configured_by_id": str(interaction.user.id),
        "configured_by_name": str(interaction.user),
        "configured_at": _utc_iso(),
    }

    try:
        from .public_setup_group import _upsert_config

        await _upsert_config(guild_id, updates)
    except Exception:
        try:
            from ..guild_config import upsert_guild_config

            await upsert_guild_config(guild_id, updates)
        except Exception:
            raise

    try:
        from ..guild_config import clear_guild_config_cache

        clear_guild_config_cache(guild_id)
    except Exception:
        pass


async def _status_callback(interaction: discord.Interaction) -> None:
    if not await _require_status_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    channel = await _resolve_status_channel(guild)
    if channel is None:
        return await interaction.followup.send(
            "🚫 No writable status channel is configured. Run `/dank setup-status` and choose a channel, or set a status channel in setup.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    ok = await _send_status_report(interaction.client, guild, event="startup", force=True)
    if ok:
        await interaction.followup.send(
            f"✅ Sent a fresh Dank Shield status report to {channel.mention}.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    else:
        await interaction.followup.send(
            "⚠️ I found the status channel, but the status report did not send. Check bot permissions for View Channel, Send Messages, Embed Links, and Read Message History.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def _setup_status_callback(interaction: discord.Interaction, status_channel: discord.TextChannel) -> None:
    if not await _require_status_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    bot_member = guild.me
    if bot_member is not None:
        perms = status_channel.permissions_for(bot_member)
        missing: list[str] = []

        if not perms.view_channel:
            missing.append("View Channel")
        if not perms.send_messages:
            missing.append("Send Messages")
        if not perms.embed_links:
            missing.append("Embed Links")
        if not perms.read_message_history:
            missing.append("Read Message History")

        if missing:
            return await interaction.followup.send(
                f"🚫 Status channel {status_channel.mention} is missing bot permissions: {', '.join(missing)}.",
                ephemeral=True,
            )

    try:
        await _save_status_channel(int(guild.id), int(status_channel.id), interaction)
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed saving status setup: `{e}`", ephemeral=True)

    embed = discord.Embed(
        title="✅ Status Reporting Saved",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Server", value=f"`{guild.name}` (`{guild.id}`)", inline=False)
    embed.add_field(
        name="Status Reports",
        value=f"Status channel: {status_channel.mention} (`{status_channel.id}`)\nBack-online reports will post here after restarts/reconnects.",
        inline=False,
    )
    embed.add_field(
        name="Note",
        value="This does not replace a true external uptime watchdog. The bot cannot send Discord alerts while its own process is offline.",
        inline=False,
    )

    await interaction.followup.send(embed=embed, ephemeral=True)

    try:
        await _send_status_report(interaction.client, guild, event="startup", force=True)
    except Exception:
        pass


def _attach_setup_status_command() -> None:
    try:
        from .public_setup_group import dank_group
    except Exception:
        return

    def has_child(name: str) -> bool:
        try:
            return dank_group.get_command(name) is not None
        except Exception:
            return False

    if not has_child("status"):
        status_command = discord.app_commands.Command(
            name="status",
            description="Send a fresh Dank Shield status report now.",
            callback=_status_callback,
        )
        try:
            dank_group.add_command(status_command)
        except Exception as e:
            try:
                print(f"⚠️ status_reporter failed adding /dank status: {repr(e)}")
            except Exception:
                pass

    if not has_child("setup-status"):
        setup_command = discord.app_commands.Command(
            name="setup-status",
            description="Choose where Dank Shield posts online/restored status reports.",
            callback=_setup_status_callback,
        )

        try:
            setup_command._params["status_channel"].description = "Text channel for online/restored status notices."
        except Exception:
            pass

        try:
            dank_group.add_command(setup_command)
        except Exception as e:
            try:
                print(f"⚠️ status_reporter failed adding /dank setup-status: {repr(e)}")
            except Exception:
                pass


# ============================================================
# Listener registration
# ============================================================

def _ensure_tasks(bot: Any) -> None:
    global _REPORT_TASK, _HEARTBEAT_TASK

    try:
        if _HEARTBEAT_TASK is None or _HEARTBEAT_TASK.done():
            _HEARTBEAT_TASK = asyncio.create_task(_heartbeat_loop(bot), name="stoney_status_heartbeat")
    except Exception as e:
        try:
            print(f"⚠️ status_reporter heartbeat start failed: {repr(e)}")
        except Exception:
            pass

    try:
        if _REPORT_TASK is None or _REPORT_TASK.done():
            _REPORT_TASK = asyncio.create_task(_startup_report_all_guilds(bot), name="stoney_status_startup_report")
    except Exception as e:
        try:
            print(f"⚠️ status_reporter startup report start failed: {repr(e)}")
        except Exception:
            pass


def register_public_status_reporter(bot, tree) -> None:
    global _REGISTERED

    _attach_setup_status_command()

    if _REGISTERED:
        return

    _REGISTERED = True

    @bot.listen("on_ready")
    async def _stoney_status_on_ready() -> None:
        if not _enabled():
            return
        _ensure_tasks(bot)
        try:
            print("📡 status_reporter tasks ensured on_ready")
        except Exception:
            pass

    @bot.listen("on_resumed")
    async def _stoney_status_on_resumed() -> None:
        if not _enabled():
            return

        for guild in list(getattr(bot, "guilds", []) or []):
            try:
                await _send_status_report(bot, guild, event="resumed", force=False)
                await asyncio.sleep(0.35)
            except Exception:
                continue

    try:
        print("✅ public_status_reporter: startup/restored reports and heartbeat active")
    except Exception:
        pass


__all__ = ["register_public_status_reporter"]
