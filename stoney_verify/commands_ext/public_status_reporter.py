from __future__ import annotations

"""
Public status / uptime reporting for Stoney Verify.

Important limitation:
A Discord bot cannot send a Discord message while the bot process is fully down.
This module handles the parts the bot process *can* own:

- Posts a back-online / restored report to every configured guild on startup.
- Reports service availability per guild.
- Maintains an optional Supabase heartbeat row that an external watchdog can use
  to detect true downtime and notify all configured servers while the bot is down.
- Adds /stoney setup-status so each guild can choose where status notices go.

True down alerts require a separate monitor process, uptime service, or hosting
provider webhook. The heartbeat written here is the source of truth for that.
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
    return _env_bool("STONEY_STATUS_REPORT_ENABLED", True)


def _heartbeat_enabled() -> bool:
    return _env_bool("STONEY_STATUS_HEARTBEAT_ENABLED", True)


def _report_on_ready_enabled() -> bool:
    return _env_bool("STONEY_STATUS_REPORT_ON_READY", True)


def _heartbeat_interval_seconds() -> int:
    return max(30, _env_int("STONEY_STATUS_HEARTBEAT_SECONDS", 60))


def _startup_report_delay_seconds() -> int:
    return max(3, _env_int("STONEY_STATUS_STARTUP_REPORT_DELAY_SECONDS", 12))


def _report_cooldown_seconds() -> int:
    return max(60, _env_int("STONEY_STATUS_REPORT_COOLDOWN_SECONDS", 600))


def _bot_status_id(bot: Any) -> str:
    explicit = _env_str("STONEY_STATUS_BOT_ID", "")
    if explicit:
        return explicit
    try:
        user = getattr(bot, "user", None)
        if user is not None and getattr(user, "id", None):
            return str(int(user.id))
    except Exception:
        pass
    return "stoney-verify-helper"


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


def _table_name() -> str:
    return _env_str("STONEY_GUILD_CONFIG_TABLE", "guild_configs") or "guild_configs"


def _nested_settings(row: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    try:
        for key in ("settings", "config", "metadata", "meta"):
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


def _fetch_config_row_sync(guild_id: int) -> Optional[dict[str, Any]]:
    try:
        from ..globals import get_supabase

        sb = get_supabase()
        if sb is None:
            return None
        response = (
            sb.table(_table_name())
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .limit(1)
            .execute()
        )
        rows = getattr(response, "data", None) or []
        if not rows:
            return None
        first = rows[0]
        return dict(first) if isinstance(first, Mapping) else None
    except Exception:
        return None


async def _fetch_config_row(guild_id: int) -> Optional[dict[str, Any]]:
    return await asyncio.to_thread(_fetch_config_row_sync, int(guild_id))


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

            cfg = await asyncio.wait_for(get_guild_config(int(guild.id), refresh=False), timeout=4.0)
            channel_id = int(
                getattr(cfg, "modlog_channel_id", 0)
                or getattr(cfg, "raidlog_channel_id", 0)
                or getattr(cfg, "join_log_channel_id", 0)
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


def _service_line(name: str, ok: bool, detail: str = "") -> str:
    icon = "✅" if ok else "⚠️"
    return f"{icon} **{name}:** {detail or ('available' if ok else 'degraded')}"


async def _probe_supabase() -> tuple[bool, str]:
    try:
        from ..globals import get_supabase

        sb = get_supabase()
        if sb is None:
            return False, "Supabase client unavailable"

        def _probe() -> None:
            (
                sb.table(_table_name())
                .select("guild_id")
                .limit(1)
                .execute()
            )

        await asyncio.wait_for(asyncio.to_thread(_probe), timeout=6.0)
        return True, "database reachable"
    except asyncio.TimeoutError:
        return False, "database probe timed out"
    except Exception as e:
        return False, f"database probe failed: {type(e).__name__}"


async def _probe_guild_config(guild: discord.Guild) -> tuple[bool, str]:
    try:
        from ..guild_config import get_guild_config

        cfg = await asyncio.wait_for(get_guild_config(int(guild.id), refresh=True), timeout=6.0)
        source = _safe_str(getattr(cfg, "source", "unknown"), "unknown")
        if source.startswith("supabase:"):
            return True, f"loaded from `{source}`"
        if source.startswith("unconfigured:"):
            return False, f"server setup incomplete (`{source}`)"
        return False, f"using fallback config (`{source}`)"
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

    lines = [
        _service_line("Discord gateway", gateway_ok, gateway_detail),
        _service_line("Supabase", db_ok, db_detail),
        _service_line("Guild config", cfg_ok, cfg_detail),
        _service_line("Bot permissions", perm_ok, perm_detail),
        _service_line("Slash commands", True, "registered with Discord if this message posted"),
        _service_line("Status heartbeat", _heartbeat_enabled(), "enabled" if _heartbeat_enabled() else "disabled by env"),
    ]
    return lines, bool(gateway_ok and db_ok and cfg_ok and perm_ok)


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

    title = "🟢 Stoney Verify is back online" if event == "startup" else "🟡 Stoney Verify gateway restored"
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
        value=(
            "True **bot-down** alerts require the separate watchdog/uptime monitor, "
            "because the bot cannot send Discord messages while its own process is offline."
        ),
        inline=False,
    )
    embed.set_footer(text="Stoney Verify status reporter")

    try:
        await channel.send(embed=embed)
        _LAST_REPORT_BY_GUILD[gid] = time.monotonic()
        return True
    except Exception:
        return False


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
            await asyncio.to_thread(_write_heartbeat_sync, bot_id, guild_count)
        except asyncio.CancelledError:
            raise
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


async def _setup_status_callback(interaction: discord.Interaction, status_channel: discord.TextChannel) -> None:
    try:
        from .public_setup_group import _config_embed, _upsert_config, _utc_iso
        from .public_setup_group import _require_setup_permission
        from ..guild_config import get_guild_config, invalidate_guild_config
    except Exception as e:
        return await interaction.response.send_message(
            f"❌ Status setup dependencies are unavailable: `{e}`",
            ephemeral=True,
        )

    if not await _require_setup_permission(interaction):
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

    updates = {
        "status_channel_id": str(int(status_channel.id)),
        "configured_by_id": str(interaction.user.id),
        "configured_by_name": str(interaction.user),
        "configured_at": _utc_iso(),
    }

    try:
        await _upsert_config(guild.id, updates)
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed saving status setup: `{e}`", ephemeral=True)

    embed = _config_embed(guild, cfg, title="✅ Status Reporting Saved")
    embed.add_field(
        name="Status Reports",
        value=(
            f"Status channel: {status_channel.mention} (`{status_channel.id}`)\n"
            "Back-online reports will post here after restarts/reconnects."
        ),
        inline=False,
    )
    await interaction.followup.send(embed=embed, ephemeral=True)

    try:
        await _send_status_report(interaction.client, guild, event="startup", force=True)
    except Exception:
        pass


def _attach_setup_status_command() -> None:
    try:
        from .public_setup_group import stoney_group
    except Exception:
        return

    try:
        existing = stoney_group.get_command("setup-status")
    except Exception:
        existing = None

    if existing is not None:
        return

    command = discord.app_commands.Command(
        name="setup-status",
        description="Choose where Stoney Verify posts online/restored status reports.",
        callback=_setup_status_callback,
    )

    try:
        command._params["status_channel"].description = "Text channel for online/restored status notices."
    except Exception:
        pass

    try:
        stoney_group.add_command(command)
    except Exception as e:
        try:
            print(f"⚠️ status_reporter failed adding /stoney setup-status: {repr(e)}")
        except Exception:
            pass


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
