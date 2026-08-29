from __future__ import annotations

from pathlib import Path

service_path = Path("stoney_verify/services/server_design_majority_layout.py")
text = service_path.read_text(encoding="utf-8")
old = '    out["strength"] = max(desired_strength, min(5, _safe_int(out.get("strength"), desired_strength)))\n'
new = '    out["strength"] = desired_strength\n'
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected one majority strength assignment, found {count}")
service_path.write_text(text.replace(old, new, 1), encoding="utf-8")

test_path = Path("tests/test_server_design_majority_layout.py")
tests = test_path.read_text(encoding="utf-8")
marker = "def test_majority_strength_is_derived_from_detected_components_not_old_draft()"
if marker not in tests:
    tests += '''


def test_majority_strength_is_derived_from_detected_components_not_old_draft():
    names = [
        "💬│general",
        "📢│announcements",
        "🎮│gaming",
    ]
    analysis = majority.infer_live_majority_layout(studio, _records(names))
    options = majority.apply_majority_to_options(
        studio,
        {"theme_id": "gothic_clean", "strength": 5},
        analysis,
    )

    assert analysis["font"]["id"] == "normal"
    assert options["separator_id"] == "bar_thin"
    assert options["strength"] == 2
'''
test_path.write_text(tests, encoding="utf-8")
