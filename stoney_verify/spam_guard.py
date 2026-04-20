from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

import discord
from discord import app_commands

from .globals import *  # noqa: F401,F403

# ============================================================
# Toggleable hacked-account / invite spam blocker
# ------------------------------------------------------------
# Goals:
# - toggle on/off with slash command
# - catch hacked-account invite spam bursts fast
# - delete recent spam messages
# - timeout the user when possible
# - post a staff/modlog alert
# - work even if the persistence table does not exist yet
#   (falls back to runtime-only settings)
# ============================================================

GUILD_SECURITY_SETTINGS_TABLE = "guild_security_settings"

INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com/invite|discord\.gg)/[A-Za-z0-9-]+",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

_SETTINGS_TABLE_AVAILABLE: Optional[bool] = None
_SPAM_GUARD_COMMANDS_REGISTERED = False

# runtime fallback / cache
_RUNTIME_SPAM_SETTINGS: Dict[int, Dict[str, Any]] = {}

# guild_id, user_id -> state
_MESSAGE_WINDOWS: Dict[Tuple[int, int], Dict[str, Any]] = {}

# keyed locks
_LOCKS: Dict[str, asyncio.Lock] = {}


# ============================================================
# Small helpers
# ============================================================

def _lock(key: str) -> asyncio.Lock:
    clean = str(key or "").strip() or "default"
    lock = _LOCKS.get(clean)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[clean] = lock
    return lock


def _now_utc() -> datetime:
    try:
        return now_utc()
    except Exception:
        return datetime.now(timezone.utc)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _debug(msg: str) -> None:
    try:
        print(f"🛡️ spam_guard {msg}")
    except Exception:
        pass


def _is_table_missing_error(exc: Exception) -> bool:
    text = repr(exc or "").lower()
    return (
        GUILD_SECURITY_SETTINGS_TABLE in text
        and (
            "does not exist" in text
            or "relation" in text
            or "schema cache" in text
            or "pgrst204" in text
            or "42p01" in text
        )
    )


def _sb():
    try:
        return get_supabase()
    except Exception:
        return None


def _is_staffish(member: Optional[discord.Member]) -> bool:
    try:
        if not isinstance(member, discord.Member):
            return False
        if member.guild_permissions.administrator:
            return True
        if member.guild_permissions.manage_guild:
            return True
        if member.guild_permissions.manage_channels:
            return True
        if member.guild_permissions.manage_messages:
            return True
        staff_role_id = int(STAFF_ROLE_ID or 0)
        if staff_role_id > 0:
            return any(int(r.id) == staff_role_id for r in (member.roles or []))
    except Exception:
        pass
    return False


async def _reply_ephemeral(interaction: discord.Interaction, content: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)
    except Exception:
        try:
            await interaction.followup.send(content, ephemeral=True)
        except Exception:
            pass


def _default_settings(guild_id: int) -> Dict[str, Any]:
    return {
        "guild_id": str(guild_id),
        "enabled": False,
        "window_seconds": 12,
        "message_threshold": 5,
        "duplicate_threshold": 3,
        "invite_threshold": 2,
        "multi_invite_immediate": 2,
        "timeout_minutes": 30,
        "delete_history": 8,
        "cooldown_seconds": 20,
    }


