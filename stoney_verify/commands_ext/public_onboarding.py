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

import os
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

import discord

from .public_setup_config_writer import upsert_guild_config
from ..globals import get_supabase
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

_JSON_CONFIG_KEYS: tuple[str, ...] = ("settings", "config", "metadata", "meta")


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


def _config_table_name() -> str:
    try:
        return (os.getenv("DANK_GUILD_CONFIG_TABLE") or "guild_configs").strip() or "guild_configs"
    except Exception:
        return "guild_configs"


def _mapping(value: Any) -> dict[str, Any]:
    try:
        if isinstance(value, Mapping):
            return dict(value)
    except Exception:
        pass
    return {}


def _strip_stale_setup_ids(value: Any) -> dict[str, Any]:
    out = _mapping(value)
    for key in _STALE_SETUP_ID_KEYS:
        out.pop(key, None)
    return out


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
    embed.set_footer(text=f"Guild {guild.id} • isolated per-server config")
    return embed


def _purge_stale_existing_ids_sync(guild_id: int, neutral_payload: Mapping[str, Any]) -> None:
    """Clear copied/stale setup snowflakes from an existing row before onboarding.

    The public setup writer intentionally protects existing role/channel/category
    IDs from accidental overwrite. That is correct during normal setup, but on a
    fresh guild join it means an old row can keep another server's IDs forever.
    This pre-flight purge only runs for the joining guild and only removes known
    setup snowflake keys.
    """

    try:
        sb = get_supabase()
        if sb is None:
            return
        table = _config_table_name()
        gid = str(int(guild_id))
        res = sb.table(table).select("*").eq("guild_id", gid).limit(1).execute()
        rows = getattr(res, "data", None) or []
        if not rows or not isinstance(rows[0], Mapping):
            return
        row = dict(rows[0])
        columns = {str(k) for k in row.keys()}

        base = {str(k): v for k, v in dict(neutral_payload).items() if v is not None}
        flat_clear = {key: None for key in _STALE_SETUP_ID_KEYS if key in columns}
        json_updates: dict[str, Any] = {}
        for json_key in _JSON_CONFIG_KEYS:
            if json_key not in columns:
                continue
            current = _strip_stale_setup_ids(row.get(json_key))
            current.update(base)
            json_updates[json_key] = current

        attempts: list[dict[str, Any]] = []
        if json_updates or flat_clear:
            attempts.append({**base, **json_updates, **flat_clear})
        if json_updates:
            attempts.append({**base, **json_updates})
        if flat_clear:
            attempts.append({**base, **flat_clear})
        attempts.append(base)

        for payload in attempts:
            try:
                sb.table(table).update(payload).eq("guild_id", gid).execute()
                print(f"✅ public_onboarding purged stale setup IDs guild={guild_id} cleared_flat={len(flat_clear)} json={list(json_updates.keys())}")
                return
            except Exception:
                continue
    except Exception as e:
        try:
            print(f"⚠️ public_onboarding stale setup ID purge failed guild={guild_id}: {repr(e)}")
        except Exception:
            pass


async def _purge_stale_existing_ids(guild_id: int, neutral_payload: Mapping[str, Any]) -> None:
    try:
        import asyncio
        await asyncio.to_thread(_purge_stale_existing_ids_sync, int(guild_id), dict(neutral_payload))
    except Exception:
        pass


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
        await _purge_stale_existing_ids(int(guild.id), payload)
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
