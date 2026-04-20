from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import discord

from ..globals import (
    bot,
    get_supabase,
    reset_supabase,
    claim_startup_flag,
    now_utc,
)
from ..commands_ext.common import TICKET_LAST_ACTIVITY

try:
    from ..tickets_new.service import mark_ticket_closed as service_mark_ticket_closed
except Exception:
    service_mark_ticket_closed = None  # type: ignore

try:
    from ..tickets_new.transcript_service import (
        post_transcript_to_channel as transcript_post_to_channel,
    )
except Exception:
    transcript_post_to_channel = None  # type: ignore


AUTOMATION_POLL_SECONDS = 120
DB_MAX_ATTEMPTS = 5

_TICKET_AUTOMATION_TASK: asyncio.Task | None = None
_LAST_AUTOMATION_SUMMARY: Dict[int, Dict[str, Any]] = {}
_LAST_AUTOMATION_RUN_AT: Optional[datetime] = None

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": False,
    "sla_breach_alerts_enabled": True,
    "inactivity_reminders_enabled": True,
    "auto_close_enabled": False,
    "inactivity_reminder_minutes": 240,
    "auto_close_minutes": 1440,
    "staff_alert_channel_id": None,
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _parse_iso(value: Any) -> Optional[datetime]:
    try:
        raw = _safe_str(value)
        if not raw:
            return None
        raw = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _discord_ts(dt: Optional[datetime]) -> str:
    try:
        if dt is None:
            return "unknown"
        return f"<t:{int(dt.timestamp())}:F>"
    except Exception:
        return "unknown"


def _is_retryable_db_error(error: Exception) -> bool:
    text = repr(error).lower()
    markers = (
        "remoteprotocolerror",
        "server disconnected",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "eof",
        "network",
        "closed connection",
        "connection refused",
        "httpcore",
        "httpx",
    )
    return any(marker in text for marker in markers)


def _sleep_backoff(attempt: int) -> None:
    base = min(0.35 * (2 ** max(0, attempt - 1)), 3.0)
    jitter = random.uniform(0.05, 0.25)
    time.sleep(base + jitter)


def _execute_db(op_name: str, executor, max_attempts: int = DB_MAX_ATTEMPTS):
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return executor()
        except Exception as e:
            last_error = e
            if _is_retryable_db_error(e) and attempt < max_attempts:
                try:
                    reset_supabase()
                except Exception:
                    pass
                print(f"⚠️ {op_name}: transient DB error on attempt {attempt}/{max_attempts}: {repr(e)}")
                _sleep_backoff(attempt)
                continue
            raise
    raise last_error


def _settings_table():
    sb = get_supabase(force_new=False)
    if sb is None:
        raise RuntimeError("Supabase is not configured.")
    return sb.table("ticket_automation_settings")


def _state_table():
    sb = get_supabase(force_new=False)
    if sb is None:
        raise RuntimeError("Supabase is not configured.")
    return sb.table("ticket_automation_state")


