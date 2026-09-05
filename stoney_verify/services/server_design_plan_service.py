from __future__ import annotations

"""Authoritative plan builder for Dank Design.

The public Studio asks one service for every rename plan. Historical startup
helpers replaced ``public_design_studio.build_design_plan`` at runtime, making
results depend on import order. Live auto-detect, strict matching, saved-rule
precedence, confidence gating, and compatibility defaults are explicit here.
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
    # rewriting the ThemePreset tuple. Dynamic visual catalog entries are owned
    # explicitly by the majority service.
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
    """Turn an unsafe Smart Auto-Detect preview into a non-applicable plan.

    The old runtime bridge disabled its Apply button after confidence analysis.
    Encoding the gate in the plan is safer because every consumer, including a
    future UI, receives a plan that cannot accidentally be treated as approved.
    """

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
        plan_options["__use_live_majority_layout"] = True
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


def confidence_allows_apply(options: Mapping[str, Any]) -> bool:
    confidence = options.get("__repair_confidence_result")
    return bool(isinstance(confidence, Mapping) and confidence.get("apply_allowed"))


__all__ = [
    "build_drift_repair_plan",
    "build_plan",
    "build_saved_design_plan",
    "confidence_allows_apply",
    "live_records",
    "normalize_plan_options",
]
