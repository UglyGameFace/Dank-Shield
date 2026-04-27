from __future__ import annotations

import asyncio
from typing import Any, Dict, Mapping, Optional

import discord
from discord import app_commands

from .common import reply_once, safe_defer
from ..guild_config import get_guild_config, invalidate_guild_config, guild_config_cache_snapshot
from ..globals import get_supabase, now_utc


# ============================================================
# public_setup_group.py
# ------------------------------------------------------------
# Public/beta-safe server setup commands.
#
# Legal/public-safe design:
# - only server administrators / Manage Server users can configure the bot
# - config is stored per guild_id, not globally in env
# - no cross-server config reads or writes
# - no token/secret values are shown or accepted in Discord commands
# ============================================================


stoney_group = app_commands.Group(
    name="stoney",
    description="Stoney Verify setup and server configuration.",
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        if isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        if not text:
            return int(default)
        return int(text)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
    except Exception:
        return default


def _utc_iso() -> str:
    try:
        return now_utc().isoformat()
    except Exception:
        return ""


def _config_table_name() -> str:
    try:
        import os

        return (os.getenv("STONEY_GUILD_CONFIG_TABLE") or "guild_configs").strip() or "guild_configs"
    except Exception:
        return "guild_configs"


def _settings_payload_update(original: Optional[Mapping[str, Any]], updates: Mapping[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {}
    try:
        if isinstance(original, Mapping):
            for key in ("settings", "config", "metadata", "meta"):
                value = original.get(key)
                if isinstance(value, Mapping):
                    base.update(dict(value))
            # flat row wins over older json values
            for key, value in original.items():
                if key not in {"settings", "config", "metadata", "meta"} and value is not None:
                    base[key] = value
    except Exception:
        base = {}

    for key, value in updates.items():
        if value is not None:
            base[str(key)] = value

    return base


def _fetch_existing_config_row_sync(guild_id: int) -> Optional[dict[str, Any]]:
    sb = get_supabase()
    if sb is None:
        return None

    response = (
        sb.table(_config_table_name())
        .select("*")
        .eq("guild_id", str(guild_id))
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if not rows:
        return None
    row = rows[0]
    return dict(row) if isinstance(row, Mapping) else None


def _upsert_config_sync(guild_id: int, updates: Mapping[str, Any]) -> dict[str, Any]:
    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase is not configured/available.")

    table = _config_table_name()
    existing = _fetch_existing_config_row_sync(guild_id)
    settings = _settings_payload_update(existing, updates)

    base_fields = {
        "guild_id": str(guild_id),
        "updated_at": _utc_iso(),
    }

    # Prefer JSON settings because it survives schema changes better.
    attempts: list[dict[str, Any]] = [
        {**base_fields, "settings": settings},
        {**base_fields, "config": settings},
        {**base_fields, **dict(updates)},
    ]

    last_error: Optional[Exception] = None

    for payload in attempts:
        clean_payload = {k: v for k, v in payload.items() if v is not None}
        try:
            if existing:
                response = (
                    sb.table(table)
                    .update(clean_payload)
                    .eq("guild_id", str(guild_id))
                    .execute()
                )
            else:
                try:
                    response = sb.table(table).upsert(clean_payload, on_conflict="guild_id").execute()
                except TypeError:
                    response = sb.table(table).upsert(clean_payload).execute()

            rows = getattr(response, "data", None) or []
            if rows and isinstance(rows[0], Mapping):
                return dict(rows[0])

            refreshed = _fetch_existing_config_row_sync(guild_id)
            return refreshed or clean_payload
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(f"Failed writing guild config: {last_error!r}")


async def _upsert_config(guild_id: int, updates: Mapping[str, Any]) -> dict[str, Any]:
    return await asyncio.to_thread(_upsert_config_sync, int(guild_id), dict(updates))


def _admin_or_manage_guild(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None:
            return False
        user = interaction.user
        if not isinstance(user, discord.Member):
            return False
        perms = user.guild_permissions
        return bool(perms.administrator or perms.manage_guild)
    except Exception:
        return False


async def _require_setup_permission(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await reply_once(interaction, {"content": "❌ This command must be used inside a server.", "ephemeral": True})
        return False

    if not _admin_or_manage_guild(interaction):
        await reply_once(
            interaction,
            {
                "content": "❌ Server setup requires **Administrator** or **Manage Server** permission.",
                "ephemeral": True,
            },
        )
        return False

    return True


def _role_value(role: Optional[discord.Role]) -> Optional[str]:
    if role is None:
        return None
    return str(int(role.id))


def _channel_value(channel: Optional[discord.abc.GuildChannel]) -> Optional[str]:
    if channel is None:
        return None
    return str(int(channel.id))


def _config_embed(guild: discord.Guild, cfg: Any, *, title: str = "🧭 Stoney Server Config") -> discord.Embed:
    source = _safe_str(getattr(cfg, "source", "unknown"), "unknown")
    embed = discord.Embed(
        title=title,
        description=f"Config source: `{source}`\nGuild: `{guild.id}`",
        color=discord.Color.blurple(),
    )

    def ch_line(channel_id: int) -> str:
        cid = _safe_int(channel_id, 0)
        if cid <= 0:
            return "Not set"
        ch = guild.get_channel(cid)
        if ch is not None:
            return f"{ch.mention} (`{cid}`)"
        return f"Missing/unknown channel (`{cid}`)"

    def role_line(role_id: int) -> str:
        rid = _safe_int(role_id, 0)
        if rid <= 0:
            return "Not set"
        role = guild.get_role(rid)
        if role is not None:
            return f"{role.mention} (`{rid}`)"
        return f"Missing/unknown role (`{rid}`)"

    embed.add_field(
        name="Tickets",
        value=(
            f"Category: {ch_line(getattr(cfg, 'ticket_category_id', 0))}\n"
            f"Staff role: {role_line(getattr(cfg, 'staff_role_id', 0))}\n"
            f"Transcripts: {ch_line(getattr(cfg, 'transcripts_channel_id', 0))}\n"
            f"Prefix: `{_safe_str(getattr(cfg, 'ticket_prefix', 'ticket'), 'ticket')}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Verification",
        value=(
            f"Verify channel: {ch_line(getattr(cfg, 'verify_channel_id', 0))}\n"
            f"VC verify: {ch_line(getattr(cfg, 'vc_verify_channel_id', 0))}\n"
            f"Unverified: {role_line(getattr(cfg, 'unverified_role_id', 0))}\n"
            f"Verified: {role_line(getattr(cfg, 'verified_role_id', 0))}\n"
            f"Resident: {role_line(getattr(cfg, 'resident_role_id', 0))}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Logs",
        value=(
            f"Modlog: {ch_line(getattr(cfg, 'modlog_channel_id', 0))}\n"
            f"Raidlog: {ch_line(getattr(cfg, 'raidlog_channel_id', 0))}\n"
            f"Join log: {ch_line(getattr(cfg, 'join_log_channel_id', 0))}"
        ),
        inline=False,
    )
    return embed


@stoney_group.command(
    name="setup-tickets",
    description="Configure ticket category, staff role, transcripts, and prefix for this server.",
)
@app_commands.describe(
    ticket_category="Category where open ticket channels should be created.",
    staff_role="Role that can manage/support tickets.",
    transcripts_channel="Channel where ticket transcripts should be posted.",
    ticket_prefix="Ticket channel prefix. Example: ticket",
)
async def setup_tickets(
    interaction: discord.Interaction,
    ticket_category: discord.CategoryChannel,
    staff_role: discord.Role,
    transcripts_channel: Optional[discord.TextChannel] = None,
    ticket_prefix: Optional[str] = "ticket",
):
    if not await _require_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    assert guild is not None

    updates: Dict[str, Any] = {
        "ticket_category_id": _channel_value(ticket_category),
        "staff_role_id": _role_value(staff_role),
        "vc_staff_role_id": _role_value(staff_role),
        "transcripts_channel_id": _channel_value(transcripts_channel),
        "ticket_prefix": (_safe_str(ticket_prefix, "ticket") or "ticket")[:32],
        "configured_by_id": str(interaction.user.id),
        "configured_by_name": str(interaction.user),
        "configured_at": _utc_iso(),
    }

    try:
        await _upsert_config(guild.id, updates)
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed saving ticket setup: `{e}`", ephemeral=True)

    embed = _config_embed(guild, cfg, title="✅ Ticket Setup Saved")
    await interaction.followup.send(embed=embed, ephemeral=True)


@stoney_group.command(
    name="setup-verify",
    description="Configure verification channels and roles for this server.",
)
@app_commands.describe(
    verify_channel="Main text verification channel.",
    unverified_role="Role new/unverified members receive.",
    verified_role="Role approved/verified members receive.",
    resident_role="Optional resident/member role.",
    vc_verify_channel="Optional VC verification channel.",
    vc_queue_channel="Optional VC verification queue/status channel.",
)
async def setup_verify(
    interaction: discord.Interaction,
    verify_channel: discord.TextChannel,
    unverified_role: discord.Role,
    verified_role: discord.Role,
    resident_role: Optional[discord.Role] = None,
    vc_verify_channel: Optional[discord.TextChannel] = None,
    vc_queue_channel: Optional[discord.TextChannel] = None,
):
    if not await _require_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    assert guild is not None

    updates: Dict[str, Any] = {
        "verify_channel_id": _channel_value(verify_channel),
        "vc_verify_channel_id": _channel_value(vc_verify_channel) or _channel_value(verify_channel),
        "vc_verify_queue_channel_id": _channel_value(vc_queue_channel),
        "unverified_role_id": _role_value(unverified_role),
        "verified_role_id": _role_value(verified_role),
        "resident_role_id": _role_value(resident_role),
        "configured_by_id": str(interaction.user.id),
        "configured_by_name": str(interaction.user),
        "configured_at": _utc_iso(),
    }

    try:
        await _upsert_config(guild.id, updates)
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed saving verification setup: `{e}`", ephemeral=True)

    embed = _config_embed(guild, cfg, title="✅ Verification Setup Saved")
    await interaction.followup.send(embed=embed, ephemeral=True)


@stoney_group.command(
    name="setup-logs",
    description="Configure modlog/raidlog/join-log channels for this server.",
)
@app_commands.describe(
    modlog_channel="Main moderation log channel.",
    raidlog_channel="Optional raid/spam/security log channel.",
    join_log_channel="Optional join/leave log channel.",
    force_verify_log_channel="Optional forced verification action log channel.",
)
async def setup_logs(
    interaction: discord.Interaction,
    modlog_channel: discord.TextChannel,
    raidlog_channel: Optional[discord.TextChannel] = None,
    join_log_channel: Optional[discord.TextChannel] = None,
    force_verify_log_channel: Optional[discord.TextChannel] = None,
):
    if not await _require_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    assert guild is not None

    updates: Dict[str, Any] = {
        "modlog_channel_id": _channel_value(modlog_channel),
        "raidlog_channel_id": _channel_value(raidlog_channel),
        "join_log_channel_id": _channel_value(join_log_channel),
        "force_verify_log_channel_id": _channel_value(force_verify_log_channel),
        "configured_by_id": str(interaction.user.id),
        "configured_by_name": str(interaction.user),
        "configured_at": _utc_iso(),
    }

    try:
        await _upsert_config(guild.id, updates)
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed saving log setup: `{e}`", ephemeral=True)

    embed = _config_embed(guild, cfg, title="✅ Log Setup Saved")
    await interaction.followup.send(embed=embed, ephemeral=True)


@stoney_group.command(
    name="config",
    description="Show this server's current Stoney Verify configuration.",
)
async def show_config(interaction: discord.Interaction):
    if not await _require_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    assert guild is not None

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed loading config: `{e}`", ephemeral=True)

    embed = _config_embed(guild, cfg)
    await interaction.followup.send(embed=embed, ephemeral=True)


@stoney_group.command(
    name="cache",
    description="Show runtime guild-config cache status.",
)
async def cache_status(interaction: discord.Interaction):
    if not await _require_setup_permission(interaction):
        return

    snapshot = guild_config_cache_snapshot()
    guild_id = str(interaction.guild.id) if interaction.guild else "0"
    guild_info = (snapshot.get("guilds") or {}).get(guild_id, {}) if isinstance(snapshot.get("guilds"), dict) else {}

    await reply_once(
        interaction,
        {
            "content": (
                "🧭 **Guild Config Cache**\n"
                f"Table: `{snapshot.get('table')}`\n"
                f"TTL: `{snapshot.get('ttl_seconds')}` seconds\n"
                f"Cached guilds: `{snapshot.get('cached_guilds')}`\n"
                f"This guild: `{guild_info or 'not cached'}`"
            ),
            "ephemeral": True,
        },
    )


@stoney_group.command(
    name="refresh-config",
    description="Reload this server's config from the database.",
)
async def refresh_config(interaction: discord.Interaction):
    if not await _require_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    assert guild is not None

    try:
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed refreshing config: `{e}`", ephemeral=True)

    embed = _config_embed(guild, cfg, title="🔄 Config Refreshed")
    await interaction.followup.send(embed=embed, ephemeral=True)


def register_public_setup_group_commands(bot, tree) -> None:
    _ = bot
    existing = None
    try:
        existing = tree.get_command("stoney", guild=None)
    except Exception:
        existing = None

    if existing is not None:
        try:
            print("ℹ️ public_setup_group: /stoney already registered; skipping")
        except Exception:
            pass
        return

    tree.add_command(stoney_group)
    try:
        print("✅ public_setup_group: registered /stoney grouped setup commands")
    except Exception:
        pass


__all__ = ["register_public_setup_group_commands"]
