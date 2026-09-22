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

from stoney_verify.guild_config import get_guild_config
from stoney_verify.security_stats import (
    SECURITY_STATS_CATEGORY_ID_KEY,
    SECURITY_STATS_CATEGORY_NAME,
    SECURITY_STATS_CHANNEL_IDS_KEY,
    SECURITY_STATS_ENABLED_KEY,
)
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


def theme_default_separator_id(options: Mapping[str, Any]) -> str:
    """Return the separator the selected theme really uses in native planning."""

    theme_id = str(options.get("theme_id") or "gothic_clean").strip() or "gothic_clean"
    theme = studio.THEMES_BY_ID.get(theme_id, studio.THEMES_BY_ID["gothic_clean"])

    # Gothic Clean intentionally uses a readable spaced ASCII pipe in the live
    # plan even though the historical theme tuple still carries bar_full. Keep
    # that established behavior on the stable first-class catalog entry instead
    # of synthesizing a separator into global runtime state.
    if theme_id == "gothic_clean":
        return "pipe_spaced"

    return str(getattr(theme, "channel_separator", "none") or "none").strip() or "none"


def effective_server_separator_id(options: Mapping[str, Any]) -> str:
    explicit = str(options.get("separator_id") or "").strip()
    return explicit or theme_default_separator_id(options)


def theme_default_category_frame_id(options: Mapping[str, Any]) -> str:
    """Return the selected theme's canonical category-frame id."""

    theme_id = str(options.get("theme_id") or "gothic_clean").strip() or "gothic_clean"
    theme = studio.THEMES_BY_ID.get(theme_id, studio.THEMES_BY_ID["gothic_clean"])
    frame_id = str(getattr(theme, "category_frame", "line") or "line").strip() or "line"
    return frame_id if frame_id in studio.CATEGORY_FRAMES_BY_ID else "line"


def effective_server_category_frame_id(options: Mapping[str, Any]) -> str:
    """Return the explicit server frame override or the selected theme default."""

    explicit = str(options.get("category_frame_id") or "").strip()
    if explicit in studio.CATEGORY_FRAMES_BY_ID:
        return explicit
    return theme_default_category_frame_id(options)


def normalize_plan_options(options: Mapping[str, Any], *, strict: bool = True) -> dict[str, Any]:
    """Apply native compatibility defaults without replacing live functions."""

    out = dict(options)

    # Preserve the established Gothic Clean spaced-pipe default without a guard
    # rewriting the ThemePreset tuple. An explicit saved separator always wins.
    if not str(out.get("separator_id") or "").strip():
        default_separator = theme_default_separator_id(out)
        if str(out.get("theme_id") or "gothic_clean") == "gothic_clean":
            out["separator_id"] = default_separator

    explicit_frame = str(out.get("category_frame_id") or "").strip()
    if explicit_frame in studio.CATEGORY_FRAMES_BY_ID:
        out["category_frame_id"] = explicit_frame
    else:
        out.pop("category_frame_id", None)

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


def _positive_id(value: Any) -> int:
    try:
        if value is None or isinstance(value, bool):
            return 0
        parsed = int(str(value).strip())
        return parsed if parsed > 0 else 0
    except Exception:
        return 0


async def _functional_design_resource_ids(guild: Any) -> set[int]:
    """Return bot-owned live resources that Dank Design must not rename.

    Live Server Stats owns both its category and counter channels. Including
    those dynamic names in style detection makes ordinary drift look noisy and
    creates an ownership fight because the stats refresher will rename them
    back. Resolve identity from canonical saved IDs first, with the current
    canonical category name only as recovery when the display is enabled.
    """

    guild_id = _positive_id(getattr(guild, "id", 0))
    if guild_id <= 0:
        return set()

    try:
        cfg = await get_guild_config(guild_id, refresh=False)
    except Exception:
        return set()
    if not isinstance(cfg, Mapping):
        return set()

    owned: set[int] = set()
    category_id = _positive_id(cfg.get(SECURITY_STATS_CATEGORY_ID_KEY))
    raw_channel_ids = cfg.get(SECURITY_STATS_CHANNEL_IDS_KEY)

    if isinstance(raw_channel_ids, Mapping):
        candidates = raw_channel_ids.values()
    elif isinstance(raw_channel_ids, (list, tuple, set)):
        candidates = raw_channel_ids
    else:
        candidates = ()
    for value in candidates:
        channel_id = _positive_id(value)
        if channel_id > 0:
            owned.add(channel_id)

    category = None
    if category_id > 0:
        try:
            category = guild.get_channel(category_id)
        except Exception:
            category = None

    if category is None and bool(cfg.get(SECURITY_STATS_ENABLED_KEY)):
        for candidate in list(getattr(guild, "categories", []) or []):
            if str(getattr(candidate, "name", "") or "") == SECURITY_STATS_CATEGORY_NAME:
                category = candidate
                category_id = _positive_id(getattr(candidate, "id", 0))
                break

    if category_id > 0 and category is not None:
        owned.add(category_id)
        for channel in list(getattr(guild, "channels", []) or []):
            parent_id = _positive_id(getattr(channel, "category_id", 0))
            if parent_id <= 0:
                parent_id = _positive_id(getattr(getattr(channel, "category", None), "id", 0))
            if parent_id == category_id:
                channel_id = _positive_id(getattr(channel, "id", 0))
                if channel_id > 0:
                    owned.add(channel_id)

    # Saved channel IDs remain authoritative even if the category is temporarily
    # missing. Discord snowflake IDs are not reused for unrelated channels.
    return owned


