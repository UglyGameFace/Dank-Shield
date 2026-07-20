from __future__ import annotations

"""Live Discord channel counters for auditable Dank Shield server statistics.

The display intentionally uses only durable, auditable actions or authoritative
current state that Dank Shield can prove. It does not invent estimates such as
"users protected" or "raids prevented".

The public display is opt-in per guild. When enabled, Dank Shield creates a visible
category containing locked voice channels, matching the common Discord server-stats
pattern. Members can see the counters but cannot connect to them.
"""

import asyncio
import time
from typing import Any, Dict, Mapping, Optional, Tuple

import discord
from discord.ext import tasks

from .globals import bot, get_supabase
from .guild_config import get_guild_config, upsert_guild_config

SECURITY_STATS_CATEGORY_NAME = "🛡️ DANK SHIELD STATS"
SECURITY_STATS_ENABLED_KEY = "security_stats_display_enabled"
SECURITY_STATS_CATEGORY_ID_KEY = "security_stats_category_id"
SECURITY_STATS_CHANNEL_IDS_KEY = "security_stats_channel_ids"
SECURITY_STATS_COUNTS_KEY = "security_stats_counts"

SECURITY_STATS_REFRESH_MIN_SECONDS = 9 * 60

DEFAULT_SECURITY_STATS: Dict[str, int] = {
    "spam_blocked": 0,
    "invites_blocked": 0,
    "timeouts_issued": 0,
    "quarantines": 0,
}

DEFAULT_TICKET_STATUS_COUNTS: Dict[str, int] = {
    "open_tickets": 0,
    "claimed_tickets": 0,
    "closed_tickets": 0,
}

# key -> static visible prefix. Prefixes are also used to recover channels if a
# saved channel ID is stale but the owned stats category still exists.
STAT_CHANNEL_PREFIXES: Dict[str, str] = {
    "status": "🛡️ SpamGuard:",
    "members": "👥 Members:",
    "spam_blocked": "🚫 Spam Blocked:",
    "invites_blocked": "🔗 Invites Blocked:",
    "timeouts_issued": "⏱️ Timeouts Issued:",
    "quarantines": "🔒 Quarantined:",
    "open_tickets": "🎫 Open Tickets:",
    "claimed_tickets": "🙋 Claimed Tickets:",
    "closed_tickets": "✅ Closed Tickets:",
}

_STATS_LOCKS: Dict[int, asyncio.Lock] = {}
_DISPLAY_LOCKS: Dict[int, asyncio.Lock] = {}
_LAST_REFRESH_AT: Dict[int, float] = {}


def _lock_for(store: Dict[int, asyncio.Lock], guild_id: int) -> asyncio.Lock:
    gid = int(guild_id)
    found = store.get(gid)
    if found is None:
        found = asyncio.Lock()
        store[gid] = found
    return found


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return bool(default)


def _mapping(value: Any) -> Dict[str, Any]:
    try:
        if isinstance(value, Mapping):
            return dict(value)
    except Exception:
        pass
    return {}


def normalize_security_stats(value: Any) -> Dict[str, int]:
    raw = _mapping(value)
    normalized = dict(DEFAULT_SECURITY_STATS)
    for key in normalized:
        normalized[key] = max(0, _safe_int(raw.get(key), 0))
    return normalized


def format_security_stat_count(value: Any) -> str:
    """Compact a non-negative counter while keeping small values exact."""
    number = max(0, _safe_int(value, 0))
    if number < 1_000:
        return str(number)

    units = ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K"))
    for divisor, suffix in units:
        if number < divisor:
            continue
        scaled = number / divisor
        if scaled < 10:
            text = f"{scaled:.2f}"
        elif scaled < 100:
            text = f"{scaled:.1f}"
        else:
            text = f"{scaled:.0f}"
        return f"{text.rstrip('0').rstrip('.')}{suffix}"
    return str(number)


def _format_live_count(value: Optional[int]) -> str:
    if value is None:
        return "N/A"
    return format_security_stat_count(value)


def _guild_member_count(guild: discord.Guild) -> Optional[int]:
    """Return Discord's guild member total without trusting a partial member cache."""
    try:
        raw = getattr(guild, "member_count", None)
        if raw is not None and not isinstance(raw, bool):
            count = int(raw)
            if count >= 0:
                return count
    except Exception:
        pass

    try:
        if bool(getattr(guild, "chunked", False)):
            return max(0, len(list(getattr(guild, "members", []) or [])))
    except Exception:
        pass
    return None


def _normalize_ticket_status_counts(value: Any) -> Optional[Dict[str, int]]:
    if value is None:
        return None
    raw = _mapping(value)
    return {
        key: max(0, _safe_int(raw.get(key), 0))
        for key in DEFAULT_TICKET_STATUS_COUNTS
    }


