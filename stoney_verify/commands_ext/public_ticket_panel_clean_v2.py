from __future__ import annotations

"""Consolidated public ticket panel compatibility layer.

This file keeps one active /ticket-panel registration path while fixing the
clean panel DB fallback. The previous clean panel implementation remains the
implementation owner for the Discord UI; this layer applies the corrected ticket
schema contract before that owner registers commands/views.

Why this exists:
- old fallback insert left out tickets.title and tickets.username
- your current tickets table requires title, so DB logging failed after the
  Discord ticket channel opened
- this avoids adding another slash command or modal path
"""

from typing import Any, Tuple

# Required by the fallback insert path. Do not include generated columns like id.
_FIXED_TICKET_REQUIRED_COLUMNS: Tuple[str, ...] = (
    "guild_id",
    "user_id",
    "username",
    "title",
    "status",
    "category",
    "channel_id",
    "discord_thread_id",
    "ticket_number",
    "created_at",
    "updated_at",
)


def _apply_clean_panel_contract() -> Any:
    from . import public_ticket_panel_clean as clean

    clean.TICKET_REQUIRED_COLUMNS = _FIXED_TICKET_REQUIRED_COLUMNS

    # Keep the setup health probe honest. The old probe did not require title,
    # which let health pass while real ticket inserts failed.
    try:
        columns = list(getattr(clean, "TICKET_REQUIRED_COLUMNS", ()))
        for required in ("title", "username"):
            if required not in columns:
                columns.append(required)
        clean.TICKET_REQUIRED_COLUMNS = tuple(columns)
    except Exception:
        clean.TICKET_REQUIRED_COLUMNS = _FIXED_TICKET_REQUIRED_COLUMNS

    return clean


def register_public_ticket_panel_clean(bot: Any, tree: Any) -> None:
    clean = _apply_clean_panel_contract()
    clean.register_public_ticket_panel_clean(bot, tree)


# Optional re-exports for any older import path that expects these names.
def __getattr__(name: str) -> Any:
    clean = _apply_clean_panel_contract()
    return getattr(clean, name)


__all__ = ["register_public_ticket_panel_clean"]
