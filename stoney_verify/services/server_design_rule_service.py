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


def reset_layout_overrides(options: Mapping[str, Any]) -> dict[str, Any]:
    """Clear saved visual/name exceptions while preserving protection and draft settings."""

    out = dict(options)
    out["format_lock_global"] = {}
    out["category_format_locks"] = {}
    out["channel_format_locks"] = {}
    out["manual_name_overrides"] = {}
    return out


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


# Minimum lock strength at which each visible piece actually renders.
_DIMENSION_MIN_STRENGTH = {"separator_id": 2, "font": 3, "category_frame_id": 4}
_DIMENSION_LABELS = {"separator_id": "separator", "font": "font", "category_frame_id": "category frame"}


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _hidden_value(dimension: str) -> str:
    return "plain" if dimension == "category_frame_id" else ("normal" if dimension == "font" else "none")


def sync_saved_rules_to_detected(
    options: Mapping[str, Any],
    conflicts: Iterable[Mapping[str, Any]],
    *,
    settled_ids: Iterable[Any],
    persistable_separator_ids: Iterable[str],
    persistable_frame_ids: Iterable[str],
    persistable_fonts: Iterable[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Update saved locks that Scan & Fix overruled so they match the live look.

    Scan & Fix treats saved locks as advisory: the live server's established
    look wins. Without this step the saved rule keeps disagreeing, so Scan
    Saved Design flags the repaired items and a saved-design apply would undo
    the repair. Only items whose live name now matches the detected look
    (``settled_ids``) are synced.

    One saved rule can style many items (a category rule styles its header and
    its channels; the global rule styles everything). A dimension is updated
    only when every settled item governed by that rule agrees on the detected
    value and that value is a built-in style that survives restarts. Anything
    else is reported back unchanged so the owner can decide. Exact-name rules
    and protection are never touched.
    """

    settled = {_text(value) for value in settled_ids if _text(value)}
    separators = {_text(value) for value in persistable_separator_ids}
    frames = {_text(value) for value in persistable_frame_ids}
    fonts = {_text(value) for value in persistable_fonts}

    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for conflict in conflicts:
        if not isinstance(conflict, Mapping) or _text(conflict.get("id")) not in settled:
            continue
        scope = _text(conflict.get("scope"))
        if scope not in {"channel", "category", "global"}:
            continue
        groups.setdefault((scope, _text(conflict.get("rule_key"))), []).append(conflict)

    out = dict(options)
    channel_locks = _mapping(out.get("channel_format_locks"))
    category_locks = _mapping(out.get("category_format_locks"))
    global_lock = _mapping(out.get("format_lock_global"))
    updated: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for (scope, rule_key), rows in sorted(groups.items()):
        if scope == "channel":
            lock = _mapping(channel_locks.get(rule_key))
        elif scope == "category":
            lock = _mapping(category_locks.get(rule_key))
        else:
            lock = dict(global_lock) if global_lock.get("enabled") else {}
        if not lock:
            continue

        names = sorted({_text(row.get("name")) for row in rows if _text(row.get("name"))})
        wanted: dict[str, set[str]] = {}
        for row in rows:
            saved = _mapping(row.get("saved"))
            detected = _mapping(row.get("detected"))
            for dimension, value in detected.items():
                if _text(value) != _text(saved.get(dimension)):
                    wanted.setdefault(str(dimension), set()).add(_text(value))

        changes: dict[str, str] = {}
        reasons: list[str] = []
        for dimension, values in sorted(wanted.items()):
            label = _DIMENSION_LABELS.get(dimension, dimension)
            if len(values) != 1:
                reasons.append(f"items under this rule use different {label}s")
                continue
            value = next(iter(values))
            allowed = separators if dimension == "separator_id" else frames if dimension == "category_frame_id" else fonts
            if value not in allowed:
                reasons.append(f"detected {label} is a one-off style, not a saved library style")
                continue
            changes[dimension] = value

        if changes:
            new_lock = dict(lock)
            strength = _safe_int({**dict(options), **lock}.get("strength"), 2)
            for dimension, value in changes.items():
                new_lock[dimension] = value
                # A non-default piece only renders once strength reaches its tier.
                if value != _hidden_value(dimension):
                    strength = max(strength, _DIMENSION_MIN_STRENGTH.get(dimension, strength))
            new_lock["strength"] = strength
            if scope == "channel":
                channel_locks[rule_key] = new_lock
            elif scope == "category":
                category_locks[rule_key] = new_lock
            else:
                global_lock = new_lock
            updated.append({"scope": scope, "rule_key": rule_key, "changes": dict(changes), "names": names})
        if reasons:
            unresolved.append({"scope": scope, "rule_key": rule_key, "reasons": sorted(set(reasons)), "names": names})

    out["channel_format_locks"] = channel_locks
    out["category_format_locks"] = category_locks
    out["format_lock_global"] = global_lock
    return out, {"updated": updated, "unresolved": unresolved}


__all__ = [
    "effective_draft_separator",
    "persist_separator_choice",
    "protection_allows_separator",
    "removal_count",
    "reset_all_overrides",
    "reset_item_overrides",
    "reset_layout_overrides",
    "sync_saved_rules_to_detected",
]
