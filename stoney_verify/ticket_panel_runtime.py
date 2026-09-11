from __future__ import annotations

"""Restart-safe runtime registration for the canonical public ticket panel.

Ticket creation behavior remains owned by
``commands_ext.public_ticket_panel_clean``.  This module owns only the Discord
runtime bindings that must exist even when slash-command profile selection or an
unrelated command registration failure skips the public ticket command module.
"""

import asyncio
import time
from typing import Any

import discord

from .commands_ext import public_ticket_panel_clean as panel

_RUNTIME_VIEW_REGISTERED = False
_RUNTIME_FALLBACK_LISTENER_REGISTERED = False
_RUNTIME_REGISTRATION_ERROR = ""

_TICKET_SELECT_COMPONENT_TYPE = 3
_TICKET_PICKER_CONTENT = "Choose a ticket type."
_TICKET_PICKER_TITLE = "Create Ticket"


def _custom_id(interaction: discord.Interaction) -> str:
    data = interaction.data if isinstance(interaction.data, dict) else {}
    try:
        return str(data.get("custom_id") or "").strip()
    except Exception:
        return ""


def _safe_id(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _component_type(interaction: discord.Interaction) -> int:
    data = interaction.data if isinstance(interaction.data, dict) else {}
    return _safe_id(data.get("component_type"))


def _selected_values(interaction: discord.Interaction) -> list[str]:
    data = interaction.data if isinstance(interaction.data, dict) else {}
    values = data.get("values")
    if not isinstance(values, list):
        return []
    return [str(value or "").strip() for value in values if str(value or "").strip()]


def _interaction_age_ms(interaction: discord.Interaction) -> int:
    """Best-effort age of the Discord interaction when our listener sees it."""
    try:
        created_at = getattr(interaction, "created_at", None)
        if created_at is None:
            return -1
        age = (discord.utils.utcnow() - created_at).total_seconds() * 1000.0
        return max(0, int(round(age)))
    except Exception:
        return -1


def _response_done(interaction: discord.Interaction) -> bool:
    try:
        return bool(interaction.response.is_done())
    except Exception:
        return False


def _trace(
    interaction: discord.Interaction,
    stage: str,
    *,
    elapsed_ms: int | None = None,
) -> None:
    """Emit narrow telemetry for the clean Create Ticket interaction only."""
    try:
        guild = getattr(interaction, "guild", None)
        user = getattr(interaction, "user", None)
        parts = [
            "🔎 ticket_panel_trace",
            f"stage={stage}",
            f"interaction={_safe_id(getattr(interaction, 'id', 0))}",
            f"guild={_safe_id(getattr(guild, 'id', 0))}",
            f"user={_safe_id(getattr(user, 'id', 0))}",
            f"age_ms={_interaction_age_ms(interaction)}",
            f"response_done={_response_done(interaction)}",
        ]
        if elapsed_ms is not None:
            parts.append(f"listener_elapsed_ms={max(0, int(elapsed_ms))}")
        print(" ".join(parts))
    except Exception:
        pass


def _is_ticket_category_select(interaction: discord.Interaction) -> bool:
    """Identify only the temporary category picker emitted by the canonical panel.

    The select's Discord custom_id is intentionally not treated as durable state.
    Temporary views may use generated custom IDs, so recovery is instead scoped
    to the exact ticket-picker surface plus one selected value.  The current
    per-user menu session is validated again before delegation.
    """
    if _component_type(interaction) != _TICKET_SELECT_COMPONENT_TYPE:
        return False
    if len(_selected_values(interaction)) != 1:
        return False

    message = getattr(interaction, "message", None)
    if message is None:
        return False

    try:
        content = str(getattr(message, "content", "") or "").strip()
    except Exception:
        content = ""
    if content != _TICKET_PICKER_CONTENT:
        return False

    try:
        embeds = list(getattr(message, "embeds", None) or [])
    except Exception:
        embeds = []
    return any(
        str(getattr(embed, "title", "") or "").strip() == _TICKET_PICKER_TITLE
        for embed in embeds
    )


def _current_ticket_menu_session_id(interaction: discord.Interaction) -> str:
    guild = getattr(interaction, "guild", None)
    user = getattr(interaction, "user", None)
    guild_id = _safe_id(getattr(guild, "id", 0))
    user_id = _safe_id(getattr(user, "id", 0))
    if guild_id <= 0 or user_id <= 0:
        return ""

    try:
        key = panel._session_key(guild_id, user_id)
        state = panel._MENU_SESSIONS.get(key) or {}
        session_id = str(state.get("id") or "")
        if not session_id:
            return ""
        if not panel._menu_session_current(guild_id, user_id, session_id):
            return ""
        return session_id
    except Exception:
        return ""


async def _recover_ticket_category_select(
    interaction: discord.Interaction,
    *,
    started: float,
) -> None:
    """Recover a category select when discord.py's temporary view dispatch misses it."""
    selected = _selected_values(interaction)
    if len(selected) != 1:
        return

    guild = getattr(interaction, "guild", None)
    user = getattr(interaction, "user", None)
    guild_id = _safe_id(getattr(guild, "id", 0))
    user_id = _safe_id(getattr(user, "id", 0))
    if guild is None or user is None or guild_id <= 0 or user_id <= 0:
        return

    session_id = _current_ticket_menu_session_id(interaction)

    # Acknowledge first so a DB/config lookup cannot burn Discord's component
    # response window.  deferred_message_update keeps the source ephemeral
    # message editable by the canonical callback.
    try:
        await interaction.response.defer()
    except Exception:
        if _response_done(interaction):
            elapsed_ms = int(round((time.monotonic() - started) * 1000.0))
            _trace(
                interaction,
                "category_ack_observed_during_fallback",
                elapsed_ms=elapsed_ms,
            )
            return
        raise

    elapsed_ms = int(round((time.monotonic() - started) * 1000.0))
    _trace(interaction, "category_fallback_dispatch", elapsed_ms=elapsed_ms)

    if not session_id:
        await panel._stale_ticket_menu(interaction)
        _trace(interaction, "category_fallback_stale", elapsed_ms=elapsed_ms)
        return

    rows, _warning = await panel._load_rows(guild)
    slug = selected[0]
    valid_slugs = {panel._row_slug(row) for row in rows}
    if slug not in valid_slugs:
        await panel._stale_ticket_menu(interaction)
        elapsed_ms = int(round((time.monotonic() - started) * 1000.0))
        _trace(interaction, "category_fallback_invalid_value", elapsed_ms=elapsed_ms)
        return

    # Delegate the actual transition to the canonical TicketSelect callback.
    # This runtime layer does not create tickets or own category semantics.
    select = panel.TicketSelect(rows, user_id, session_id)
    select._values = [slug]
    await select.callback(interaction)
    elapsed_ms = int(round((time.monotonic() - started) * 1000.0))
    _trace(interaction, "category_fallback_return", elapsed_ms=elapsed_ms)


async def _ticket_panel_fallback_listener(
    interaction: discord.Interaction,
) -> None:
    """Recover clean-panel interactions only when earlier component dispatch missed them."""
    started = time.monotonic()
    try:
        if interaction.type is not discord.InteractionType.component:
            return

        if _custom_id(interaction) in panel.PANEL_BUTTON_CUSTOM_IDS:
            _trace(interaction, "listener_received")

            # Give discord.py's registered component handlers the first chance to
            # acknowledge the interaction. The canonical handler has its own
            # interaction-id lock, so this listener cannot create a duplicate
            # ticket/menu if another route wakes at nearly the same time.
            await asyncio.sleep(0.15)
            elapsed_ms = int(round((time.monotonic() - started) * 1000.0))
            if _response_done(interaction):
                _trace(
                    interaction,
                    "ack_observed_before_fallback",
                    elapsed_ms=elapsed_ms,
                )
                return

            _trace(interaction, "fallback_dispatch", elapsed_ms=elapsed_ms)
            await panel.handle_public_ticket_panel_click(interaction)
            elapsed_ms = int(round((time.monotonic() - started) * 1000.0))
            _trace(interaction, "fallback_return", elapsed_ms=elapsed_ms)
            return

        if not _is_ticket_category_select(interaction):
            return

        _trace(interaction, "category_listener_received")

        # Temporary/ephemeral views normally dispatch through discord.py.  The
        # live failure proved that path can miss while the persistent panel
        # button still works, so give it the same bounded recovery chance.
        await asyncio.sleep(0.15)
        elapsed_ms = int(round((time.monotonic() - started) * 1000.0))
        if _response_done(interaction):
            _trace(
                interaction,
                "category_ack_observed_before_fallback",
                elapsed_ms=elapsed_ms,
            )
            return

        await _recover_ticket_category_select(interaction, started=started)
    except Exception as exc:
        try:
            elapsed_ms = int(round((time.monotonic() - started) * 1000.0))
            _trace(interaction, "fallback_exception", elapsed_ms=elapsed_ms)
            print(
                "⚠️ ticket_panel_runtime fallback failed: "
                f"{type(exc).__name__}: {exc}"
            )
        except Exception:
            pass


def ticket_panel_runtime_status() -> dict[str, Any]:
    return {
        "persistent_view_registered": bool(_RUNTIME_VIEW_REGISTERED),
        "fallback_listener_registered": bool(
            _RUNTIME_FALLBACK_LISTENER_REGISTERED
        ),
        "ready": bool(
            _RUNTIME_VIEW_REGISTERED
            or _RUNTIME_FALLBACK_LISTENER_REGISTERED
        ),
        "error": str(_RUNTIME_REGISTRATION_ERROR or ""),
    }


def install_public_ticket_panel_runtime(
    bot: Any,
    *,
    strict: bool = False,
) -> bool:
    """Install restart-safe handlers for already-posted clean ticket panels.

    The persistent view is the primary route.  The delayed listener is an
    independent recovery route for a missed persistent-view or temporary
    category-select dispatch.  Neither route owns ticket creation; both delegate
    to the canonical clean panel.
    """
    global _RUNTIME_VIEW_REGISTERED
    global _RUNTIME_FALLBACK_LISTENER_REGISTERED
    global _RUNTIME_REGISTRATION_ERROR

    errors: list[str] = []

    # Reconcile with the command registrar when this function is called after
    # command setup in tests or alternate entrypoints.  In normal production
    # startup this installer runs first and then marks the clean registrar's
    # flags so registration remains single-owner and idempotent.
    if bool(getattr(panel, "_PANEL_VIEW_REGISTERED", False)):
        _RUNTIME_VIEW_REGISTERED = True
    elif (
        bool(getattr(panel, "_PANEL_FALLBACK_LISTENER_REGISTERED", False))
        and not _RUNTIME_FALLBACK_LISTENER_REGISTERED
    ):
        # The clean registrar sets this flag without a listener when its view
        # succeeds, so only trust it as evidence of a real fallback when the
        # persistent view itself is absent.
        _RUNTIME_FALLBACK_LISTENER_REGISTERED = True

    if not _RUNTIME_VIEW_REGISTERED:
        try:
            add_view = getattr(bot, "add_view", None)
            if not callable(add_view):
                raise RuntimeError("Discord client has no callable add_view")
            add_view(panel.PublicCreateTicketPanelView())
            _RUNTIME_VIEW_REGISTERED = True
            panel._PANEL_VIEW_REGISTERED = True
        except Exception as exc:
            errors.append(
                "persistent view: "
                f"{type(exc).__name__}: {exc}"
            )

    if not _RUNTIME_FALLBACK_LISTENER_REGISTERED:
        try:
            add_listener = getattr(bot, "add_listener", None)
            if not callable(add_listener):
                raise RuntimeError(
                    "Discord client has no callable add_listener"
                )
            add_listener(
                _ticket_panel_fallback_listener,
                "on_interaction",
            )
            _RUNTIME_FALLBACK_LISTENER_REGISTERED = True
            panel._PANEL_FALLBACK_LISTENER_REGISTERED = True
        except Exception as exc:
            errors.append(
                "fallback listener: "
                f"{type(exc).__name__}: {exc}"
            )

    ready = bool(
        _RUNTIME_VIEW_REGISTERED
        or _RUNTIME_FALLBACK_LISTENER_REGISTERED
    )
    _RUNTIME_REGISTRATION_ERROR = " | ".join(errors)

    if ready and not errors:
        print(
            "✅ ticket_panel_runtime ready "
            "persistent_view=True fallback_listener=True"
        )
    elif ready:
        print(
            "⚠️ ticket_panel_runtime degraded but operational "
            f"persistent_view={_RUNTIME_VIEW_REGISTERED} "
            "fallback_listener="
            f"{_RUNTIME_FALLBACK_LISTENER_REGISTERED} "
            f"error={_RUNTIME_REGISTRATION_ERROR}"
        )
    else:
        message = "Public ticket panel has no registered interaction handler"
        if _RUNTIME_REGISTRATION_ERROR:
            message += f": {_RUNTIME_REGISTRATION_ERROR}"
        print(f"❌ ticket_panel_runtime unavailable: {message}")
        if strict:
            raise RuntimeError(message)

    return ready


__all__ = [
    "install_public_ticket_panel_runtime",
    "ticket_panel_runtime_status",
]
