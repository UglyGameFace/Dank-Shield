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

from .panel_lifecycle import PRIVATE_MENU_RECOVERY_GRACE_SECONDS
from .runtime_release import runtime_release_label

T = TypeVar("T")
_LOG = logging.getLogger("dank_shield.interactions")
_RECENT_FAILURE_LIMIT = 250
_RECENT_FAILURES: list["InteractionFailureRecord"] = []
_ACTION_LOCKS: dict[str, asyncio.Lock] = {}
_COMPONENT_OBSERVER_INSTALLED = False
_COMPONENT_OBSERVER_READY_LOGGED = False
_COMPONENT_OBSERVER_GRACE_SECONDS = 2.5
_COMPONENT_OBSERVER_PROBE_WINDOW_SECONDS = 60.0
_COMPONENT_OBSERVER_PROBE_LIMIT = 60
_COMPONENT_OBSERVER_PROBE_TIMES: list[float] = []
_COMPONENT_OBSERVER_LOG_WINDOW_SECONDS = 60.0
_COMPONENT_OBSERVER_LOG_LIMIT = 20
_COMPONENT_OBSERVER_LOG_TIMES: list[float] = []
_COMPONENT_INGRESS_COUNT = 0
_COMPONENT_RECOVERY_COUNT = 0
_COMPONENT_UNACKNOWLEDGED_COUNT = 0
_COMPONENT_LAST_INGRESS: dict[str, Any] = {}
_VIEW_STORE_LAYOUT_DISCORD_VERSION = "2.7.1"


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


def _latest_interaction_failure(
    interaction: Any,
    *,
    stage: str,
) -> InteractionFailureRecord | None:
    trace_id = _trace_id(interaction)
    for record in reversed(_RECENT_FAILURES):
        if record.stage == stage and record.context.trace_id == trace_id:
            return record
    return None


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
        # A previous canonical step may already have acknowledged the same
        # interaction (for example a component edit before a guarded mutation).
        # That is a valid claim, not a defer failure.
        if interaction.response.is_done():
            return True
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


def _observer_probe_allowed() -> bool:
    now = time.monotonic()
    cutoff = now - _COMPONENT_OBSERVER_PROBE_WINDOW_SECONDS
    while _COMPONENT_OBSERVER_PROBE_TIMES and _COMPONENT_OBSERVER_PROBE_TIMES[0] <= cutoff:
        _COMPONENT_OBSERVER_PROBE_TIMES.pop(0)
    if len(_COMPONENT_OBSERVER_PROBE_TIMES) >= _COMPONENT_OBSERVER_PROBE_LIMIT:
        return False
    _COMPONENT_OBSERVER_PROBE_TIMES.append(now)
    return True


def _observer_log_allowed() -> bool:
    now = time.monotonic()
    cutoff = now - _COMPONENT_OBSERVER_LOG_WINDOW_SECONDS
    while _COMPONENT_OBSERVER_LOG_TIMES and _COMPONENT_OBSERVER_LOG_TIMES[0] <= cutoff:
        _COMPONENT_OBSERVER_LOG_TIMES.pop(0)
    if len(_COMPONENT_OBSERVER_LOG_TIMES) >= _COMPONENT_OBSERVER_LOG_LIMIT:
        return False
    _COMPONENT_OBSERVER_LOG_TIMES.append(now)
    return True


def _persistent_view_snapshot(bot: Any) -> tuple[int, str]:
    try:
        views = list(getattr(bot, "persistent_views", None) or [])
    except Exception:
        views = []
    names = sorted({type(view).__name__ for view in views if view is not None})
    return len(views), ",".join(names[:24])


def _component_store_key(interaction: Any) -> tuple[int, str]:
    data = _interaction_data(interaction)
    return (
        _safe_int(data.get("component_type"), 0),
        _safe_text(data.get("custom_id"), limit=180),
    )


