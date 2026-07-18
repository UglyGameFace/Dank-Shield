from __future__ import annotations

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
    for font in ("fraktur", "bold_sans", "monospace", "serif_bold", "fullwidth", "small_caps"):
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


def test_keep_existing_icon_mode_does_not_invent_placeholder_icon() -> None:
    assert studio.suggested_icon("random-channel", existing="", mode="keep_existing") == ""
    assert studio.suggested_icon("random-channel", existing="🎯", mode="keep_existing") == "🎯"
