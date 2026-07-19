from __future__ import annotations

from copy import deepcopy

from stoney_verify.services import server_design_majority_layout as majority
from stoney_verify.services import server_design_studio as studio


def _styled(text: str, font: str) -> str:
    output, _ = studio.transform_text_safe(text, font)
    return output


def _record(record_id: int, category_id: int | None, name: str, *, kind: str = "text") -> dict[str, str]:
    return {
        "id": str(record_id),
        "category_id": "" if category_id is None else str(category_id),
        "name": name,
        "kind": kind,
    }


def test_exact_font_detection_distinguishes_supported_lettering_families() -> None:
    for font in (
        "fraktur",
        "bold_fraktur",
        "bold_sans",
        "italic_sans",
        "bold_italic_sans",
        "monospace",
        "serif_bold",
        "serif_italic",
        "serif_bold_italic",
        "fullwidth",
        "small_caps",
        "script",
        "bold_script",
        "circled",
        "parenthesized",
    ):
        visible = f"🎮│{_styled('gaming-news', font)}"
        assert majority.detect_font_id(studio, visible) == font


def test_separator_detection_supports_non_vertical_spacers() -> None:
    middle = majority.detect_channel_separator(studio, "🎮・gaming")
    dot = majority.detect_channel_separator(studio, "🎮·gaming")
    triangle = majority.detect_channel_separator(studio, "🎮▸gaming")
    wrapped = majority.detect_channel_separator(studio, "「🎮」gaming")

    assert middle["separator_id"] == "katakana_dot"
    assert dot["separator_id"] == "middle_dot"
    assert triangle["separator_id"] == "tri_right"
    assert wrapped["separator_id"] == "bracket_corner"


def test_separator_identity_preserves_raw_spacing() -> None:
    compact_id = majority.ensure_separator_spec(studio, "│", "compact")
    spaced_id = majority.ensure_separator_spec(studio, "│", "spaced")

    assert compact_id == "bar_thin"
    assert spaced_id == "bar_thin_spaced"
    assert compact_id != spaced_id
    assert studio.SEPARATORS_BY_ID[compact_id].value == "│"
    assert studio.SEPARATORS_BY_ID[spaced_id].value == " │ "


def test_each_category_gets_its_own_font_and_separator_majority() -> None:
    records = [
        _record(10, None, "── lounge ──", kind="category"),
        _record(11, 10, f"💬│{_styled('general', 'fraktur')}"),
        _record(12, 10, f"🎮│{_styled('gaming', 'fraktur')}"),
        _record(13, 10, f"🎭┃{_styled('profile', 'bold_sans')}"),
        _record(20, None, "【 staff 】", kind="category"),
        _record(21, 20, f"🛡・{_styled('staff-chat', 'monospace')}"),
        _record(22, 20, f"📋・{_styled('mod-log', 'monospace')}"),
        _record(23, 20, f"🤖│{_styled('bot-room', 'fraktur')}"),
    ]

    profiles = majority.infer_category_local_layouts(studio, records)
    lounge = profiles["channel_groups"]["10"]
    staff = profiles["channel_groups"]["20"]

    assert lounge["font"]["id"] == "fraktur"
    assert lounge["separator"]["separator_id"] == "bar_thin"
    assert staff["font"]["id"] == "monospace"
    assert staff["separator"]["separator_id"] == "katakana_dot"


def test_smart_options_create_local_ephemeral_rules_without_overwriting_saved_locks() -> None:
    records = [
        _record(10, None, "── lounge ──", kind="category"),
        _record(11, 10, f"💬│{_styled('general', 'fraktur')}"),
        _record(12, 10, f"🎮│{_styled('gaming', 'fraktur')}"),
        _record(13, 10, f"🎭┃{_styled('profile', 'bold_sans')}"),
        _record(20, None, "【 staff 】", kind="category"),
        _record(21, 20, f"🛡・{_styled('staff-chat', 'monospace')}"),
        _record(22, 20, f"📋・{_styled('mod-log', 'monospace')}"),
        _record(23, 20, f"🤖│{_styled('bot-room', 'fraktur')}"),
    ]
    saved = {
        "theme_id": "gothic_clean",
        "channel_format_locks": {
            "13": {"font": "bold_sans", "separator_id": "bar_heavy", "strength": 4},
        },
        "category_format_locks": {
            "20": {"font": "monospace", "separator_id": "katakana_dot", "strength": 4},
        },
        "protection_rules": {"mod-log": "never"},
    }

    inferred, _profiles = majority.build_category_aware_options(studio, saved, records)
    locks = inferred["channel_format_locks"]

    # Explicit channel lock remains byte-for-byte the owner-approved rule.
    assert locks["13"] == saved["channel_format_locks"]["13"]
    # Category 20 is locked, so Smart Auto-Detect adds no fake channel overrides there.
    assert "21" not in locks
    assert "22" not in locks
    assert "23" not in locks
    # Unlocked category 10 gets local temporary rules using its own majority.
    assert locks["11"]["font"] == "fraktur"
    assert locks["11"]["separator_id"] == "bar_thin"
    assert locks["12"]["font"] == "fraktur"
    assert locks["12"]["separator_id"] == "bar_thin"
    assert locks["11"]["__auto_detect"] is True
    # Category headers are preserved unless an explicit saved rule applies.
    assert "10" in inferred["__auto_detect_preserve_ids"]


