from __future__ import annotations

"""Authoritative public member lifecycle router.

Welcome Card Studio owns the single member-facing join sender and Exit Card
Studio owns the single member-facing leave sender. Staff audit/modlog listeners
remain separate and are never folded into these public lifecycle cards.
"""

from typing import Any, Optional

import discord
from discord import app_commands

from stoney_verify.exit_card_runtime import (
    resolve_exit_card_channel,
    send_live_exit_card,
)
from stoney_verify.welcome_card_runtime import (
    resolve_join_card_channel,
    send_live_welcome_card,
)

try:
    from stoney_verify.globals import bot
except Exception:  # pragma: no cover
    bot = None  # type: ignore

try:
    from stoney_verify.commands_ext.public_setup_group import dank_group
except Exception:  # pragma: no cover
    dank_group = None  # type: ignore

_INSTALLED = False

JOIN_LEAVE_KEYS = (
    "join_leave_log_channel_id",
    "join_leave_channel_id",
    "member_join_leave_log_channel_id",
    "member_lifecycle_log_channel_id",
    "member_log_channel_id",
    "member_logs_channel_id",
    "join_log_channel_id",
    "join_exit_log_channel_id",
    "joinlog_channel_id",
    "joinleave_channel_id",
    "leave_log_channel_id",
    "welcome_leave_channel_id",
    "welcome_exit_channel_id",
    "welcome_exit_log_channel_id",
    "leave_channel_id",
)

STAFF_AUDIT_KEYS = (
    "staff_join_audit_channel_id",
    "member_audit_log_channel_id",
    "staff_log_channel_id",
    "staff_logs_channel_id",
    "modlog_channel_id",
    "mod_log_channel_id",
    "audit_log_channel_id",
)


def _log(message: str) -> None:
    try:
        print(f"👋 member_lifecycle_router_guard {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip().strip("<#@!&>")
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
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
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, dict) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, dict) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


async def _load_config(guild_id: int) -> Any:
    try:
        from stoney_verify.guild_config import get_guild_config

        return await get_guild_config(int(guild_id), refresh=True)
    except Exception:
        return None


def _resolve_channel(
    guild: discord.Guild,
    cfg: Any,
    keys: tuple[str, ...],
) -> Optional[discord.TextChannel]:
    for key in keys:
        cid = _safe_int(_cfg_value(cfg, key, None), 0)
        if cid <= 0:
            continue
        channel = guild.get_channel(cid)
        if isinstance(channel, discord.TextChannel):
            return channel
    return None


def _bot_can_read_invites(guild: discord.Guild) -> bool:
    try:
        me = guild.me
        if not isinstance(me, discord.Member):
            return False
        perms = me.guild_permissions
        return bool(
            getattr(perms, "manage_guild", False)
            or getattr(perms, "administrator", False)
        )
    except Exception:
        return False


async def _join_listener(member: discord.Member) -> None:
    try:
        delivery = await send_live_welcome_card(member)
        _log(
            f"canonical join result guild={member.guild.id} member={member.id} "
            f"sent={delivery.sent} code={delivery.code} "
            f"channel={delivery.channel_id or '-'} image={delivery.used_image}"
        )
    except Exception as exc:
        _log(
            f"canonical join failed guild={getattr(member.guild, 'id', 'unknown')} "
            f"member={getattr(member, 'id', 'unknown')}: "
            f"{type(exc).__name__}: {exc}"
        )


async def _leave_listener(member: discord.Member) -> None:
    try:
        delivery = await send_live_exit_card(member)
        _log(
            f"canonical exit result guild={member.guild.id} member={member.id} "
            f"sent={delivery.sent} code={delivery.code} "
            f"channel={delivery.channel_id or '-'} image={delivery.used_image}"
        )
    except Exception as exc:
        _log(
            f"canonical exit failed guild={getattr(member.guild, 'id', 'unknown')} "
            f"member={getattr(member, 'id', 'unknown')}: "
            f"{type(exc).__name__}: {exc}"
        )


