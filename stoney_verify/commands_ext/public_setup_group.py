from __future__ import annotations

import asyncio
import re
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
# - hard blockers refuse to save known-broken setup
# - warning-level choices are allowed but surfaced clearly
# ============================================================


stoney_group = app_commands.Group(
    name="dank",
    description="Dank Shield setup, help, and server configuration.",
)


# ============================================================
# small safe helpers
# ============================================================


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


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    """Read config from object attrs, dict keys, or nested json config buckets."""
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass

    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass

    try:
        for bucket in ("settings", "config", "metadata", "meta"):
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
    except Exception:
        pass

    try:
        if hasattr(cfg, "get"):
            for bucket in ("settings", "config", "metadata", "meta"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
    except Exception:
        pass

    return default


def _cfg_snowflake(cfg: Any, *names: str) -> int:
    """Read a Discord snowflake from config attrs, dict keys, or nested config buckets."""
    for name in names:
        parsed = _safe_int(_cfg_value(cfg, name, None), 0)
        if parsed > 0:
            return parsed
    return 0


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
                    base[str(key)] = value
    except Exception:
        base = {}

    for key, value in updates.items():
        if value is not None:
            base[str(key)] = value

    return base


# ============================================================
# DB write helpers
# ============================================================


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


# ============================================================
# permission / value helpers
# ============================================================


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
        await reply_once(
            interaction,
            {"content": "❌ This command must be used inside a server.", "ephemeral": True},
        )
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

    channel = guild.get_channel(cid)
    if channel is not None:
        return f"{channel.mention} (`{cid}`)"

    return f"Missing/unknown channel (`{cid}`)"


def _is_voice_channel(channel: Optional[discord.abc.GuildChannel]) -> bool:
    voice_types: list[type] = [discord.VoiceChannel]

    stage_type = getattr(discord, "StageChannel", None)
    if stage_type is not None:
        voice_types.append(stage_type)

    return isinstance(channel, tuple(voice_types))


def _text_channel_missing_perms(
    channel: discord.TextChannel,
    bot_member: discord.Member,
    *,
    need_files: bool = False,
) -> list[str]:
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

    return missing


def _category_missing_perms(category: discord.CategoryChannel, bot_member: discord.Member) -> list[str]:
    perms = category.permissions_for(bot_member)
    missing: list[str] = []

    if not perms.view_channel:
        missing.append("View Channels")
    if not perms.manage_channels:
        missing.append("Manage Channels")
    if not perms.send_messages:
        missing.append("Send Messages")
    if not perms.read_message_history:
        missing.append("Read Message History")

    return missing


def _can_manage_role(
    guild: discord.Guild,
    bot_member: Optional[discord.Member],
    role: discord.Role,
) -> tuple[bool, str]:
    if bot_member is None:
        return False, "Bot member object is unavailable, so role hierarchy could not be checked."

    if not bot_member.guild_permissions.manage_roles:
        return False, "Bot is missing **Manage Roles**."

    try:
        if role >= bot_member.top_role and guild.owner_id != bot_member.id:
            return False, f"{role.mention} is above or equal to the bot's top role. Move the bot role above it."
    except Exception:
        return False, f"Could not verify role hierarchy for {role.mention}."

    return True, ""


# ============================================================
# validation embeds
# ============================================================


def _validation_embed(title: str, blockers: list[str], warnings: list[str], ok: list[str]) -> discord.Embed:
    blocked = bool(blockers)

    embed = discord.Embed(
        title=title,
        description=(
            "❌ **Setup was not saved. Fix the blockers below.**"
            if blocked
            else "✅ **Setup can be saved. Review any warnings below.**"
        ),
        color=discord.Color.red() if blocked else discord.Color.gold() if warnings else discord.Color.green(),
    )
    embed.add_field(name="Blockers", value=_field_text(blockers, empty="✅ None"), inline=False)
    embed.add_field(name="Warnings", value=_field_text(warnings, empty="✅ None"), inline=False)
    embed.add_field(name="Passing Checks", value=_field_text(ok, empty="No passing checks reported."), inline=False)
    return embed


def _add_validation_summary(embed: discord.Embed, warnings: list[str], ok: list[str]) -> None:
    if warnings:
        embed.add_field(name="Saved With Warnings", value=_field_text(warnings, empty="✅ None"), inline=False)
    if ok:
        embed.add_field(name="Pre-save Checks", value=_field_text(ok, empty="✅ Passed"), inline=False)


async def _send_blocked_setup(
    interaction: discord.Interaction,
    title: str,
    blockers: list[str],
    warnings: list[str],
    ok: list[str],
) -> None:
    await interaction.followup.send(
        embed=_validation_embed(title, blockers, warnings, ok),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


# ============================================================
# setup validators
# ============================================================


def _validate_ticket_setup(
    guild: discord.Guild,
    ticket_category: discord.CategoryChannel,
    staff_role: discord.Role,
    archive_category: Optional[discord.CategoryChannel],
    transcripts_channel: Optional[discord.TextChannel],
) -> tuple[list[str], list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    bot_member = _bot_member(guild)
    if bot_member is None:
        blockers.append("Bot member could not be resolved in this guild.")
        return blockers, warnings, ok

    missing = _category_missing_perms(ticket_category, bot_member)
    if missing:
        blockers.append(f"Open ticket category {ticket_category.mention} is missing bot permissions: {', '.join(missing)}.")
    else:
        ok.append(f"Open ticket category is usable: {ticket_category.mention}.")

    if archive_category is None:
        warnings.append("Archive category was not set. Closed tickets may remain in the open ticket category until archive config is added.")
    else:
        archive_missing = _category_missing_perms(archive_category, bot_member)
        if archive_missing:
            blockers.append(f"Archive category {archive_category.mention} is missing bot permissions: {', '.join(archive_missing)}.")
        else:
            ok.append(f"Archive category is usable: {archive_category.mention}.")

    if staff_role is None:
        blockers.append("Ticket staff role is required.")
    elif staff_role.is_default():
        blockers.append("Ticket staff role cannot be @everyone.")
    else:
        ok.append(f"Ticket staff role is set: {staff_role.mention}.")

    if transcripts_channel is None:
        warnings.append("Transcript channel was not set. Transcript posting will be limited until it is configured.")
    else:
        transcript_missing = _text_channel_missing_perms(transcripts_channel, bot_member, need_files=True)
        if transcript_missing:
            blockers.append(f"Transcript channel {transcripts_channel.mention} is missing bot permissions: {', '.join(transcript_missing)}.")
        else:
            ok.append(f"Transcript channel is writable: {transcripts_channel.mention}.")

    return blockers, warnings, ok


def _validate_verify_setup(
    guild: discord.Guild,
    verify_channel: discord.TextChannel,
    unverified_role: discord.Role,
    verified_role: discord.Role,
    resident_role: Optional[discord.Role],
    vc_verify_channel: Optional[discord.abc.GuildChannel],
    vc_queue_channel: Optional[discord.TextChannel],
) -> tuple[list[str], list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    bot_member = _bot_member(guild)
    if bot_member is None:
        blockers.append("Bot member could not be resolved in this guild.")
        return blockers, warnings, ok

    verify_missing = _text_channel_missing_perms(verify_channel, bot_member)
    if verify_missing:
        blockers.append(f"Verify text channel {verify_channel.mention} is missing bot permissions: {', '.join(verify_missing)}.")
    else:
        ok.append(f"Verify text channel is writable: {verify_channel.mention}.")

    for label, role, required in (
        ("Unverified", unverified_role, True),
        ("Verified", verified_role, True),
        ("Resident", resident_role, False),
    ):
        if role is None:
            if required:
                blockers.append(f"{label} role is required.")
            continue

        if role.is_default():
            blockers.append(f"{label} role cannot be @everyone.")
            continue

        manageable, reason = _can_manage_role(guild, bot_member, role)
        if not manageable:
            blockers.append(f"Bot cannot manage {label} role {role.mention}: {reason}")
        else:
            ok.append(f"Bot can manage {label} role {role.mention}.")

    if vc_verify_channel is None:
        warnings.append("VC verify channel was not set. Voice verification can stay disabled, but VC verification will not be ready.")
    elif _is_voice_channel(vc_verify_channel):
        ok.append(f"VC verify channel is set: {vc_verify_channel.mention}.")
    else:
        blockers.append(f"VC verify channel must be a voice/stage channel, got {vc_verify_channel.mention}.")

    if vc_queue_channel is None:
        if vc_verify_channel is not None:
            blockers.append("VC verify requests / queue channel is required when VC verification is configured.")
        else:
            warnings.append("VC verify requests / queue channel was not set. VC verification status messages will be disabled.")
    else:
        queue_missing = _text_channel_missing_perms(vc_queue_channel, bot_member, need_files=True)
        if queue_missing:
            blockers.append(f"VC verify requests / queue channel {vc_queue_channel.mention} is missing bot permissions: {', '.join(queue_missing)}.")
        else:
            ok.append(f"VC verify requests / queue channel is writable: {vc_queue_channel.mention}.")

    return blockers, warnings, ok


def _validate_log_setup(
    guild: discord.Guild,
    modlog_channel: discord.TextChannel,
    raidlog_channel: Optional[discord.TextChannel],
    join_log_channel: Optional[discord.TextChannel],
    force_verify_log_channel: Optional[discord.TextChannel],
) -> tuple[list[str], list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    bot_member = _bot_member(guild)
    if bot_member is None:
        blockers.append("Bot member could not be resolved in this guild.")
        return blockers, warnings, ok

    seen: set[int] = set()

    def check_log_channel(label: str, channel: discord.TextChannel) -> None:
        if int(channel.id) in seen:
            ok.append(f"{label} reuses already validated channel {channel.mention}.")
            return

        seen.add(int(channel.id))
        missing = _text_channel_missing_perms(channel, bot_member)

        if missing:
            blockers.append(f"{label} channel {channel.mention} is missing bot permissions: {', '.join(missing)}.")
        else:
            ok.append(f"{label} channel is writable: {channel.mention}.")

    check_log_channel("Modlog", modlog_channel)

    if raidlog_channel is None:
        warnings.append("Raid/security log channel was not set. It will fall back to the modlog channel.")
    else:
        check_log_channel("Raid/security log", raidlog_channel)

    if join_log_channel is None:
        warnings.append("Join/exit log channel was not set. It will fall back to the modlog channel.")
    else:
        check_log_channel("Join/exit log", join_log_channel)

    if force_verify_log_channel is None:
        warnings.append("Forced verification log channel was not set. It will fall back to the modlog channel.")
    else:
        check_log_channel("Forced verification log", force_verify_log_channel)

    return blockers, warnings, ok


# ============================================================
# config display
# ============================================================


def _config_embed(guild: discord.Guild, cfg: Any, *, title: str = "🧭 Dank Shield Server Config") -> discord.Embed:
    source = _safe_str(_cfg_value(cfg, "source", "unknown"), "unknown")

    embed = discord.Embed(
        title=title,
        description=f"Config source: `{source}`\nGuild: `{guild.id}`",
        color=discord.Color.blurple(),
    )

    embed.add_field(
        name="Tickets",
        value=(
            f"Open category: {_channel_line(guild, _cfg_snowflake(cfg, 'ticket_category_id'))}\n"
            f"Archive category: {_channel_line(guild, _cfg_snowflake(cfg, 'ticket_archive_category_id', 'archive_category_id'))}\n"
            f"Staff role: {_role_line(guild, _cfg_snowflake(cfg, 'staff_role_id', 'vc_staff_role_id'))}\n"
            f"Transcripts: {_channel_line(guild, _cfg_snowflake(cfg, 'transcripts_channel_id'))}\n"
            f"Prefix: `{_safe_str(_cfg_value(cfg, 'ticket_prefix', 'ticket'), 'ticket')}`"
        ),
        inline=False,
    )

    embed.add_field(
        name="Verification",
        value=(
            f"Verify text channel: {_channel_line(guild, _cfg_snowflake(cfg, 'verify_channel_id'))}\n"
            f"VC verify voice: {_channel_line(guild, _cfg_snowflake(cfg, 'vc_verify_channel_id'))}\n"
            f"VC verify requests / queue: {_channel_line(guild, _cfg_snowflake(cfg, 'vc_verify_queue_channel_id', 'vc_queue_channel_id', 'vc_request_channel_id', 'vc_verify_requests_channel_id'))}\n"
            f"Unverified: {_role_line(guild, _cfg_snowflake(cfg, 'unverified_role_id'))}\n"
            f"Verified: {_role_line(guild, _cfg_snowflake(cfg, 'verified_role_id'))}\n"
            f"Resident: {_role_line(guild, _cfg_snowflake(cfg, 'resident_role_id'))}"
        ),
        inline=False,
    )

    embed.add_field(
        name="Logs",
        value=(
            f"Modlog: {_channel_line(guild, _cfg_snowflake(cfg, 'modlog_channel_id'))}\n"
            f"Raid/security log: {_channel_line(guild, _cfg_snowflake(cfg, 'raidlog_channel_id'))}\n"
            f"Join/exit log: {_channel_line(guild, _cfg_snowflake(cfg, 'join_log_channel_id'))}"
        ),
        inline=False,
    )

    return embed


# ============================================================
# health check helpers
# ============================================================


def _check_category(
    *,
    guild: discord.Guild,
    bot_member: Optional[discord.Member],
    category_id: int,
    label: str,
    required: bool,
    blockers: List[str],
    warnings: List[str],
    ok: List[str],
) -> None:
    cid = _safe_int(category_id, 0)

    if cid <= 0:
        (blockers if required else warnings).append(f"{label} category is not set.")
        return

    channel = guild.get_channel(cid)

    if not isinstance(channel, discord.CategoryChannel):
        blockers.append(f"{label} category is missing or is not a category: `{cid}`.")
        return

    if bot_member is None:
        blockers.append("Bot member object is unavailable, so category permissions could not be checked.")
        return

    missing = _category_missing_perms(channel, bot_member)

    if missing:
        blockers.append(f"{label} category {channel.mention} is missing bot permissions: {', '.join(missing)}.")
    else:
        ok.append(f"{label} category is configured and usable: {channel.mention}.")


def _check_text_channel(
    *,
    guild: discord.Guild,
    bot_member: Optional[discord.Member],
    channel_id: int,
    label: str,
    required: bool,
    blockers: List[str],
    warnings: List[str],
    ok: List[str],
    need_files: bool = False,
) -> None:
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

    missing = _text_channel_missing_perms(channel, bot_member, need_files=need_files)

    if missing:
        blockers.append(f"{label} channel {channel.mention} is missing bot permissions: {', '.join(missing)}.")
    else:
        ok.append(f"{label} channel is configured and writable: {channel.mention}.")


def _check_voice_channel(
    *,
    guild: discord.Guild,
    channel_id: int,
    label: str,
    warnings: List[str],
    ok: List[str],
) -> None:
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



def _voice_channel_missing_perms(
    channel: discord.abc.GuildChannel,
    bot_member: discord.Member,
) -> tuple[list[str], list[str]]:
    """Return blocker-level and warning-level missing VC permissions.

    VC verify needs more than "is this a voice channel". The bot must be able
    to see the channel, generate/join access paths, move members when needed,
    and edit overwrites when temporary VC access is granted.
    """
    perms = channel.permissions_for(bot_member)
    blockers: list[str] = []
    warnings: list[str] = []

    if not perms.view_channel:
        blockers.append("View Channel")
    if not getattr(perms, "connect", False):
        blockers.append("Connect")
    if not getattr(perms, "create_instant_invite", False):
        blockers.append("Create Invite")
    if not getattr(perms, "move_members", False):
        blockers.append("Move Members")
    if not perms.manage_channels:
        blockers.append("Manage Channels")

    # Speak is useful for future voice guidance, but the bot does not always
    # need to talk during VC verification, so keep it as a warning.
    if not getattr(perms, "speak", False):
        warnings.append("Speak")

    return blockers, warnings


def _check_vc_verify_voice_channel(
    *,
    guild: discord.Guild,
    bot_member: Optional[discord.Member],
    channel_id: int,
    label: str,
    required: bool,
    blockers: List[str],
    warnings: List[str],
    ok: List[str],
) -> None:
    cid = _safe_int(channel_id, 0)

    if cid <= 0:
        (blockers if required else warnings).append(f"{label} channel is not set.")
        return

    channel = guild.get_channel(cid)

    if channel is None:
        blockers.append(f"{label} channel is configured but missing/unknown: `{cid}`.")
        return

    if not _is_voice_channel(channel):
        blockers.append(f"{label} must be a voice/stage channel, got {channel.mention}.")
        return

    if bot_member is None:
        blockers.append("Bot member object is unavailable, so voice-channel permissions could not be checked.")
        return

    missing, warn_missing = _voice_channel_missing_perms(channel, bot_member)

    if missing:
        blockers.append(f"{label} channel {channel.mention} is missing bot permissions: {', '.join(missing)}.")
    else:
        ok.append(f"{label} channel is configured and usable: {channel.mention}.")

    if warn_missing:
        warnings.append(f"{label} channel {channel.mention} is missing optional permissions: {', '.join(warn_missing)}.")


def _check_role_exists(
    *,
    guild: discord.Guild,
    role_id: int,
    label: str,
    required: bool,
    blockers: List[str],
    warnings: List[str],
    ok: List[str],
) -> Optional[discord.Role]:
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


def _check_manageable_role(
    *,
    guild: discord.Guild,
    bot_member: Optional[discord.Member],
    role: Optional[discord.Role],
    label: str,
    blockers: List[str],
    ok: List[str],
) -> None:
    if role is None:
        return

    manageable, reason = _can_manage_role(guild, bot_member, role)

    if not manageable:
        blockers.append(f"Bot cannot manage {label} role {role.mention}: {reason}")
    else:
        ok.append(f"Bot can manage {label} role {role.mention}.")



def _check_supabase_select(
    *,
    table: str,
    columns: str,
    label: str,
    required: bool,
    blockers: List[str],
    warnings: List[str],
    ok: List[str],
) -> None:
    """Verify Supabase table + required columns through the REST client.

    This catches missing tables/columns like PGRST204/PGRST125 before users hit
    broken buttons.
    """
    sb = get_supabase()

    if sb is None:
        (blockers if required else warnings).append(f"{label} could not be checked because Supabase is not configured.")
        return

    try:
        sb.table(table).select(columns).limit(1).execute()
        ok.append(f"Supabase `{table}` table has required {label.lower()} columns.")
    except Exception as e:
        message = str(e).replace("\n", " ")
        (blockers if required else warnings).append(
            f"Supabase `{table}` check failed for {label}: `{type(e).__name__}: {message[:260]}`"
        )



def _norm_name(value: Any) -> str:
    text = _safe_str(value, "").lower()
    keep = []
    for ch in text:
        if ch.isalnum():
            keep.append(ch)
        elif ch in {" ", "-", "_"}:
            keep.append(" ")
    return " ".join("".join(keep).split())


def _category_name_hits(category: Optional[discord.CategoryChannel], *needles: str) -> bool:
    if category is None:
        return False

    name = _norm_name(category.name)
    return any(_norm_name(needle) in name for needle in needles)


def _channel_parent(channel: Optional[discord.abc.GuildChannel]) -> Optional[discord.CategoryChannel]:
    try:
        parent = getattr(channel, "category", None)
        return parent if isinstance(parent, discord.CategoryChannel) else None
    except Exception:
        return None


def _same_category(a: Optional[discord.abc.GuildChannel], b: Optional[discord.abc.GuildChannel]) -> bool:
    pa = _channel_parent(a)
    pb = _channel_parent(b)
    return pa is not None and pb is not None and int(pa.id) == int(pb.id)


def _placement_line(channel: Optional[discord.abc.GuildChannel]) -> str:
    if channel is None:
        return "missing"

    parent = _channel_parent(channel)
    if parent is None:
        return f"{channel.mention} has no category"

    return f"{channel.mention} under **{parent.name}**"


def _find_duplicate_setup_categories(guild: discord.Guild) -> dict[str, list[discord.CategoryChannel]]:
    groups: dict[str, list[discord.CategoryChannel]] = {
        "start/public": [],
        "active tickets": [],
        "ticket archive": [],
        "staff tools": [],
    }

    for category in guild.categories:
        if _category_name_hits(category, "start", "welcome", "verify"):
            groups["start/public"].append(category)
        if _category_name_hits(category, "active ticket", "open ticket"):
            groups["active tickets"].append(category)
        if _category_name_hits(category, "ticket archive", "archive", "closed ticket"):
            groups["ticket archive"].append(category)
        if _category_name_hits(category, "staff tool", "support tool", "mod tool", "admin tool"):
            groups["staff tools"].append(category)

    return {label: cats for label, cats in groups.items() if len(cats) > 1}


def _channel_by_id(guild: discord.Guild, channel_id: int) -> Optional[discord.abc.GuildChannel]:
    cid = _safe_int(channel_id, 0)
    return guild.get_channel(cid) if cid > 0 else None


def _check_channel_placement(
    *,
    guild: discord.Guild,
    channel_id: int,
    label: str,
    expected_parent: Optional[discord.CategoryChannel],
    expected_parent_label: str,
    required: bool,
    blockers: List[str],
    warnings: List[str],
    ok: List[str],
) -> None:
    channel = _channel_by_id(guild, channel_id)

    if channel is None:
        if required:
            blockers.append(f"{label} placement could not be checked because the channel is missing.")
        return

    if expected_parent is None:
        if required:
            warnings.append(f"{label} placement could not be checked because {expected_parent_label} category is not configured.")
        return

    parent = _channel_parent(channel)
    if parent is None:
        (blockers if required else warnings).append(
            f"{label} channel {channel.mention} is not inside any category. Expected it under **{expected_parent.name}**."
        )
        return

    if int(parent.id) != int(expected_parent.id):
        (blockers if required else warnings).append(
            f"{label} channel is in the wrong category: {_placement_line(channel)}. Expected it under **{expected_parent.name}**."
        )
        return

    ok.append(f"{label} channel placement is correct: {_placement_line(channel)}.")


def _check_not_inside_category(
    *,
    guild: discord.Guild,
    channel_id: int,
    label: str,
    forbidden_categories: list[discord.CategoryChannel],
    blockers: List[str],
    warnings: List[str],
    ok: List[str],
    blocker: bool = True,
) -> None:
    channel = _channel_by_id(guild, channel_id)
    if channel is None:
        return

    parent = _channel_parent(channel)
    if parent is None:
        warnings.append(f"{label} channel {channel.mention} has no category. Put it in the proper public/staff area.")
        return

    forbidden_ids = {int(cat.id) for cat in forbidden_categories if cat is not None}
    if int(parent.id) in forbidden_ids:
        target = blockers if blocker else warnings
        target.append(f"{label} channel is in a bad category: {_placement_line(channel)}.")
    else:
        ok.append(f"{label} channel is not inside a restricted ticket lifecycle category.")


def _check_category_order(
    *,
    first: Optional[discord.CategoryChannel],
    second: Optional[discord.CategoryChannel],
    first_label: str,
    second_label: str,
    warnings: List[str],
    ok: List[str],
) -> None:
    if first is None or second is None:
        return

    try:
        if int(first.position) < int(second.position):
            ok.append(f"Category order looks right: **{first.name}** is above **{second.name}**.")
        else:
            warnings.append(f"Category order looks backwards: **{first.name}** should be above **{second.name}**.")
    except Exception:
        warnings.append(f"Could not verify category order for {first_label} and {second_label}.")


def _check_ticket_channel_lifecycle_placement(
    *,
    guild: discord.Guild,
    active_category: Optional[discord.CategoryChannel],
    archive_category: Optional[discord.CategoryChannel],
    warnings: List[str],
    ok: List[str],
) -> None:
    if active_category is None or archive_category is None:
        return

    open_wrong: list[str] = []
    closed_wrong: list[str] = []

    for channel in guild.text_channels:
        name = _safe_str(channel.name, "").lower()

        if re.match(r"^ticket-\d{3,6}$", name):
            parent = _channel_parent(channel)
            if parent is None or int(parent.id) != int(active_category.id):
                open_wrong.append(_placement_line(channel))

        if re.match(r"^closed-\d{3,6}$", name):
            parent = _channel_parent(channel)
            if parent is None or int(parent.id) != int(archive_category.id):
                closed_wrong.append(_placement_line(channel))

    if open_wrong:
        warnings.append("Some open ticket channels are outside the active ticket category: " + "; ".join(open_wrong[:5]) + ("; …" if len(open_wrong) > 5 else ""))
    else:
        ok.append("Existing open ticket channel placement looks correct.")

    if closed_wrong:
        warnings.append("Some closed ticket channels are outside the archive category: " + "; ".join(closed_wrong[:5]) + ("; …" if len(closed_wrong) > 5 else ""))
    else:
        ok.append("Existing closed ticket channel placement looks correct.")


def _check_layout_health(
    *,
    guild: discord.Guild,
    ticket_category_id: int,
    ticket_archive_category_id: int,
    ticket_panel_channel_id: int,
    verify_channel_id: int,
    vc_verify_channel_id: int,
    vc_verify_requests_channel_id: int,
    transcripts_channel_id: int,
    modlog_channel_id: int,
    raidlog_channel_id: int,
    join_log_channel_id: int,
    force_verify_log_channel_id: int,
    status_channel_id: int,
    blockers: List[str],
    warnings: List[str],
    ok: List[str],
) -> None:
    active_category = guild.get_channel(_safe_int(ticket_category_id, 0))
    archive_category = guild.get_channel(_safe_int(ticket_archive_category_id, 0))

    active_category = active_category if isinstance(active_category, discord.CategoryChannel) else None
    archive_category = archive_category if isinstance(archive_category, discord.CategoryChannel) else None

    panel_channel = _channel_by_id(guild, ticket_panel_channel_id)
    verify_channel = _channel_by_id(guild, verify_channel_id)
    vc_voice_channel = _channel_by_id(guild, vc_verify_channel_id)
    vc_queue_channel = _channel_by_id(guild, vc_verify_requests_channel_id)
    transcripts_channel = _channel_by_id(guild, transcripts_channel_id)
    modlog_channel = _channel_by_id(guild, modlog_channel_id)

    public_parent = _channel_parent(panel_channel) or _channel_parent(verify_channel) or _channel_parent(vc_voice_channel)
    staff_parent = _channel_parent(modlog_channel) or _channel_parent(transcripts_channel) or _channel_parent(vc_queue_channel)

    # Duplicate setup categories usually confuse admins and cause wrong placements.
    duplicates = _find_duplicate_setup_categories(guild)
    for label, cats in duplicates.items():
        warnings.append(
            f"Multiple possible **{label}** categories found: "
            + ", ".join(f"**{cat.name}**" for cat in cats[:5])
            + ". Keep one clean setup category or make sure setup points to the intended one."
        )

    _check_category_order(
        first=public_parent,
        second=active_category,
        first_label="public/start",
        second_label="active tickets",
        warnings=warnings,
        ok=ok,
    )
    _check_category_order(
        first=active_category,
        second=archive_category,
        first_label="active tickets",
        second_label="ticket archive",
        warnings=warnings,
        ok=ok,
    )
    _check_category_order(
        first=archive_category,
        second=staff_parent,
        first_label="ticket archive",
        second_label="staff tools",
        warnings=warnings,
        ok=ok,
    )

    restricted_lifecycle_categories = [cat for cat in (active_category, archive_category) if cat is not None]

    _check_not_inside_category(
        guild=guild,
        channel_id=ticket_panel_channel_id,
        label="Public ticket panel",
        forbidden_categories=restricted_lifecycle_categories,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
        blocker=True,
    )

    _check_not_inside_category(
        guild=guild,
        channel_id=verify_channel_id,
        label="Verify",
        forbidden_categories=restricted_lifecycle_categories,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
        blocker=True,
    )

    if public_parent is not None:
        _check_channel_placement(
            guild=guild,
            channel_id=ticket_panel_channel_id,
            label="Public ticket panel",
            expected_parent=public_parent,
            expected_parent_label="public/start",
            required=True,
            blockers=blockers,
            warnings=warnings,
            ok=ok,
        )
        _check_channel_placement(
            guild=guild,
            channel_id=verify_channel_id,
            label="Verify",
            expected_parent=public_parent,
            expected_parent_label="public/start",
            required=False,
            blockers=blockers,
            warnings=warnings,
            ok=ok,
        )
        if vc_verify_channel_id > 0:
            _check_channel_placement(
                guild=guild,
                channel_id=vc_verify_channel_id,
                label="VC verify voice",
                expected_parent=public_parent,
                expected_parent_label="public/start",
                required=False,
                blockers=blockers,
                warnings=warnings,
                ok=ok,
            )

    if staff_parent is not None:
        for label, channel_id in (
            ("VC verify requests / queue", vc_verify_requests_channel_id),
            ("Transcript", transcripts_channel_id),
            ("Modlog", modlog_channel_id),
            ("Raid/security log", raidlog_channel_id),
            ("Join/exit log", join_log_channel_id),
            ("Forced verification log", force_verify_log_channel_id),
            ("Bot status", status_channel_id),
        ):
            if _safe_int(channel_id, 0) > 0:
                _check_channel_placement(
                    guild=guild,
                    channel_id=channel_id,
                    label=label,
                    expected_parent=staff_parent,
                    expected_parent_label="staff tools",
                    required=False,
                    blockers=blockers,
                    warnings=warnings,
                    ok=ok,
                )

    _check_ticket_channel_lifecycle_placement(
        guild=guild,
        active_category=active_category,
        archive_category=archive_category,
        warnings=warnings,
        ok=ok,
    )


def _check_db_health(
    *,
    blockers: List[str],
    warnings: List[str],
    ok: List[str],
) -> None:
    _check_supabase_select(
        table=_config_table_name(),
        columns="guild_id",
        label="server config",
        required=True,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    _check_supabase_select(
        table="tickets",
        columns="guild_id,user_id,channel_id,status,title,category,ticket_number,metadata,meta",
        label="ticket",
        required=True,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    _check_supabase_select(
        table="ticket_categories",
        columns="guild_id,slug,name,intake_type,match_keywords,is_default,sort_order",
        label="ticket menu",
        required=True,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    _check_supabase_select(
        table="verification_tokens",
        columns="guild_id,user_id,token,expires_at",
        label="verification token",
        required=True,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )


def _build_setup_health(guild: discord.Guild, cfg: Any) -> Tuple[List[str], List[str], List[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    bot_member = _bot_member(guild)

    # ------------------------------
    # Server-level bot permissions
    # ------------------------------
    if bot_member is None:
        blockers.append("Bot member could not be resolved in this guild.")
    else:
        guild_perms = bot_member.guild_permissions
        guild_missing: list[str] = []
        guild_warnings: list[str] = []

        required_guild_perms = (
            ("Manage Channels", guild_perms.manage_channels),
            ("Manage Roles", guild_perms.manage_roles),
            ("View Channels", guild_perms.view_channel),
            ("Send Messages", guild_perms.send_messages),
            ("Read Message History", guild_perms.read_message_history),
            ("Embed Links", guild_perms.embed_links),
        )

        for label, allowed in required_guild_perms:
            if not allowed:
                guild_missing.append(label)

        helpful_guild_perms = (
            ("Attach Files", guild_perms.attach_files),
            ("Manage Messages", guild_perms.manage_messages),
            ("View Audit Log", guild_perms.view_audit_log),
            ("Moderate Members", getattr(guild_perms, "moderate_members", False)),
            ("Kick Members", guild_perms.kick_members),
            ("Ban Members", guild_perms.ban_members),
            ("Move Members", guild_perms.move_members),
            ("Create Invite", guild_perms.create_instant_invite),
        )

        for label, allowed in helpful_guild_perms:
            if not allowed:
                guild_warnings.append(label)

        if guild_missing:
            blockers.append(f"Bot is missing required server permissions: {', '.join(guild_missing)}.")
        else:
            ok.append("Bot has required server-level channel/role permissions.")

        if guild_warnings:
            warnings.append(
                "Bot is missing useful server permissions for full moderation/verification coverage: "
                + ", ".join(guild_warnings)
                + "."
            )

    # ------------------------------
    # Saved IDs / aliases
    # ------------------------------
    ticket_category_id = _cfg_snowflake(cfg, "ticket_category_id", "active_ticket_category_id", "open_ticket_category_id")
    ticket_archive_category_id = _cfg_snowflake(cfg, "ticket_archive_category_id", "archive_category_id", "closed_ticket_category_id")
    ticket_panel_channel_id = _cfg_snowflake(
        cfg,
        "ticket_panel_channel_id",
        "support_channel_id",
        "ticket_support_channel_id",
        "public_ticket_panel_channel_id",
        "panel_channel_id",
    )
    staff_role_id = _cfg_snowflake(cfg, "staff_role_id", "ticket_staff_role_id", "support_role_id")
    vc_staff_role_id = _cfg_snowflake(cfg, "vc_staff_role_id", "staff_role_id", "ticket_staff_role_id", "support_role_id")
    transcripts_channel_id = _cfg_snowflake(cfg, "transcripts_channel_id", "transcript_channel_id")

    verify_channel_id = _cfg_snowflake(cfg, "verify_channel_id", "verification_channel_id")
    vc_verify_channel_id = _cfg_snowflake(cfg, "vc_verify_channel_id", "voice_verify_channel_id")
    vc_verify_requests_channel_id = _cfg_snowflake(
        cfg,
        "vc_verify_queue_channel_id",
        "vc_queue_channel_id",
        "vc_request_channel_id",
        "vc_verify_requests_channel_id",
        "vc_verify_requests_id",
        "voice_verify_requests_channel_id",
    )

    modlog_channel_id = _cfg_snowflake(cfg, "modlog_channel_id", "mod_log_channel_id", "raidlog_channel_id")
    raidlog_channel_id = _cfg_snowflake(cfg, "raidlog_channel_id", "raid_log_channel_id", "security_log_channel_id")
    join_log_channel_id = _cfg_snowflake(cfg, "join_log_channel_id", "join_leave_log_channel_id", "joinlog_channel_id")
    force_verify_log_channel_id = _cfg_snowflake(cfg, "force_verify_log_channel_id", "forced_verify_log_channel_id")
    status_channel_id = _cfg_snowflake(cfg, "status_channel_id", "bot_status_channel_id")

    unverified_role_id = _cfg_snowflake(cfg, "unverified_role_id")
    verified_role_id = _cfg_snowflake(cfg, "verified_role_id")
    resident_role_id = _cfg_snowflake(cfg, "resident_role_id", "member_role_id")
    server_control_role_id = _cfg_snowflake(cfg, "server_control_role_id", "control_role_id", "perm_role_id", "admin_role_id")

    # ------------------------------
    # Ticket system
    # ------------------------------
    _check_category(
        guild=guild,
        bot_member=bot_member,
        category_id=ticket_category_id,
        label="Open ticket",
        required=True,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    _check_category(
        guild=guild,
        bot_member=bot_member,
        category_id=ticket_archive_category_id,
        label="Archive/closed ticket",
        required=True,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    _check_text_channel(
        guild=guild,
        bot_member=bot_member,
        channel_id=ticket_panel_channel_id,
        label="Public ticket panel",
        required=True,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    _check_text_channel(
        guild=guild,
        bot_member=bot_member,
        channel_id=transcripts_channel_id,
        label="Transcript",
        required=True,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
        need_files=True,
    )

    ticket_staff_role = _check_role_exists(
        guild=guild,
        role_id=staff_role_id,
        label="Ticket staff",
        required=True,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    if ticket_staff_role is not None and ticket_staff_role.is_default():
        blockers.append("Ticket staff role cannot be @everyone.")

    # ------------------------------
    # Verification text + VC flow
    # ------------------------------
    _check_text_channel(
        guild=guild,
        bot_member=bot_member,
        channel_id=verify_channel_id,
        label="Verify",
        required=True,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
        need_files=False,
    )

    vc_enabled = vc_verify_channel_id > 0 or vc_verify_requests_channel_id > 0

    _check_vc_verify_voice_channel(
        guild=guild,
        bot_member=bot_member,
        channel_id=vc_verify_channel_id,
        label="VC verify voice",
        required=vc_enabled,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    _check_text_channel(
        guild=guild,
        bot_member=bot_member,
        channel_id=vc_verify_requests_channel_id,
        label="VC verify requests / queue",
        required=vc_enabled,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
        need_files=True,
    )

    vc_staff_role = _check_role_exists(
        guild=guild,
        role_id=vc_staff_role_id,
        label="VC/ticket staff",
        required=False,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    # ------------------------------
    # Logs/status
    # ------------------------------
    _check_text_channel(
        guild=guild,
        bot_member=bot_member,
        channel_id=modlog_channel_id,
        label="Modlog",
        required=True,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    _check_text_channel(
        guild=guild,
        bot_member=bot_member,
        channel_id=raidlog_channel_id,
        label="Raid/security log",
        required=False,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    _check_text_channel(
        guild=guild,
        bot_member=bot_member,
        channel_id=join_log_channel_id,
        label="Join/exit log",
        required=False,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    _check_text_channel(
        guild=guild,
        bot_member=bot_member,
        channel_id=force_verify_log_channel_id,
        label="Forced verification log",
        required=False,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    _check_text_channel(
        guild=guild,
        bot_member=bot_member,
        channel_id=status_channel_id,
        label="Bot status",
        required=False,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    # ------------------------------
    # Roles + hierarchy
    # ------------------------------
    unverified_role = _check_role_exists(
        guild=guild,
        role_id=unverified_role_id,
        label="Unverified",
        required=True,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )
    verified_role = _check_role_exists(
        guild=guild,
        role_id=verified_role_id,
        label="Verified",
        required=True,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )
    resident_role = _check_role_exists(
        guild=guild,
        role_id=resident_role_id,
        label="Resident/member",
        required=False,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )
    control_role = _check_role_exists(
        guild=guild,
        role_id=server_control_role_id,
        label="Server-control",
        required=False,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    for label, role in (
        ("Unverified", unverified_role),
        ("Verified", verified_role),
        ("Resident/member", resident_role),
    ):
        if role is not None and role.is_default():
            blockers.append(f"{label} role cannot be @everyone.")
        _check_manageable_role(
            guild=guild,
            bot_member=bot_member,
            role=role,
            label=label,
            blockers=blockers,
            ok=ok,
        )

    if control_role is not None:
        if control_role.is_default():
            blockers.append("Server-control role cannot be @everyone.")
        else:
            ok.append(f"Server-control role is a safe plain/access role: {control_role.mention}.")

    # Staff roles do not need to be managed by the bot, but they must exist and
    # not be @everyone.
    for label, role in (("Ticket staff", ticket_staff_role), ("VC/ticket staff", vc_staff_role)):
        if role is not None and role.is_default():
            blockers.append(f"{label} role cannot be @everyone.")

    # ------------------------------
    # Cross-check common setup mistakes
    # ------------------------------
    if verify_channel_id > 0 and ticket_panel_channel_id > 0 and verify_channel_id == ticket_panel_channel_id:
        warnings.append("Verify channel and ticket panel channel are the same. This can work, but separate channels are cleaner.")

    if modlog_channel_id > 0 and transcripts_channel_id > 0 and modlog_channel_id == transcripts_channel_id:
        warnings.append("Modlog and transcript channel are the same. This can work, but separate channels are cleaner.")

    if ticket_category_id > 0 and ticket_archive_category_id > 0 and ticket_category_id == ticket_archive_category_id:
        blockers.append("Open ticket category and archive category cannot be the same category.")

    if unverified_role_id > 0 and verified_role_id > 0 and unverified_role_id == verified_role_id:
        blockers.append("Unverified role and Verified role cannot be the same role.")


    # ------------------------------
    # Channel/category placement checks
    # ------------------------------
    _check_layout_health(
        guild=guild,
        ticket_category_id=ticket_category_id,
        ticket_archive_category_id=ticket_archive_category_id,
        ticket_panel_channel_id=ticket_panel_channel_id,
        verify_channel_id=verify_channel_id,
        vc_verify_channel_id=vc_verify_channel_id,
        vc_verify_requests_channel_id=vc_verify_requests_channel_id,
        transcripts_channel_id=transcripts_channel_id,
        modlog_channel_id=modlog_channel_id,
        raidlog_channel_id=raidlog_channel_id,
        join_log_channel_id=join_log_channel_id,
        force_verify_log_channel_id=force_verify_log_channel_id,
        status_channel_id=status_channel_id,
        blockers=blockers,
        warnings=warnings,
        ok=ok,
    )

    # ------------------------------
    # Database schema checks
    # ------------------------------
    _check_db_health(blockers=blockers, warnings=warnings, ok=ok)

    if _safe_str(_cfg_value(cfg, "source", ""), "") in {"env", "defaults", "default"}:
        warnings.append("This server appears to be using env/default fallback config. Run `/dank setup` before public use.")

    return blockers, warnings, ok



def _health_embed(guild: discord.Guild, cfg: Any) -> discord.Embed:
    blockers, warnings, ok = _build_setup_health(guild, cfg)
    ready = not blockers

    embed = discord.Embed(
        title="🩺 Dank Shield Setup Health",
        description="✅ **Core setup is ready to test.**" if ready else "🚫 **Fix the blockers before testing.**",
        color=discord.Color.green() if ready else discord.Color.red(),
    )
    embed.add_field(name="Blockers", value=_field_text(blockers, empty="✅ None"), inline=False)
    embed.add_field(name="Warnings", value=_field_text(warnings, empty="✅ None"), inline=False)
    embed.add_field(name="Passing Checks", value=_field_text(ok, empty="No passing checks reported."), inline=False)
    embed.set_footer(text=f"Guild {guild.id} • config source: {_safe_str(_cfg_value(cfg, 'source', 'unknown'), 'unknown')}")
    return embed


# ============================================================
# slash commands
# ============================================================


@stoney_group.command(
    name="setup-tickets",
    description="Configure ticket categories, staff role, transcripts, and prefix for this server.",
)
@app_commands.describe(
    ticket_category="Category where open ticket channels should be created.",
    staff_role="Role that can manage/support tickets.",
    archive_category="Optional category where closed tickets should be moved.",
    transcripts_channel="Channel where ticket transcripts should be posted.",
    ticket_prefix="Ticket channel prefix. Example: ticket",
)
async def setup_tickets(
    interaction: discord.Interaction,
    ticket_category: discord.CategoryChannel,
    staff_role: discord.Role,
    archive_category: Optional[discord.CategoryChannel] = None,
    transcripts_channel: Optional[discord.TextChannel] = None,
    ticket_prefix: Optional[str] = "ticket",
) -> None:
    if not await _require_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    assert guild is not None

    blockers, warnings, ok = _validate_ticket_setup(
        guild,
        ticket_category,
        staff_role,
        archive_category,
        transcripts_channel,
    )

    if blockers:
        await _send_blocked_setup(interaction, "🚫 Ticket Setup Blocked", blockers, warnings, ok)
        return

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
        await interaction.followup.send(
            f"❌ Failed saving ticket setup: `{type(e).__name__}: {str(e)[:300]}`",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    embed = _config_embed(guild, cfg, title="✅ Ticket Setup Saved")
    _add_validation_summary(embed, warnings, ok)

    await interaction.followup.send(
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@stoney_group.command(
    name="setup-verify",
    description="Configure verification channels and roles for this server.",
)
@app_commands.describe(
    verify_channel="Main TEXT channel where users read/start verification.",
    unverified_role="Role new/unverified members receive.",
    verified_role="Role approved/verified members receive.",
    resident_role="Optional resident/member role.",
    vc_verify_channel="Optional VOICE channel used for VC verification sessions.",
    vc_queue_channel="Required TEXT channel for VC verification requests/status if VC verify is used.",
)
async def setup_verify(
    interaction: discord.Interaction,
    verify_channel: discord.TextChannel,
    unverified_role: discord.Role,
    verified_role: discord.Role,
    resident_role: Optional[discord.Role] = None,
    vc_verify_channel: Optional[discord.VoiceChannel] = None,
    vc_queue_channel: Optional[discord.TextChannel] = None,
) -> None:
    if not await _require_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    assert guild is not None

    blockers, warnings, ok = _validate_verify_setup(
        guild,
        verify_channel,
        unverified_role,
        verified_role,
        resident_role,
        vc_verify_channel,
        vc_queue_channel,
    )

    if blockers:
        await _send_blocked_setup(interaction, "🚫 Verification Setup Blocked", blockers, warnings, ok)
        return

    updates: Dict[str, Any] = {
        "verify_channel_id": _channel_value(verify_channel),
        "unverified_role_id": _role_value(unverified_role),
        "verified_role_id": _role_value(verified_role),
        "resident_role_id": _role_value(resident_role),
        "vc_verify_channel_id": _channel_value(vc_verify_channel),
        "vc_verify_queue_channel_id": _channel_value(vc_queue_channel),
        "vc_queue_channel_id": _channel_value(vc_queue_channel),
        "vc_request_channel_id": _channel_value(vc_queue_channel),
        "vc_verify_requests_channel_id": _channel_value(vc_queue_channel),
        "configured_by_id": str(interaction.user.id),
        "configured_by_name": str(interaction.user),
        "configured_at": _utc_iso(),
    }

    try:
        await _upsert_config(guild.id, updates)
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        await interaction.followup.send(
            f"❌ Failed saving verification setup: `{type(e).__name__}: {str(e)[:300]}`",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    embed = _config_embed(guild, cfg, title="✅ Verification Setup Saved")
    _add_validation_summary(embed, warnings, ok)

    await interaction.followup.send(
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@stoney_group.command(
    name="setup-logs",
    description="Configure moderation, security, join/exit, and forced-verification log channels.",
)
@app_commands.describe(
    modlog_channel="Main moderation log channel.",
    raidlog_channel="Optional raid/security log channel.",
    join_log_channel="Optional join/exit log channel.",
    force_verify_log_channel="Optional forced verification log channel.",
)
async def setup_logs(
    interaction: discord.Interaction,
    modlog_channel: discord.TextChannel,
    raidlog_channel: Optional[discord.TextChannel] = None,
    join_log_channel: Optional[discord.TextChannel] = None,
    force_verify_log_channel: Optional[discord.TextChannel] = None,
) -> None:
    if not await _require_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    assert guild is not None

    blockers, warnings, ok = _validate_log_setup(
        guild,
        modlog_channel,
        raidlog_channel,
        join_log_channel,
        force_verify_log_channel,
    )

    if blockers:
        await _send_blocked_setup(interaction, "🚫 Log Setup Blocked", blockers, warnings, ok)
        return

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
        await interaction.followup.send(
            f"❌ Failed saving log setup: `{type(e).__name__}: {str(e)[:300]}`",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    embed = _config_embed(guild, cfg, title="✅ Log Setup Saved")
    _add_validation_summary(embed, warnings, ok)

    await interaction.followup.send(
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@stoney_group.command(
    name="health",
    description="Run a setup health check for this server.",
)
async def health(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    assert guild is not None

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
        embed = _health_embed(guild, cfg)
    except Exception as e:
        embed = discord.Embed(
            title="❌ Health Check Failed",
            description=f"`{type(e).__name__}: {str(e)[:350]}`",
            color=discord.Color.red(),
        )

    await interaction.followup.send(
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@stoney_group.command(
    name="current",
    description="Show the current saved Dank Shield setup for this server.",
)
async def current(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    assert guild is not None

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
        embed = _config_embed(guild, cfg, title="📋 Current Dank Shield Setup")
    except Exception as e:
        embed = discord.Embed(
            title="❌ Current Setup Failed",
            description=f"`{type(e).__name__}: {str(e)[:350]}`",
            color=discord.Color.red(),
        )

    await interaction.followup.send(
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@stoney_group.command(
    name="config-cache",
    description="Show setup config cache diagnostics for this server.",
)
async def config_cache(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    assert guild is not None

    try:
        snapshot = guild_config_cache_snapshot()
    except Exception as e:
        snapshot = {"error": f"{type(e).__name__}: {str(e)[:250]}"}

    embed = discord.Embed(
        title="🧩 Config Cache",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Guild", value=f"`{guild.id}`", inline=False)
    embed.add_field(name="Snapshot", value=f"```py\n{str(snapshot)[:950]}\n```", inline=False)

    await interaction.followup.send(
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )

# ============================================================
# command registration
# ============================================================

def register_public_setup_group_commands(bot: Any, tree: Any) -> None:
    """Register the clean public Dank Shield command group.

    The variable is still named stoney_group internally for compatibility with
    older modules that attach setup/help/cleanup/spam commands to it.
    The actual Discord slash command name is /dank.
    """
    try:
        if tree.get_command("stoney", guild=None) is not None:
            tree.remove_command("stoney", guild=None)
    except Exception:
        pass

    try:
        if tree.get_command(stoney_group.name, guild=None) is not None:
            tree.remove_command(stoney_group.name, guild=None)
    except Exception:
        pass

    tree.add_command(stoney_group)

    try:
        print("✅ public_setup_group registered /dank command group")
    except Exception:
        pass
