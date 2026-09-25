from __future__ import annotations

"""Native reconciliation for Discord invite messages missed by live enforcement.

Live deletion remains owned by the guaranteed listener installed from
``stoney_verify.globals``. This module owns only recovery: bounded history scans
after startup/resume and short delayed rescans after invite-related create/edit
events. Every delete still goes through ``invite_policy_engine``.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from stoney_verify import invite_policy_engine as policy
from stoney_verify.settings_registry import (
    INVITE_PROTECTED_POSTER_RULE_KEY,
    invite_shield_enabled as _registry_invite_shield_enabled,
    link_shield_enabled as _registry_link_shield_enabled,
    setting_bool as _registry_setting_bool,
)
from stoney_verify.startup_recovery_coordinator import startup_recovery_slot
from stoney_verify.startup_guards.discord_api_safety import (
    recovery_request_weight,
    reserve_recovery_discord_rest_requests,
)

_READY_DELAY_SECONDS = 3.0
_POLICY_RETRY_DELAY_SECONDS = 15.0
_GUILD_RECONCILE_COOLDOWN_SECONDS = 120.0
_CHANNEL_SWEEP_COOLDOWN_SECONDS = 8.0
_AUTO_HISTORY_LIMIT = 250
_EVENT_HISTORY_LIMIT = 75
_RECONCILE_CONCURRENCY = 2
_INVITE_CHECKPOINT_KEY = "invite_reconcile_checkpoint_at"
_INITIAL_BACKFILL_SECONDS = 24 * 60 * 60
_MAX_RECOVERY_GAP_SECONDS = 7 * 24 * 60 * 60

_LAST_GUILD_RECONCILE_AT: dict[int, float] = {}
_LAST_CHANNEL_SWEEP_AT: dict[tuple[int, int], float] = {}
_CHANNEL_SWEEP_TASKS: dict[tuple[int, int], asyncio.Task[Any]] = {}
_RECONCILE_TASK: asyncio.Task[Any] | None = None
_RAW_EDIT_TASKS: dict[tuple[int, int], asyncio.Task[Any]] = {}


async def _sleep(seconds: float) -> None:
    """Local sleep boundary so tests never monkeypatch asyncio globally."""

    await asyncio.sleep(seconds)


def _log(message: str) -> None:
    try:
        print(f"🧹 invite_reconcile {message}")
    except Exception:
        pass


def _config_source(cfg: Any) -> str:
    try:
        if hasattr(cfg, "get"):
            value = cfg.get("source")
            if value is not None:
                return str(value).strip().lower()
    except Exception:
        pass
    try:
        return str(getattr(cfg, "source", "") or "").strip().lower()
    except Exception:
        return ""


async def _guild_reconciliation_enabled(guild: Any) -> bool | None:
    """Return True/False for known policy state, or None when it is unavailable.

    This is only a work-avoidance preflight. The central policy engine still
    decides every individual message action. A transient/unavailable config must
    not be mistaken for a confirmed OFF state during a database outage.
    """

    try:
        cfg, settings = await policy.load_invite_policy(guild, refresh=True)
        settings = dict(settings or {})
    except Exception as exc:
        _log(
            f"policy_preflight_failed guild={getattr(guild, 'id', 0)} "
            f"error={type(exc).__name__}: {str(exc)[:160]}"
        )
        return None

    config_source = _config_source(cfg)
    if config_source.startswith("unavailable:"):
        _log(
            f"policy_preflight_unavailable guild={getattr(guild, 'id', 0)} "
            f"config_source={config_source} action=defer"
        )
        return None

    if cfg is None and not settings:
        _log(
            f"policy_preflight_unavailable guild={getattr(guild, 'id', 0)} "
            "action=defer"
        )
        return None

    invite_shield = _registry_invite_shield_enabled(cfg, settings)
    link_shield = _registry_link_shield_enabled(cfg, settings)
    protected_rule = _registry_setting_bool(
        settings,
        INVITE_PROTECTED_POSTER_RULE_KEY,
        False,
    )

    return bool(invite_shield or link_shield or protected_rule)


def _channel_can_reconcile(channel: Any, bot_member: Any) -> bool:
    if bot_member is None:
        return False
    try:
        perms = channel.permissions_for(bot_member)
        return bool(
            getattr(perms, "view_channel", getattr(perms, "read_messages", False))
            and getattr(perms, "read_message_history", False)
            and getattr(perms, "manage_messages", False)
        )
    except Exception:
        return False


async def _scan_channel(
    channel: Any,
    *,
    limit: int,
    source: str,
    after: datetime | None = None,
    before: datetime | None = None,
) -> dict[str, Any]:
    try:
        scan_kwargs: dict[str, Any] = {
            "limit": max(1, min(int(limit), _AUTO_HISTORY_LIMIT)),
            "repost_mixed": True,
            "source": source,
        }
        if after is not None:
            scan_kwargs["after"] = after
        if before is not None:
            scan_kwargs["before"] = before

        # Startup/resume recovery shares the same process-wide Discloud REST
        # budget as authoritative activity repair. Live event recovery stays on
        # Discord.py's normal route limiter so enforcement is not delayed by
        # background catch-up work.
        if str(source or "").startswith("auto-reconcile:"):
            await reserve_recovery_discord_rest_requests(
                recovery_request_weight(int(scan_kwargs["limit"])),
                label=(
                    "invite history "
                    f"guild={int(getattr(getattr(channel, 'guild', None), 'id', 0) or 0)} "
                    f"channel={int(getattr(channel, 'id', 0) or 0)}"
                ),
            )

        return dict(
            await policy.scan_channel_invites(
                channel,
                **scan_kwargs,
            )
            or {}
        )
    except Exception as exc:
        return {
            "checked": 0,
            "matched": 0,
            "allowed": 0,
            "deleted": 0,
            "failed": 1,
            "warning": f"{type(exc).__name__}: {str(exc)[:170]}",
        }


def _parse_checkpoint(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


async def _recovery_window(guild: Any) -> tuple[datetime, datetime] | None:
    """Use an invite-specific durable checkpoint, not the activity heartbeat.

    The previous implementation borrowed the activity tracker checkpoint. That
    allowed activity recovery to advance past invite messages that Invite Shield
    had never scanned, so old external-invite posts could survive every restart.
    """

    gid = int(getattr(guild, "id", 0) or 0)
    if gid <= 0:
        return None

    before = discord.utils.utcnow().astimezone(timezone.utc)
    after: datetime | None = None
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(gid, force_refresh=True)
        after = _parse_checkpoint(getattr(cfg, "get", lambda *_: None)(_INVITE_CHECKPOINT_KEY))
    except Exception as exc:
        _log(
            f"checkpoint_read_failed guild={gid} "
            f"error={type(exc).__name__}: {str(exc)[:160]}"
        )

    if after is None:
        after = before - timedelta(seconds=_INITIAL_BACKFILL_SECONDS)
        _log(
            f"checkpoint_bootstrap guild={gid} "
            f"lookback_seconds={_INITIAL_BACKFILL_SECONDS}"
        )

    gap_seconds = (before - after).total_seconds()
    if gap_seconds < 0:
        after = before - timedelta(seconds=_INITIAL_BACKFILL_SECONDS)
        _log(
            f"checkpoint_in_future guild={gid} "
            f"action=bootstrap lookback_seconds={_INITIAL_BACKFILL_SECONDS}"
        )
    elif gap_seconds > _MAX_RECOVERY_GAP_SECONDS:
        after = before - timedelta(seconds=_MAX_RECOVERY_GAP_SECONDS)
        _log(
            f"checkpoint_gap_capped guild={gid} "
            f"gap_seconds={int(gap_seconds)} max_gap_seconds={_MAX_RECOVERY_GAP_SECONDS}"
        )

    return after, before


async def _persist_recovery_checkpoint(guild_id: int, checkpoint: datetime) -> None:
    gid = int(guild_id)
    if gid <= 0:
        return
    try:
        from stoney_verify.guild_config import upsert_guild_config

        await upsert_guild_config(
            gid,
            {
                _INVITE_CHECKPOINT_KEY: checkpoint.astimezone(timezone.utc).isoformat(),
                "__config_write_mode": "runtime_discovery",
                "__config_write_source": "invite_reconciliation_runtime",
            },
        )
        _log(
            f"checkpoint_saved guild={gid} "
            f"at={checkpoint.astimezone(timezone.utc).isoformat()}"
        )
    except Exception as exc:
        _log(
            f"checkpoint_write_failed guild={gid} "
            f"error={type(exc).__name__}: {str(exc)[:160]}"
        )


def _channel_may_have_messages_after(channel: Any, after: datetime | None) -> bool:
    if after is None:
        return True
    try:
        message_id = int(getattr(channel, "last_message_id", 0) or 0)
    except Exception:
        return True
    if message_id <= 0:
        return False
    try:
        return discord.utils.snowflake_time(message_id) > after
    except Exception:
        return True


def _empty_totals() -> dict[str, int]:
    return {
        "channels": 0,
        "skipped_permission": 0,
        "skipped_inactive": 0,
        "checked": 0,
        "matched": 0,
        "allowed": 0,
        "deleted": 0,
        "failed": 0,
        "warnings": 0,
        "deferred": 0,
        "disabled": 0,
    }


async def _flush_bulk_recovery_stats(guild_id: int, *, reason: str) -> None:
    """Mirror one final durable total after an all-channel recovery scan.

    Individual recovery deletes remain durable immediately. The legacy visible
    compatibility counter is deliberately mirrored once at the end instead of
    after every deleted historical message.
    """

    gid = int(guild_id)
    if gid <= 0:
        return
    try:
        from stoney_verify import durable_invite_stats

        active_seeds = getattr(durable_invite_stats, "_BULK_RECOVERY_SEED", {})
        if gid not in active_seeds:
            return
        count = await durable_invite_stats.finish_bulk_recovery(gid)
        if count is None:
            _log(f"stats_flush_deferred guild={gid} reason={reason} durable_count=unavailable")
            return
        _log(f"stats_flush guild={gid} reason={reason} durable_count={int(count)}")
    except Exception as exc:
        _log(
            f"stats_flush_failed guild={gid} reason={reason} "
            f"error={type(exc).__name__}: {str(exc)[:170]}"
        )


async def _reconcile_guild(
    guild: Any,
    *,
    reason: str,
    force: bool = False,
    after: datetime | None = None,
    before: datetime | None = None,
) -> dict[str, int]:
    gid = int(getattr(guild, "id", 0) or 0)
    totals = _empty_totals()
    if gid <= 0:
        return totals

    now = time.monotonic()
    previous = float(_LAST_GUILD_RECONCILE_AT.get(gid, 0.0) or 0.0)
    if not force and previous and now - previous < _GUILD_RECONCILE_COOLDOWN_SECONDS:
        return totals

    enabled = await _guild_reconciliation_enabled(guild)
    if enabled is None:
        totals["deferred"] = 1
        _log(f"deferred guild={gid} reason={reason} policy=unavailable")
        return totals
    if not enabled:
        totals["disabled"] = 1
        _LAST_GUILD_RECONCILE_AT[gid] = now
        _log(f"skipped guild={gid} reason={reason} delete_path=disabled")
        return totals

    bot_member = getattr(guild, "me", None)
    channels = list(getattr(guild, "text_channels", []) or [])

    async def run(channel: Any) -> tuple[str, dict[str, Any]]:
        if not _channel_can_reconcile(channel, bot_member):
            return "permission", {}
        if not _channel_may_have_messages_after(channel, after):
            return "inactive", {}
        result = await _scan_channel(
            channel,
            limit=_AUTO_HISTORY_LIMIT,
            source=f"auto-reconcile:{reason}",
            after=after,
            before=before,
        )
        return "scanned", result

    async with startup_recovery_slot(gid, f"invite_reconcile:{reason}"):
        for start in range(0, len(channels), _RECONCILE_CONCURRENCY):
            batch = channels[start : start + _RECONCILE_CONCURRENCY]
            results = await asyncio.gather(*(run(channel) for channel in batch))
            for disposition, result in results:
                if disposition == "permission":
                    totals["skipped_permission"] += 1
                    continue
                if disposition == "inactive":
                    totals["skipped_inactive"] += 1
                    continue
                totals["channels"] += 1
                for key in ("checked", "matched", "allowed", "deleted", "failed"):
                    totals[key] += int(result.get(key) or 0)
                warning = str(result.get("warning") or "").strip()
                if warning:
                    totals["warnings"] += 1
                    _log(
                        f"channel_warning guild={gid} reason={reason} "
                        f"warning={warning[:220]}"
                    )

        if totals["deleted"] > 0:
            await _flush_bulk_recovery_stats(gid, reason=reason)

    _LAST_GUILD_RECONCILE_AT[gid] = time.monotonic()
    _log(
        f"guild={gid} reason={reason} channels={totals['channels']} "
        f"skipped_permission={totals['skipped_permission']} "
        f"skipped_inactive={totals['skipped_inactive']} checked={totals['checked']} "
        f"matched={totals['matched']} allowed={totals['allowed']} "
        f"deleted={totals['deleted']} failed={totals['failed']} warnings={totals['warnings']}"
    )
    return totals


async def _reconcile_all(bot: Any, *, reason: str) -> None:
    global _RECONCILE_TASK
    try:
        if reason == "ready":
            await _sleep(_READY_DELAY_SECONDS)

        deferred: list[tuple[Any, datetime, datetime]] = []
        for guild in list(getattr(bot, "guilds", []) or []):
            try:
                window = await _recovery_window(guild)
                if window is None:
                    continue
                after, before = window
                result = await _reconcile_guild(
                    guild,
                    reason=reason,
                    after=after,
                    before=before,
                )
                if int(result.get("deferred") or 0):
                    deferred.append((guild, after, before))
                elif not int(result.get("disabled") or 0):
                    await _persist_recovery_checkpoint(int(getattr(guild, "id", 0) or 0), before)
            except Exception as exc:
                _log(
                    f"guild_failed guild={getattr(guild, 'id', 0)} reason={reason} "
                    f"error={type(exc).__name__}: {str(exc)[:170]}"
                )

        if deferred:
            await _sleep(_POLICY_RETRY_DELAY_SECONDS)
            for guild, after, before in deferred:
                try:
                    retry_result = await _reconcile_guild(
                        guild,
                        reason=f"{reason}-policy-retry",
                        force=True,
                        after=after,
                        before=before,
                    )
                    if (
                        not int(retry_result.get("deferred") or 0)
                        and not int(retry_result.get("disabled") or 0)
                    ):
                        await _persist_recovery_checkpoint(
                            int(getattr(guild, "id", 0) or 0),
                            before,
                        )
                except Exception as exc:
                    _log(
                        f"guild_retry_failed guild={getattr(guild, 'id', 0)} reason={reason} "
                        f"error={type(exc).__name__}: {str(exc)[:170]}"
                    )
    finally:
        _RECONCILE_TASK = None


def _schedule_all(bot: Any, *, reason: str) -> None:
    global _RECONCILE_TASK
    try:
        if _RECONCILE_TASK is not None and not _RECONCILE_TASK.done():
            return
        loop = asyncio.get_running_loop()
        _RECONCILE_TASK = loop.create_task(
            _reconcile_all(bot, reason=reason),
            name=f"dank-invite-reconcile-{reason}",
        )
    except Exception as exc:
        _log(f"schedule_all_failed reason={reason} error={type(exc).__name__}: {exc}")


async def _sweep_channel(channel: Any, *, reason: str) -> None:
    try:
        guild = getattr(channel, "guild", None)
        gid = int(getattr(guild, "id", 0) or 0)
        cid = int(getattr(channel, "id", 0) or 0)
        if gid <= 0 or cid <= 0:
            return
        key = (gid, cid)
        now = time.monotonic()
        previous = float(_LAST_CHANNEL_SWEEP_AT.get(key, 0.0) or 0.0)
        if previous and now - previous < _CHANNEL_SWEEP_COOLDOWN_SECONDS:
            return
        _LAST_CHANNEL_SWEEP_AT[key] = now

        bot_member = getattr(guild, "me", None)
        if not _channel_can_reconcile(channel, bot_member):
            return
        if await _guild_reconciliation_enabled(guild) is not True:
            return

        result = await _scan_channel(
            channel,
            limit=_EVENT_HISTORY_LIMIT,
            source=f"live-recovery:{reason}",
        )
        matched = int(result.get("matched") or 0)
        allowed = int(result.get("allowed") or 0)
        deleted = int(result.get("deleted") or 0)
        failed = int(result.get("failed") or 0)
        warning = str(result.get("warning") or "").strip()
        if matched or allowed or deleted or failed or warning:
            warning_text = f" warning={warning[:180]!r}" if warning else ""
            _log(
                f"channel={cid} guild={gid} reason={reason} "
                f"checked={int(result.get('checked') or 0)} matched={matched} "
                f"allowed={allowed} deleted={deleted} failed={failed}{warning_text}"
            )
    finally:
        try:
            guild = getattr(channel, "guild", None)
            key = (int(getattr(guild, "id", 0) or 0), int(getattr(channel, "id", 0) or 0))
            _CHANNEL_SWEEP_TASKS.pop(key, None)
        except Exception:
            pass


async def _delayed_channel_sweep(channel: Any, *, reason: str) -> None:
    await _sleep(1.5)
    await _sweep_channel(channel, reason=reason)
    await _sleep(8.5)
    await _sweep_channel(channel, reason=f"{reason}-second-pass")


def _schedule_channel_sweep(channel: Any, *, reason: str) -> None:
    try:
        guild = getattr(channel, "guild", None)
        gid = int(getattr(guild, "id", 0) or 0)
        cid = int(getattr(channel, "id", 0) or 0)
        if gid <= 0 or cid <= 0:
            return
        key = (gid, cid)
        existing = _CHANNEL_SWEEP_TASKS.get(key)
        if existing is not None and not existing.done():
            return
        loop = asyncio.get_running_loop()
        _CHANNEL_SWEEP_TASKS[key] = loop.create_task(
            _delayed_channel_sweep(channel, reason=reason),
            name=f"dank-invite-channel-recovery-{gid}-{cid}",
        )
    except Exception as exc:
        _log(f"schedule_channel_failed reason={reason} error={type(exc).__name__}: {exc}")


def _looks_invite_related(message: Any) -> bool:
    try:
        return bool(
            policy.extract_invite_codes_from_message(message)
            or policy.is_contentless_protected_poster_candidate(message)
        )
    except Exception:
        return False


async def _recovery_message_listener(message: discord.Message) -> None:
    try:
        if getattr(message, "guild", None) is None:
            return
        if _looks_invite_related(message):
            _schedule_channel_sweep(getattr(message, "channel", None), reason="create")
    except Exception as exc:
        _log(f"message_trigger_failed error={type(exc).__name__}: {str(exc)[:150]}")


async def _recovery_edit_listener(before: discord.Message, after: discord.Message) -> None:
    _ = before
    try:
        if getattr(after, "guild", None) is None:
            return
        if _looks_invite_related(after):
            _schedule_channel_sweep(getattr(after, "channel", None), reason="edit")
    except Exception as exc:
        _log(f"edit_trigger_failed error={type(exc).__name__}: {str(exc)[:150]}")


async def _raw_message_edit_worker(
    *,
    guild_id: int,
    channel_id: int,
    message_id: int,
) -> None:
    key = (channel_id, message_id)
    try:
        await _sleep(0.75)
        from stoney_verify.globals import bot

        guild = bot.get_guild(int(guild_id))
        channel = bot.get_channel(int(channel_id))
        if guild is None or not isinstance(channel, discord.TextChannel):
            return
        if await _guild_reconciliation_enabled(guild) is not True:
            return
        me = getattr(guild, "me", None)
        if me is None:
            return
        try:
            perms = channel.permissions_for(me)
            if not bool(getattr(perms, "view_channel", False)):
                return
            if not bool(getattr(perms, "read_message_history", False)):
                return
        except Exception:
            return

        try:
            # This is live recovery, not startup backfill. Let discord.py own
            # the route-aware REST limiter so a startup-history budget cannot
            # delay enforcement of a just-edited message.
            message = await channel.fetch_message(int(message_id))
        except (discord.NotFound, discord.Forbidden):
            return

        decision = await policy.enforce_live_invite_message(
            message,
            source="raw_message_edit_recovery",
            refresh_policy=True,
        )
        if decision is not None:
            _log(
                f"raw_edit guild={guild_id} channel={channel_id} message={message_id} "
                f"rule={decision.rule_id} action={decision.action} "
                f"deleted={bool(decision.delete_succeeded)}"
            )
    except Exception as exc:
        _log(
            f"raw_edit_failed guild={guild_id} channel={channel_id} message={message_id} "
            f"error={type(exc).__name__}: {str(exc)[:160]}"
        )
    finally:
        _RAW_EDIT_TASKS.pop(key, None)


async def _raw_message_edit_listener(payload: discord.RawMessageUpdateEvent) -> None:
    try:
        if getattr(payload, "cached_message", None) is not None:
            return
        data = dict(getattr(payload, "data", {}) or {})
        if not any(
            key in data
            for key in ("content", "embeds", "components", "attachments", "poll")
        ):
            return
        guild_id = int(getattr(payload, "guild_id", 0) or 0)
        channel_id = int(getattr(payload, "channel_id", 0) or 0)
        message_id = int(getattr(payload, "message_id", 0) or 0)
        if guild_id <= 0 or channel_id <= 0 or message_id <= 0:
            return
        key = (channel_id, message_id)
        existing = _RAW_EDIT_TASKS.get(key)
        if existing is not None and not existing.done():
            return
        loop = asyncio.get_running_loop()
        _RAW_EDIT_TASKS[key] = loop.create_task(
            _raw_message_edit_worker(
                guild_id=guild_id,
                channel_id=channel_id,
                message_id=message_id,
            ),
            name=f"dank-invite-raw-edit-{guild_id}-{channel_id}-{message_id}",
        )
    except Exception as exc:
        _log(f"raw_edit_trigger_failed error={type(exc).__name__}: {str(exc)[:150]}")


async def _ready_listener() -> None:
    from stoney_verify.globals import bot

    _schedule_all(bot, reason="ready")


async def _resumed_listener() -> None:
    from stoney_verify.globals import bot

    _schedule_all(bot, reason="resumed")


def _has_listener(bot: Any, event: str, function: Any) -> bool:
    try:
        listeners = list((getattr(bot, "extra_events", {}) or {}).get(event) or [])
        return any(
            getattr(item, "__name__", "") == getattr(function, "__name__", "")
            and getattr(item, "__module__", "") == getattr(function, "__module__", "")
            for item in listeners
        )
    except Exception:
        return False


def install_invite_reconciliation(bot: Any) -> bool:
    """Attach bounded recovery listeners to the real bot exactly once."""

    marker = "_dank_invite_reconciliation_runtime_installed"
    if bool(getattr(bot, marker, False)):
        return True

    try:
        bindings = (
            ("on_message", _recovery_message_listener),
            ("on_message_edit", _recovery_edit_listener),
            ("on_raw_message_edit", _raw_message_edit_listener),
            ("on_ready", _ready_listener),
            ("on_resumed", _resumed_listener),
        )
        for event, function in bindings:
            if not _has_listener(bot, event, function):
                bot.add_listener(function, event)

        setattr(bot, marker, True)
        _log(
            "active; ready/resume use an invite-specific durable checkpoint, "
            f"scan up to {_AUTO_HISTORY_LIMIT} messages per readable channel, "
            f"live invite events rescan {_EVENT_HISTORY_LIMIT} recent messages, "
            "and uncached raw edits are fetched directly"
        )
        return True
    except Exception as exc:
        _log(f"install_failed error={type(exc).__name__}: {str(exc)[:180]}")
        return False


__all__ = [
    "install_invite_reconciliation",
    "_guild_reconciliation_enabled",
    "_reconcile_guild",
    "_schedule_channel_sweep",
]