def _query_ticket_status_counts_sync(guild_id: int) -> Optional[Dict[str, int]]:
    """Read current ticket lifecycle totals from the canonical tickets table."""
    sb = get_supabase()
    if sb is None:
        return None

    response = (
        sb.table("tickets")
        .select("status,claimed_by,assigned_to")
        .eq("guild_id", str(int(guild_id)))
        .execute()
    )
    rows = getattr(response, "data", None)
    if rows is None:
        return None

    counts = dict(DEFAULT_TICKET_STATUS_COUNTS)
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status") or "").strip().lower()
        if status in {"active", "reopened"}:
            status = "open"
        if status == "open":
            claimed_by = str(row.get("claimed_by") or "").strip()
            assigned_to = str(row.get("assigned_to") or "").strip()
            if claimed_by or assigned_to:
                status = "claimed"

        if status == "open":
            counts["open_tickets"] += 1
        elif status == "claimed":
            counts["claimed_tickets"] += 1
        elif status == "closed":
            counts["closed_tickets"] += 1
    return counts


async def _ticket_status_counts(guild_id: int) -> Optional[Dict[str, int]]:
    try:
        counts = await asyncio.to_thread(_query_ticket_status_counts_sync, int(guild_id))
        return _normalize_ticket_status_counts(counts)
    except Exception:
        return None


async def _spam_guard_enabled(guild_id: int) -> bool:
    try:
        from .spam_guard import get_spam_settings

        spam_settings = await get_spam_settings(int(guild_id))
        return bool(spam_settings.get("enabled"))
    except Exception:
        return False


def _display_names(
    *,
    spam_guard_enabled: bool,
    counts: Mapping[str, int],
    member_count: Optional[int] = None,
    ticket_counts: Optional[Mapping[str, int]] = None,
) -> Dict[str, str]:
    normalized = normalize_security_stats(counts)
    tickets = _normalize_ticket_status_counts(ticket_counts)
    return {
        "status": f"🛡️ SpamGuard: {'ONLINE' if spam_guard_enabled else 'OFFLINE'}",
        "members": f"👥 Members: {_format_live_count(member_count)}",
        "spam_blocked": f"🚫 Spam Blocked: {format_security_stat_count(normalized['spam_blocked'])}",
        "invites_blocked": f"🔗 Invites Blocked: {format_security_stat_count(normalized['invites_blocked'])}",
        "timeouts_issued": f"⏱️ Timeouts Issued: {format_security_stat_count(normalized['timeouts_issued'])}",
        "quarantines": f"🔒 Quarantined: {format_security_stat_count(normalized['quarantines'])}",
        "open_tickets": (
            f"🎫 Open Tickets: {_format_live_count(None if tickets is None else tickets['open_tickets'])}"
        ),
        "claimed_tickets": (
            f"🙋 Claimed Tickets: {_format_live_count(None if tickets is None else tickets['claimed_tickets'])}"
        ),
        "closed_tickets": (
            f"✅ Closed Tickets: {_format_live_count(None if tickets is None else tickets['closed_tickets'])}"
        ),
    }


async def _display_names_for_guild(
    guild: discord.Guild,
    *,
    counts: Mapping[str, int],
) -> Dict[str, str]:
    gid = int(guild.id)
    spam_enabled, ticket_counts = await asyncio.gather(
        _spam_guard_enabled(gid),
        _ticket_status_counts(gid),
    )
    return _display_names(
        spam_guard_enabled=bool(spam_enabled),
        counts=counts,
        member_count=_guild_member_count(guild),
        ticket_counts=ticket_counts,
    )


def _saved_channel_ids(cfg: Any) -> Dict[str, int]:
    raw = _mapping(getattr(cfg, "get", lambda *_args, **_kwargs: {}) (SECURITY_STATS_CHANNEL_IDS_KEY, {}))
    return {
        key: _safe_int(raw.get(key), 0)
        for key in STAT_CHANNEL_PREFIXES
    }


def _stats_enabled(cfg: Any) -> bool:
    try:
        return _safe_bool(cfg.get(SECURITY_STATS_ENABLED_KEY), False)
    except Exception:
        return False


def _stats_counts(cfg: Any) -> Dict[str, int]:
    try:
        return normalize_security_stats(cfg.get(SECURITY_STATS_COUNTS_KEY, {}))
    except Exception:
        return dict(DEFAULT_SECURITY_STATS)


def _find_owned_category(guild: discord.Guild, cfg: Any) -> Optional[discord.CategoryChannel]:
    try:
        category_id = _safe_int(cfg.get(SECURITY_STATS_CATEGORY_ID_KEY), 0)
    except Exception:
        category_id = 0

    if category_id > 0:
        found = guild.get_channel(category_id)
        if isinstance(found, discord.CategoryChannel):
            return found

    for category in list(getattr(guild, "categories", []) or []):
        if str(getattr(category, "name", "") or "") == SECURITY_STATS_CATEGORY_NAME:
            return category
    return None


