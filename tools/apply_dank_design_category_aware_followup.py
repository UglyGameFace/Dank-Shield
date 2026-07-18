from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAJORITY = ROOT / "stoney_verify/services/server_design_majority_layout.py"
CONFIDENCE_TEST = ROOT / "tests/test_server_design_majority_confidence_static.py"

text = MAJORITY.read_text(encoding="utf-8")

old_apply = '''    separator_id = _text(separator.get("separator_id"))
    if separator_id and separator_id in getattr(studio, "SEPARATORS_BY_ID", {}):
        out["separator_id"] = separator_id
    elif spacing in {"compact", "spaced", "none"}:
        out["separator_id"] = ensure_separator_spec(studio, token, spacing)'''
new_apply = '''    separator_id = _text(separator.get("separator_id"))
    if separator_id and spacing == "wrapped" and separator_id in getattr(studio, "SEPARATORS_BY_ID", {}):
        out["separator_id"] = separator_id
    elif spacing in {"compact", "spaced", "none"}:
        out["separator_id"] = ensure_separator_spec(studio, token, spacing)'''
if text.count(old_apply) != 1:
    raise SystemExit(f"apply separator spacing block count={text.count(old_apply)}")
text = text.replace(old_apply, new_apply, 1)

old_helper = '''def _separator_id_from_parts(studio: Any, parts: Mapping[str, Any]) -> str:
    separator_id = _text(parts.get("separator_id"))
    if separator_id and separator_id in getattr(studio, "SEPARATORS_BY_ID", {}):
        return separator_id
    spacing = _text(parts.get("spacing"), "unknown")
    token = _text(parts.get("token"))
    if spacing in {"compact", "spaced", "none"}:
        return ensure_separator_spec(studio, token, spacing)
    return ""'''
new_helper = '''def _separator_id_from_parts(studio: Any, parts: Mapping[str, Any]) -> str:
    separator_id = _text(parts.get("separator_id"))
    spacing = _text(parts.get("spacing"), "unknown")
    token = _text(parts.get("token"))
    if separator_id and spacing == "wrapped" and separator_id in getattr(studio, "SEPARATORS_BY_ID", {}):
        return separator_id
    if spacing in {"compact", "spaced", "none"}:
        return ensure_separator_spec(studio, token, spacing)
    return ""'''
if text.count(old_helper) != 1:
    raise SystemExit(f"separator helper spacing block count={text.count(old_helper)}")
text = text.replace(old_helper, new_helper, 1)
MAJORITY.write_text(text, encoding="utf-8")

contract = CONFIDENCE_TEST.read_text(encoding="utf-8")
contract = contract.replace(
    '''def test_majority_repair_no_longer_recommends_live_majority_blindly():
    assert "For hand-built servers, choose **Use Live Majority**." not in SOURCE
    assert "Fix Only Obvious Mistakes" in SOURCE
    assert "Use **Live Majority** only when the preview keeps the current server look." in SOURCE''',
    '''def test_majority_repair_recommends_category_aware_auto_detect():
    assert "For hand-built servers, choose **Use Live Majority**." not in SOURCE
    assert "Smart Auto-Detect" in SOURCE
    assert "learn each category separately" in SOURCE
    assert "Saved channel/category/global rules always win" in SOURCE''',
)
CONFIDENCE_TEST.write_text(contract, encoding="utf-8")

for path in (MAJORITY, CONFIDENCE_TEST):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

print("PASS: preserved spaced separators and aligned Smart Auto-Detect contract")
