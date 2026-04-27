from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Mapping, Optional, Tuple

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
# Production design rules:
# - only Administrator / Manage Server users can configure the bot
# - config is stored per guild_id, not globally in env
# - no cross-server config reads or writes
# - no token/secret values are shown or accepted in Discord commands
# - text-channel fields and voice-channel fields stay separate
# - omitted optional log channels fall back to modlog in a predictable way
# ============================================================


stoney_group = app_commands.Group(
    name="stoney",
    description="Stoney Verify setup and server configuration.",
)


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
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        perms = interaction.user.guild_permissions
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
    return str(int(role.id)) if role is not None else None


def _channel_value(channel: Optional[discord.abc.GuildChannel]) -> Optional[str]:
    return str(int(channel.id)) if channel is not None else None


def _bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        if guild.me is not None:
            return guild.me
    except Exception:
        pass

    try:
        user = getattr(getattr(guild, "_state", None), "user", None)
        if user is not None:
            member = guild.get_member(int(user.id))
            if isinstance(member, discord.Member):
                return member
    except Exception:
        pass

    return None


def _field_text(lines: List[str], *, empty: str, limit: int = 1000) -> str:
    if not lines:
        return empty

    out: list[str] = []
    total = 0
    for line in lines:
        text = str(line)
        extra = len(text) + 1
        if total + extra > limit:
            remaining = len(lines) - len(out)
            out.append(f"…and {remaining} more")
            break
        out.append(text)
        total += extra
    return "\n".join(out) or empty


def _role_line(guild: discord.Guild, role_id: int) -> str:
    rid = _safe_int(role_id, 0)
    if rid <= 0:
        return "Not set"
    role = guild.get_role(rid)
    if role is not None:
        return f"{role.mention} (`{rid}`)"
    return f"Missing/unknown role (`{rid}`)"


def _channel_line(guild: discord.Guild, channel_id: int) -> str:
    cid = _safe_int(channel_id, 0)
    if cid <= 0:
        return "Not set"
    ch = guild.get_channel(cid)
    if ch is not None:
        return f"{ch.mention} (`{cid}`)"
    return f"Missing/unknown channel (`{cid}`)"


def _is_text_channel(channel: Optional[discord.abc.GuildChannel]) -> bool:
    return isinstance(channel, discord.TextChannel)


def _is_voice_channel(channel: Optional[discord.abc.GuildChannel]) -> bool:
    voice_types: list[type] = [discord.VoiceChannel]
    stage_type = getattr(discord, "StageChannel", None)
    if stage_type is not None:
        voice_types.append(stage_type)
    return isinstance(channel, tuple(voice_types))