def _view_store_component_owner_state(bot: Any, interaction: Any) -> bool | None:
    """Return True/False only when the pinned ViewStore contract is known.

    discord.py 2.7.1 resolves message-specific ownership first, then a global
    persistent view registered under the None message key, while dynamic items
    are dispatched independently before that lookup. Reading the store mirrors
    that contract without dispatching anything. Unknown library/store layouts
    return None so stale recovery fails closed instead of stealing a live click.
    """
    if str(getattr(discord, "__version__", "") or "") != _VIEW_STORE_LAYOUT_DISCORD_VERSION:
        return None
    component_type, custom_id = _component_store_key(interaction)
    if component_type <= 0 or not custom_id:
        return None
    try:
        state = getattr(bot, "_connection", None)
        store = getattr(state, "_view_store", None)
        views = getattr(store, "_views", None)
        dynamic = getattr(store, "_dynamic_items", None)
        if not isinstance(views, dict) or not isinstance(dynamic, dict):
            return None
        message_id = _safe_int(
            getattr(getattr(interaction, "message", None), "id", 0),
            0,
        )
        key = (component_type, custom_id)
        if message_id > 0 and key in (views.get(message_id, {}) or {}):
            return True
        if key in (views.get(None, {}) or {}):
            return True
        for pattern in dynamic.keys():
            try:
                if pattern.fullmatch(custom_id) is not None:
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return None


def _view_store_has_component_owner(bot: Any, interaction: Any) -> bool:
    """Compatibility helper used by diagnostics/tests."""
    return _view_store_component_owner_state(bot, interaction) is True


def _record_component_ingress(bot: Any, interaction: Any) -> None:
    global _COMPONENT_INGRESS_COUNT
    global _COMPONENT_LAST_INGRESS

    try:
        if interaction.type is not discord.InteractionType.component:
            return
    except Exception:
        return

    _COMPONENT_INGRESS_COUNT += 1
    ctx = interaction_context(interaction, action_name="component_ingress")
    _COMPONENT_LAST_INGRESS = {
        "trace_id": ctx.trace_id,
        "guild_id": ctx.guild_id,
        "channel_id": ctx.channel_id,
        "user_id": ctx.user_id,
        "message_id": ctx.message_id,
        "custom_id": ctx.custom_id,
        "owner_state": _view_store_component_owner_state(bot, interaction),
        "ephemeral": _message_is_ephemeral(interaction),
        "response_done": _response_done(interaction),
        "interaction_age_ms": _interaction_age_ms(interaction),
    }


def component_runtime_status(bot: Any = None) -> dict[str, Any]:
    count = 0
    names = ""
    if bot is not None:
        count, names = _persistent_view_snapshot(bot)
    return {
        "installed": bool(_COMPONENT_OBSERVER_INSTALLED),
        "release": runtime_release_label(),
        "ingress_count": int(_COMPONENT_INGRESS_COUNT),
        "recovery_count": int(_COMPONENT_RECOVERY_COUNT),
        "unacknowledged_count": int(_COMPONENT_UNACKNOWLEDGED_COUNT),
        "persistent_view_count": int(count),
        "persistent_view_types": names,
        "last_ingress": dict(_COMPONENT_LAST_INGRESS),
    }


def _message_is_ephemeral(interaction: Any) -> bool:
    try:
        flags = getattr(getattr(interaction, "message", None), "flags", None)
        return bool(getattr(flags, "ephemeral", False))
    except Exception:
        return False


