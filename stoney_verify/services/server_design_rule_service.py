from __future__ import annotations

"""Pure saved-rule mutations for Dank Design.

This module owns the boring but critical part humans reasonably expect to work:
when a separator is saved it stays saved, and when an override is reset the
advertised authority is actually removed.  It performs no Discord I/O.
"""

from collections.abc import Iterable, Mapping
from typing import Any

_SEPARATOR_ALLOWED_PROTECTION = {"separator_only", "font_only", "full"}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
    except Exception:
        return default


def effective_draft_separator(options: Mapping[str, Any], *, theme_separator: str) -> str:
    """Return the explicit saved separator before falling back to the theme."""

    explicit = _text(options.get("separator_id"))
    return explicit or _text(theme_separator, "none")


def protection_allows_separator(mode: Any) -> bool:
    return _text(mode).lower().replace("-", "_") in _SEPARATOR_ALLOWED_PROTECTION


def persist_separator_choice(
    options: Mapping[str, Any],
    *,
    separator_id: str,
    applied_rows: Iterable[Any] = (),
) -> dict[str, Any]:
    """Persist one reviewed separator without clobbering unrelated rule pieces.

    Server draft, enabled global rule, category rules, and channel rules adopt
    only the separator component. Exact manual-name rows changed by the batch are
    updated to the actual resulting name so the next saved-design preview cannot
    immediately propose reverting the just-applied separator.
    """

    chosen = _text(separator_id)
    if not chosen:
        raise ValueError("separator_id is required")

    out = dict(options)
    out["separator_id"] = chosen

    global_lock = _mapping(out.get("format_lock_global"))
    if global_lock.get("enabled"):
        global_lock["separator_id"] = chosen
        out["format_lock_global"] = global_lock

    category_locks = _mapping(out.get("category_format_locks"))
    for key, raw in list(category_locks.items()):
        if isinstance(raw, Mapping):
            category_locks[str(key)] = {**dict(raw), "separator_id": chosen}
    out["category_format_locks"] = category_locks

    channel_locks = _mapping(out.get("channel_format_locks"))
    for key, raw in list(channel_locks.items()):
        if isinstance(raw, Mapping):
            channel_locks[str(key)] = {**dict(raw), "separator_id": chosen}
    out["channel_format_locks"] = channel_locks

    manual_names = _mapping(out.get("manual_name_overrides"))
    for row in applied_rows:
        if isinstance(row, Mapping):
            channel_id = _text(row.get("channel_id"))
            after = _text(row.get("after") or row.get("new_name"))
        else:
            channel_id = _text(getattr(row, "channel_id", ""))
            after = _text(getattr(row, "after", ""))
        if not channel_id or not after or channel_id not in manual_names:
            continue
        raw = manual_names.get(channel_id)
        if isinstance(raw, Mapping):
            manual_names[channel_id] = {**dict(raw), "name": after}
        else:
            manual_names[channel_id] = {"name": after, "scope": "channel"}
    out["manual_name_overrides"] = manual_names

    return out


def reset_item_overrides(options: Mapping[str, Any], *, target_id: int) -> tuple[dict[str, Any], dict[str, bool]]:
    """Remove every exact/same-ID override for one category or channel.

    Parent category, global rule, server draft, and inherited name protection are
    deliberately not removed because they are broader authorities and may affect
    other items. The caller can report those remaining inherited layers clearly.
    """

    key = str(int(target_id))
    out = dict(options)
    removed = {
        "category": False,
        "channel": False,
        "manual_name": False,
        "protection_item": False,
    }

    category_locks = _mapping(out.get("category_format_locks"))
    removed["category"] = category_locks.pop(key, None) is not None
    out["category_format_locks"] = category_locks

    channel_locks = _mapping(out.get("channel_format_locks"))
    removed["channel"] = channel_locks.pop(key, None) is not None
    out["channel_format_locks"] = channel_locks

    manual_names = _mapping(out.get("manual_name_overrides"))
    removed["manual_name"] = manual_names.pop(key, None) is not None
    out["manual_name_overrides"] = manual_names

    item_rules = _mapping(out.get("protection_item_rules"))
    removed["protection_item"] = item_rules.pop(key, None) is not None
    out["protection_item_rules"] = item_rules

    return out, removed


def reset_all_overrides(options: Mapping[str, Any]) -> dict[str, Any]:
    """Remove every saved override layer while preserving the server draft."""

    out = dict(options)
    out["format_lock_global"] = {}
    out["category_format_locks"] = {}
    out["channel_format_locks"] = {}
    out["manual_name_overrides"] = {}
    out["protection_item_rules"] = {}
    out["protection_rules"] = {}
    return out


def removal_count(removed: Mapping[str, Any]) -> int:
    return sum(1 for value in removed.values() if bool(value))


__all__ = [
    "effective_draft_separator",
    "persist_separator_choice",
    "protection_allows_separator",
    "removal_count",
    "reset_all_overrides",
    "reset_item_overrides",
]