def _config_embed(guild: discord.Guild, cfg: Any, *, title: str = "🧭 Stoney Server Config") -> discord.Embed:
    source = _safe_str(getattr(cfg, "source", "unknown"), "unknown")
    embed = discord.Embed(
        title=title,
        description=f"Config source: `{source}`\nGuild: `{guild.id}`",
        color=discord.Color.blurple(),
    )

    embed.add_field(
        name="Tickets",
        value=(
            f"Open category: {_channel_line(guild, getattr(cfg, 'ticket_category_id', 0))}\n"
            f"Archive category: {_channel_line(guild, getattr(cfg, 'ticket_archive_category_id', 0))}\n"
            f"Staff role: {_role_line(guild, getattr(cfg, 'staff_role_id', 0))}\n"
            f"Transcripts: {_channel_line(guild, getattr(cfg, 'transcripts_channel_id', 0))}\n"
            f"Prefix: `{_safe_str(getattr(cfg, 'ticket_prefix', 'ticket'), 'ticket')}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Verification",
        value=(
            f"Verify text channel: {_channel_line(guild, getattr(cfg, 'verify_channel_id', 0))}\n"
            f"VC verify channel: {_channel_line(guild, getattr(cfg, 'vc_verify_channel_id', 0))}\n"
            f"Unverified: {_role_line(guild, getattr(cfg, 'unverified_role_id', 0))}\n"
            f"Verified: {_role_line(guild, getattr(cfg, 'verified_role_id', 0))}\n"
            f"Resident: {_role_line(guild, getattr(cfg, 'resident_role_id', 0))}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Logs",
        value=(
            f"Modlog: {_channel_line(guild, getattr(cfg, 'modlog_channel_id', 0))}\n"
            f"Raid/security log: {_channel_line(guild, getattr(cfg, 'raidlog_channel_id', 0))}\n"
            f"Join/exit log: {_channel_line(guild, getattr(cfg, 'join_log_channel_id', 0))}"
        ),
        inline=False,
    )
    return embed


def _check_category(*, guild: discord.Guild, bot_member: Optional[discord.Member], category_id: int, label: str, required: bool, blockers: List[str], warnings: List[str], ok: List[str]) -> None:
    cid = _safe_int(category_id, 0)
    if cid <= 0:
        (blockers if required else warnings).append(f"{label} category is not set. Run `/stoney setup-tickets`.")
        return

    channel = guild.get_channel(cid)
    if not isinstance(channel, discord.CategoryChannel):
        blockers.append(f"{label} category is missing or is not a category: `{cid}`.")
        return

    if bot_member is None:
        blockers.append("Bot member object is unavailable, so category permissions could not be checked.")
        return

    perms = channel.permissions_for(bot_member)
    missing: list[str] = []
    if not perms.view_channel:
        missing.append("View Channels")
    if not perms.manage_channels:
        missing.append("Manage Channels")
    if not perms.send_messages:
        missing.append("Send Messages")
    if not perms.read_message_history:
        missing.append("Read Message History")

    if missing:
        blockers.append(f"{label} category {channel.mention} is missing bot permissions: {', '.join(missing)}.")
    else:
        ok.append(f"{label} category is configured and usable: {channel.mention}.")


def _check_text_channel(*, guild: discord.Guild, bot_member: Optional[discord.Member], channel_id: int, label: str, required: bool, blockers: List[str], warnings: List[str], ok: List[str], need_files: bool = False) -> None:
    cid = _safe_int(channel_id, 0)
    if cid <= 0:
        (blockers if required else warnings).append(f"{label} channel is not set.")
        return

    channel = guild.get_channel(cid)
    if not isinstance(channel, discord.TextChannel):
        blockers.append(f"{label} channel is missing or is not a text channel: `{cid}`.")
        return

    if bot_member is None:
        blockers.append("Bot member object is unavailable, so text-channel permissions could not be checked.")
        return

    perms = channel.permissions_for(bot_member)
    missing: list[str] = []
    if not perms.view_channel:
        missing.append("View Channel")
    if not perms.send_messages:
        missing.append("Send Messages")
    if not perms.read_message_history:
        missing.append("Read Message History")
    if not perms.embed_links:
        missing.append("Embed Links")
    if need_files and not perms.attach_files:
        missing.append("Attach Files")

    if missing:
        blockers.append(f"{label} channel {channel.mention} is missing bot permissions: {', '.join(missing)}.")
    else:
        ok.append(f"{label} channel is configured and writable: {channel.mention}.")


def _check_voice_channel(*, guild: discord.Guild, channel_id: int, label: str, warnings: List[str], ok: List[str]) -> None:
    cid = _safe_int(channel_id, 0)
    if cid <= 0:
        return
    channel = guild.get_channel(cid)
    if _is_voice_channel(channel):
        ok.append(f"{label} channel is configured: {channel.mention}.")
    elif channel is not None:
        warnings.append(f"{label} is configured but is not a voice/stage channel: {channel.mention}.")
    else:
        warnings.append(f"{label} channel is configured but missing/unknown: `{cid}`.")


def _check_role_exists(*, guild: discord.Guild, role_id: int, label: str, required: bool, blockers: List[str], warnings: List[str], ok: List[str]) -> Optional[discord.Role]:
    rid = _safe_int(role_id, 0)
    if rid <= 0:
        (blockers if required else warnings).append(f"{label} role is not set.")
        return None

    role = guild.get_role(rid)
    if role is None:
        blockers.append(f"{label} role is missing: `{rid}`.")
        return None

    ok.append(f"{label} role exists: {role.mention}.")
    return role


def _check_manageable_role(*, guild: discord.Guild, bot_member: Optional[discord.Member], role: Optional[discord.Role], label: str, blockers: List[str], ok: List[str]) -> None:
    if role is None:
        return
    if bot_member is None:
        blockers.append(f"Could not check hierarchy for {label} role because bot member object is unavailable.")
        return
    if not bot_member.guild_permissions.manage_roles:
        blockers.append("Bot is missing **Manage Roles**, so verification roles cannot be assigned/removed.")
        return
    try:
        if role >= bot_member.top_role and guild.owner_id != bot_member.id:
            blockers.append(f"{label} role {role.mention} is above or equal to the bot's top role. Move the bot role above it.")
            return
    except Exception:
        blockers.append(f"Could not verify bot role hierarchy for {label} role {role.mention}.")
        return
    ok.append(f"Bot can manage {label} role {role.mention}.")


def _build_setup_health(guild: discord.Guild, cfg: Any) -> Tuple[List[str], List[str], List[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []
    bot_member = _bot_member(guild)

    if bot_member is None:
        blockers.append("Bot member could not be resolved in this guild.")
    else:
        guild_perms = bot_member.guild_permissions
        guild_missing: list[str] = []
        if not guild_perms.manage_channels:
            guild_missing.append("Manage Channels")
        if not guild_perms.manage_roles:
            guild_missing.append("Manage Roles")
        if not guild_perms.view_audit_log:
            warnings.append("Bot is missing **View Audit Log**. Some moderation attribution may be weaker.")
        if guild_missing:
            blockers.append(f"Bot is missing required server permissions: {', '.join(guild_missing)}.")
        else:
            ok.append("Bot has required server-level channel/role permissions.")

    _check_category(guild=guild, bot_member=bot_member, category_id=getattr(cfg, "ticket_category_id", 0), label="Open ticket", required=True, blockers=blockers, warnings=warnings, ok=ok)
    _check_category(guild=guild, bot_member=bot_member, category_id=getattr(cfg, "ticket_archive_category_id", 0), label="Archive/closed ticket", required=False, blockers=blockers, warnings=warnings, ok=ok)
    _check_role_exists(guild=guild, role_id=getattr(cfg, "staff_role_id", 0), label="Ticket staff", required=True, blockers=blockers, warnings=warnings, ok=ok)
    _check_text_channel(guild=guild, bot_member=bot_member, channel_id=getattr(cfg, "transcripts_channel_id", 0), label="Transcript", required=False, blockers=blockers, warnings=warnings, ok=ok, need_files=True)
    _check_text_channel(guild=guild, bot_member=bot_member, channel_id=getattr(cfg, "verify_channel_id", 0), label="Verify", required=False, blockers=blockers, warnings=warnings, ok=ok)
    _check_voice_channel(guild=guild, channel_id=getattr(cfg, "vc_verify_channel_id", 0), label="VC verify", warnings=warnings, ok=ok)
    _check_text_channel(guild=guild, bot_member=bot_member, channel_id=getattr(cfg, "modlog_channel_id", 0), label="Modlog", required=False, blockers=blockers, warnings=warnings, ok=ok)
    _check_text_channel(guild=guild, bot_member=bot_member, channel_id=getattr(cfg, "join_log_channel_id", 0), label="Join/exit log", required=False, blockers=blockers, warnings=warnings, ok=ok)

    unverified_role = _check_role_exists(guild=guild, role_id=getattr(cfg, "unverified_role_id", 0), label="Unverified", required=False, blockers=blockers, warnings=warnings, ok=ok)
    verified_role = _check_role_exists(guild=guild, role_id=getattr(cfg, "verified_role_id", 0), label="Verified", required=False, blockers=blockers, warnings=warnings, ok=ok)
    resident_role = _check_role_exists(guild=guild, role_id=getattr(cfg, "resident_role_id", 0), label="Resident", required=False, blockers=blockers, warnings=warnings, ok=ok)

    _check_manageable_role(guild=guild, bot_member=bot_member, role=unverified_role, label="Unverified", blockers=blockers, ok=ok)
    _check_manageable_role(guild=guild, bot_member=bot_member, role=verified_role, label="Verified", blockers=blockers, ok=ok)
    _check_manageable_role(guild=guild, bot_member=bot_member, role=resident_role, label="Resident", blockers=blockers, ok=ok)

    if _safe_str(getattr(cfg, "source", ""), "") in {"env", "defaults", "default"}:
        warnings.append("This server appears to be using env/default fallback config. Run `/stoney setup-tickets` before public use.")

    return blockers, warnings, ok


def _health_embed(guild: discord.Guild, cfg: Any) -> discord.Embed:
    blockers, warnings, ok = _build_setup_health(guild, cfg)
    ready = not blockers

    embed = discord.Embed(
        title="🩺 Stoney Setup Health",
        description="✅ **Ready for beta testing**" if ready else "🚫 **Needs fixes before public/beta use**",
        color=discord.Color.green() if ready else discord.Color.red(),
    )
    embed.add_field(name="Blockers", value=_field_text(blockers, empty="✅ None"), inline=False)
    embed.add_field(name="Warnings", value=_field_text(warnings, empty="✅ None"), inline=False)
    embed.add_field(name="Passing Checks", value=_field_text(ok, empty="No passing checks reported."), inline=False)
    embed.set_footer(text=f"Guild {guild.id} • config source: {_safe_str(getattr(cfg, 'source', 'unknown'), 'unknown')}")
    return embed


@stoney_group.command(name="setup-tickets", description="Configure ticket categories, staff role, transcripts, and prefix for this server.")
@app_commands.describe(ticket_category="Category where open ticket channels should be created.", staff_role="Role that can manage/support tickets.", archive_category="Optional category where closed tickets should be moved.", transcripts_channel="Channel where ticket transcripts should be posted.", ticket_prefix="Ticket channel prefix. Example: ticket")
async def setup_tickets(interaction: discord.Interaction, ticket_category: discord.CategoryChannel, staff_role: discord.Role, archive_category: Optional[discord.CategoryChannel] = None, transcripts_channel: Optional[discord.TextChannel] = None, ticket_prefix: Optional[str] = "ticket"):
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)
    guild = interaction.guild
    assert guild is not None

    updates: Dict[str, Any] = {
        "ticket_category_id": _channel_value(ticket_category),
        "ticket_archive_category_id": _channel_value(archive_category),
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
    await interaction.followup.send(embed=_config_embed(guild, cfg, title="✅ Ticket Setup Saved"), ephemeral=True)


@stoney_group.command(name="setup-verify", description="Configure verification channels and roles for this server.")
@app_commands.describe(verify_channel="Main TEXT channel where users read/start verification.", unverified_role="Role new/unverified members receive.", verified_role="Role approved/verified members receive.", resident_role="Optional resident/member role.", vc_verify_channel="Optional VOICE channel used for VC verification sessions.", vc_queue_channel="Optional TEXT channel for VC verification queue/status.")
async def setup_verify(interaction: discord.Interaction, verify_channel: discord.TextChannel, unverified_role: discord.Role, verified_role: discord.Role, resident_role: Optional[discord.Role] = None, vc_verify_channel: Optional[discord.VoiceChannel] = None, vc_queue_channel: Optional[discord.TextChannel] = None):
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)
    guild = interaction.guild
    assert guild is not None

    updates: Dict[str, Any] = {
        "verify_channel_id": _channel_value(verify_channel),
        "vc_verify_channel_id": _channel_value(vc_verify_channel),
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
    await interaction.followup.send(embed=_config_embed(guild, cfg, title="✅ Verification Setup Saved"), ephemeral=True)


@stoney_group.command(name="setup-logs", description="Configure modlog, raid/security log, and member join/exit log channels.")
@app_commands.describe(modlog_channel="Main moderation log channel.", raidlog_channel="Optional raid/spam/security log channel. Defaults to modlog when omitted.", join_log_channel="Optional member join/exit log channel. Use #welcome-exit here if desired.", force_verify_log_channel="Optional forced verification action log channel. Defaults to modlog when omitted.")
async def setup_logs(interaction: discord.Interaction, modlog_channel: discord.TextChannel, raidlog_channel: Optional[discord.TextChannel] = None, join_log_channel: Optional[discord.TextChannel] = None, force_verify_log_channel: Optional[discord.TextChannel] = None):
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)
    guild = interaction.guild
    assert guild is not None

    effective_raidlog = raidlog_channel or modlog_channel
    effective_join_log = join_log_channel or modlog_channel
    effective_force_verify_log = force_verify_log_channel or modlog_channel

    updates: Dict[str, Any] = {
        "modlog_channel_id": _channel_value(modlog_channel),
        "raidlog_channel_id": _channel_value(effective_raidlog),
        "join_log_channel_id": _channel_value(effective_join_log),
        "force_verify_log_channel_id": _channel_value(effective_force_verify_log),
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
    await interaction.followup.send(embed=_config_embed(guild, cfg, title="✅ Log Setup Saved"), ephemeral=True)


@stoney_group.command(name="config", description="Show this server's current Stoney Verify configuration.")
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
    await interaction.followup.send(embed=_config_embed(guild, cfg), ephemeral=True)


@stoney_group.command(name="health", description="Check whether this server is configured safely for tickets and verification.")
async def health(interaction: discord.Interaction):
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)
    guild = interaction.guild
    assert guild is not None
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed loading config for health check: `{e}`", ephemeral=True)
    await interaction.followup.send(embed=_health_embed(guild, cfg), ephemeral=True)


@stoney_group.command(name="cache", description="Show runtime guild-config cache status.")
async def cache_status(interaction: discord.Interaction):
    if not await _require_setup_permission(interaction):
        return
    snapshot = guild_config_cache_snapshot()
    guild_id = str(interaction.guild.id) if interaction.guild else "0"
    guild_info = (snapshot.get("guilds") or {}).get(guild_id, {}) if isinstance(snapshot.get("guilds"), dict) else {}
    await reply_once(interaction, {"content": ("🧭 **Guild Config Cache**\n" f"Table: `{snapshot.get('table')}`\n" f"TTL: `{snapshot.get('ttl_seconds')}` seconds\n" f"Cached guilds: `{snapshot.get('cached_guilds')}`\n" f"This guild: `{guild_info or 'not cached'}`"), "ephemeral": True})


@stoney_group.command(name="refresh-config", description="Reload this server's config from the database.")
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
    await interaction.followup.send(embed=_config_embed(guild, cfg, title="🔄 Config Refreshed"), ephemeral=True)


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