def _exclude_functional_items(items: list[dict[str, Any]], excluded_ids: set[int]) -> list[dict[str, Any]]:
    if not excluded_ids:
        return [dict(item) for item in items]
    return [
        dict(item)
        for item in items
        if _positive_id(item.get("channel_id")) not in excluded_ids
    ]


def _exclude_functional_records(records: list[dict[str, Any]], excluded_ids: set[int]) -> list[dict[str, Any]]:
    if not excluded_ids:
        return [dict(record) for record in records]
    return [
        dict(record)
        for record in records
        if _positive_id(record.get("id")) not in excluded_ids
    ]


def live_records(guild: Any) -> list[dict[str, Any]]:
    """Use the same editable channel set as the historical Studio backend."""

    from stoney_verify.commands_ext import public_design_studio as legacy

    return list(legacy._live_majority_records_for_design(guild))  # type: ignore[attr-defined]


def _fail_closed_on_low_confidence(items: list[dict[str, Any]], confidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Fail only unsafe Smart Auto-Detect rows while preserving safe repairs.

    The aggregate confidence flag still answers whether the whole plan is safe.
    Mixed plans use the evaluator's aligned per-row results so one blocked row
    cannot erase unrelated high-confidence repairs.
    """

    if bool(confidence.get("apply_allowed")):
        return [dict(item) for item in items]

    raw_results = confidence.get("row_results")
    row_results = list(raw_results) if isinstance(raw_results, list) else []

    guarded: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        item = dict(raw)
        if item.get("status") != "changed":
            guarded.append(item)
            continue

        if index < len(row_results) and isinstance(row_results[index], Mapping):
            score = dict(row_results[index])
        else:
            # Compatibility fallback for callers/tests that provide only the
            # historical aggregate confidence shape.
            score = repair_confidence.score_repair_item(
                item,
                context="smart_category_auto_detect",
            )

        classification = str(score.get("classification") or "")
        reason = str(score.get("reason") or "Smart Repair could not safely apply this row.")

        item["repair_confidence_classification"] = classification
        item["repair_confidence_score"] = int(score.get("confidence", 0) or 0)
        item["repair_confidence_reason"] = reason

        if classification != repair_confidence.SAFE_AUTO_FIX:
            blockers = list(item.get("blockers") or [])
            message = f"Smart Auto-Detect confidence is too low for this row: {reason}"
            if message not in blockers:
                blockers.append(message)
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
    excluded_ids = await _functional_design_resource_ids(guild)

    if use_live_majority:
        records = _exclude_functional_records(live_records(guild), excluded_ids)
        inferred, profiles = majority.build_category_aware_options(studio, plan_options, records)
        plan_options = normalize_plan_options(inferred, strict=strict)
        plan_options["__respect_saved_rules"] = bool(respect_saved_rules)
        analysis = {
            "mode": "category_aware",
            "profiles": dict(profiles) if isinstance(profiles, Mapping) else {},
        }

    items = list(await legacy.build_design_plan(guild, plan_options))
    items = _exclude_functional_items(items, excluded_ids)
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
    excluded_ids = await _functional_design_resource_ids(guild)
    records = _exclude_functional_records(live_records(guild), excluded_ids)
    inferred, profiles = majority.build_category_aware_options(studio, plan_options, records)
    plan_options = normalize_plan_options(inferred, strict=True)
    plan_options["__respect_saved_rules"] = True
    plan_options["__scoped_editor_repair"] = True

    all_items = list(await legacy.build_design_plan(guild, plan_options))
    all_items = _exclude_functional_items(all_items, excluded_ids)
    items = _scope_items(all_items, category_id=category_id, channel_id=channel_id)
    items = list(majority.annotate_category_aware_plan_items(studio, items, plan_options))

    header_source = ""
    if category_id is not None:
        saved_options = normalize_plan_options(options, strict=True)
        saved_items = list(await legacy.build_design_plan(guild, saved_options))
        saved_items = _exclude_functional_items(saved_items, excluded_ids)
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
    "effective_server_separator_id",
    "live_records",
    "normalize_plan_options",
    "theme_default_separator_id",
]
