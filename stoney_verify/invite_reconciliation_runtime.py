from __future__ import annotations

"""Native reconciliation for Discord invite messages missed by live enforcement.

Live deletion remains owned by the guaranteed listener installed from
``stoney_verify.globals``. This module owns only recovery: bounded history scans
after startup/resume and short delayed rescans after invite-related create/edit
events. Every delete still goes through ``invite_policy_engine``.
"""

import asyncio
import time
from typing import Any

import discord

from stoney_verify import invite_policy_engine as policy

_READY_DELAY_SECONDS = 3.0
_GUILD_RECONCILE_COOLDOWN_SECONDS = 120.0
_CHANNEL_SWEEP_COOLDOWN_SECONDS = 8.0
_AUTO_HISTORY_LIMIT = 250
_EVENT_HISTORY_LIMIT = 75
_RECONCILE_CONCURRENCY = 2

_LAST_GUILD_RECONCILE_AT: dict[int, float] = {}
_LAST_CHANNEL_SWEEP_AT: dict[tuple[int, int], float] = {}
_CHANNEL_SWEEP_TASKS: dict[tuple[int, int], asyncio.Task[Any]] = {}
_RECONCILE_TASK: asyncio.Task[Any] | None = None


def _log(message: str) -> None:
    try:
        print(f"🧹 invite_reconcile {message}")
    except Exception:
        pass


def _setting_enabled(settings: dict[str, Any], key: str, default: bool = False) -> bool:
    try:
        return bool(policy._setting_bool(settings, key, default))  # type: ignore[attr-defined]
    except Exception:
        value = settings.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _cfg_enabled(cfg: Any, key: str, default: bool = False) -> bool:
    try:
        return bool(policy._cfg_bool(cfg, key, default))  # type: ignore[attr-defined]
    except Exception:
        value = getattr(cfg, key, default)
        if hasattr(cfg, "get"):
            try:
                value = cfg.get(key, value)
            except Exception:
                pass
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