def _find_existing_stat_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    *,
    key: str,
    saved_id: int,
) -> Optional[discord.VoiceChannel]:
    if saved_id > 0:
        found = guild.get_channel(saved_id)
        if isinstance(found, discord.VoiceChannel) and int(getattr(found, "category_id", 0) or 0) == int(category.id):
            return found

    prefix = STAT_CHANNEL_PREFIXES[key]
    for channel in list(getattr(category, "voice_channels", []) or []):
        if str(getattr(channel, "name", "") or "").startswith(prefix):
            return channel
    return None


async def record_security_event(
    guild_id: int,
    *,
    spam_blocked: int = 0,
    invites_blocked: int = 0,
    timeouts_issued: int = 0,
    quarantines: int = 0,
) -> Dict[str, int]:
    """Persist actual protection actions for one guild.

    Counters are updated under a per-guild lock so concurrent moderation events do
    not race each other inside this process. Channel names are refreshed separately
    to avoid renaming Discord channels for every blocked message.
    """

    gid = int(guild_id)
    deltas = {
        "spam_blocked": max(0, _safe_int(spam_blocked, 0)),
        "invites_blocked": max(0, _safe_int(invites_blocked, 0)),
        "timeouts_issued": max(0, _safe_int(timeouts_issued, 0)),
        "quarantines": max(0, _safe_int(quarantines, 0)),
    }
    if gid <= 0 or not any(deltas.values()):
        return dict(DEFAULT_SECURITY_STATS)

    async with _lock_for(_STATS_LOCKS, gid):
        cfg = await get_guild_config(gid, refresh=True)
        counts = _stats_counts(cfg)
        for key, delta in deltas.items():
            counts[key] = max(0, int(counts.get(key, 0))) + int(delta)
        await upsert_guild_config(gid, {SECURITY_STATS_COUNTS_KEY: counts})
        return counts


async def record_spam_guard_action(
    guild_id: int,
    *,
    deleted_messages: int,
    action_taken: str,
    quarantine_case: Optional[Mapping[str, Any]] = None,
) -> Dict[str, int]:
    """Translate a completed Spam Guard action into durable counters."""

    action = str(action_taken or "").strip().lower()
    case = _mapping(quarantine_case)
    timeout_count = 1 if action.startswith("timeout:") else 0
    quarantine_count = 1 if action.startswith("quarantine:") else 0
    if quarantine_count and _safe_bool(case.get("timeout_applied"), False):
        timeout_count = 1

    return await record_security_event(
        int(guild_id),
        spam_blocked=max(0, _safe_int(deleted_messages, 0)),
        timeouts_issued=timeout_count,
        quarantines=quarantine_count,
    )


async def ensure_security_stats_display(guild: discord.Guild) -> Tuple[bool, str]:
    """Create or repair the locked Discord voice-channel stats display."""

    gid = int(guild.id)
    async with _lock_for(_DISPLAY_LOCKS, gid):
        me = guild.me
        if me is None:
            return False, "❌ Dank Shield could not resolve its server member permissions."

        perms = me.guild_permissions
        if not bool(getattr(perms, "manage_channels", False)):
            return False, "❌ Dank Shield needs **Manage Channels** to create and update the live stats display."
        if not bool(getattr(perms, "manage_roles", False)) and not bool(getattr(perms, "administrator", False)):
            return False, "❌ Dank Shield needs **Manage Roles** to lock the stats voice channels so members can see them but cannot join."

        cfg = await get_guild_config(gid, refresh=True)
        counts = _stats_counts(cfg)
        names = await _display_names_for_guild(guild, counts=counts)
        category = _find_owned_category(guild, cfg)

        try:
            if category is None:
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
                }
                category = await guild.create_category(
                    SECURITY_STATS_CATEGORY_NAME,
                    overwrites=overwrites,
                    reason="Dank Shield live stats display",
                )
                try:
                    await category.edit(position=0, reason="Place Dank Shield live stats near the top")
                except Exception:
                    pass
            else:
                await category.set_permissions(
                    guild.default_role,
                    view_channel=True,
                    connect=False,
                    reason="Keep Dank Shield stats visible but non-joinable",
                )
        except discord.Forbidden:
            return False, "❌ Discord denied permission to create or lock the stats category. Check **Manage Channels** and **Manage Roles**."
        except discord.HTTPException as exc:
            return False, f"❌ Discord could not create the stats category: `{type(exc).__name__}`."

        saved_ids = _saved_channel_ids(cfg)
        resolved_ids: Dict[str, str] = {}

        for key in STAT_CHANNEL_PREFIXES:
            channel = _find_existing_stat_channel(
                guild,
                category,
                key=key,
                saved_id=saved_ids.get(key, 0),
            )
            try:
                if channel is None:
                    channel = await guild.create_voice_channel(
                        names[key],
                        category=category,
                        reason="Dank Shield live stats display",
                    )
                elif channel.name != names[key]:
                    await channel.edit(name=names[key], reason="Refresh Dank Shield live stats")
                resolved_ids[key] = str(int(channel.id))
            except discord.Forbidden:
                return False, f"❌ Discord denied permission while creating **{names[key]}**. Check channel permission overrides."
            except discord.HTTPException as exc:
                return False, f"❌ Discord could not create or update **{names[key]}**: `{type(exc).__name__}`."

        await upsert_guild_config(
            gid,
            {
                SECURITY_STATS_ENABLED_KEY: True,
                SECURITY_STATS_CATEGORY_ID_KEY: str(int(category.id)),
                SECURITY_STATS_CHANNEL_IDS_KEY: resolved_ids,
                SECURITY_STATS_COUNTS_KEY: counts,
            },
        )
        _LAST_REFRESH_AT[gid] = time.monotonic()

        return (
            True,
            f"✅ Live Dank Shield stats are active in **{SECURITY_STATS_CATEGORY_NAME}**. The voice channels are visible but locked so nobody can join them.",
        )


