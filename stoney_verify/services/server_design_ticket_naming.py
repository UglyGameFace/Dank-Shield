from __future__ import annotations

"""Dank Design ticket lifecycle naming adapter.

This module is intentionally small and name-only. It does not touch ticket
permissions, panels, transcripts, categories, roles, or database schema.
"""

from typing import Any, Mapping

from stoney_verify.services import server_design_studio as studio


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        text = str(value).strip()
        return text if text and text.lower() != "none" else default
    except Exception:
        return default


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def plain_ticket_channel_name(ticket_number: int, *, closed: bool = False) -> str:
    prefix = "closed" if closed else "ticket"
    return f"{prefix}-{int(ticket_number):04d}"


def _lock_for_category(options: Mapping[str, Any], parent_category_id: int | None) -> dict[str, Any]:
    if not parent_category_id:
        return {}

    locks = _mapping(options.get("category_format_locks"))
    lock = locks.get(str(int(parent_category_id))) or locks.get(int(parent_category_id))
    lock = _mapping(lock)

    if lock and lock.get("enabled", True) is not False:
        return lock

    return {}


def _global_lock(options: Mapping[str, Any]) -> dict[str, Any]:
    lock = _mapping(options.get("format_lock_global"))
    if lock and lock.get("enabled", False):
        return lock
    return {}


def _design_options_for_ticket(
    options: Mapping[str, Any],
    *,
    parent_category_id: int | None = None,
    closed: bool = False,
) -> dict[str, Any]:
    """Resolve the format policy for a new/closed ticket channel.

    Priority:
    1. Category saved rule for active/archive category
    2. Global saved rule
    3. Current Dank Design options
    """

    base = dict(options)
    lock = _lock_for_category(base, parent_category_id) or _global_lock(base)

    if lock:
        for key in (
            "theme_id",
            "strength",
            "font",
            "separator_id",
            "category_frame_id",
            "emoji_override",
            "exact_match",
            "icon_mode",
        ):
            if key in lock:
                base[key] = lock[key]

    # Ticket lifecycle names should be recognizable even when the selected theme
    # would otherwise guess a generic icon.
    if not _safe_str(base.get("emoji_override")):
        base["emoji_override"] = "📦" if closed else "🎫"

    # New ticket names should follow the visible design exactly, not get skipped
    # because the plain base text semantically matches.
    base["exact_match"] = True

    return base


def build_ticket_channel_name(
    ticket_number: int,
    *,
    closed: bool = False,
    options: Mapping[str, Any] | None = None,
    parent_category_id: int | None = None,
) -> str:
    """Return the visible channel name for a ticket lifecycle state.

    If no Dank Design options are provided, this preserves the legacy plain name.
    """

    plain = plain_ticket_channel_name(ticket_number, closed=closed)

    if not isinstance(options, Mapping) or not options:
        return plain

    design_options = _design_options_for_ticket(
        options,
        parent_category_id=parent_category_id,
        closed=closed,
    )

    try:
        result = studio.build_styled_name(
            plain,
            kind="text",
            theme_id=_safe_str(design_options.get("theme_id"), "gothic_clean"),
            strength=max(1, min(5, _safe_int(design_options.get("strength"), 4))),
            saved_base_name=plain,
            icon_mode=_safe_str(design_options.get("icon_mode"), "replace_missing"),
            protection_rules=_mapping(design_options.get("protection_rules")),
            separator_id=_safe_str(design_options.get("separator_id")) or None,
            font=_safe_str(design_options.get("font")) or None,
            emoji_override=_safe_str(design_options.get("emoji_override")) or None,
            exact_match=True,
        )
    except Exception:
        return plain

    if result.blockers:
        return plain

    after = _safe_str(result.after).strip()
    return after[: studio.DISCORD_NAME_LIMIT] if after else plain


__all__ = [
    "build_ticket_channel_name",
    "plain_ticket_channel_name",
]
