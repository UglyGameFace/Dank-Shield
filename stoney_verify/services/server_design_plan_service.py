from __future__ import annotations

"""Authoritative plan builder for Dank Design.

The public Studio must ask one service for a rename plan. Historical startup
helpers used to replace ``public_design_studio.build_design_plan`` at runtime;
that made the result depend on import order. This module makes live-majority
repair, strict layout matching, saved-rule precedence, and the Gothic Clean
compatibility defaults explicit inputs instead.
"""

from collections.abc import Mapping
from typing import Any

from stoney_verify.services import server_design_majority_layout as majority
from stoney_verify.services import server_design_studio as studio

# These names were historically removed from DEFAULT_PROTECTED_NAMES by a
# runtime guard. Keep the behavior without mutating the global service catalog.
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
    """Return plan options with native compatibility defaults applied.

    No command module, class, or plan function is replaced. Saved narrow rules
    remain authoritative; the compatibility defaults only fill missing behavior.
    """

    out = dict(options)

    # Preserve the established Gothic Clean visible separator without relying on
    # server_design_strict_layout_guard to rewrite the global theme catalog.
    majority.ensure_separator_spec(studio, "|", "spaced")
    if str(out.get("theme_id") or "gothic_clean") == "gothic_clean" and not str(out.get("separator_id") or "").strip():
        out["separator_id"] = "pipe_spaced"

    # Preserve prior visual-name behavior without deleting names from the
    # service's global protected-name set. Explicit owner rules still win.
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
    """Build majority-analysis records from the same editable items as Studio."""

    from stoney_verify.commands_ext import public_design_studio as legacy

    return list(legacy._live_majority_records_for_design(guild))  # type: ignore[attr-defined]


async def build_plan(
    guild: Any,
    options: Mapping[str, Any],
    *,
    use_live_majority: bool = False,
    strict: bool = True,
    respect_saved_rules: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Build one deterministic design plan.

    ``use_live_majority`` is explicit rather than a magic option flag that only
    works when a startup guard happened to replace the plan function.
    """

    from stoney_verify.commands_ext import public_design_studio as legacy

    plan_options = normalize_plan_options(options, strict=strict)
    analysis: dict[str, Any] = {}

    if use_live_majority:
        analysis = dict(majority.infer_live_majority_layout(studio, live_records(guild)))
        plan_options = majority.apply_majority_to_options(
            studio,
            plan_options,
            analysis,
            respect_locks=bool(respect_saved_rules),
        )
        plan_options = normalize_plan_options(plan_options, strict=strict)
        plan_options["__use_live_majority_layout"] = True

    items = await legacy.build_design_plan(guild, plan_options)
    if analysis:
        items = majority.annotate_plan_items(items, analysis, plan_options, studio=studio)
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


__all__ = [
    "build_drift_repair_plan",
    "build_plan",
    "build_saved_design_plan",
    "live_records",
    "normalize_plan_options",
]