def test_global_lock_prevents_smart_auto_detect_from_overwriting_any_unlocked_dimension() -> None:
    records = [
        _record(10, None, "Lounge", kind="category"),
        _record(11, 10, f"💬│{_styled('general', 'fraktur')}"),
        _record(12, 10, f"🎮│{_styled('gaming', 'fraktur')}"),
    ]
    saved_global = {
        "theme_id": "gothic_clean",
        "format_lock_global": {
            "enabled": True,
            "font": "monospace",
            "separator_id": "katakana_dot",
            "icon_mode": "keep_existing",
        },
    }

    inferred, _ = majority.build_category_aware_options(studio, saved_global, records)

    assert inferred["format_lock_global"] == saved_global["format_lock_global"]
    assert inferred["channel_format_locks"] == {}
    assert inferred["__auto_detect_ephemeral_channel_ids"] == []


def test_uncertain_local_dimensions_preserve_each_channels_current_style() -> None:
    records = [
        _record(10, None, "Lounge", kind="category"),
        _record(11, 10, f"💬│{_styled('general', 'fraktur')}"),
        _record(12, 10, f"🎮・{_styled('gaming', 'monospace')}"),
    ]

    inferred, profiles = majority.build_category_aware_options(studio, {"theme_id": "gothic_clean"}, records)
    analysis = profiles["channel_groups"]["10"]
    locks = inferred["channel_format_locks"]

    assert analysis["separator"]["mixed"] is True
    assert analysis["font"]["mixed"] is True
    assert locks["11"]["separator_id"] == "bar_thin"
    assert locks["11"]["font"] == "fraktur"
    assert locks["12"]["separator_id"] == "katakana_dot"
    assert locks["12"]["font"] == "monospace"


def test_category_local_detection_is_semantically_deterministic_when_scan_order_changes() -> None:
    records = [
        _record(10, None, "Lounge", kind="category"),
        _record(11, 10, f"💬│{_styled('general', 'fraktur')}"),
        _record(12, 10, f"🎮│{_styled('gaming', 'fraktur')}"),
        _record(20, None, "Staff", kind="category"),
        _record(21, 20, f"🛡・{_styled('staff-chat', 'monospace')}"),
        _record(22, 20, f"📋・{_styled('mod-log', 'monospace')}"),
    ]

    forward, forward_profiles = majority.build_category_aware_options(studio, {"theme_id": "gothic_clean"}, records)
    reverse, reverse_profiles = majority.build_category_aware_options(studio, {"theme_id": "gothic_clean"}, list(reversed(records)))

    assert forward["channel_format_locks"] == reverse["channel_format_locks"]
    assert forward_profiles["channel_groups"] == reverse_profiles["channel_groups"]


def test_preview_annotation_preserves_uncertain_rows_and_explains_local_safety_reason() -> None:
    options = {
        "__auto_detect_preserve_ids": ["11"],
        "__auto_detect_ephemeral_channel_ids": [],
        "__auto_detect_category_analyses": {},
        "__auto_detect_saved_rules_respected": {"global": 0, "categories": 0, "channels": 0},
    }
    items = [
        {
            "channel_id": "11",
            "category_id": "10",
            "kind": "text",
            "before": "💬│general",
            "after": "💬・general",
            "status": "changed",
            "warnings": [],
            "blockers": [],
        }
    ]

    annotated = majority.annotate_category_aware_plan_items(studio, deepcopy(items), options)

    assert annotated[0]["status"] == "unchanged"
    assert annotated[0]["after"] == annotated[0]["before"]
    assert annotated[0]["auto_detect_preserved"] is True
    assert "no safe local majority target" in annotated[0]["warnings"][0]


def test_keep_existing_icon_mode_does_not_invent_placeholder_icon() -> None:
    assert studio.suggested_icon("random-channel", existing="", mode="keep_existing") == ""
    assert studio.suggested_icon("random-channel", existing="🎯", mode="keep_existing") == "🎯"
