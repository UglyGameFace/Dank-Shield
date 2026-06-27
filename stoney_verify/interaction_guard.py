from __future__ import annotations

"""Native interaction response guard for Dank Shield.

Discord interactions have one tight rule: acknowledge fast, then send exactly
one clean user-visible outcome. Public-production commands should not each
rebuild their own defer/follow-up/error handling.

This module is intentionally native and explicit:
- no monkey patches
- no command tree mutation
- no Discord channel/role/config/database mutation
- no silent exception swallowing
- structured error records for diagnostics
- compatible with future entitlement and feature-gate checks
"""

import asyncio
import hashlib
import logging
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Optional, TypeVar

import discord

T = TypeVar("T")
_LOG = logging.getLogger("dank_shield.interactions")
_RECENT_FAILURE_LIMIT = 250
_RECENT_FAILURES: list["InteractionFailureRecord"] = []
_ACTION_LOCKS: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True)
class InteractionContext:
    """Small, serializable context captured from one Discord interaction."""

    trace_id: str
    action_name: str = "unknown"
    guild_id: int = 0
    channel_id: int = 0
    user_id: int = 0
    message_id: int = 0
    custom_id: str = ""
    component_type: str = ""
    command_path: str = ""
    response_done: bool = False


@dataclass(frozen=True)
class InteractionFailureRecord:
    """Structured failure record safe for logs and diagnostics."""

    error_id: str
    context: InteractionContext
    stage: str
    error_type: str
    error_message: str
    fix_hint: str
    traceback_text: str = ""
    sent_to_user: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InteractionGuardResult:
    ok: bool
    error_id: str = ""
    error_type: str = ""
    error_message: str = ""
    sent_to_user: bool = False
    duplicate: bool = False


class DuplicateInteractionAction(RuntimeError):
    """Raised internally when a user double-clicks a locked action."""


class InteractionSendFailure(RuntimeError):
    """Raised internally when both initial response and follow-up send fail."""


# ---------------------------------------------------------------------------
# safe extraction helpers
# ---------------------------------------------------------------------------


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_text(value: Any, *, limit: int = 400) -> str:
    try:
        text = str(value or "").strip()
    except Exception:
        text = repr(value)
    if not text:
        return ""
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return text[: max(0, int(limit))]


def _safe_error_text(error: BaseException, *, limit: int = 300) -> str:
    try:
        text = str(error or "").strip() or repr(error)
    except Exception:
        text = repr(error)
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return text[: max(0, int(limit))]


def _response_done(interaction: Any) -> bool:
    try:
        return bool(interaction.response.is_done())
    except Exception:
        return False


def _interaction_data(interaction: Any) -> Mapping[str, Any]:
    try:
        data = getattr(interaction, "data", None) or {}
        return data if isinstance(data, Mapping) else {}
    except Exception:
        return {}


def _command_names_from_data(data: Mapping[str, Any]) -> list[str]:
    names: list[str] = []

    def walk(node: Any) -> None:
        if not isinstance(node, Mapping):
            return
        raw_name = node.get("name")
        if raw_name:
            names.append(str(raw_name))
        for child in node.get("options") or []:
            walk(child)

    try:
        walk(data)
    except Exception:
        return names
    return names


def _command_path(interaction: Any) -> str:
    data = _interaction_data(interaction)
    names = _command_names_from_data(data)
    if names:
        return "/".join(names)
    custom_id = _safe_text(data.get("custom_id"), limit=140)
    if custom_id:
        return f"component:{custom_id}"
    try:
        command = getattr(interaction, "command", None)
        qualified = getattr(command, "qualified_name", None) or getattr(command, "name", None)
        if qualified:
            return str(qualified)
    except Exception:
        pass
    return "unknown"


def _trace_id(interaction: Any | None = None) -> str:
    try:
        iid = _safe_int(getattr(interaction, "id", 0), 0)
        if iid:
            return str(iid)[-10:]
    except Exception:
        pass
    return str(int(time.time() * 1000))[-10:]


def interaction_context(interaction: Any, *, action_name: str | None = None) -> InteractionContext:
    data = _interaction_data(interaction)
    guild = getattr(interaction, "guild", None)
    channel = getattr(interaction, "channel", None)
    user = getattr(interaction, "user", None)
    message = getattr(interaction, "message", None)
    custom_id = _safe_text(data.get("custom_id"), limit=180)
    command_path = _command_path(interaction)

    return InteractionContext(
        trace_id=_trace_id(interaction),
        action_name=_safe_text(action_name or command_path or "unknown", limit=160) or "unknown",
        guild_id=_safe_int(getattr(guild, "id", 0), 0),
        channel_id=_safe_int(getattr(channel, "id", 0), 0),
        user_id=_safe_int(getattr(user, "id", 0), 0),
        message_id=_safe_int(getattr(message, "id", 0), 0),
        custom_id=custom_id,
        component_type=_safe_text(data.get("component_type"), limit=60),
        command_path=command_path,
        response_done=_response_done(interaction),
    )