def _normalize_settings(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    src = dict(row or {})
    out = dict(_DEFAULT_SETTINGS)
    out["enabled"] = _safe_bool(src.get("enabled"), _DEFAULT_SETTINGS["enabled"])
    out["sla_breach_alerts_enabled"] = _safe_bool(
        src.get("sla_breach_alerts_enabled"),
        _DEFAULT_SETTINGS["sla_breach_alerts_enabled"],
    )
    out["inactivity_reminders_enabled"] = _safe_bool(
        src.get("inactivity_reminders_enabled"),
        _DEFAULT_SETTINGS["inactivity_reminders_enabled"],
    )
    out["auto_close_enabled"] = _safe_bool(
        src.get("auto_close_enabled"),
        _DEFAULT_SETTINGS["auto_close_enabled"],
    )

    reminder_minutes = max(
        1,
        _safe_int(
            src.get("inactivity_reminder_minutes"),
            _DEFAULT_SETTINGS["inactivity_reminder_minutes"],
        ),
    )
    auto_close_minutes = max(
        5,
        _safe_int(
            src.get("auto_close_minutes"),
            _DEFAULT_SETTINGS["auto_close_minutes"],
        ),
    )

    if auto_close_minutes <= reminder_minutes:
        auto_close_minutes = reminder_minutes + 1

    out["inactivity_reminder_minutes"] = reminder_minutes
    out["auto_close_minutes"] = auto_close_minutes
    out["staff_alert_channel_id"] = _safe_str(src.get("staff_alert_channel_id")) or None
    return out


def _get_automation_settings_sync(guild_id: int) -> Dict[str, Any]:
    def _read():
        res = (
            _settings_table()
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return dict(rows[0]) if rows else {}

    try:
        return _normalize_settings(_execute_db(f"ticket automation settings read ({guild_id})", _read) or {})
    except Exception:
        return dict(_DEFAULT_SETTINGS)


async def get_ticket_automation_settings(guild_id: int) -> Dict[str, Any]:
    return await asyncio.to_thread(_get_automation_settings_sync, guild_id)


def _upsert_automation_settings_sync(guild_id: int, patch: Dict[str, Any]) -> bool:
    payload = {
        "guild_id": str(int(guild_id)),
        **patch,
        "updated_at": now_utc().isoformat(),
    }

    def _write():
        _settings_table().upsert(payload, on_conflict="guild_id").execute()

    try:
        _execute_db(f"ticket automation settings upsert ({guild_id})", _write)
        return True
    except Exception as e:
        print("⚠️ Failed writing ticket automation settings:", repr(e))
        return False


async def upsert_ticket_automation_settings(guild_id: int, patch: Dict[str, Any]) -> bool:
    return await asyncio.to_thread(_upsert_automation_settings_sync, guild_id, patch)


def _get_ticket_state_sync(guild_id: int, channel_id: int) -> Dict[str, Any]:
    def _read():
        res = (
            _state_table()
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .eq("channel_id", str(int(channel_id)))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return dict(rows[0]) if rows else {}

    try:
        return _execute_db(f"ticket automation state read ({guild_id}/{channel_id})", _read) or {}
    except Exception:
        return {}


def _upsert_ticket_state_sync(guild_id: int, channel_id: int, patch: Dict[str, Any]) -> bool:
    payload = {
        "guild_id": str(int(guild_id)),
        "channel_id": str(int(channel_id)),
        **patch,
        "updated_at": now_utc().isoformat(),
    }

    def _write():
        _state_table().upsert(payload, on_conflict="guild_id,channel_id").execute()

    try:
        _execute_db(f"ticket automation state upsert ({guild_id}/{channel_id})", _write)
        return True
    except Exception as e:
        print("⚠️ Failed writing ticket automation state:", repr(e))
        return False


def _list_active_tickets_sync(guild_id: int) -> List[Dict[str, Any]]:
    def _read():
        sb = get_supabase(force_new=False)
        if sb is None:
            return []
        res = (
            sb.table("tickets")
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .or_("status.eq.open,status.eq.claimed")
            .order("created_at", desc=False)
            .execute()
        )
        return getattr(res, "data", None) or []

    try:
        rows = _execute_db(f"ticket automation active tickets read ({guild_id})", _read) or []
        return [dict(x) for x in rows if isinstance(x, dict)]
    except Exception as e:
        print("⚠️ Failed listing active tickets:", repr(e))
        return []


def _row_status(row: Dict[str, Any]) -> str:
    try:
        raw = _safe_str(row.get("status")).lower()
        if raw in {"open", "claimed", "closed", "deleted"}:
            return raw
    except Exception:
        pass
    return "unknown"


def _channel_looks_closed(channel: discord.abc.GuildChannel) -> bool:
    try:
        return _safe_str(getattr(channel, "name", "")).lower().startswith("closed-")
    except Exception:
        return False


async def _resolve_ticket_channel(guild: discord.Guild, row: Dict[str, Any]) -> Optional[discord.abc.GuildChannel]:
    channel_id = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
    if channel_id <= 0:
        return None

    try:
        ch = guild.get_channel(channel_id)
        if ch is not None:
            return ch
    except Exception:
        pass

    try:
        fetched = await guild.fetch_channel(channel_id)
        return fetched
    except Exception:
        return None


async def _resolve_owner(guild: discord.Guild, row: Dict[str, Any]) -> Optional[discord.Member]:
    owner_id = _safe_int(row.get("owner_id") or row.get("user_id"), 0)
    if owner_id <= 0:
        return None

    try:
        member = guild.get_member(owner_id)
        if member is not None:
            return member
    except Exception:
        pass

    try:
        fetched = await guild.fetch_member(owner_id)
        if isinstance(fetched, discord.Member):
            return fetched
    except Exception:
        pass

    return None


async def _resolve_assignee(guild: discord.Guild, row: Dict[str, Any]) -> Optional[discord.Member]:
    assignee_id = _safe_int(row.get("assigned_to") or row.get("claimed_by"), 0)
    if assignee_id <= 0:
        return None

    try:
        member = guild.get_member(assignee_id)
        if member is not None:
            return member
    except Exception:
        pass

    try:
        fetched = await guild.fetch_member(assignee_id)
        if isinstance(fetched, discord.Member):
            return fetched
    except Exception:
        pass

    return None


def _effective_last_activity(row: Dict[str, Any]) -> Optional[datetime]:
    candidates: List[datetime] = []

    try:
        cid = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
        if cid > 0:
            runtime_dt = TICKET_LAST_ACTIVITY.get(int(cid))
            if isinstance(runtime_dt, datetime):
                if runtime_dt.tzinfo is None:
                    runtime_dt = runtime_dt.replace(tzinfo=timezone.utc)
                candidates.append(runtime_dt.astimezone(timezone.utc))
    except Exception:
        pass

    for field in ("last_activity_at", "last_message_at", "updated_at", "created_at"):
        dt = _parse_iso(row.get(field))
        if dt is not None:
            candidates.append(dt)

    if not candidates:
        return None
    return max(candidates)


async def _safe_send_message(channel: discord.abc.Messageable, content: str) -> bool:
    try:
        await channel.send(
            content,
            allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False),
        )
        return True
    except Exception as e:
        print("⚠️ Ticket automation send failed:", repr(e))
        return False


async def _send_staff_alert(guild: discord.Guild, settings: Dict[str, Any], content: str) -> bool:
    channel_id = _safe_int(settings.get("staff_alert_channel_id"), 0)
    if channel_id <= 0:
        return False
    try:
        ch = guild.get_channel(channel_id)
        if ch is None:
            try:
                ch = await guild.fetch_channel(channel_id)
            except Exception:
                ch = None
        if ch is None or not hasattr(ch, "send"):
            return False
        return await _safe_send_message(ch, content)  # type: ignore[arg-type]
    except Exception:
        return False


async def _send_sla_breach_alert(
    guild: discord.Guild,
    channel: discord.abc.GuildChannel,
    row: Dict[str, Any],
    settings: Dict[str, Any],
) -> bool:
    owner = await _resolve_owner(guild, row)
    assignee = await _resolve_assignee(guild, row)
    due = _parse_iso(row.get("sla_deadline"))
    channel_ref = getattr(channel, "mention", f"`{getattr(channel, 'id', 'unknown')}`")
    msg = (
        f"🚨 **SLA BREACH** for {channel_ref} "
        f"(ticket #{_safe_str(row.get('ticket_number'), 'unknown')}).\n"
        f"Owner: {owner.mention if owner else 'unknown'}\n"
        f"Assignee: {assignee.mention if assignee else 'unassigned'}\n"
        f"Due: {_discord_ts(due)}"
    )
    sent_main = False
    if isinstance(channel, (discord.TextChannel, discord.Thread)):
        sent_main = await _safe_send_message(channel, msg)
    sent_staff = await _send_staff_alert(guild, settings, msg)
    return sent_main or sent_staff


async def _send_inactivity_reminder(
    guild: discord.Guild,
    channel: discord.abc.GuildChannel,
    row: Dict[str, Any],
    minutes_idle: int,
    settings: Dict[str, Any],
) -> bool:
    owner = await _resolve_owner(guild, row)
    assignee = await _resolve_assignee(guild, row)
    mentions = []
    if owner is not None:
        mentions.append(owner.mention)
    if assignee is not None and (owner is None or assignee.id != owner.id):
        mentions.append(assignee.mention)

    prefix = " ".join(mentions).strip()
    if prefix:
        prefix += " "

    msg = (
        f"{prefix}⏰ This ticket has been inactive for about **{minutes_idle} minute(s)**.\n"
        f"Please respond before it is auto-closed."
    )
    sent_main = False
    if isinstance(channel, (discord.TextChannel, discord.Thread)):
        sent_main = await _safe_send_message(channel, msg)

    channel_ref = getattr(channel, "mention", f"`{getattr(channel, 'id', 'unknown')}`")
    sent_staff = await _send_staff_alert(
        guild,
        settings,
        f"⏰ Inactivity reminder sent for {channel_ref} "
        f"(ticket #{_safe_str(row.get('ticket_number'), 'unknown')}).",
    )
    return sent_main or sent_staff


async def _repair_stale_closed_drift(
    guild: discord.Guild,
    channel: discord.abc.GuildChannel,
    row: Dict[str, Any],
) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False
    if service_mark_ticket_closed is None:
        return False
    if not _channel_looks_closed(channel):
        return False
    if _row_status(row) not in {"open", "claimed", "unknown"}:
        return False

    try:
        await service_mark_ticket_closed(
            channel=channel,
            closed_by=guild.me if getattr(guild, "me", None) else None,
            reason="State repaired by ticket automation worker",
        )
        return True
    except Exception as e:
        print("⚠️ Ticket automation state repair failed:", repr(e))
        return False


async def _auto_close_ticket(
    guild: discord.Guild,
    channel: discord.abc.GuildChannel,
    row: Dict[str, Any],
    minutes_idle: int,
    settings: Dict[str, Any],
) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False
    if not callable(service_mark_ticket_closed):
        return False

    reason = f"Auto-closed after {minutes_idle} minute(s) of inactivity."

    owner = await _resolve_owner(guild, row)
    actor = guild.me if getattr(guild, "me", None) else None

    transcript_url: Optional[str] = None
    if callable(transcript_post_to_channel):
        try:
            _msg, transcript_url = await transcript_post_to_channel(
                ticket_channel=channel,
                deleted_by=actor,
                reason=reason,
            )
        except Exception as e:
            print("⚠️ Ticket automation transcript failed:", repr(e))

    try:
        closed_ok = await service_mark_ticket_closed(
            channel=channel,
            closed_by=actor,
            reason=reason,
        )
    except Exception as e:
        print("⚠️ Ticket automation DB close failed:", repr(e))
        return False

    if not closed_ok:
        return False

    try:
        transcript_line = f"\n🧾 Transcript: {transcript_url}" if transcript_url else ""
        owner_line = f"{owner.mention} " if owner is not None else ""
        await _safe_send_message(channel, f"🧹 {owner_line}{reason}{transcript_line}")
    except Exception:
        pass

    channel_ref = getattr(channel, "mention", f"`{getattr(channel, 'id', 'unknown')}`")
    try:
        await _send_staff_alert(
            guild,
            settings,
            f"🧹 Auto-closed {channel_ref} "
            f"(ticket #{_safe_str(row.get('ticket_number'), 'unknown')}) after {minutes_idle} minute(s) of inactivity.",
        )
    except Exception:
        pass

    return True


async def _process_ticket(guild: discord.Guild, row: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, int]:
    summary = {
        "checked": 1,
        "sla_breach_alerts": 0,
        "inactivity_reminders": 0,
        "auto_closed": 0,
    }

    if _row_status(row) not in {"open", "claimed"}:
        return summary

    channel = await _resolve_ticket_channel(guild, row)
    if channel is None:
        return summary

    channel_id = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
    if channel_id <= 0:
        return summary

    repaired = await _repair_stale_closed_drift(guild, channel, row)
    if repaired:
        return summary

    if _channel_looks_closed(channel):
        return summary

    state = await asyncio.to_thread(_get_ticket_state_sync, guild.id, channel_id)
    now = now_utc()

    due = _parse_iso(row.get("sla_deadline"))
    sla_alerted_at = _parse_iso(state.get("sla_breach_alert_sent_at"))
    if (
        _safe_bool(settings.get("sla_breach_alerts_enabled"), True)
        and due is not None
        and now >= due
        and sla_alerted_at is None
    ):
        sent = await _send_sla_breach_alert(guild, channel, row, settings)
        if sent:
            summary["sla_breach_alerts"] += 1
            await asyncio.to_thread(
                _upsert_ticket_state_sync,
                guild.id,
                channel_id,
                {"sla_breach_alert_sent_at": now.isoformat()},
            )
            state["sla_breach_alert_sent_at"] = now.isoformat()

    last_activity = _effective_last_activity(row)
    if last_activity is None:
        return summary

    minutes_idle = max(0, int((now - last_activity).total_seconds() // 60))
    reminder_after = max(1, _safe_int(settings.get("inactivity_reminder_minutes"), 240))
    auto_close_after = max(reminder_after + 1, _safe_int(settings.get("auto_close_minutes"), 1440))

    last_reminder_at = _parse_iso(state.get("last_inactivity_reminder_at"))
    auto_closed_at = _parse_iso(state.get("auto_closed_at"))

    should_send_reminder = (
        _safe_bool(settings.get("inactivity_reminders_enabled"), True)
        and minutes_idle >= reminder_after
        and (
            last_reminder_at is None
            or last_activity > last_reminder_at
        )
    )

    if should_send_reminder:
        sent = await _send_inactivity_reminder(guild, channel, row, minutes_idle, settings)
        if sent:
            summary["inactivity_reminders"] += 1
            await asyncio.to_thread(
                _upsert_ticket_state_sync,
                guild.id,
                channel_id,
                {"last_inactivity_reminder_at": now.isoformat()},
            )
            state["last_inactivity_reminder_at"] = now.isoformat()

    if (
        _safe_bool(settings.get("auto_close_enabled"), False)
        and minutes_idle >= auto_close_after
        and auto_closed_at is None
    ):
        closed = await _auto_close_ticket(guild, channel, row, minutes_idle, settings)
        if closed:
            summary["auto_closed"] += 1
            await asyncio.to_thread(
                _upsert_ticket_state_sync,
                guild.id,
                channel_id,
                {"auto_closed_at": now.isoformat()},
            )

    return summary


async def run_ticket_automation_pass(*, guild_id: Optional[int] = None) -> Dict[str, Any]:
    global _LAST_AUTOMATION_RUN_AT

    guilds = []
    if guild_id is not None:
        guild = bot.get_guild(int(guild_id))
        if guild is not None:
            guilds = [guild]
    else:
        guilds = list(bot.guilds)

    overall = {
        "guilds_checked": 0,
        "tickets_checked": 0,
        "sla_breach_alerts": 0,
        "inactivity_reminders": 0,
        "auto_closed": 0,
    }

    for guild in guilds:
        settings = await get_ticket_automation_settings(int(guild.id))
        if not _safe_bool(settings.get("enabled"), False):
            continue

        overall["guilds_checked"] += 1
        rows = await asyncio.to_thread(_list_active_tickets_sync, int(guild.id))

        guild_summary = {
            "guild_id": int(guild.id),
            "tickets_checked": 0,
            "sla_breach_alerts": 0,
            "inactivity_reminders": 0,
            "auto_closed": 0,
        }

        for row in rows:
            try:
                result = await _process_ticket(guild, row, settings)
                guild_summary["tickets_checked"] += result.get("checked", 0)
                guild_summary["sla_breach_alerts"] += result.get("sla_breach_alerts", 0)
                guild_summary["inactivity_reminders"] += result.get("inactivity_reminders", 0)
                guild_summary["auto_closed"] += result.get("auto_closed", 0)
            except Exception as e:
                print("⚠️ Ticket automation process error:", repr(e))

        _LAST_AUTOMATION_SUMMARY[int(guild.id)] = guild_summary
        overall["tickets_checked"] += guild_summary["tickets_checked"]
        overall["sla_breach_alerts"] += guild_summary["sla_breach_alerts"]
        overall["inactivity_reminders"] += guild_summary["inactivity_reminders"]
        overall["auto_closed"] += guild_summary["auto_closed"]

        print(
            "🤖 ticket_automation:",
            f"guild={guild.id}",
            f"checked={guild_summary['tickets_checked']}",
            f"sla_alerts={guild_summary['sla_breach_alerts']}",
            f"reminders={guild_summary['inactivity_reminders']}",
            f"auto_closed={guild_summary['auto_closed']}",
        )

    _LAST_AUTOMATION_RUN_AT = now_utc()
    return overall


async def ticket_automation_loop() -> None:
    await bot.wait_until_ready()
    print("🤖 Ticket automation worker started")

    while not bot.is_closed():
        try:
            await run_ticket_automation_pass()
        except Exception as e:
            print("❌ Ticket automation loop error:", repr(e))

        await asyncio.sleep(AUTOMATION_POLL_SECONDS)


def start_ticket_automation_worker() -> None:
    global _TICKET_AUTOMATION_TASK

    if _TICKET_AUTOMATION_TASK is not None and not _TICKET_AUTOMATION_TASK.done():
        return

    if not claim_startup_flag("ticket_automation_worker"):
        return

    _TICKET_AUTOMATION_TASK = bot.loop.create_task(ticket_automation_loop())


def get_ticket_automation_runtime_status() -> Dict[str, Any]:
    task_running = bool(_TICKET_AUTOMATION_TASK is not None and not _TICKET_AUTOMATION_TASK.done())
    return {
        "task_running": task_running,
        "poll_seconds": AUTOMATION_POLL_SECONDS,
        "last_run_at": _LAST_AUTOMATION_RUN_AT.isoformat() if _LAST_AUTOMATION_RUN_AT else None,
        "guild_summaries": dict(_LAST_AUTOMATION_SUMMARY),
    }
