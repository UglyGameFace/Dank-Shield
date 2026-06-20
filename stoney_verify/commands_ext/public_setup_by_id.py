from __future__ import annotations

from typing import Any, Dict, Optional

import discord

from .common import safe_defer
from .public_setup_config_writer import apply_public_setup_writer_patch, upsert_guild_config
from .public_setup_group import (
    _channel_value,
    _config_embed,
    _field_text,
    _require_setup_permission,
    _role_value,
    _safe_int,
    _send_blocked_setup,
    _utc_iso,
    _validate_verify_setup,
    invalidate_guild_config,
    get_guild_config,
    dank_group,
)


# ============================================================
# public_setup_by_id.py
# ------------------------------------------------------------
# Production-safe fallback setup commands for Discord mobile /
# autocomplete edge cases.
#
# Discord's slash-command role picker can omit roles in some
# clients/contexts. This module lets admins paste Snowflake IDs
# instead, while still resolving and validating the actual
# Discord objects before saving anything.
# ============================================================


_VERIFY_IDS_COMMAND_ATTACHED = False


def _clean_snowflake(value: Optional[str]) -> int:
    text = str(value or "").strip()
    for prefix in ("<@&", "<#", "<@", "<!@"):
        if text.startswith(prefix) and text.endswith(">"):
            text = text[len(prefix) : -1]
            break
    text = text.replace("`", "").replace(" ", "")
    return _safe_int(text, 0)


def _resolve_text_channel(guild: discord.Guild, value: str, label: str, errors: list[str]) -> Optional[discord.TextChannel]:
    channel_id = _clean_snowflake(value)
    if channel_id <= 0:
        errors.append(f"{label} must be a valid text-channel ID.")
        return None
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        errors.append(f"{label} ID `{channel_id}` does not resolve to a text channel in this server.")
        return None
    return channel


def _resolve_voice_channel(guild: discord.Guild, value: Optional[str], label: str, errors: list[str]) -> Optional[discord.VoiceChannel]:
    channel_id = _clean_snowflake(value)
    if channel_id <= 0:
        return None
    channel = guild.get_channel(channel_id)
    if isinstance(channel, discord.VoiceChannel):
        return channel
    stage_type = getattr(discord, "StageChannel", None)
    if stage_type is not None and isinstance(channel, stage_type):
        return channel  # type: ignore[return-value]
    errors.append(f"{label} ID `{channel_id}` does not resolve to a voice/stage channel in this server.")
    return None


def _resolve_role(guild: discord.Guild, value: str, label: str, errors: list[str]) -> Optional[discord.Role]:
    role_id = _clean_snowflake(value)
    if role_id <= 0:
        errors.append(f"{label} must be a valid role ID.")
        return None
    role = guild.get_role(role_id)
    if not isinstance(role, discord.Role):
        errors.append(f"{label} ID `{role_id}` does not resolve to a role in this server.")
        return None
    return role


def _resolve_optional_role(guild: discord.Guild, value: Optional[str], label: str, errors: list[str]) -> Optional[discord.Role]:
    role_id = _clean_snowflake(value)
    if role_id <= 0:
        return None
    role = guild.get_role(role_id)
    if not isinstance(role, discord.Role):
        errors.append(f"{label} ID `{role_id}` does not resolve to a role in this server.")
        return None
    return role


def _resolution_error_embed(errors: list[str]) -> discord.Embed:
    embed = discord.Embed(
        title="🚫 Verification Setup ID Check Failed",
        description="Setup was not saved. One or more pasted IDs could not be resolved in this server.",
        color=discord.Color.red(),
    )
    embed.add_field(name="Errors", value=_field_text(errors, empty="Unknown ID resolution error."), inline=False)
    embed.add_field(
        name="How to copy IDs",
        value=(
            "Enable Discord Developer Mode, long-press/right-click the channel or role, then choose **Copy ID**.\n"
            "For roles on mobile: Server Settings → Roles → select role → three dots/menu → Copy ID."
        ),
        inline=False,
    )
    return embed


async def _setup_verify_ids_callback(
    interaction: discord.Interaction,
    verify_channel_id: str,
    unverified_role_id: str,
    verified_role_id: str,
    resident_role_id: Optional[str] = None,
    vc_verify_channel_id: Optional[str] = None,
    vc_queue_channel_id: Optional[str] = None,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    errors: list[str] = []
    verify_channel = _resolve_text_channel(guild, verify_channel_id, "Verify text channel", errors)
    unverified_role = _resolve_role(guild, unverified_role_id, "Unverified role", errors)
    verified_role = _resolve_role(guild, verified_role_id, "Verified role", errors)
    resident_role = _resolve_optional_role(guild, resident_role_id, "Resident role", errors)
    vc_verify_channel = _resolve_voice_channel(guild, vc_verify_channel_id, "VC verify channel", errors)
    vc_queue_channel = _resolve_text_channel(guild, vc_queue_channel_id, "VC queue/status text channel", errors) if _clean_snowflake(vc_queue_channel_id) > 0 else None

    if errors or verify_channel is None or unverified_role is None or verified_role is None:
        return await interaction.followup.send(embed=_resolution_error_embed(errors or ["Required ID failed to resolve."]), ephemeral=True)

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
        return await _send_blocked_setup(interaction, "🚫 Verification Setup Blocked", blockers, warnings, ok)

    updates: Dict[str, Any] = {
        "verify_channel_id": _channel_value(verify_channel),
        "unverified_role_id": _role_value(unverified_role),
        "verified_role_id": _role_value(verified_role),
        "resident_role_id": _role_value(resident_role),
        "vc_verify_channel_id": _channel_value(vc_verify_channel),
        "vc_verify_queue_channel_id": _channel_value(vc_queue_channel),
        "configured_by_id": str(interaction.user.id),
        "configured_by_name": str(interaction.user),
        "configured_at": _utc_iso(),
    }

    try:
        await upsert_guild_config(guild.id, updates)
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed saving verification setup by IDs: `{e}`", ephemeral=True)

    embed = _config_embed(guild, cfg, title="✅ Verification Setup Saved From IDs")
    if warnings:
        embed.add_field(name="Saved With Warnings", value=_field_text(warnings, empty="✅ None"), inline=False)
    if ok:
        embed.add_field(name="Pre-save Checks", value=_field_text(ok, empty="✅ Passed"), inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


def _attach_verify_ids_command() -> None:
    global _VERIFY_IDS_COMMAND_ATTACHED
    if _VERIFY_IDS_COMMAND_ATTACHED:
        return

    try:
        existing = dank_group.get_command("setup-verify-ids")
    except Exception:
        existing = None

    if existing is not None:
        _VERIFY_IDS_COMMAND_ATTACHED = True
        return

    command = discord.app_commands.Command(
        name="setup-verify-ids",
        description="Configure verification using pasted channel/role IDs when Discord role picker hides roles.",
        callback=_setup_verify_ids_callback,
    )
    dank_group.add_command(command)
    _VERIFY_IDS_COMMAND_ATTACHED = True


apply_public_setup_writer_patch()
_attach_verify_ids_command()


def register_public_setup_by_id_commands(bot, tree) -> None:
    _ = bot
    _ = tree
    apply_public_setup_writer_patch()
    _attach_verify_ids_command()
    try:
        print("✅ public_setup_by_id: attached /dank setup-verify-ids command + durable config writer")
    except Exception:
        pass


__all__ = ["register_public_setup_by_id_commands"]