async def _recover_unowned_private_component(
    bot: Any,
    interaction: discord.Interaction,
) -> bool:
    """Recover a private component only when discord.py has no ViewStore owner.

    This is not a second business handler. It never executes the stale action.
    It only replaces a dead private menu with the canonical Control Center after
    discord.py has already failed to find a message, persistent, or dynamic view
    owner and existing additive listeners have had a short grace period.
    """
    try:
        if interaction.type is not discord.InteractionType.component:
            return False
        if _response_done(interaction):
            return False
        if not _message_is_ephemeral(interaction):
            return False
        owner_state = _view_store_component_owner_state(bot, interaction)
        if owner_state is not False:
            return False

        await asyncio.sleep(PRIVATE_MENU_RECOVERY_GRACE_SECONDS)
        if _response_done(interaction):
            return False
        owner_state = _view_store_component_owner_state(bot, interaction)
        if owner_state is not False:
            return False

        # Atomically claim the interaction by replacing the stale ephemeral
        # message itself. discord.py stores the replacement view under the same
        # message ID as part of InteractionResponse.edit_message(), so recovery
        # both removes the dead controls and immediately restores ViewStore
        # ownership. If another listener wins first, this response raises and
        # recovery exits without a competing message.
        from .commands_ext.public_command_surface_v2 import (
            replace_with_compact_dank_home,
        )

        try:
            await replace_with_compact_dank_home(
                interaction,
                content=(
                    "♻️ That private Dank Shield menu expired or belonged to an older bot session. "
                    "I refreshed the Control Center in place; the stale action was not executed."
                ),
            )
        except Exception:
            if _response_done(interaction):
                return False
            raise
        recovered = True
        if recovered:
            global _COMPONENT_RECOVERY_COUNT
            _COMPONENT_RECOVERY_COUNT += 1
            ctx = interaction_context(interaction, action_name="private_menu_stale_recovery")
            print(
                "♻️ component_runtime recovered stale private menu "
                f"interaction={getattr(interaction, 'id', 0)} "
                f"guild={ctx.guild_id} user={ctx.user_id} "
                f"message={ctx.message_id} custom_id={ctx.custom_id!r}"
            )
        return recovered
    except Exception as exc:
        if _observer_log_allowed():
            print(
                "⚠️ component_runtime private-menu recovery failed "
                f"error={type(exc).__name__}: {_safe_error_text(exc)}"
            )
        return False


def _interaction_age_ms(interaction: Any) -> int:
    try:
        created_at = getattr(interaction, "created_at", None)
        if created_at is None:
            return -1
        age = (discord.utils.utcnow() - created_at).total_seconds() * 1000.0
        return max(0, int(round(age)))
    except Exception:
        return -1


async def _observe_component_ack(bot: Any, interaction: discord.Interaction) -> None:
    """Record component clicks that reach this process but remain unanswered.

    This observer is deliberately passive. It never acknowledges an interaction,
    dispatches a callback, or mutates feature state. Its only purpose is to tell
    production logs whether Discord delivered a click to the current process and
    whether native ViewStore/business handling acknowledged it within the normal
    component window.
    """
    try:
        if interaction.type is not discord.InteractionType.component:
            return
        if _response_done(interaction):
            return
        if not _observer_probe_allowed():
            return
        await asyncio.sleep(_COMPONENT_OBSERVER_GRACE_SECONDS)
        if _response_done(interaction):
            return

        message = getattr(interaction, "message", None)
        author = getattr(message, "author", None)
        bot_user = getattr(bot, "user", None)
        message_author_id = _safe_int(getattr(author, "id", 0), 0)
        bot_user_id = _safe_int(getattr(bot_user, "id", 0), 0)
        application_id = _safe_int(getattr(interaction, "application_id", 0), 0)
        persistent_count, persistent_names = _persistent_view_snapshot(bot)
        extra = {
            "interaction_age_ms": _interaction_age_ms(interaction),
            "message_author_id": message_author_id,
            "bot_user_id": bot_user_id,
            "interaction_application_id": application_id,
            "message_author_matches_bot": bool(
                message_author_id > 0
                and bot_user_id > 0
                and message_author_id == bot_user_id
            ),
            "persistent_view_count": persistent_count,
            "persistent_view_types": persistent_names,
        }
        if not _observer_log_allowed():
            return

        error = RuntimeError(
            "component reached Dank Shield but remained unacknowledged after "
            f"{_COMPONENT_OBSERVER_GRACE_SECONDS:.1f}s"
        )
        global _COMPONENT_UNACKNOWLEDGED_COUNT
        _COMPONENT_UNACKNOWLEDGED_COUNT += 1
        record = log_interaction_failure(
            interaction,
            error,
            stage="component_unacknowledged",
            action_name=_command_path(interaction),
            fix_hint=(
                "The click reached the running bot but no callback acknowledged it. "
                "Inspect persistent-view ownership, callback errors, and acknowledgement logs."
            ),
            extra=extra,
        )
        print(
            "🚨 component_runtime unacknowledged "
            f"error_id={record.error_id} "
            f"interaction={getattr(interaction, 'id', 0)} "
            f"custom_id={record.context.custom_id!r} "
            f"guild={record.context.guild_id} message={record.context.message_id} "
            f"age_ms={extra['interaction_age_ms']} "
            f"message_author={message_author_id} bot_user={bot_user_id} "
            f"application_id={application_id} "
            f"persistent_views={persistent_count}"
        )
    except Exception as exc:
        if _observer_log_allowed():
            print(
                "⚠️ component_runtime observer failed "
                f"error={type(exc).__name__}: {_safe_error_text(exc)}"
            )


