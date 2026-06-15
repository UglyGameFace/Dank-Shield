from __future__ import annotations

from typing import Any, Optional

import discord
from discord import app_commands

from ..guild_action_guard import decide_guild_action
from ..guild_config import get_guild_config, invalidate_guild_config
from ..guild_context import get_guild_context
from ..interaction_guard import safe_send_error, safe_send_interaction
from .public_setup_group import _require_setup_permission, _upsert_config, stoney_group

_ATTACHED = False

modlog_group = app_commands.Group(
    name="modlog",
    description="Set up and test this server's moderation log channel.",
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _cfg_int(cfg: Any, *keys: str) -> int:
    for key in keys:
        try:
            value = getattr(cfg, key, None)
            if value not in {None, "", 0, "0"}:
                out = _safe_int(value, 0)
                if out > 0:
                    return out
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                value = cfg.get(key)
                if value not in {None, "", 0, "0"}:
                    out = _safe_int(value, 0)
                    if out > 0:
                        return out
        except Exception:
            pass
    return 0


def _modlog_channel(guild: discord.Guild, cfg: Any) -> Optional[discord.TextChannel]:
    cid = _cfg_int(cfg, "modlog_channel_id", "mod_log_channel_id", "logs_channel_id")
    if cid > 0:
        channel = guild.get_channel(cid)
        if isinstance(channel, discord.TextChannel):
            return channel
    for name in ("mod-log", "modlog", "logs", "audit-log", "audit-logs"):
        for channel in list(getattr(guild, "text_channels", []) or []):
            raw = str(getattr(channel, "name", "") or "").lower().replace("_", "-").replace(" ", "-")
            if name in raw:
                return channel if isinstance(channel, discord.TextChannel) else None
    return None


def _missing_perms(channel: discord.TextChannel, member: Optional[discord.Member]) -> list[str]:
    if not isinstance(member, discord.Member):
        return ["Resolve bot member"]
    perms = channel.permissions_for(member)
    checks = {
        "View Channel": perms.view_channel,
        "Send Messages": perms.send_messages,
        "Embed Links": perms.embed_links,
        "Read Message History": perms.read_message_history,
        "View Audit Log": member.guild_permissions.view_audit_log or member.guild_permissions.administrator,
    }
    return [name for name, ok in checks.items() if not ok]


async def _defer(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


async def _send(interaction: discord.Interaction, content: str = "", **kwargs: Any) -> None:
    await safe_send_interaction(interaction, content=content, ephemeral=True, **kwargs)


async def _require_safe_modlog_test(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    if guild is None:
        await safe_send_interaction(interaction, content="❌ This command must be used inside a server.", ephemeral=True)
        return False
    try:
        context = await get_guild_context(int(guild.id), refresh=True)
        decision = decide_guild_action(
            context,
            action="send modlog test",
            feature="logging",
            required_keys=("modlog_channel_id",),
        )
    except Exception as exc:
        await safe_send_interaction(
            interaction,
            content=(
                "❌ Could not verify this server's modlog safety state. "
                f"Nothing was sent. `{type(exc).__name__}: {str(exc)[:250]}`"
            ),
            ephemeral=True,
        )
        return False
    if decision.denied:
        await safe_send_interaction(interaction, content=decision.user_message(), ephemeral=True)
        return False
    return True


async def save_modlog_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This command must be used inside a server.")
    await _defer(interaction)
    missing = _missing_perms(channel, interaction.guild.me)
    await _upsert_config(
        int(interaction.guild.id),
        {
            "modlog_channel_id": str(int(channel.id)),
            "modlog_enabled": True,
            "modlog_updated_by_id": str(int(interaction.user.id)),
        },
    )
    invalidate_guild_config(int(interaction.guild.id))
    suffix = "" if not missing else "\n⚠️ Still missing: " + ", ".join(missing)
    await interaction.followup.send(f"✅ Modlog channel saved as {channel.mention}.{suffix}", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def open_modlog_health(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send(interaction, "❌ This command must be used inside a server.")
    await _defer(interaction)
    cfg = await get_guild_config(int(guild.id), refresh=True)
    channel = _modlog_channel(guild, cfg)
    embed = discord.Embed(title="🧾 Modlog Health", color=discord.Color.green() if channel else discord.Color.red(), timestamp=discord.utils.utcnow())
    if channel is None:
        embed.add_field(name="Channel", value="❌ Not saved. Use Core Setup → Use Existing Roles/Channels → Logs + Status, or `/dank modlog set-channel`.", inline=False)
    else:
        missing = _missing_perms(channel, guild.me)
        embed.add_field(name="Channel", value=f"✅ {channel.mention}", inline=False)
        embed.add_field(name="Permissions", value="✅ Ready" if not missing else "❌ Missing: " + ", ".join(missing), inline=False)
    listeners = getattr(getattr(interaction.client, "extra_events", None), "copy", lambda: {})()
    expected = [
        "on_message_delete", "on_raw_message_delete", "on_bulk_message_delete", "on_raw_bulk_message_delete",
        "on_message_edit", "on_raw_message_edit", "on_member_join", "on_member_remove", "on_member_ban",
        "on_member_update", "on_voice_state_update", "on_guild_channel_create", "on_guild_channel_delete",
        "on_guild_channel_update", "on_guild_role_create", "on_guild_role_delete", "on_guild_role_update",
        "on_thread_create", "on_thread_delete", "on_thread_update", "on_invite_create", "on_invite_delete",
        "on_guild_update", "on_member_unban", "on_guild_emojis_update", "on_guild_stickers_update", "on_webhooks_update",
    ]
    present = []
    missing_events = []
    if isinstance(listeners, dict):
        for event in expected:
            count = len(list(listeners.get(event) or []))
            (present if count else missing_events).append(f"`{event}`" + (f" ({count})" if count else ""))
    embed.add_field(name="Coverage", value=("✅ " + str(len(present)) + f"/{len(expected)} event families active") if present else "⚠️ Could not inspect event listeners.", inline=False)
    if missing_events:
        embed.add_field(name="Missing / Core handled elsewhere", value="\n".join(missing_events[:12])[:1024], inline=False)
    embed.set_footer(text="Uses existing modlog_channel_id. No separate overlapping log setting.")
    await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def send_modlog_test(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send(interaction, "❌ This command must be used inside a server.")
    await _defer(interaction)
    if not await _require_safe_modlog_test(interaction):
        return
    cfg = await get_guild_config(int(guild.id), refresh=True)
    channel = _modlog_channel(guild, cfg)
    if channel is None:
        return await interaction.followup.send("❌ No modlog channel saved. Use Core Setup → Use Existing Roles/Channels → Logs + Status, or `/dank modlog set-channel`.", ephemeral=True)
    missing = _missing_perms(channel, guild.me)
    if missing:
        return await interaction.followup.send("❌ Missing permissions in saved modlog channel: " + ", ".join(missing), ephemeral=True)
    embed = discord.Embed(title="✅ Dank Shield Modlog Test", description="If you can see this, this server's modlog channel is wired correctly.", color=discord.Color.green(), timestamp=discord.utils.utcnow())
    embed.add_field(name="Triggered By", value=f"{interaction.user} (`{interaction.user.id}`)", inline=False)
    embed.set_footer(text="Dank Shield modlog test")
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        await safe_send_error(
            interaction,
            exc,
            title="❌ Could not send modlog test safely",
            guidance="Nothing else was changed. Check the saved modlog channel permissions, then retry `/dank modlog test`.",
            ephemeral=True,
        )
        return
    await interaction.followup.send(f"✅ Test log sent to {channel.mention}.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@modlog_group.command(name="set-channel", description="Save the moderation log channel for this server.")
@app_commands.describe(channel="Channel where Dank Shield should send moderation/audit logs.")
async def modlog_set_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    await save_modlog_channel(interaction, channel)


@modlog_group.command(name="health", description="Check modlog channel, permissions, and listener coverage.")
async def modlog_health(interaction: discord.Interaction) -> None:
    await open_modlog_health(interaction)


@modlog_group.command(name="test", description="Send a test modlog embed to the saved modlog channel.")
async def modlog_test(interaction: discord.Interaction) -> None:
    await send_modlog_test(interaction)


def _attach() -> bool:
    global _ATTACHED
    if _ATTACHED:
        return True
    try:
        if stoney_group.get_command("modlog") is not None:
            _ATTACHED = True
            return True
    except Exception:
        pass
    try:
        stoney_group.add_command(modlog_group)
        _ATTACHED = True
        return True
    except Exception as exc:
        try:
            print(f"⚠️ public_modlog_group failed attaching /dank modlog: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


def register_public_modlog_group_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    if _attach():
        try:
            print("✅ public_modlog_group: attached /dank modlog commands")
        except Exception:
            pass


_attach()

__all__ = [
    "register_public_modlog_group_commands",
    "modlog_group",
    "open_modlog_health",
    "send_modlog_test",
    "save_modlog_channel",
]