def _normalize_settings(guild_id: int, row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = _default_settings(guild_id)
    if not isinstance(row, dict):
        return base

    base["enabled"] = _safe_bool(
        row.get("spam_blocker_enabled", row.get("enabled")),
        base["enabled"],
    )
    base["window_seconds"] = max(
        5,
        min(60, _safe_int(row.get("spam_window_seconds", row.get("window_seconds")), base["window_seconds"])),
    )
    base["message_threshold"] = max(
        3,
        min(12, _safe_int(row.get("spam_message_threshold", row.get("message_threshold")), base["message_threshold"])),
    )
    base["duplicate_threshold"] = max(
        2,
        min(8, _safe_int(row.get("spam_duplicate_threshold", row.get("duplicate_threshold")), base["duplicate_threshold"])),
    )
    base["invite_threshold"] = max(
        1,
        min(8, _safe_int(row.get("spam_invite_threshold", row.get("invite_threshold")), base["invite_threshold"])),
    )
    base["multi_invite_immediate"] = max(
        2,
        min(6, _safe_int(row.get("spam_multi_invite_immediate", row.get("multi_invite_immediate")), base["multi_invite_immediate"])),
    )
    base["timeout_minutes"] = max(
        1,
        min(1440, _safe_int(row.get("spam_timeout_minutes", row.get("timeout_minutes")), base["timeout_minutes"])),
    )
    base["delete_history"] = max(
        1,
        min(20, _safe_int(row.get("spam_delete_history", row.get("delete_history")), base["delete_history"])),
    )
    base["cooldown_seconds"] = max(
        5,
        min(120, _safe_int(row.get("spam_cooldown_seconds", row.get("cooldown_seconds")), base["cooldown_seconds"])),
    )
    return base


def _normalize_message_content(content: str) -> str:
    text = _safe_str(content).lower()
    if not text:
        return ""
    text = INVITE_RE.sub("<invite>", text)
    text = URL_RE.sub("<url>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:240]


def _cleanup_old_state(window: Dict[str, Any], *, now_mono: float, max_window_seconds: int) -> None:
    cutoff = float(now_mono) - float(max_window_seconds)

    for key in ("timestamps", "invite_timestamps"):
        dq = window.get(key)
        if not isinstance(dq, deque):
            dq = deque()
            window[key] = dq
        while dq and float(dq[0]) < cutoff:
            dq.popleft()

    messages = window.get("messages")
    if not isinstance(messages, deque):
        messages = deque(maxlen=20)
        window["messages"] = messages

    while messages and float(messages[0].get("ts", 0.0) or 0.0) < cutoff:
        messages.popleft()


def _state_for_user(guild_id: int, user_id: int) -> Dict[str, Any]:
    key = (int(guild_id), int(user_id))
    state = _MESSAGE_WINDOWS.get(key)
    if state is None:
        state = {
            "timestamps": deque(maxlen=40),
            "invite_timestamps": deque(maxlen=40),
            "messages": deque(maxlen=20),
            "last_action_at": 0.0,
        }
        _MESSAGE_WINDOWS[key] = state
    return state


async def _post_modlog_embed(guild: discord.Guild, embed: discord.Embed) -> None:
    try:
        from .modlog import _post_modlog
        await _post_modlog(guild, embed)
        return
    except Exception:
        pass

    try:
        if MODLOG_CHANNEL_ID:
            ch = guild.get_channel(int(MODLOG_CHANNEL_ID))
            if isinstance(ch, discord.TextChannel):
                await ch.send(embed=embed)
    except Exception:
        pass


def _runtime_settings_get(guild_id: int) -> Dict[str, Any]:
    cached = _RUNTIME_SPAM_SETTINGS.get(int(guild_id))
    return _normalize_settings(int(guild_id), cached)


# ============================================================
# Settings persistence
# ============================================================

def _fetch_settings_sync(guild_id: int) -> Optional[Dict[str, Any]]:
    global _SETTINGS_TABLE_AVAILABLE

    sb = _sb()
    if sb is None:
        return None

    try:
        res = (
            sb.table(GUILD_SECURITY_SETTINGS_TABLE)
            .select("*")
            .eq("guild_id", str(guild_id))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        _SETTINGS_TABLE_AVAILABLE = True
        if rows:
            return dict(rows[0])
        return None
    except Exception as e:
        if _is_table_missing_error(e):
            _SETTINGS_TABLE_AVAILABLE = False
            return None
        raise


def _upsert_settings_sync(payload: Dict[str, Any]) -> bool:
    global _SETTINGS_TABLE_AVAILABLE

    sb = _sb()
    if sb is None:
        return False

    try:
        sb.table(GUILD_SECURITY_SETTINGS_TABLE).upsert(
            payload,
            on_conflict="guild_id",
        ).execute()
        _SETTINGS_TABLE_AVAILABLE = True
        return True
    except Exception as e:
        if _is_table_missing_error(e):
            _SETTINGS_TABLE_AVAILABLE = False
            return False
        raise


async def get_spam_blocker_settings(guild_id: int) -> Dict[str, Any]:
    gid = int(guild_id)

    # runtime override wins
    runtime = _RUNTIME_SPAM_SETTINGS.get(gid)
    if isinstance(runtime, dict):
        return _normalize_settings(gid, runtime)

    if _SETTINGS_TABLE_AVAILABLE is False:
        return _default_settings(gid)

    try:
        row = await asyncio.to_thread(_fetch_settings_sync, gid)
        if isinstance(row, dict):
            normalized = _normalize_settings(gid, row)
            _RUNTIME_SPAM_SETTINGS[gid] = dict(normalized)
            return normalized
    except Exception as e:
        _debug(f"settings fetch failed guild={gid} error={repr(e)}")

    return _default_settings(gid)


async def update_spam_blocker_settings(
    guild_id: int,
    *,
    enabled: Optional[bool] = None,
    timeout_minutes: Optional[int] = None,
    updated_by: Optional[int] = None,
    updated_by_name: Optional[str] = None,
) -> Tuple[Dict[str, Any], bool]:
    gid = int(guild_id)
    current = await get_spam_blocker_settings(gid)

    if enabled is not None:
        current["enabled"] = bool(enabled)
    if timeout_minutes is not None:
        current["timeout_minutes"] = max(1, min(1440, int(timeout_minutes)))

    _RUNTIME_SPAM_SETTINGS[gid] = dict(current)

    payload = {
        "guild_id": str(gid),
        "spam_blocker_enabled": bool(current["enabled"]),
        "spam_window_seconds": int(current["window_seconds"]),
        "spam_message_threshold": int(current["message_threshold"]),
        "spam_duplicate_threshold": int(current["duplicate_threshold"]),
        "spam_invite_threshold": int(current["invite_threshold"]),
        "spam_multi_invite_immediate": int(current["multi_invite_immediate"]),
        "spam_timeout_minutes": int(current["timeout_minutes"]),
        "spam_delete_history": int(current["delete_history"]),
        "spam_cooldown_seconds": int(current["cooldown_seconds"]),
        "updated_at": _now_utc().isoformat(),
        "updated_by": str(updated_by) if updated_by else None,
        "updated_by_name": _safe_str(updated_by_name) or None,
    }

    persisted = False
    if _SETTINGS_TABLE_AVAILABLE is not False:
        try:
            persisted = await asyncio.to_thread(_upsert_settings_sync, payload)
        except Exception as e:
            _debug(f"settings upsert failed guild={gid} error={repr(e)}")
            persisted = False

    return current, persisted


# ============================================================
# Spam detection / enforcement
# ============================================================

async def _delete_recent_messages(
    *,
    guild: discord.Guild,
    refs: List[Dict[str, Any]],
    reason: str,
) -> int:
    deleted = 0
    seen: Set[Tuple[int, int]] = set()

    for row in sorted(refs, key=lambda x: float(x.get("ts", 0.0) or 0.0), reverse=True):
        channel_id = _safe_int(row.get("channel_id"), 0)
        message_id = _safe_int(row.get("message_id"), 0)
        if channel_id <= 0 or message_id <= 0:
            continue

        key = (channel_id, message_id)
        if key in seen:
            continue
        seen.add(key)

        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None

        if not isinstance(channel, discord.TextChannel):
            continue

        try:
            msg = await channel.fetch_message(message_id)
        except Exception:
            continue

        try:
            await msg.delete(reason=reason)
            deleted += 1
        except Exception:
            continue

    return deleted


async def _apply_spam_timeout(
    member: discord.Member,
    *,
    timeout_minutes: int,
    reason: str,
) -> str:
    try:
        guild = member.guild
        me = guild.me
        if me is None:
            return "no-action"

        if me.top_role <= member.top_role and not me.guild_permissions.administrator:
            return "no-action"

        if me.guild_permissions.moderate_members:
            until = _now_utc() + timedelta(minutes=max(1, int(timeout_minutes)))
            await member.timeout(until, reason=reason)
            return f"timeout:{int(timeout_minutes)}m"

    except Exception:
        pass

    return "no-action"


async def _log_spam_action(
    *,
    guild: discord.Guild,
    member: discord.Member,
    reasons: List[str],
    deleted_count: int,
    action_taken: str,
    recent_count: int,
    invite_recent_count: int,
    duplicate_count: int,
) -> None:
    try:
        embed = discord.Embed(
            title="🛡️ Spam Blocker Triggered",
            description="Possible hacked-account / invite spam burst was blocked.",
            color=discord.Color.red(),
            timestamp=_now_utc(),
        )
        embed.add_field(
            name="User",
            value=f"{member.mention} (`{member}` • `{member.id}`)",
            inline=False,
        )
        embed.add_field(
            name="Action",
            value=f"`{action_taken}`",
            inline=True,
        )
        embed.add_field(
            name="Deleted Messages",
            value=f"`{deleted_count}`",
            inline=True,
        )
        embed.add_field(
            name="Recent Burst",
            value=(
                f"messages=`{recent_count}` • "
                f"invite_msgs=`{invite_recent_count}` • "
                f"duplicates=`{duplicate_count}`"
            ),
            inline=False,
        )
        if reasons:
            embed.add_field(
                name="Trigger Reasons",
                value="\n".join(f"• {r}" for r in reasons[:6]),
                inline=False,
            )
        await _post_modlog_embed(guild, embed)
    except Exception:
        pass


async def handle_incoming_spam_message(message: discord.Message) -> bool:
    try:
        if message.guild is None:
            return False
        if not isinstance(message.author, discord.Member):
            return False
        if getattr(message.author, "bot", False):
            return False
        if not isinstance(message.channel, discord.TextChannel):
            return False

        member = message.author
        guild = message.guild

        if _is_staffish(member):
            return False

        settings = await get_spam_blocker_settings(guild.id)
        if not bool(settings.get("enabled")):
            return False

        key = f"spam:{guild.id}:{member.id}"
        async with _lock(key):
            state = _state_for_user(guild.id, member.id)
            now_mono = time.monotonic()

            _cleanup_old_state(
                state,
                now_mono=now_mono,
                max_window_seconds=max(15, int(settings["window_seconds"]) * 3),
            )

            content_norm = _normalize_message_content(message.content or "")
            invite_matches = INVITE_RE.findall(message.content or "")
            has_invite = bool(invite_matches)

            timestamps: Deque[float] = state["timestamps"]
            invite_timestamps: Deque[float] = state["invite_timestamps"]
            messages: Deque[Dict[str, Any]] = state["messages"]

            timestamps.append(now_mono)
            if has_invite:
                invite_timestamps.append(now_mono)

            messages.append(
                {
                    "ts": now_mono,
                    "channel_id": int(message.channel.id),
                    "message_id": int(message.id),
                    "norm": content_norm,
                    "invite_count": len(invite_matches),
                }
            )

            # prune to current window for counts
            recent_cutoff = now_mono - float(settings["window_seconds"])
            recent_messages = [
                row for row in list(messages)
                if float(row.get("ts", 0.0) or 0.0) >= recent_cutoff
            ]
            recent_count = len([
                ts for ts in list(timestamps)
                if float(ts) >= recent_cutoff
            ])
            invite_recent_count = len([
                ts for ts in list(invite_timestamps)
                if float(ts) >= recent_cutoff
            ])
            duplicate_count = 0
            if content_norm:
                duplicate_count = sum(
                    1 for row in recent_messages
                    if str(row.get("norm") or "") == content_norm
                )

            reasons: List[str] = []
            should_fire = False

            if len(invite_matches) >= int(settings["multi_invite_immediate"]):
                should_fire = True
                reasons.append(
                    f"single message contained `{len(invite_matches)}` invite links"
                )

            if invite_recent_count >= int(settings["invite_threshold"]):
                should_fire = True
                reasons.append(
                    f"`{invite_recent_count}` invite-link messages inside `{int(settings['window_seconds'])}s`"
                )

            if duplicate_count >= int(settings["duplicate_threshold"]) and recent_count >= 3:
                should_fire = True
                reasons.append(
                    f"same message repeated `{duplicate_count}` times inside `{int(settings['window_seconds'])}s`"
                )

            if recent_count >= int(settings["message_threshold"]) and has_invite:
                should_fire = True
                reasons.append(
                    f"`{recent_count}` total messages in `{int(settings['window_seconds'])}s` and current message contained an invite"
                )

            last_action_at = float(state.get("last_action_at", 0.0) or 0.0)
            if should_fire and (now_mono - last_action_at) < float(settings["cooldown_seconds"]):
                return True

            if not should_fire:
                return False

            state["last_action_at"] = now_mono

            refs_to_delete = recent_messages[-int(settings["delete_history"]):]
            deleted_count = await _delete_recent_messages(
                guild=guild,
                refs=refs_to_delete,
                reason="Spam blocker invite-spam cleanup",
            )

            action_taken = await _apply_spam_timeout(
                member,
                timeout_minutes=int(settings["timeout_minutes"]),
                reason="Spam blocker: probable hacked-account invite spam burst",
            )

            await _log_spam_action(
                guild=guild,
                member=member,
                reasons=reasons,
                deleted_count=deleted_count,
                action_taken=action_taken,
                recent_count=recent_count,
                invite_recent_count=invite_recent_count,
                duplicate_count=duplicate_count,
            )

            try:
                RUNTIME_STATS["spam_blocks"] = int(RUNTIME_STATS.get("spam_blocks", 0) or 0) + 1
            except Exception:
                pass

            return True

    except Exception as e:
        _debug(f"message handler failed error={repr(e)}")
        return False


# ============================================================
# Slash commands
# ============================================================

def _register_spam_guard_commands() -> None:
    global _SPAM_GUARD_COMMANDS_REGISTERED

    if _SPAM_GUARD_COMMANDS_REGISTERED:
        return

    if bot.tree.get_command("spam_blocker") is None:
        @bot.tree.command(
            name="spam_blocker",
            description="(Staff) Turn the hacked-account invite spam blocker on or off.",
        )
        @app_commands.guild_only()
        @app_commands.describe(
            enabled="Turn the spam blocker on or off",
            timeout_minutes="Timeout to apply when the blocker triggers (default 30)",
        )
        async def spam_blocker(
            interaction: discord.Interaction,
            enabled: bool,
            timeout_minutes: Optional[int] = None,
        ):
            member = interaction.user if isinstance(interaction.user, discord.Member) else None
            guild = interaction.guild

            if not isinstance(member, discord.Member) or guild is None:
                return await _reply_ephemeral(interaction, "This command must be used in the server.")

            if not _is_staffish(member):
                return await _reply_ephemeral(interaction, "You do not have permission to use this command.")

            settings, persisted = await update_spam_blocker_settings(
                guild.id,
                enabled=bool(enabled),
                timeout_minutes=int(timeout_minutes) if timeout_minutes is not None else None,
                updated_by=int(member.id),
                updated_by_name=str(getattr(member, "display_name", member)),
            )

            state_text = "enabled" if settings["enabled"] else "disabled"
            persistence_text = "saved to DB" if persisted else "runtime only"

            await _reply_ephemeral(
                interaction,
                (
                    f"🛡️ Spam blocker **{state_text}**.\n"
                    f"Timeout: `{int(settings['timeout_minutes'])}m`\n"
                    f"Window: `{int(settings['window_seconds'])}s`\n"
                    f"Invite threshold: `{int(settings['invite_threshold'])}` invite-messages\n"
                    f"Message threshold: `{int(settings['message_threshold'])}` total messages\n"
                    f"Persistence: `{persistence_text}`"
                ),
            )

    if bot.tree.get_command("spam_blocker_status") is None:
        @bot.tree.command(
            name="spam_blocker_status",
            description="(Staff) Show the current spam blocker status for this server.",
        )
        @app_commands.guild_only()
        async def spam_blocker_status(interaction: discord.Interaction):
            member = interaction.user if isinstance(interaction.user, discord.Member) else None
            guild = interaction.guild

            if not isinstance(member, discord.Member) or guild is None:
                return await _reply_ephemeral(interaction, "This command must be used in the server.")

            if not _is_staffish(member):
                return await _reply_ephemeral(interaction, "You do not have permission to use this command.")

            settings = await get_spam_blocker_settings(guild.id)

            await _reply_ephemeral(
                interaction,
                (
                    f"🛡️ Spam blocker: `{'enabled' if settings['enabled'] else 'disabled'}`\n"
                    f"Window: `{int(settings['window_seconds'])}s`\n"
                    f"Message threshold: `{int(settings['message_threshold'])}`\n"
                    f"Duplicate threshold: `{int(settings['duplicate_threshold'])}`\n"
                    f"Invite threshold: `{int(settings['invite_threshold'])}`\n"
                    f"Immediate multi-invite trigger: `{int(settings['multi_invite_immediate'])}`\n"
                    f"Timeout: `{int(settings['timeout_minutes'])}m`\n"
                    f"Delete history: `{int(settings['delete_history'])}`"
                ),
            )

    _SPAM_GUARD_COMMANDS_REGISTERED = True


# ============================================================
# Listener registration
# ============================================================

@bot.listen("on_message")
async def _spam_guard_on_message(message: discord.Message):
    await handle_incoming_spam_message(message)


_register_spam_guard_commands()


__all__ = [
    "get_spam_blocker_settings",
    "update_spam_blocker_settings",
    "handle_incoming_spam_message",
]