def interaction_action_key(interaction: Any, *, action_name: str | None = None) -> str:
    """Return a guild/user/action key for duplicate-click protection."""

    ctx = interaction_context(interaction, action_name=action_name)
    action = ctx.custom_id or ctx.command_path or ctx.action_name or "unknown"
    return f"{ctx.guild_id}:{ctx.channel_id}:{ctx.user_id}:{action}"


# ---------------------------------------------------------------------------
# structured failure handling
# ---------------------------------------------------------------------------


def make_error_id(context: InteractionContext, error: BaseException, *, stage: str) -> str:
    raw = "|".join(
        [
            stage,
            context.action_name,
            context.command_path,
            context.custom_id,
            str(context.guild_id),
            str(context.channel_id),
            str(context.user_id),
            type(error).__name__,
            _safe_error_text(error, limit=180),
            str(int(time.time() // 60)),
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:8].upper()
    return f"DANK-{digest}"


def _store_failure(record: InteractionFailureRecord) -> None:
    _RECENT_FAILURES.append(record)
    while len(_RECENT_FAILURES) > _RECENT_FAILURE_LIMIT:
        _RECENT_FAILURES.pop(0)


def recent_interaction_failures(*, limit: int = 25) -> list[InteractionFailureRecord]:
    """Return recent native interaction failures for diagnostics/tests."""

    try:
        size = max(1, min(int(limit), _RECENT_FAILURE_LIMIT))
    except Exception:
        size = 25
    return list(_RECENT_FAILURES[-size:])


def clear_recent_interaction_failures() -> None:
    """Test/diagnostic helper to clear the in-memory failure ring."""

    _RECENT_FAILURES.clear()


def log_interaction_failure(
    interaction: Any,
    error: BaseException,
    *,
    stage: str,
    action_name: str | None = None,
    fix_hint: str = "Nothing was changed. Try again, then check `/dank diagnostics` if it keeps happening.",
    sent_to_user: bool = False,
    extra: Mapping[str, Any] | None = None,
) -> InteractionFailureRecord:
    ctx = interaction_context(interaction, action_name=action_name)
    traceback_text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    record = InteractionFailureRecord(
        error_id=make_error_id(ctx, error, stage=stage),
        context=ctx,
        stage=_safe_text(stage, limit=80) or "unknown",
        error_type=type(error).__name__,
        error_message=_safe_error_text(error),
        fix_hint=_safe_text(fix_hint, limit=500),
        traceback_text=traceback_text[-6000:],
        sent_to_user=bool(sent_to_user),
        extra=dict(extra or {}),
    )
    _store_failure(record)

    try:
        _LOG.error(
            "dank_interaction_failure error_id=%s stage=%s action=%s guild_id=%s channel_id=%s user_id=%s custom_id=%s error_type=%s error=%s sent_to_user=%s extra=%s",
            record.error_id,
            record.stage,
            record.context.action_name,
            record.context.guild_id,
            record.context.channel_id,
            record.context.user_id,
            record.context.custom_id,
            record.error_type,
            record.error_message,
            record.sent_to_user,
            dict(record.extra or {}),
            exc_info=error,
        )
    except Exception:
        # Last-resort fallback. Avoid raising while already handling an interaction failure.
        pass

    return record


def _error_embed(record: InteractionFailureRecord, *, title: str) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=(
            f"Dank Shield hit a safe-handled error.\n\n"
            f"**Error ID:** `{record.error_id}`\n"
            f"**Where:** `{record.context.action_name}`\n"
            f"**Problem:** `{record.error_type}: {record.error_message}`\n\n"
            f"**What to do:** {record.fix_hint}"
        )[:3900],
        color=discord.Color.red(),
    )
    return embed


# ---------------------------------------------------------------------------
# safe response helpers
# ---------------------------------------------------------------------------


async def safe_defer_interaction(
    interaction: discord.Interaction,
    *,
    ephemeral: bool = True,
    action_name: str | None = None,
) -> bool:
    """Acknowledge an interaction once and log failures with context."""

    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
            return True
    except Exception as exc:
        log_interaction_failure(
            interaction,
            exc,
            stage="defer_failed",
            action_name=action_name,
            fix_hint="Discord rejected the interaction acknowledgement. Reopen the panel and try again.",
        )
        return False
    return False


async def safe_send_interaction(
    interaction: discord.Interaction,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    ephemeral: bool = True,
    allowed_mentions: Optional[discord.AllowedMentions] = None,
    action_name: str | None = None,
    **kwargs: Any,
) -> bool:
    """Send a response/follow-up once and log send failures with context."""

    payload: dict[str, Any] = dict(kwargs)
    if content is not None:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    payload.setdefault("ephemeral", ephemeral)
    payload.setdefault("allowed_mentions", allowed_mentions or discord.AllowedMentions.none())

    first_error: BaseException | None = None

    try:
        if interaction.response.is_done():
            await interaction.followup.send(**payload)
        else:
            await interaction.response.send_message(**payload)
        return True
    except Exception as exc:
        first_error = exc

    try:
        await interaction.followup.send(**payload)
        return True
    except Exception as exc:
        combined = InteractionSendFailure(
            f"initial_send={type(first_error).__name__ if first_error else 'not_attempted'}; "
            f"followup_send={type(exc).__name__}: {_safe_error_text(exc, limit=180)}"
        )
        log_interaction_failure(
            interaction,
            combined,
            stage="send_failed",
            action_name=action_name,
            fix_hint="Dank Shield could not send a Discord response. Reopen the panel and try again; check bot permissions if this repeats.",
            extra={
                "initial_error": type(first_error).__name__ if first_error else "",
                "followup_error": type(exc).__name__,
            },
        )
        return False


async def safe_send_error(
    interaction: discord.Interaction,
    error: BaseException,
    *,
    title: str = "❌ Command failed safely",
    guidance: str = "Nothing was changed. Try again, then check `/dank diagnostics` if it keeps happening.",
    ephemeral: bool = True,
    action_name: str | None = None,
    stage: str = "callback_exception",
    record: InteractionFailureRecord | None = None,
) -> bool:
    """Send a clear, non-generic failure message for command exceptions."""

    failure = record or log_interaction_failure(
        interaction,
        error,
        stage=stage,
        action_name=action_name,
        fix_hint=guidance,
    )
    sent = await safe_send_interaction(
        interaction,
        embed=_error_embed(failure, title=title),
        ephemeral=ephemeral,
        action_name=action_name or failure.context.action_name,
    )
    if sent and not failure.sent_to_user:
        updated = InteractionFailureRecord(
            error_id=failure.error_id,
            context=failure.context,
            stage=failure.stage,
            error_type=failure.error_type,
            error_message=failure.error_message,
            fix_hint=failure.fix_hint,
            traceback_text=failure.traceback_text,
            sent_to_user=True,
            extra=failure.extra,
        )
        _store_failure(updated)
    return sent


async def run_guarded_interaction(
    interaction: discord.Interaction,
    action: Callable[[], Awaitable[T]],
    *,
    defer: bool = True,
    ephemeral: bool = True,
    action_name: str | None = None,
    lock_key: str | None = None,
    reject_duplicate: bool = True,
    duplicate_message: str = "⏳ That action is already running. Wait a moment, then refresh the panel if needed.",
    error_title: str = "❌ Command failed safely",
    error_guidance: str = "Nothing was changed. Try again, then check `/dank diagnostics` if it keeps happening.",
) -> InteractionGuardResult:
    """Run one command/component body behind a consistent native wrapper."""

    resolved_action = action_name or _command_path(interaction)
    key = lock_key or interaction_action_key(interaction, action_name=resolved_action)
    lock = _ACTION_LOCKS.setdefault(key, asyncio.Lock())

    if reject_duplicate and lock.locked():
        duplicate_error = DuplicateInteractionAction(f"duplicate interaction action key={key}")
        record = log_interaction_failure(
            interaction,
            duplicate_error,
            stage="duplicate_action",
            action_name=resolved_action,
            fix_hint="The first click is still being processed. Wait a moment, then press Refresh instead of clicking repeatedly.",
            extra={"lock_key": key},
        )
        sent = await safe_send_interaction(
            interaction,
            content=duplicate_message,
            ephemeral=True,
            action_name=resolved_action,
        )
        return InteractionGuardResult(
            ok=False,
            error_id=record.error_id,
            error_type=record.error_type,
            error_message=record.error_message,
            sent_to_user=sent,
            duplicate=True,
        )

    async with lock:
        if defer:
            await safe_defer_interaction(interaction, ephemeral=ephemeral, action_name=resolved_action)

        try:
            await action()
            return InteractionGuardResult(ok=True)
        except Exception as exc:
            record = log_interaction_failure(
                interaction,
                exc,
                stage="callback_exception",
                action_name=resolved_action,
                fix_hint=error_guidance,
            )
            sent = await safe_send_error(
                interaction,
                exc,
                title=error_title,
                guidance=error_guidance,
                ephemeral=ephemeral,
                action_name=resolved_action,
                record=record,
            )
            return InteractionGuardResult(
                ok=False,
                error_id=record.error_id,
                error_type=record.error_type,
                error_message=record.error_message,
                sent_to_user=sent,
            )


__all__ = [
    "DuplicateInteractionAction",
    "InteractionActionLocks",
    "InteractionContext",
    "InteractionFailureRecord",
    "InteractionGuardResult",
    "InteractionSendFailure",
    "clear_recent_interaction_failures",
    "interaction_action_key",
    "interaction_context",
    "log_interaction_failure",
    "make_error_id",
    "recent_interaction_failures",
    "run_guarded_interaction",
    "safe_defer_interaction",
    "safe_send_error",
    "safe_send_interaction",
]

# Backwards-compatible alias for older imports/docs that expected a service name.
InteractionActionLocks = _ACTION_LOCKS
