from __future__ import annotations

from stoney_verify.services import server_design_majority_layout as majority
from stoney_verify.services import server_design_studio as studio


def _styled(text: str, font: str = "fraktur") -> str:
    output, _subs = studio.transform_text_safe(text, font)
    return output


def _records(names: list[str], *, kind: str = "text") -> list[dict[str, str]]:
    return [{"name": name, "kind": kind} for name in names]


def _changed(before: str, after: str, *, kind: str = "text") -> dict[str, object]:
    return {
        "kind": kind,
        "status": "changed",
        "before": before,
        "after": after,
        "warnings": [],
        "blockers": [],
    }


def test_repair_safety_blocks_missing_separator_output():
    names = [
        f"💬┃{_styled('general')}",
        f"📢┃{_styled('announcements')}",
        f"🎮┃{_styled('gaming')}",
        f"🎭{_styled('profile')}",
    ]
    analysis = majority.infer_live_majority_layout(studio, _records(names))
    item = _changed(f"🎭{_styled('profile')}", f"🎭{_styled('profile')}")

    out = majority.annotate_plan_items([item], analysis, {"__majority_layout_inferred": True}, studio=studio)

    assert out[0]["status"] == "failed"
    assert out[0]["majority_repair_safety_blocked"] is True
    assert "detected majority separator" in out[0]["blockers"][0]


def test_repair_safety_blocks_wrong_separator_weight():
    names = [
        f"💬┃{_styled('general')}",
        f"📢┃{_styled('announcements')}",
        f"🎮┃{_styled('gaming')}",
        f"🎭│{_styled('profile')}",
    ]
    analysis = majority.infer_live_majority_layout(studio, _records(names))
    item = _changed(f"🎭│{_styled('profile')}", f"🎭│{_styled('profile')}")

    out = majority.annotate_plan_items([item], analysis, {"__majority_layout_inferred": True}, studio=studio)

    assert out[0]["status"] == "failed"
    assert "detected majority separator" in out[0]["blockers"][0]


def test_repair_safety_blocks_doubled_separator_output():
    names = [
        f"💬 | {_styled('general')}",
        f"📢 | {_styled('announcements')}",
        f"🎮 | {_styled('gaming')}",
    ]
    analysis = majority.infer_live_majority_layout(studio, _records(names))
    item = _changed("🎭profile", f"🎭 || {_styled('profile')}")

    out = majority.annotate_plan_items([item], analysis, {"__majority_layout_inferred": True}, studio=studio)

    assert out[0]["status"] == "failed"
    assert "detected majority separator" in out[0]["blockers"][0]


def test_repair_safety_allows_correct_majority_separator():
    names = [
        f"💬┃{_styled('general')}",
        f"📢┃{_styled('announcements')}",
        f"🎮┃{_styled('gaming')}",
    ]
    analysis = majority.infer_live_majority_layout(studio, _records(names))
    item = _changed("🎭profile", f"🎭┃{_styled('profile')}")

    out = majority.annotate_plan_items([item], analysis, {"__majority_layout_inferred": True}, studio=studio)

    assert out[0]["status"] == "changed"
    assert not out[0].get("majority_repair_safety_blocked")


def test_repair_safety_blocks_category_frame_removal():
    names = ["── lounge ──", "── staff ──", "── voice ──", "plain"]
    analysis = majority.infer_live_majority_layout(studio, _records(names, kind="category"))
    item = _changed("plain", "plain", kind="category")

    out = majority.annotate_plan_items([item], analysis, {"__majority_layout_inferred": True}, studio=studio)

    assert out[0]["status"] == "failed"
    assert "category frame" in out[0]["blockers"][0]


def test_repair_safety_allows_correct_category_frame():
    names = ["── lounge ──", "── staff ──", "── voice ──"]
    analysis = majority.infer_live_majority_layout(studio, _records(names, kind="category"))
    item = _changed("plain", "── plain ──", kind="category")

    out = majority.annotate_plan_items([item], analysis, {"__majority_layout_inferred": True}, studio=studio)

    assert out[0]["status"] == "changed"
    assert not out[0].get("majority_repair_safety_blocked")


def test_category_frame_visual_count_is_not_overwritten_by_usage_count():
    names = ["── lounge ──", "── staff ──", "── voice ──"]
    analysis = majority.infer_live_majority_layout(studio, _records(names, kind="category"))

    assert analysis["category_frame"]["count"] == 2
    assert analysis["category_frame"]["occurrence_count"] == 3
    assert majority._frame_matches_expected(studio, "── plain ──", analysis["category_frame"]) is True
