"""Scan & Fix treats saved layout rules as advisory; the live server look wins.

Behavioral coverage for the advisory saved-rule mode and the follow-up sync
that keeps overruled saved rules from fighting a repair.
"""

from __future__ import annotations

from stoney_verify.services import server_design_majority_layout as majority
from stoney_verify.services import server_design_rule_service as rules
from stoney_verify.services import server_design_studio as studio


def _record(record_id: int, category_id: int | None, name: str, *, kind: str = "text") -> dict[str, str]:
    return {
        "id": str(record_id),
        "category_id": "" if category_id is None else str(category_id),
        "name": name,
        "kind": kind,
    }


RECORDS = [
    _record(10, None, "── lounge ──", kind="category"),
    _record(11, 10, "💬│general"),
    _record(12, 10, "🎮│gaming"),
    _record(13, 10, "🎭│memes"),
    _record(14, 10, "📷・pics"),
    _record(20, None, "── staff ──", kind="category"),
    _record(21, 20, "🛡│staff-chat"),
    _record(22, 20, "📋│mod-log"),
]

# A stale channel rule on #gaming and a stale category rule on staff, both
# saying "katakana dot" while the whole server actually uses "│".
STALE_OPTIONS = {
    "theme_id": "gothic_clean",
    "channel_format_locks": {"12": {"separator_id": "katakana_dot", "strength": 2, "font": "normal"}},
    "category_format_locks": {"20": {"separator_id": "katakana_dot", "strength": 2}},
    "manual_name_overrides": {"13": {"name": "🎭│memes"}},
    "protection_item_rules": {"21": "full"},
}


def _separator_targets(options: dict) -> dict[str, str]:
    return {key: lock.get("separator_id") for key, lock in options["channel_format_locks"].items()}


def test_authoritative_mode_lets_stale_saved_rules_block_the_fix() -> None:
    out, _ = majority.build_category_aware_options(studio, STALE_OPTIONS, RECORDS)

    targets = _separator_targets(out)
    assert targets["12"] == "katakana_dot"
    assert "21" not in targets and "22" not in targets
    assert out["__saved_rule_conflicts"] == []


def test_scan_and_fix_advisory_mode_follows_live_look_over_saved_rules() -> None:
    out, _ = majority.build_category_aware_options(
        studio, STALE_OPTIONS, RECORDS, saved_rules=majority.SAVED_RULES_ADVISORY
    )

    targets = _separator_targets(out)
    assert targets["12"] == targets["11"] == targets["21"] == targets["22"] == "bar_thin"
    assert targets["14"] == "bar_thin"  # the real outlier is repaired too
    assert out["__saved_rules_mode"] == majority.SAVED_RULES_ADVISORY
    assert out["format_lock_global"] == {}


def test_advisory_mode_reports_every_overruled_saved_rule() -> None:
    out, _ = majority.build_category_aware_options(
        studio, STALE_OPTIONS, RECORDS, saved_rules=majority.SAVED_RULES_ADVISORY
    )

    conflicts = {(row["id"], row["scope"], row["rule_key"]) for row in out["__saved_rule_conflicts"]}
    assert ("12", "channel", "12") in conflicts
    assert ("21", "category", "20") in conflicts
    assert ("22", "category", "20") in conflicts
    gaming = next(row for row in out["__saved_rule_conflicts"] if row["id"] == "12")
    assert gaming["saved"]["separator_id"] == "katakana_dot"
    assert gaming["detected"]["separator_id"] == "bar_thin"


def test_advisory_mode_never_drops_exact_names_or_protection() -> None:
    out, _ = majority.build_category_aware_options(
        studio, STALE_OPTIONS, RECORDS, saved_rules=majority.SAVED_RULES_ADVISORY
    )

    assert out["manual_name_overrides"] == STALE_OPTIONS["manual_name_overrides"]
    assert out["protection_item_rules"] == STALE_OPTIONS["protection_item_rules"]


def test_advisory_mode_uses_saved_rule_where_detection_has_no_safe_answer() -> None:
    # A doubled separator gives detection no safe target, so the saved rule decides.
    records = [_record(30, None, "misc", kind="category"), _record(31, 30, "🎮││gaming")]
    options = {"theme_id": "gothic_clean", "channel_format_locks": {"31": {"separator_id": "bar_thin", "strength": 2}}}

    out, _ = majority.build_category_aware_options(studio, options, records, saved_rules=majority.SAVED_RULES_ADVISORY)

    assert out["__auto_detect_saved_fallback_ids"] == ["31"]
    assert out["channel_format_locks"]["31"]["separator_id"] == "bar_thin"
    assert out["channel_format_locks"]["31"]["scope"] == "saved_channel"


def test_advisory_mode_reports_rule_when_live_names_have_no_separator() -> None:
    records = [_record(30, None, "misc", kind="category"), _record(31, 30, "notes")]
    options = {"theme_id": "gothic_clean", "channel_format_locks": {"31": {"separator_id": "bar_thin", "strength": 2}}}

    out, _ = majority.build_category_aware_options(studio, options, records, saved_rules=majority.SAVED_RULES_ADVISORY)

    assert out["channel_format_locks"]["31"]["separator_id"] == "none"
    assert [row["id"] for row in out["__saved_rule_conflicts"]] == ["31"]