def install_component_interaction_runtime(bot: Any) -> bool:
    """Install the shared component lifecycle safety runtime on the bot.

    The runtime has two responsibilities only: recover definitely unowned
    private-session controls to the canonical Control Center, and observe any
    component that still remains unacknowledged. It never replays a stale action.
    """
    global _COMPONENT_OBSERVER_INSTALLED
    global _COMPONENT_OBSERVER_READY_LOGGED

    marker = "_dank_component_interaction_observer_installed"
    if _COMPONENT_OBSERVER_INSTALLED or bool(getattr(bot, marker, False)):
        _COMPONENT_OBSERVER_INSTALLED = True
        return True

    add_listener = getattr(bot, "add_listener", None)
    if not callable(add_listener):
        return False

    async def interaction_listener(interaction: discord.Interaction) -> None:
        _record_component_ingress(bot, interaction)
        recovered = await _recover_unowned_private_component(bot, interaction)
        if recovered:
            return
        await _observe_component_ack(bot, interaction)

    async def ready_listener() -> None:
        global _COMPONENT_OBSERVER_READY_LOGGED
        if _COMPONENT_OBSERVER_READY_LOGGED:
            return
        _COMPONENT_OBSERVER_READY_LOGGED = True
        # Let the already-registered persistent-view on_ready listeners get one
        # event-loop turn before taking the startup inventory snapshot.
        await asyncio.sleep(0)
        count, names = _persistent_view_snapshot(bot)
        bot_user_id = _safe_int(getattr(getattr(bot, "user", None), "id", 0), 0)
        app_id = _safe_int(getattr(bot, "application_id", 0), 0)
        print(
            "🔎 component_runtime ready "
            f"release={runtime_release_label()} "
            f"bot_user={bot_user_id} application_id={app_id} "
            f"persistent_views={count} types={names or 'none'}"
        )

    try:
        add_listener(interaction_listener, "on_interaction")
        add_listener(ready_listener, "on_ready")
        setattr(bot, marker, True)
        _COMPONENT_OBSERVER_INSTALLED = True
        return True
    except Exception as exc:
        print(
            "⚠️ component_runtime observer registration failed "
            f"error={type(exc).__name__}: {_safe_error_text(exc)}"
        )
        return False


# Compatibility alias for older imports while the runtime name becomes canonical.
install_component_interaction_observer = install_component_interaction_runtime


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
            acknowledged = await safe_defer_interaction(
                interaction,
                ephemeral=ephemeral,
                action_name=resolved_action,
            )
            if not acknowledged:
                record = _latest_interaction_failure(
                    interaction,
                    stage="defer_failed",
                )
                return InteractionGuardResult(
                    ok=False,
                    error_id=str(getattr(record, "error_id", "") or ""),
                    error_type=str(getattr(record, "error_type", "") or ""),
                    error_message=str(getattr(record, "error_message", "") or ""),
                    sent_to_user=False,
                )

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
    "component_runtime_status",
    "install_component_interaction_observer",
    "install_component_interaction_runtime",
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
