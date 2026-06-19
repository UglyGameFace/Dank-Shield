from __future__ import annotations

from stoney_verify.services import server_design_majority_layout as majority
from stoney_verify.services import server_design_studio as studio


def _styled(text: str, font: str = "fraktur") -> str:
    output, _subs = studio.transform_text_safe(text, font)
    return output


def _records(names: list[str], *, kind: str = "text") -> list[dict[str, str]]:
    return [{"name": name, "kind": kind} for name in names]


def _build(name: str, options: dict[str, object], *, kind: str = "text"):
    return studio.build_styled_name(
        name,
        kind=kind,
        theme_id="gothic_clean",
        strength=int(options.get("strength", 4)),
        icon_mode=str(options.get("icon_mode", "replace_missing")),
        separator_id=str(options.get("separator_id", "bar_full")),
        category_frame_id=str(options.get("category_frame_id", "line")),
        font=str(options.get("font", "normal")),
        exact_match=bool(options.get("exact_match", False)),
    )


def test_hand_built_majority_pipe_fraktur_layout_repairs_outlier():
    names = [
        f"💬 | {_styled('general-chat')}",
        f"📢 | {_styled('announcements')}",
        f"🎮 | {_styled('gaming-news')}",
        "🎭profile-roles",
    ]
    analysis = majority.infer_live_majority_layout(studio, _records(names))
    options = majority.apply_majority_to_options(studio, {"theme_id": "gothic_clean"}, analysis)

    assert analysis["separator"]["label"] == "emoji | name"
    assert analysis["font"]["id"] == "fraktur"
    assert options["separator_id"] == "pipe_spaced"
    assert options["font"] == "fraktur"
    assert options["exact_match"] is True

    result = _build("🎭profile-roles", options)
    assert result.status == "changed"
    assert " | " in result.after
    assert "||" not in result.after
    assert _styled("profile-roles") in result.after


def test_mixed_thin_and_thick_separators_repairs_to_thin_majority():
    names = [
        f"💬│{_styled('general')}",
        f"📢│{_styled('announcements')}",
        f"🎮│{_styled('gaming')}",
        f"🎭┃{_styled('profile')}",
    ]
    analysis = majority.infer_live_majority_layout(studio, _records(names))
    options = majority.apply_majority_to_options(studio, {"theme_id": "gothic_clean"}, analysis)

    assert analysis["separator"]["token"] == "│"
    assert options["separator_id"] == "bar_thin"

    result = _build(f"🎭┃{_styled('profile')}", options)
    assert "│" in result.after
    assert "┃" not in result.after


def test_missing_separator_repairs_to_majority_separator():
    names = [
        f"💬 | {_styled('general')}",
        f"📢 | {_styled('announcements')}",
        f"🎮 | {_styled('gaming')}",
        f"🎭{_styled('profile')}",
    ]
    options = majority.apply_majority_to_options(studio, {"theme_id": "gothic_clean"}, majority.infer_live_majority_layout(studio, _records(names)))
    result = _build(f"🎭{_styled('profile')}", options)
    assert " | " in result.after


def test_wrong_separator_repairs_to_majority_separator():
    names = [
        f"💬│{_styled('general')}",
        f"📢│{_styled('announcements')}",
        f"🎮│{_styled('gaming')}",
        f"🎭┃{_styled('profile')}",
    ]
    options = majority.apply_majority_to_options(studio, {"theme_id": "gothic_clean"}, majority.infer_live_majority_layout(studio, _records(names)))
    result = _build(f"🎭┃{_styled('profile')}", options)
    assert "│" in result.after
    assert "┃" not in result.after


def test_correct_separator_wrong_spacing_repairs_to_majority_spacing():
    names = [
        f"💬 │ {_styled('general')}",
        f"📢 │ {_styled('announcements')}",
        f"🎮 │ {_styled('gaming')}",
        f"🎭│{_styled('profile')}",
    ]
    options = majority.apply_majority_to_options(studio, {"theme_id": "gothic_clean"}, majority.infer_live_majority_layout(studio, _records(names)))
    assert options["separator_id"] == "bar_thin_spaced"
    result = _build(f"🎭│{_styled('profile')}", options)
    assert " │ " in result.after
    assert "🎭│" not in result.after


def test_parser_distinguishes_doubled_separator_and_separator_inside_name_text():
    doubled = majority.detect_channel_separator(studio, "💬 || general")
    inside_name = majority.detect_channel_separator(studio, "💬 general | old")

    assert doubled["doubled"] is True
    assert doubled["spacing"] == "doubled"
    assert inside_name["missing"] is True
    assert inside_name["separator_in_name_text"] is True


def test_category_frame_majority_detection_repairs_category_outlier():
    category_names = ["── lounge ──", "── staff ──", "── voice ──", "plain category"]
    analysis = majority.infer_live_majority_layout(studio, _records(category_names, kind="category"))
    options = majority.apply_majority_to_options(studio, {"theme_id": "gothic_clean", "icon_mode": "clear"}, analysis)

    assert analysis["category_frame"]["label"] == "── name ──"
    assert options["category_frame_id"] == "majority_line_2"

    result = _build("plain category", options, kind="category")
    assert result.status == "changed"
    assert result.after.startswith("──")
    assert result.after.endswith("──")


def test_saved_layout_rules_are_reported_when_majority_takes_precedence():
    names = [
        f"💬 | {_styled('general')}",
        f"📢 | {_styled('announcements')}",
        f"🎮 | {_styled('gaming')}",
    ]
    analysis = majority.infer_live_majority_layout(studio, _records(names))
    saved = {
        "theme_id": "gothic_clean",
        "format_lock_global": {"enabled": True, "separator_id": "bar_heavy", "font": "normal"},
    }

    majority_first = majority.apply_majority_to_options(studio, saved, analysis, respect_locks=False)
    saved_first = majority.apply_majority_to_options(studio, saved, analysis, respect_locks=True)

    assert majority_first["format_lock_global"] == {}
    assert majority_first["__majority_layout_overrode_locks"] == 1
    assert majority_first["separator_id"] == "pipe_spaced"
    assert saved_first["format_lock_global"]["enabled"] is True
    assert saved_first["__majority_layout_lock_override_active"] == 1


def test_clean_design_text_replaces_literal_newline_artifacts():
    cleaned = majority.clean_design_text("Line 1\\nLine 2/nLine 3\\\\nLine 4")
    assert cleaned == "Line 1\nLine 2\nLine 3\nLine 4"
    assert "/n" not in cleaned
    assert "\\n" not in cleaned