async def _ready_listener() -> None:
    try:
        if bot is None:
            return
        intents = getattr(bot, "intents", None)
        if not bool(getattr(intents, "members", False)):
            _log("members intent is disabled in code; join/leave events will not fire")
        for guild in list(getattr(bot, "guilds", []) or []):
            try:
                cfg = await _load_config(int(guild.id))
                join_channel, join_reason = resolve_join_card_channel(guild, cfg)
                exit_channel, exit_reason = resolve_exit_card_channel(guild, cfg)
                staff_channel = _resolve_channel(guild, cfg, STAFF_AUDIT_KEYS)
                _log(
                    "member lifecycle routes ready "
                    f"guild={guild.id} "
                    f"join={getattr(join_channel, 'id', None) or '-'} "
                    f"join_reason={join_reason!r} "
                    f"exit={getattr(exit_channel, 'id', None) or '-'} "
                    f"exit_reason={exit_reason!r} "
                    f"staff={getattr(staff_channel, 'id', None) or '-'}"
                )
            except Exception:
                pass
    except Exception as exc:
        _log(f"ready warm failed: {type(exc).__name__}: {exc}")


def _remove_old_welcome_listeners() -> None:
    if bot is None:
        return
    try:
        extra = getattr(bot, "extra_events", {}) or {}
        for event_name in ("on_member_join", "on_member_remove"):
            listeners = list(extra.get(event_name) or [])
            kept = []
            removed = 0
            for fn in listeners:
                module = _safe_str(getattr(fn, "__module__", ""))
                name = _safe_str(getattr(fn, "__name__", ""))
                if "welcome_member_events_guard" in module:
                    removed += 1
                    continue
                if (
                    "member_lifecycle_verify_runtime_hardening" in module
                    and name in {"_patched_join_listener", "_patched_leave_listener"}
                ):
                    removed += 1
                    continue
                kept.append(fn)
            extra[event_name] = kept
            if removed:
                _log(
                    "removed old/conflicting member lifecycle listeners "
                    f"event={event_name} count={removed}"
                )
    except Exception as exc:
        _log(f"old listener removal failed: {type(exc).__name__}: {exc}")


def _install_listener(fn: Any, event_name: str) -> None:
    if bot is None:
        return
    existing = list(
        (getattr(bot, "extra_events", {}) or {}).get(event_name) or []
    )
    if any(
        getattr(x, "__name__", "") == getattr(fn, "__name__", "")
        and getattr(x, "__module__", "") == __name__
        for x in existing
    ):
        return
    bot.add_listener(fn, event_name)


