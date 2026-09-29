from __future__ import annotations

"""
Public guild onboarding lifecycle.

This is the production-friendly first-run layer:

- When Dank Shield joins a server, create a neutral guild_configs row for that guild.
- Never copy the beta/home server's channels or roles.
- Purge stale role/channel/category IDs that may already exist for the new guild row.
- Post a simple setup prompt in the best safe channel the bot can write to.
- When Dank Shield leaves a server, mark the config inactive instead of deleting it.

The goal is TicketTool-simple onboarding without hidden cross-server state.
"""

from datetime import datetime, timezone
from typing import Any, Optional

import discord

from .public_setup_config_writer import clear_guild_config_keys, upsert_guild_config
from ..guild_config import invalidate_guild_config

_LISTENERS_REGISTERED = False

_STALE_SETUP_ID_KEYS: tuple[str, ...] = (
    "verify_channel_id",
    "vc_verify_channel_id",
    "vc_verify_queue_channel_id",
    "ticket_category_id",
    "ticket_archive_category_id",
    "transcripts_channel_id",
    "ticket_panel_channel_id",
    "support_channel_id",
    "status_channel_id",
    "bot_status_channel_id",
    "uptime_channel_id",
    "health_channel_id",
    "modlog_channel_id",
    "raidlog_channel_id",
    "join_log_channel_id",
    "force_verify_log_channel_id",
    "welcome_channel_id",
    "start_category_id",
    "management_category_id",
    "staff_tools_category_id",
    "unverified_role_id",
    "verified_role_id",
    "resident_role_id",
    "member_role_id",
    "staff_role_id",
    "ticket_staff_role_id",
    "support_role_id",
    "vc_staff_role_id",
    "server_control_role_id",
    "control_role_id",
    "perm_role_id",
)

def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _can_send_setup_prompt(channel: Any, guild: discord.Guild) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        me = guild.me
        if me is None:
            return False
        perms = channel.permissions_for(me)
        return bool(perms.view_channel and perms.send_messages and perms.embed_links)
    except Exception:
        return False


def _best_setup_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    candidates: list[discord.TextChannel] = []

    try:
        if isinstance(guild.system_channel, discord.TextChannel):
            candidates.append(guild.system_channel)
    except Exception:
        pass

    try:
        rules_channel = getattr(guild, "rules_channel", None)
        if isinstance(rules_channel, discord.TextChannel):
            candidates.append(rules_channel)
    except Exception:
        pass

    try:
        public_updates_channel = getattr(guild, "public_updates_channel", None)
        if isinstance(public_updates_channel, discord.TextChannel):
            candidates.append(public_updates_channel)
    except Exception:
        pass

    try:
        for channel in guild.text_channels:
            candidates.append(channel)
    except Exception:
        pass

    seen: set[int] = set()
    for channel in candidates:
        cid = _safe_int(getattr(channel, "id", 0), 0)
        if cid <= 0 or cid in seen:
            continue
        seen.add(cid)
        if _can_send_setup_prompt(channel, guild):
            return channel
    return None


def _setup_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="👋 Thanks for adding Dank Shield",
        description=(
            "I’m ready, and I won’t use another server’s channels or roles.\n\n"
            "Start setup with **`/dank setup`**. It walks you through the safest setup path for this server."
        ),
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="Fast setup order",
        value=(
            "1. Run `/dank setup`\n"
            "2. Choose **Create Missing Items** or **Use My Existing Server**\n"
            "3. Let Dank Shield save this server’s ticket, verification, and log settings\n"
            "4. Use `/ticket`, `/tickets`, and `/ticket-panel` once setup is ready"
        ),
        inline=False,
    )
    embed.add_field(
        name="Production safety",
        value="Until setup is saved for this server, staff/ticket workflows stay locked instead of guessing.",
        inline=False,
    )
    embed.set_footer(text="Setup is isolated to this server.")
    return embed


async def _purge_stale_existing_ids(guild_id: int) -> None:
    """Atomically clear stale setup snowflakes through the canonical writer."""

    await clear_guild_config_keys(
        int(guild_id),
        _STALE_SETUP_ID_KEYS,
        source="public onboarding stale setup ID purge",
    )


async def _create_neutral_config_row(guild: discord.Guild, *, joined: bool) -> None:
    owner_id = _safe_int(getattr(guild, "owner_id", 0), 0)
    payload = {
        "guild_name": str(getattr(guild, "name", "") or ""),
        "owner_id": str(owner_id) if owner_id else None,
        "bot_active": bool(joined),
        "last_seen_at": _utc_iso(),
        "use_env_fallbacks": False,
        "allow_runtime_discovery": True,
    }
    if joined:
        payload.update(
            {
                "bot_joined_at": _utc_iso(),
                "setup_status": "needs_setup",
                "configured": False,
            }
        )
        await _purge_stale_existing_ids(int(guild.id))
    else:
        payload.update(
            {
                "bot_left_at": _utc_iso(),
                "setup_status": "bot_left",
            }
        )

    try:
        await upsert_guild_config(int(guild.id), payload)
        invalidate_guild_config(guild.id)
    except Exception as e:
        try:
            print(f"⚠️ public_onboarding failed writing guild lifecycle row guild={guild.id}: {repr(e)}")
        except Exception:
            pass


async def _on_guild_join(guild: discord.Guild) -> None:
    try:
        await _create_neutral_config_row(guild, joined=True)
        channel = _best_setup_channel(guild)
        if channel is not None:
            try:
                await channel.send(embed=_setup_embed(guild), allowed_mentions=discord.AllowedMentions.none())
            except Exception as e:
                print(f"⚠️ public_onboarding could not send setup prompt guild={guild.id}: {repr(e)}")
        print(f"✅ public_onboarding initialized isolated setup row guild={guild.id} name={guild.name!r}")
    except Exception as e:
        print(f"⚠️ public_onboarding on_guild_join failed guild={getattr(guild, 'id', 'unknown')}: {repr(e)}")


async def _on_guild_remove(guild: discord.Guild) -> None:
    try:
        await _create_neutral_config_row(guild, joined=False)
        print(f"ℹ️ public_onboarding marked bot inactive guild={guild.id} name={guild.name!r}")
    except Exception as e:
        print(f"⚠️ public_onboarding on_guild_remove failed guild={getattr(guild, 'id', 'unknown')}: {repr(e)}")


def register_public_onboarding_listeners(bot, tree) -> None:
    global _LISTENERS_REGISTERED
    _ = tree
    if _LISTENERS_REGISTERED:
        return
    try:
        bot.add_listener(_on_guild_join, "on_guild_join")
        bot.add_listener(_on_guild_remove, "on_guild_remove")
        _LISTENERS_REGISTERED = True
        print("✅ public_onboarding: registered isolated guild join/leave onboarding listeners")
    except Exception as e:
        print(f"⚠️ public_onboarding failed registering listeners: {repr(e)}")


__all__ = ["register_public_onboarding_listeners"]