async def _guild_reconciliation_enabled(guild: Any) -> bool:
    """Return whether a historical scan can currently lead to an invite delete.

    This is only a work-avoidance preflight. The central policy engine still
    decides every individual message action.
    """

    try:
        cfg, settings = await policy.load_invite_policy(guild, refresh=True)
        settings = dict(settings or {})
    except Exception as exc:
        _log(
            f"policy_preflight_failed guild={getattr(guild, 'id', 0)} "
            f"error={type(exc).__name__}: {str(exc)[:160]}"
        )
        return False

    invite_shield = (
        _cfg_enabled(cfg, "automod_block_invites")
        or _setting_enabled(settings, "invite_shield_enabled")
        or _setting_enabled(settings, "invite_hard_block_enabled")
        or _setting_enabled(settings, "automod_block_invites")
        or _setting_enabled(settings, "block_invites")
    )
    link_shield = (
        _cfg_enabled(cfg, "automod_block_links")
        or _setting_enabled(settings, "automod_block_links")
    )

    protected_rule = False
    try:
        protected_rule = bool(policy._protected_poster_rule_enabled(settings))  # type: ignore[attr-defined]
    except Exception:
        protected_rule = any(
            _setting_enabled(settings, key)
            for key in (
                "invite_protected_poster_rule_enabled",
                "protected_poster_invite_rule_enabled",
                "invite_hard_block_protected_posters_enabled",
            )
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


async def _scan_channel(channel: Any, *, limit: int, source: str) -> dict[str, Any]:
    try:
        return dict(
            await policy.scan_channel_invites(
                channel,
                limit=max(1, min(int(limit), _AUTO_HISTORY_LIMIT)),
                repost_mixed=True,
                source=source,
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


async def _reconcile_guild(guild: Any, *, reason: str, force: bool = False) -> dict[str, int]:
    gid = int(getattr(guild, "id", 0) or 0)
    totals = {
        "channels": 0,
        "skipped_permission": 0,
        "checked": 0,
        "matched": 0,
        "allowed": 0,
        "deleted": 0,
        "failed": 0,
    }
    if gid <= 0:
        return totals

    now = time.monotonic()
    previous = float(_LAST_GUILD_RECONCILE_AT.get(gid, 0.0) or 0.0)
    if not force and previous and now - previous < _GUILD_RECONCILE_COOLDOWN_SECONDS:
        return totals

    if not await _guild_reconciliation_enabled(guild):
        _LAST_GUILD_RECONCILE_AT[gid] = now
        _log(f"skipped guild={gid} reason={reason} delete_path=disabled")
        return totals

    bot_member = getattr(guild, "me", None)
    channels = list(getattr(guild, "text_channels", []) or [])
    semaphore = asyncio.Semaphore(_RECONCILE_CONCURRENCY)

    async def run(channel: Any) -> tuple[bool, dict[str, Any]]:
        if not _channel_can_reconcile(channel, bot_member):
            return False, {}
        async with semaphore:
            result = await _scan_channel(
                channel,
                limit=_AUTO_HISTORY_LIMIT,
                source=f"auto-reconcile:{reason}",
            )
            return True, result

    results = await asyncio.gather(*(run(channel) for channel in channels))
    for eligible, result in results:
        if not eligible:
            totals["skipped_permission"] += 1
            continue
        totals["channels"] += 1
        for key in ("checked", "matched", "allowed", "deleted", "failed"):
            totals[key] += int(result.get(key) or 0)

    _LAST_GUILD_RECONCILE_AT[gid] = time.monotonic()
    _log(
        f"guild={gid} reason={reason} channels={totals['channels']} "
        f"skipped_permission={totals['skipped_permission']} checked={totals['checked']} "
        f"matched={totals['matched']} allowed={totals['allowed']} "
        f"deleted={totals['deleted']} failed={totals['failed']}"
    )
    return totals


async def _reconcile_all(bot: Any, *, reason: str) -> None:
    global _RECONCILE_TASK
    try:
        if reason == "ready":
            await asyncio.sleep(_READY_DELAY_SECONDS)
        for guild in list(getattr(bot, "guilds", []) or []):
            try:
                await _reconcile_guild(guild, reason=reason)
            except Exception as exc:
                _log(
                    f"guild_failed guild={getattr(guild, 'id', 0)} reason={reason} "
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
        if not await _guild_reconciliation_enabled(guild):
            return

        result = await _scan_channel(
            channel,
            limit=_EVENT_HISTORY_LIMIT,
            source=f"live-recovery:{reason}",
        )
        matched = int(result.get("matched") or 0)
        deleted = int(result.get("deleted") or 0)
        failed = int(result.get("failed") or 0)
        if matched or deleted or failed:
            _log(
                f"channel={cid} guild={gid} reason={reason} "
                f"checked={int(result.get('checked') or 0)} matched={matched} "
                f"deleted={deleted} failed={failed}"
            )
    finally:
        try:
            guild = getattr(channel, "guild", None)
            key = (int(getattr(guild, "id", 0) or 0), int(getattr(channel, "id", 0) or 0))
            _CHANNEL_SWEEP_TASKS.pop(key, None)
        except Exception:
            pass


async def _delayed_channel_sweep(channel: Any, *, reason: str) -> None:
    await asyncio.sleep(1.5)
    await _sweep_channel(channel, reason=reason)
    await asyncio.sleep(8.5)
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
        return bool(policy.extract_invite_codes_from_message(message))
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
            ("on_ready", _ready_listener),
            ("on_resumed", _resumed_listener),
        )
        for event, function in bindings:
            if not _has_listener(bot, event, function):
                bot.add_listener(function, event)

        setattr(bot, marker, True)
        _log(
            "active; ready/resume scans up to "
            f"{_AUTO_HISTORY_LIMIT} messages per readable channel and live invite events "
            f"rescan {_EVENT_HISTORY_LIMIT} recent messages"
        )
        return True
    except Exception as exc:
        _log(f"install_failed error={type(exc).__name__}: {str(exc)[:180]}")
        return False


__all__ = [
    "install_invite_reconciliation",
    "_guild_reconcile_enabled",
    "_reconcile_guild",
    "_schedule_channel_sweep",
]