async def _member_logs_command(
    interaction: discord.Interaction,
    public_welcome: Optional[discord.TextChannel] = None,
    join_leave_log: Optional[discord.TextChannel] = None,
    staff_audit_log: Optional[discord.TextChannel] = None,
) -> None:
    try:
        if interaction.guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )
        perms = getattr(interaction.user, "guild_permissions", None)
        if not (
            getattr(perms, "administrator", False)
            or getattr(perms, "manage_guild", False)
            or getattr(perms, "manage_channels", False)
        ):
            return await interaction.response.send_message(
                "❌ You need **Manage Server** or **Manage Channels** to configure member logs.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        guild = interaction.guild
        payload: dict[str, Any] = {}
        if public_welcome is not None:
            payload["public_welcome_channel_id"] = str(public_welcome.id)
            payload["welcome_channel_id"] = str(public_welcome.id)
        if join_leave_log is not None:
            for key in JOIN_LEAVE_KEYS:
                payload[key] = str(join_leave_log.id)
            # Member Logs remains a compatibility entry point, but Exit Card
            # Studio is the canonical runtime owner after this write.
            payload["exit_card_channel_id"] = str(join_leave_log.id)
            payload["exit_card_enabled"] = True
        if staff_audit_log is not None:
            payload["staff_join_audit_channel_id"] = str(staff_audit_log.id)
            payload["member_audit_log_channel_id"] = str(staff_audit_log.id)
            payload["modlog_channel_id"] = str(staff_audit_log.id)
        if payload:
            from stoney_verify.commands_ext.public_setup_config_writer import (
                upsert_guild_config,
            )
            from stoney_verify.guild_config import invalidate_guild_config

            payload.update(
                {
                    "__config_write_mode": "setup_builder",
                    "__config_write_source": "/dank member-logs",
                    "configured_by_id": str(interaction.user.id),
                    "configured_by_name": str(interaction.user),
                    "configured_at": discord.utils.utcnow().isoformat(),
                }
            )
            await upsert_guild_config(int(guild.id), payload)
            invalidate_guild_config(int(guild.id))

        cfg = await _load_config(int(guild.id))
        join_channel, join_reason = resolve_join_card_channel(guild, cfg)
        exit_channel, exit_reason = resolve_exit_card_channel(guild, cfg)
        staff_channel = _resolve_channel(guild, cfg, STAFF_AUDIT_KEYS)

        embed = discord.Embed(
            title="👋 Member Lifecycle Routing",
            description=(
                "Welcome Card Studio owns the live join card. Exit Card Studio "
                "owns the live leave card. Staff audit remains a separate route."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Live join card",
            value=(
                join_channel.mention
                if isinstance(join_channel, discord.TextChannel)
                else f"`Unavailable: {join_reason}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Live exit card",
            value=(
                exit_channel.mention
                if isinstance(exit_channel, discord.TextChannel)
                else f"`Unavailable: {exit_reason}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Staff audit / invite source",
            value=(
                staff_channel.mention
                if staff_channel
                else "`Not set — detailed audit will not be posted publicly`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Legacy public lifecycle cards",
            value=(
                "Retired. Neither `dank_shield:join_leave_event:v3` nor "
                "`dank_shield:leave_event:v4` is emitted by the public router."
            ),
            inline=False,
        )
        invite_status = (
            "Can read invites ✅"
            if _bot_can_read_invites(guild)
            else "Missing Manage Server permission ⚠️ invite source may stay unknown"
        )
        embed.add_field(name="Invite tracking", value=invite_status, inline=False)
        if payload:
            embed.add_field(
                name="Saved",
                value="Updated canonical member lifecycle routes.",
                inline=False,
            )
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as exc:
        try:
            await interaction.response.send_message(
                f"❌ Could not update member logs: `{type(exc).__name__}: {exc}`",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass


def _install_command() -> bool:
    if dank_group is None:
        _log("dank_group unavailable; /dank member-logs not installed")
        return False
    try:
        existing = {
            getattr(command, "name", "")
            for command in getattr(dank_group, "commands", []) or []
        }
        if "member-logs" in existing:
            return True
        decorated = app_commands.describe(
            public_welcome=(
                "Static welcome/start-here channel and fallback live join-card channel."
            ),
            join_leave_log="Compatibility route for the canonical Exit Card Studio.",
            staff_audit_log="Staff-only channel for detailed join audit and invite source.",
        )(_member_logs_command)
        try:
            decorated = app_commands.default_permissions(manage_guild=True)(decorated)
        except Exception:
            pass
        dank_group.command(
            name="member-logs",
            description="Configure join-card, exit-card, and staff-audit routes.",
        )(decorated)
        return True
    except Exception as exc:
        _log(f"command install failed: {type(exc).__name__}: {exc}")
        return False


def install() -> bool:
    global _INSTALLED
    _install_command()
    if _INSTALLED:
        return True
    if bot is None:
        _log("bot unavailable; listeners not installed")
        return False
    try:
        _remove_old_welcome_listeners()
        _install_listener(_join_listener, "on_member_join")
        _install_listener(_leave_listener, "on_member_remove")
        _install_listener(_ready_listener, "on_ready")
        _INSTALLED = True
        _log(
            "active; Welcome Card Studio owns joins, Exit Card Studio owns exits, "
            "and legacy public lifecycle cards are retired"
        )
        return True
    except Exception as exc:
        _log(f"install failed: {type(exc).__name__}: {exc}")
        return False


install()

__all__ = ["install"]
