from __future__ import annotations

"""Authoritative plan builder for Dank Design.

The public Studio asks one service for every rename plan. Historical startup
helpers replaced ``public_design_studio.build_design_plan`` at runtime, making
results depend on import order. Live auto-detect, strict matching, saved-rule
precedence, confidence gating, scoped editor repair, and compatibility defaults
are explicit here.
"""

from collections.abc import Mapping
from typing import Any

from stoney_verify.services import server_design_majority_layout as majority
from stoney_verify.services import server_design_repair_confidence as repair_confidence
from stoney_verify.services import server_design_studio as studio

_RENAME_SAFE_VISUAL_NAMES = {
    "audit-log",
    "bot-commands",
    "logs",
    "mod-log",
    "setup",
    "staff",
    "staff-chat",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _strict_lock_map(value: Any) -> dict[str, Any]:
    rows = _mapping(value)
    out: dict[str, Any] = {}
    for key, raw in rows.items():
        if isinstance(raw, Mapping):
            out[str(key)] = {**dict(raw), "exact_match": True}
        else:
            out[str(key)] = raw
    return out


def normalize_plan_options(options: Mapping[str, Any], *, strict: bool = True) -> dict[str, Any]:
    """Apply native compatibility defaults without replacing live functions."""

    out = dict(options)

    # Preserve the established Gothic Clean spaced-pipe default without a guard
    # rewriting the ThemePreset tuple. An explicit saved separator always wins.
    majority.ensure_separator_spec(studio, "|", "spaced")
    if str(out.get("theme_id") or "gothic_clean") == "gothic_clean" and not str(out.get("separator_id") or "").strip():
        out["separator_id"] = "pipe_spaced"

    # Preserve the old visual-name policy without mutating the global protected
    # name set. Explicit per-name owner choices remain authoritative.
    protection = _mapping(out.get("protection_rules"))
    for name in _RENAME_SAFE_VISUAL_NAMES:
        protection.setdefault(name, "full")
    out["protection_rules"] = protection

    if strict:
        out["exact_match"] = True
        global_lock = _mapping(out.get("format_lock_global"))
        if global_lock.get("enabled"):
            out["format_lock_global"] = {**global_lock, "exact_match": True}
        out["category_format_locks"] = _strict_lock_map(out.get("category_format_locks"))
        out["channel_format_locks"] = _strict_lock_map(out.get("channel_format_locks"))

    return out


def live_records(guild: Any) -> list[dict[str, Any]]:
    """Use the same editable channel set as the historical Studio backend."""

    from stoney_verify.commands_ext import public_design_studio as legacy

    return list(legacy._live_majority_records_for_design(guild))  # type: ignore[attr-defined]


def _fail_closed_on_low_confidence(items: list[dict[str, Any]], confidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Turn an unsafe Smart Auto-Detect preview into a non-applicable plan."""

    if bool(confidence.get("apply_allowed")):
        return items

    reason = "Smart Auto-Detect confidence is too low to apply this repair safely. Review the layout or use Saved Design / Edit One Item."
    guarded: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        if item.get("status") == "changed":
            blockers = list(item.get("blockers") or [])
            if reason not in blockers:
                blockers.append(reason)
            item["blockers"] = blockers
            item["status"] = "failed"
        guarded.append(item)
    return guarded


def _scope_items(
    items: list[dict[str, Any]],
    *,
    category_id: int | None = None,
    channel_id: int | None = None,
) -> list[dict[str, Any]]:
    """Return only rows belonging to one exact editor scope."""

    if category_id is not None and channel_id is not None:
        raise ValueError("Choose category_id or channel_id, not both.")
    if category_id is None and channel_id is None:
        raise ValueError("A category_id or channel_id is required for scoped planning.")

    if channel_id is not None:
        wanted = str(int(channel_id))
        return [dict(item) for item in items if str(item.get("channel_id") or "") == wanted]

    wanted = str(int(category_id or 0))
    scoped: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        kind = str(item.get("kind") or "")
        item_channel_id = str(item.get("channel_id") or "")
        item_category_id = str(item.get("category_id") or "")
        if (kind == "category" and item_channel_id == wanted) or item_category_id == wanted:
            scoped.append(item)
    return scoped


def _selected_category_header(items: list[dict[str, Any]], category_id: int) -> dict[str, Any] | None:
    wanted = str(int(category_id))
    for raw in items:
        item = dict(raw)
        if str(item.get("kind") or "") == "category" and str(item.get("channel_id") or "") == wanted:
            return item
    return None


def _replace_category_header(
    items: list[dict[str, Any]],
    category_id: int,
    header: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Replace the selected Category Editor header without touching child rows."""

    if not isinstance(header, Mapping):
        return [dict(item) for item in items]

    wanted = str(int(category_id))
    replacement = dict(header)
    replacement["scoped_category_header_source"] = "saved_design"
    out: list[dict[str, Any]] = []
    replaced = False
    for raw in items:
        item = dict(raw)
        if str(item.get("kind") or "") == "category" and str(item.get("channel_id") or "") == wanted:
            if not replaced:
                out.append(dict(replacement))
                replaced = True
            continue
        out.append(item)
    if not replaced:
        out.insert(0, dict(replacement))
    return out


async def build_plan(
    guild: Any,
    options: Mapping[str, Any],
    *,
    use_live_majority: bool = False,
    strict: bool = True,
    respect_saved_rules: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Build one deterministic plan through the native backend.

    Smart Auto-Detect remains category-aware. Mixed categories keep their own
    style rather than being flattened to one server-wide majority, and saved
    rule precedence remains authoritative.
    """

    from stoney_verify.commands_ext import public_design_studio as legacy

    plan_options = normalize_plan_options(options, strict=strict)
    analysis: dict[str, Any] = {}

    if use_live_majority:
        records = live_records(guild)
        inferred, profiles = majority.build_category_aware_options(studio, plan_options, records)
        plan_options = normalize_plan_options(inferred, strict=strict)
        plan_options["__respect_saved_rules"] = bool(respect_saved_rules)
        analysis = {
            "mode": "category_aware",
            "profiles": dict(profiles) if isinstance(profiles, Mapping) else {},
        }

    items = list(await legacy.build_design_plan(guild, plan_options))
    if use_live_majority:
        items = list(majority.annotate_category_aware_plan_items(studio, items, plan_options))
        confidence = repair_confidence.evaluate_repair_plan(items, context="smart_category_auto_detect")
        plan_options["__repair_confidence_result"] = dict(confidence)
        analysis["confidence"] = dict(confidence)
        items = _fail_closed_on_low_confidence(items, confidence)

    return list(items), dict(plan_options), analysis


async def build_saved_design_plan(guild: Any, options: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    return await build_plan(guild, options, use_live_majority=False, strict=True)


async def build_drift_repair_plan(guild: Any, options: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    return await build_plan(
        guild,
        options,
        use_live_majority=True,
        strict=True,
        respect_saved_rules=True,
    )


async def build_scoped_repair_plan(
    guild: Any,
    options: Mapping[str, Any],
    *,
    category_id: int | None = None,
    channel_id: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Build Smart Repair for one Category/Channel Editor scope.

    Child channels use their category-local detected layout. The selected
    category header itself uses the saved design/rule hierarchy, because the
    category-aware channel detector intentionally preserves unsaved category
    headers. Confidence is evaluated only after those two sources are combined
    into the exact scope the administrator is reviewing.
    """

    from stoney_verify.commands_ext import public_design_studio as legacy

    plan_options = normalize_plan_options(options, strict=True)
    records = live_records(guild)
    inferred, profiles = majority.build_category_aware_options(studio, plan_options, records)
    plan_options = normalize_plan_options(inferred, strict=True)
    plan_options["__use_live_majority_layout"] = True
    plan_options["__respect_saved_rules"] = True
    plan_options["__scoped_editor_repair"] = True

    all_items = list(await legacy.build_design_plan(guild, plan_options))
    items = _scope_items(all_items, category_id=category_id, channel_id=channel_id)
    items = list(majority.annotate_category_aware_plan_items(studio, items, plan_options))

    header_source = ""
    if category_id is not None:
        saved_options = normalize_plan_options(options, strict=True)
        saved_items = list(await legacy.build_design_plan(guild, saved_options))
        saved_header = _selected_category_header(saved_items, int(category_id))
        items = _replace_category_header(items, int(category_id), saved_header)
        if saved_header is not None:
            header_source = "saved_design"

    confidence = repair_confidence.evaluate_repair_plan(items, context="smart_category_auto_detect")
    plan_options["__repair_confidence_result"] = dict(confidence)
    analysis = {
        "mode": "category_aware_scoped",
        "profiles": dict(profiles) if isinstance(profiles, Mapping) else {},
        "confidence": dict(confidence),
        "category_id": str(int(category_id)) if category_id is not None else "",
        "channel_id": str(int(channel_id)) if channel_id is not None else "",
        "category_header_source": header_source,
    }
    return _fail_closed_on_low_confidence(items, confidence), dict(plan_options), analysis


def confidence_allows_apply(options: Mapping[str, Any]) -> bool:
    confidence = options.get("__repair_confidence_result")
    return bool(isinstance(confidence, Mapping) and confidence.get("apply_allowed"))


__all__ = [
    "build_drift_repair_plan",
    "build_plan",
    "build_saved_design_plan",
    "build_scoped_repair_plan",
    "confidence_allows_apply",
    "live_records",
    "normalize_plan_options",
]
