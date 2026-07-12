from __future__ import annotations

"""Authoritative, fail-closed member activity tracking.

Cleanup-authorizing activity is limited to durable direct evidence:
- retained guild messages authored by the member
- Discord interactions submitted while Dank Shield is online
- ticket messages explicitly authored by the member

Reactions are stored only as supplemental context. They never authorize
cleanup because their complete history cannot be reconstructed during bot
downtime.

Presence and voice-state changes are intentionally excluded from authoritative
inactivity proof because they can be inaccurate or caused by staff actions.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Mapping, Optional
import uuid

import discord

from stoney_verify.globals import get_supabase
from stoney_verify.members_new.activity_reconciliation import (
    audit_guild_activity_scope,
    max_reconcile_gap_seconds,
    reconcile_restart_gap,
)


LEDGER_TABLE = "member_activity_ledger"
TRACKER_STATE_TABLE = "member_activity_tracker_state"

_PROCESS_ID = uuid.uuid4().hex
_HEARTBEAT_INTERVAL_SECONDS = 60
_MAX_HEARTBEAT_GAP_SECONDS = 180

_INSTALLED = False
_HEARTBEAT_TASK: Optional[asyncio.Task] = None
_STARTED_GUILDS: set[int] = set()
_LOCAL_ERRORS: dict[int, str] = {}
_LOCAL_SCOPE_ERRORS: dict[int, str] = {}


@dataclass(frozen=True)
class ActivityCoverageStatus:
    guild_id: int
    actionable: bool
    reason: str
    continuous_since: Optional[datetime] = None
    last_heartbeat_at: Optional[datetime] = None
    observed_days: int = 0
    required_days: int = 0
    process_id: str = ""
    event_writes_failed: int = 0
    last_error: str = ""
    storage_ready: bool = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    try:
        parsed = datetime.fromisoformat(
            str(value).strip().replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _combined_local_error(guild_id: int) -> str:
    gid = int(guild_id)
    parts = [
        str(_LOCAL_ERRORS.get(gid, "") or "").strip(),
        str(_LOCAL_SCOPE_ERRORS.get(gid, "") or "").strip(),
    ]
    return " | ".join(part for part in parts if part)


def _rpc_sync(name: str, params: Mapping[str, Any]) -> None:
    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase client is unavailable.")

    sb.rpc(name, dict(params)).execute()


def _select_tracker_state_sync(guild_id: int) -> Optional[dict[str, Any]]:
    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase client is unavailable.")

    response = (
        sb.table(TRACKER_STATE_TABLE)
        .select("*")
        .eq("guild_id", str(int(guild_id)))
        .limit(1)
        .execute()
    )

    rows = getattr(response, "data", None) or []

    if not rows or not isinstance(rows[0], Mapping):
        return None

    return dict(rows[0])


def evaluate_coverage_state(
    row: Optional[Mapping[str, Any]],
    *,
    guild_id: int,
    now: datetime,
    required_days: int,
    expected_process_id: str,
    max_gap_seconds: int = _MAX_HEARTBEAT_GAP_SECONDS,
    local_error: str = "",
) -> ActivityCoverageStatus:
    safe_days = max(1, int(required_days or 1))
    current = _safe_dt(now) or _utcnow()

    if local_error:
        return ActivityCoverageStatus(
            guild_id=int(guild_id),
            actionable=False,
            reason=(
                "Tracker entered fail-closed mode after a local write error: "
                + str(local_error)[:300]
            ),
            required_days=safe_days,
            process_id=str(expected_process_id),
            last_error=str(local_error)[:500],
            storage_ready=False,
        )

    if not row:
        return ActivityCoverageStatus(
            guild_id=int(guild_id),
            actionable=False,
            reason=(
                "Authoritative activity tables are missing, unreadable, "
                "or have not started tracking this server yet."
            ),
            required_days=safe_days,
            process_id=str(expected_process_id),
            storage_ready=False,
        )

    stored_process = str(row.get("process_id") or "").strip()
    continuous_since = _safe_dt(row.get("continuous_since"))
    last_heartbeat = _safe_dt(row.get("last_heartbeat_at"))
    failures = _safe_int(row.get("event_writes_failed"), 0)
    last_error = str(row.get("last_error") or "").strip()

    if stored_process != str(expected_process_id):
        return ActivityCoverageStatus(
            guild_id=int(guild_id),
            actionable=False,
            reason=(
                "Tracker process changed. Continuous proof restarts after "
                "every bot restart so activity cannot be missed."
            ),
            continuous_since=continuous_since,
            last_heartbeat_at=last_heartbeat,
            required_days=safe_days,
            process_id=stored_process,
            event_writes_failed=failures,
            last_error=last_error,
            storage_ready=True,
        )

    if continuous_since is None or last_heartbeat is None:
        return ActivityCoverageStatus(
            guild_id=int(guild_id),
            actionable=False,
            reason="Tracker state is incomplete, so cleanup remains review-only.",
            continuous_since=continuous_since,
            last_heartbeat_at=last_heartbeat,
            required_days=safe_days,
            process_id=stored_process,
            event_writes_failed=failures,
            last_error=last_error,
            storage_ready=True,
        )

    heartbeat_age = (current - last_heartbeat).total_seconds()

    if heartbeat_age < -30:
        return ActivityCoverageStatus(
            guild_id=int(guild_id),
            actionable=False,
            reason="Tracker heartbeat is in the future; check system time.",
            continuous_since=continuous_since,
            last_heartbeat_at=last_heartbeat,
            required_days=safe_days,
            process_id=stored_process,
            event_writes_failed=failures,
            last_error=last_error,
            storage_ready=True,
        )

    if heartbeat_age > max(60, int(max_gap_seconds)):
        return ActivityCoverageStatus(
            guild_id=int(guild_id),
            actionable=False,
            reason=(
                "Tracker heartbeat is stale. Continuous proof has a gap, "
                "so cleanup remains review-only."
            ),
            continuous_since=continuous_since,
            last_heartbeat_at=last_heartbeat,
            required_days=safe_days,
            process_id=stored_process,
            event_writes_failed=failures,
            last_error=last_error,
            storage_ready=True,
        )

    observed_seconds = max(
        0.0,
        (current - continuous_since).total_seconds(),
    )
    observed_days = max(
        0,
        int(math.floor(observed_seconds / 86400.0)),
    )

    required_seconds = safe_days * 86400

    if observed_seconds < required_seconds:
        remaining_days = max(
            1,
            int(
                math.ceil(
                    (required_seconds - observed_seconds) / 86400.0
                )
            ),
        )

        return ActivityCoverageStatus(
            guild_id=int(guild_id),
            actionable=False,
            reason=(
                f"Authoritative tracker has {observed_days}/{safe_days} "
                f"continuous day(s). Approximately {remaining_days} more "
                "day(s) are required."
            ),
            continuous_since=continuous_since,
            last_heartbeat_at=last_heartbeat,
            observed_days=observed_days,
            required_days=safe_days,
            process_id=stored_process,
            event_writes_failed=failures,
            last_error=last_error,
            storage_ready=True,
        )

    return ActivityCoverageStatus(
        guild_id=int(guild_id),
        actionable=True,
        reason=(
            f"Authoritative tracker has continuously observed this server "
            f"for {observed_days} day(s), meeting the {safe_days}-day "
            "requirement."
        ),
        continuous_since=continuous_since,
        last_heartbeat_at=last_heartbeat,
        observed_days=observed_days,
        required_days=safe_days,
        process_id=stored_process,
        event_writes_failed=failures,
        last_error=last_error,
        storage_ready=True,
    )


async def get_activity_coverage_status(
    guild_id: int,
    *,
    required_days: int,
) -> ActivityCoverageStatus:
    gid = int(guild_id)

    try:
        row = await asyncio.to_thread(
            _select_tracker_state_sync,
            gid,
        )
    except Exception as exc:
        return ActivityCoverageStatus(
            guild_id=gid,
            actionable=False,
            reason=(
                "Could not read authoritative tracker state: "
                f"{type(exc).__name__}: {str(exc)[:220]}"
            ),
            required_days=max(1, int(required_days or 1)),
            process_id=_PROCESS_ID,
            last_error=str(exc)[:500],
            storage_ready=False,
        )

    return evaluate_coverage_state(
        row,
        guild_id=gid,
        now=_utcnow(),
        required_days=required_days,
        expected_process_id=_PROCESS_ID,
        local_error=_combined_local_error(gid),
    )


def _start_tracker_sync(guild_id: int) -> None:
    now = _utcnow().isoformat()

    _rpc_sync(
        "start_member_activity_tracker",
        {
            "p_guild_id": str(int(guild_id)),
            "p_process_id": _PROCESS_ID,
            "p_started_at": now,
        },
    )


def _resume_tracker_sync(
    *,
    guild_id: int,
    previous_process_id: str,
    previous_heartbeat_at: datetime,
    resumed_at: datetime,
) -> bool:
    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase client is unavailable.")

    response = sb.rpc(
        "resume_member_activity_tracker",
        {
            "p_guild_id": str(int(guild_id)),
            "p_previous_process_id": str(
                previous_process_id
            ),
            "p_new_process_id": _PROCESS_ID,
            "p_previous_heartbeat_at": (
                previous_heartbeat_at.isoformat()
            ),
            "p_resumed_at": resumed_at.isoformat(),
        },
    ).execute()

    data = getattr(response, "data", None)

    if isinstance(data, bool):
        return data

    if isinstance(data, list) and data:
        return bool(data[0])

    if isinstance(data, Mapping):
        for value in data.values():
            if isinstance(value, bool):
                return value

    return bool(data)


def _heartbeat_sync(guild_id: int) -> None:
    _rpc_sync(
        "heartbeat_member_activity_tracker",
        {
            "p_guild_id": str(int(guild_id)),
            "p_process_id": _PROCESS_ID,
            "p_heartbeat_at": _utcnow().isoformat(),
            "p_max_gap_seconds": _MAX_HEARTBEAT_GAP_SECONDS,
        },
    )


def _fail_tracker_sync(guild_id: int, error: str) -> None:
    _rpc_sync(
        "fail_member_activity_tracker",
        {
            "p_guild_id": str(int(guild_id)),
            "p_process_id": _PROCESS_ID,
            "p_failed_at": _utcnow().isoformat(),
            "p_error": str(error or "unknown tracker error")[:1000],
        },
    )


def _record_activity_sync(
    *,
    guild_id: int,
    user_id: int,
    activity_type: str,
    occurred_at: datetime,
    channel_id: Optional[int],
) -> None:
    _rpc_sync(
        "record_member_activity",
        {
            "p_guild_id": str(int(guild_id)),
            "p_user_id": str(int(user_id)),
            "p_activity_type": str(activity_type),
            "p_occurred_at": occurred_at.isoformat(),
            "p_channel_id": (
                str(int(channel_id))
                if channel_id is not None
                else ""
            ),
            "p_process_id": _PROCESS_ID,
        },
    )


async def _mark_failure(guild_id: int, error: str) -> None:
    gid = int(guild_id)
    message = str(error or "unknown tracker error")[:500]
    _LOCAL_ERRORS[gid] = message

    try:
        await asyncio.to_thread(
            _fail_tracker_sync,
            gid,
            message,
        )
    except Exception:
        pass

    print(
        "⚠️ authoritative activity tracker fail-closed "
        f"guild={gid} error={message}"
    )


async def record_direct_member_activity(
    *,
    guild_id: int,
    user_id: int,
    activity_type: str,
    occurred_at: Optional[datetime] = None,
    channel_id: Optional[int] = None,
) -> bool:
    if int(guild_id) <= 0 or int(user_id) <= 0:
        return False

    if activity_type not in {
        "message",
        "reaction",
        "interaction",
        "ticket_message",
    }:
        return False

    timestamp = _safe_dt(occurred_at) or _utcnow()

    try:
        await asyncio.to_thread(
            _record_activity_sync,
            guild_id=int(guild_id),
            user_id=int(user_id),
            activity_type=activity_type,
            occurred_at=timestamp,
            channel_id=channel_id,
        )
        _LOCAL_ERRORS.pop(int(guild_id), None)
        return True
    except Exception as exc:
        await _mark_failure(
            int(guild_id),
            f"{type(exc).__name__}: {str(exc)[:400]}",
        )
        return False


async def _refresh_scope_status(
    guild: discord.Guild,
) -> str:
    gid = int(guild.id)
    previous = str(
        _LOCAL_SCOPE_ERRORS.get(gid, "") or ""
    ).strip()
    current = str(
        audit_guild_activity_scope(guild) or ""
    ).strip()

    if current:
        _LOCAL_SCOPE_ERRORS[gid] = current

        if gid in _STARTED_GUILDS and not previous:
            try:
                await asyncio.to_thread(
                    _start_tracker_sync,
                    gid,
                )
                print(
                    "⚠️ authoritative activity coverage reset "
                    f"guild={gid} reason=scope_became_incomplete"
                )
            except Exception as exc:
                await _mark_failure(
                    gid,
                    "scope reset failed: "
                    f"{type(exc).__name__}: {str(exc)[:300]}",
                )

        return current

    if previous:
        _LOCAL_SCOPE_ERRORS.pop(gid, None)

        if gid in _STARTED_GUILDS:
            try:
                await asyncio.to_thread(
                    _start_tracker_sync,
                    gid,
                )
                _LOCAL_ERRORS.pop(gid, None)
                print(
                    "📡 authoritative activity coverage restarted "
                    f"guild={gid} reason=scope_permissions_restored"
                )
            except Exception as exc:
                await _mark_failure(
                    gid,
                    "scope recovery failed: "
                    f"{type(exc).__name__}: {str(exc)[:300]}",
                )

    return ""


async def _start_guild_tracking(
    guild: discord.Guild,
) -> bool:
    gid = int(guild.id)

    if gid in _STARTED_GUILDS:
        return True

    try:
        scope_error = await _refresh_scope_status(guild)
        row = await asyncio.to_thread(
            _select_tracker_state_sync,
            gid,
        )
        now = _utcnow()

        previous_process = str(
            (row or {}).get("process_id") or ""
        ).strip()
        previous_heartbeat = _safe_dt(
            (row or {}).get("last_heartbeat_at")
        )
        previous_continuous = _safe_dt(
            (row or {}).get("continuous_since")
        )
        previous_failures = _safe_int(
            (row or {}).get("event_writes_failed"),
            0,
        )
        previous_error = str(
            (row or {}).get("last_error") or ""
        ).strip()

        if (
            row
            and previous_process == _PROCESS_ID
            and previous_heartbeat is not None
        ):
            await asyncio.to_thread(
                _heartbeat_sync,
                gid,
            )
            _STARTED_GUILDS.add(gid)
            _LOCAL_ERRORS.pop(gid, None)
            return True

        reset_reason = "no_previous_tracker_state"

        if row:
            reset_reason = "restart_gap_not_reconciled"

        can_reconcile = bool(
            row
            and not scope_error
            and previous_process
            and previous_heartbeat is not None
            and previous_continuous is not None
            and previous_failures <= 0
            and not previous_error
        )

        if can_reconcile:
            gap_seconds = (
                now - previous_heartbeat
            ).total_seconds()

            if gap_seconds < 0:
                can_reconcile = False
                reset_reason = "previous_heartbeat_in_future"
            elif gap_seconds > max_reconcile_gap_seconds():
                can_reconcile = False
                reset_reason = (
                    "restart_gap_exceeded_safe_limit"
                )

        if can_reconcile:
            try:
                result = await reconcile_restart_gap(
                    guild,
                    after=previous_heartbeat,
                    before=now,
                )

                for user_id, (
                    occurred_at,
                    channel_id,
                ) in sorted(
                    result.latest_by_user.items()
                ):
                    await asyncio.to_thread(
                        _record_activity_sync,
                        guild_id=gid,
                        user_id=int(user_id),
                        activity_type="message",
                        occurred_at=occurred_at,
                        channel_id=channel_id or None,
                    )

                resumed = await asyncio.to_thread(
                    _resume_tracker_sync,
                    guild_id=gid,
                    previous_process_id=previous_process,
                    previous_heartbeat_at=previous_heartbeat,
                    resumed_at=now,
                )

                if not resumed:
                    raise RuntimeError(
                        "Tracker state changed while restart "
                        "reconciliation was running."
                    )

                _STARTED_GUILDS.add(gid)
                _LOCAL_ERRORS.pop(gid, None)

                print(
                    "📡 authoritative activity continuity resumed "
                    f"guild={gid} "
                    f"channels={result.scanned_channels} "
                    f"messages={result.scanned_messages} "
                    f"members={result.replayed_members} "
                    f"gap_seconds={int(gap_seconds)}"
                )
                return True

            except Exception as exc:
                reset_reason = (
                    "restart_reconciliation_failed: "
                    f"{type(exc).__name__}: {str(exc)[:250]}"
                )

        elif scope_error:
            reset_reason = (
                "activity_scope_incomplete: "
                + scope_error[:250]
            )
        elif previous_failures > 0 or previous_error:
            reset_reason = "previous_tracker_failure"

        await asyncio.to_thread(
            _start_tracker_sync,
            gid,
        )

        _STARTED_GUILDS.add(gid)
        _LOCAL_ERRORS.pop(gid, None)

        print(
            "📡 authoritative activity tracking started "
            f"guild={gid} process={_PROCESS_ID[:8]} "
            f"new_proof_window=True reason={reset_reason}"
        )
        return True

    except Exception as exc:
        await _mark_failure(
            gid,
            "tracker start failed: "
            f"{type(exc).__name__}: {str(exc)[:350]}",
        )
        return False


async def _heartbeat_loop(
    bot: discord.Client,
) -> None:
    while not bot.is_closed():
        await asyncio.sleep(
            _HEARTBEAT_INTERVAL_SECONDS
        )

        for guild in list(
            getattr(bot, "guilds", []) or []
        ):
            gid = int(guild.id)

            if gid not in _STARTED_GUILDS:
                await _start_guild_tracking(guild)
                continue

            await _refresh_scope_status(guild)

            try:
                await asyncio.to_thread(
                    _heartbeat_sync,
                    gid,
                )
                _LOCAL_ERRORS.pop(gid, None)
            except Exception as exc:
                await _mark_failure(
                    gid,
                    "heartbeat failed: "
                    f"{type(exc).__name__}: {str(exc)[:350]}",
                )


async def _on_ready(
    bot: discord.Client,
) -> None:
    global _HEARTBEAT_TASK

    for guild in list(
        getattr(bot, "guilds", []) or []
    ):
        await _start_guild_tracking(guild)

    if (
        _HEARTBEAT_TASK is None
        or _HEARTBEAT_TASK.done()
    ):
        _HEARTBEAT_TASK = asyncio.create_task(
            _heartbeat_loop(bot),
            name=(
                "authoritative_member_activity_heartbeat"
            ),
        )


async def _on_guild_join(
    guild: discord.Guild,
) -> None:
    await _start_guild_tracking(guild)


async def _on_message(message: discord.Message) -> None:
    guild = getattr(message, "guild", None)
    author = getattr(message, "author", None)

    if guild is None or author is None:
        return
    if bool(getattr(author, "bot", False)):
        return

    await record_direct_member_activity(
        guild_id=int(guild.id),
        user_id=int(author.id),
        activity_type="message",
        occurred_at=getattr(message, "created_at", None),
        channel_id=getattr(
            getattr(message, "channel", None),
            "id",
            None,
        ),
    )


async def _on_raw_reaction_add(
    bot: discord.Client,
    payload: discord.RawReactionActionEvent,
) -> None:
    if payload.guild_id is None:
        return

    if getattr(bot.user, "id", None) == payload.user_id:
        return

    member = getattr(payload, "member", None)

    if member is None:
        guild = bot.get_guild(int(payload.guild_id))
        member = (
            guild.get_member(int(payload.user_id))
            if guild is not None
            else None
        )

    if bool(getattr(member, "bot", False)):
        return

    await record_direct_member_activity(
        guild_id=int(payload.guild_id),
        user_id=int(payload.user_id),
        activity_type="reaction",
        occurred_at=_utcnow(),
        channel_id=int(payload.channel_id),
    )


async def _on_interaction(
    interaction: discord.Interaction,
) -> None:
    if interaction.guild_id is None:
        return

    user = getattr(interaction, "user", None)

    if user is None or bool(getattr(user, "bot", False)):
        return

    await record_direct_member_activity(
        guild_id=int(interaction.guild_id),
        user_id=int(user.id),
        activity_type="interaction",
        occurred_at=getattr(interaction, "created_at", None),
        channel_id=interaction.channel_id,
    )


def install_activity_tracker(bot: discord.Client) -> bool:
    global _INSTALLED

    if _INSTALLED:
        return True

    marker = "_dank_authoritative_activity_tracker_installed"

    if bool(getattr(bot, marker, False)):
        _INSTALLED = True
        return True

    async def ready_listener() -> None:
        await _on_ready(bot)

    async def raw_reaction_listener(
        payload: discord.RawReactionActionEvent,
    ) -> None:
        await _on_raw_reaction_add(bot, payload)

    bot.add_listener(ready_listener, "on_ready")
    bot.add_listener(_on_guild_join, "on_guild_join")
    bot.add_listener(_on_message, "on_message")
    bot.add_listener(
        raw_reaction_listener,
        "on_raw_reaction_add",
    )
    bot.add_listener(_on_interaction, "on_interaction")

    setattr(bot, marker, True)
    _INSTALLED = True

    print(
        "📡 authoritative member activity tracker installed; "
        "messages/interactions are cleanup evidence; "
        "reactions are supplemental only"
    )

    return True


__all__ = [
    "ActivityCoverageStatus",
    "evaluate_coverage_state",
    "get_activity_coverage_status",
    "install_activity_tracker",
    "record_direct_member_activity",
]