async def refresh_security_stats_display(
    guild: discord.Guild,
    *,
    force: bool = False,
) -> bool:
    """Refresh or repair channels inside an already-enabled owned stats category."""

    gid = int(guild.id)
    cfg = await get_guild_config(gid, refresh=True)
    if not _stats_enabled(cfg):
        return False

    now = time.monotonic()
    if not force and (now - float(_LAST_REFRESH_AT.get(gid, 0.0))) < SECURITY_STATS_REFRESH_MIN_SECONDS:
        return False

    category = _find_owned_category(guild, cfg)
    if category is None:
        return False

    names = await _display_names_for_guild(guild, counts=_stats_counts(cfg))
    saved_ids = _saved_channel_ids(cfg)
    previous_ids = {
        key: str(value)
        for key, value in saved_ids.items()
        if int(value) > 0
    }
    resolved_ids: Dict[str, str] = dict(previous_ids)
    changed = False

    async with _lock_for(_DISPLAY_LOCKS, gid):
        for key in STAT_CHANNEL_PREFIXES:
            channel = _find_existing_stat_channel(
                guild,
                category,
                key=key,
                saved_id=saved_ids.get(key, 0),
            )
            try:
                if channel is None:
                    channel = await guild.create_voice_channel(
                        names[key],
                        category=category,
                        reason="Repair Dank Shield live stats display",
                    )
                    changed = True
                elif channel.name != names[key]:
                    await channel.edit(name=names[key], reason="Refresh Dank Shield live stats")
                    changed = True
                resolved_ids[key] = str(int(channel.id))
            except (discord.Forbidden, discord.HTTPException):
                continue

        if resolved_ids and resolved_ids != previous_ids:
            try:
                await upsert_guild_config(
                    gid,
                    {SECURITY_STATS_CHANNEL_IDS_KEY: resolved_ids},
                )
            except Exception:
                pass

        _LAST_REFRESH_AT[gid] = time.monotonic()
    return changed


@tasks.loop(minutes=10)
async def refresh_all_security_stats_displays() -> None:
    for guild in list(getattr(bot, "guilds", []) or []):
        try:
            await refresh_security_stats_display(guild)
        except Exception as exc:
            try:
                print(f"⚠️ security_stats refresh failed guild={guild.id} error={type(exc).__name__}")
            except Exception:
                pass


@refresh_all_security_stats_displays.before_loop
async def _before_security_stats_refresh() -> None:
    await bot.wait_until_ready()


@bot.listen("on_ready")
async def _start_security_stats_refresh_loop() -> None:
    if refresh_all_security_stats_displays.is_running():
        return
    try:
        refresh_all_security_stats_displays.start()
        print("✅ security_stats: live Discord stats refresh loop started")
    except RuntimeError:
        pass


__all__ = [
    "DEFAULT_SECURITY_STATS",
    "DEFAULT_TICKET_STATUS_COUNTS",
    "SECURITY_STATS_CATEGORY_NAME",
    "SECURITY_STATS_COUNTS_KEY",
    "SECURITY_STATS_ENABLED_KEY",
    "ensure_security_stats_display",
    "format_security_stat_count",
    "normalize_security_stats",
    "record_security_event",
    "record_spam_guard_action",
    "refresh_security_stats_display",
]