def test_plan_annotation_flags_items_whose_saved_rule_was_overruled() -> None:
    out, _ = majority.build_category_aware_options(
        studio, STALE_OPTIONS, RECORDS, saved_rules=majority.SAVED_RULES_ADVISORY
    )
    items = [
        {"channel_id": "12", "category_id": "10", "kind": "text", "before": "🎮│gaming", "after": "🎮│gaming", "status": "unchanged"},
        {"channel_id": "11", "category_id": "10", "kind": "text", "before": "💬│general", "after": "💬│general", "status": "unchanged"},
    ]

    annotated = majority.annotate_category_aware_plan_items(studio, items, out)

    assert annotated[0]["saved_rule_overruled"]["scope"] == "channel"
    assert "saved_rule_overruled" not in annotated[1]


def _sync(options: dict, conflicts: list, settled: set[str]):
    return rules.sync_saved_rules_to_detected(
        options,
        conflicts,
        settled_ids=settled,
        persistable_separator_ids=studio.BUILTIN_SEPARATOR_IDS,
        persistable_frame_ids=studio.BUILTIN_CATEGORY_FRAME_IDS,
        persistable_fonts=studio.FONT_STYLES,
    )


def test_sync_updates_overruled_rules_to_the_live_look() -> None:
    out, _ = majority.build_category_aware_options(
        studio, STALE_OPTIONS, RECORDS, saved_rules=majority.SAVED_RULES_ADVISORY
    )
    synced, report = _sync(STALE_OPTIONS, out["__saved_rule_conflicts"], {"12", "20", "21", "22"})

    assert synced["channel_format_locks"]["12"]["separator_id"] == "bar_thin"
    assert synced["category_format_locks"]["20"]["separator_id"] == "bar_thin"
    assert synced["manual_name_overrides"] == STALE_OPTIONS["manual_name_overrides"]
    assert synced["protection_item_rules"] == STALE_OPTIONS["protection_item_rules"]
    assert {(row["scope"], row["rule_key"]) for row in report["updated"]} == {("channel", "12"), ("category", "20")}


def test_sync_does_not_persist_runtime_only_styles() -> None:
    out, _ = majority.build_category_aware_options(
        studio, STALE_OPTIONS, RECORDS, saved_rules=majority.SAVED_RULES_ADVISORY
    )
    synced, report = _sync(STALE_OPTIONS, out["__saved_rule_conflicts"], {"12", "20", "21", "22"})

    frame = synced["category_format_locks"]["20"].get("category_frame_id")
    assert frame is None or frame in studio.BUILTIN_CATEGORY_FRAME_IDS
    staff = [row for row in report["unresolved"] if row["rule_key"] == "20"]
    assert staff and "one-off style" in staff[0]["reasons"][0]


def test_sync_ignores_items_that_were_not_repaired() -> None:
    conflicts = [
        {"id": "12", "kind": "text", "name": "g", "scope": "channel", "rule_key": "12",
         "saved": {"font": "normal", "separator_id": "katakana_dot"}, "detected": {"font": "normal", "separator_id": "bar_thin"}},
    ]

    synced, report = _sync(STALE_OPTIONS, conflicts, settled=set())

    assert synced["channel_format_locks"]["12"]["separator_id"] == "katakana_dot"
    assert report == {"updated": [], "unresolved": []}


def test_sync_leaves_shared_rule_alone_when_items_disagree() -> None:
    options = {"format_lock_global": {"enabled": True, "separator_id": "katakana_dot", "strength": 2}}
    conflicts = [
        {"id": "1", "kind": "text", "name": "a", "scope": "global", "rule_key": "global",
         "saved": {"font": "normal", "separator_id": "katakana_dot"}, "detected": {"font": "normal", "separator_id": "bar_thin"}},
        {"id": "2", "kind": "text", "name": "b", "scope": "global", "rule_key": "global",
         "saved": {"font": "normal", "separator_id": "katakana_dot"}, "detected": {"font": "normal", "separator_id": "middle_dot"}},
    ]

    synced, report = _sync(options, conflicts, {"1", "2"})

    assert synced["format_lock_global"]["separator_id"] == "katakana_dot"
    assert report["unresolved"][0]["scope"] == "global"


def test_sync_raises_strength_so_the_saved_style_actually_renders() -> None:
    options = {"channel_format_locks": {"5": {"separator_id": "bar_thin", "strength": 2, "font": "normal"}}}
    conflicts = [
        {"id": "5", "kind": "text", "name": "x", "scope": "channel", "rule_key": "5",
         "saved": {"font": "normal", "separator_id": "bar_thin"}, "detected": {"font": "fraktur", "separator_id": "bar_thin"}},
    ]

    synced, _ = _sync(options, conflicts, {"5"})

    assert synced["channel_format_locks"]["5"]["font"] == "fraktur"
    assert synced["channel_format_locks"]["5"]["strength"] >= 3
