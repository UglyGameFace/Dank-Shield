from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAJORITY = ROOT / "stoney_verify/services/server_design_majority_layout.py"
CONFIDENCE_TEST = ROOT / "tests/test_server_design_majority_confidence_static.py"
LEGACY_THIRD_APPLIER = ROOT / "tools/apply_dank_design_category_aware_separator_identity.py"

text = MAJORITY.read_text(encoding="utf-8")

# A known separator ID must not erase its spacing. A compact `│` and a spaced
# ` │ ` are different visible layouts even though they share the same symbol.
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

# Runtime-added spaced separator specs are prepended to the separator library.
# Match the full visible value, not `.strip()`, so a previous spaced detection
# can never make a later compact channel inherit the wrong separator ID.
old_identity = '''        separator_id = ""
        for spec in tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()):
            value = _text(getattr(spec, "value", "")).strip()
            if value == token:
                separator_id = _text(getattr(spec, "id", ""))
                break'''
new_identity = '''        separator_id = ""
        expected_value = f" {token} " if spacing == "spaced" else token
        for spec in tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()):
            value = _text(getattr(spec, "value", ""))
            if value == expected_value:
                separator_id = _text(getattr(spec, "id", ""))
                break'''
if text.count(old_identity) != 1:
    raise SystemExit(f"separator identity block count={text.count(old_identity)}")
text = text.replace(old_identity, new_identity, 1)

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

# This separate follow-up was folded into this file so older CI reruns can use
# the latest fix without leaving temporary patch machinery in the final PR.
if LEGACY_THIRD_APPLIER.exists():
    LEGACY_THIRD_APPLIER.unlink()

for path in (MAJORITY, CONFIDENCE_TEST):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

print("PASS: preserved separator spacing/identity and aligned Smart Auto-Detect contract")
