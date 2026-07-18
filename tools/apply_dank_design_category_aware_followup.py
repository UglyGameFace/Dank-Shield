from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAJORITY = ROOT / "stoney_verify/services/server_design_majority_layout.py"
CONFIDENCE_TEST = ROOT / "tests/test_server_design_majority_confidence_static.py"

text = MAJORITY.read_text(encoding="utf-8")

# Separator registry identity must preserve whitespace. `_text()` strips leading
# and trailing spaces, which made compact `│` and spaced ` │ ` look identical.
old_spec_exists = '''def _separator_spec_exists(studio: Any, sep_id: str, value: str) -> bool:
    spec = getattr(studio, "SEPARATORS_BY_ID", {}).get(sep_id)
    return bool(spec and _text(getattr(spec, "value", "")) == value)'''
new_spec_exists = '''def _separator_spec_exists(studio: Any, sep_id: str, value: str) -> bool:
    spec = getattr(studio, "SEPARATORS_BY_ID", {}).get(sep_id)
    raw_value = str(getattr(spec, "value", "") or "") if spec is not None else ""
    return bool(spec and raw_value == value)'''
if text.count(old_spec_exists) != 1:
    raise SystemExit(f"separator spec identity block count={text.count(old_spec_exists)}")
text = text.replace(old_spec_exists, new_spec_exists, 1)

old_ensure_loop = '''    for spec in tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()):
        if _text(getattr(spec, "value", "")) == value:
            return _text(getattr(spec, "id", ""))'''
new_ensure_loop = '''    for spec in tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()):
        raw_value = str(getattr(spec, "value", "") or "")
        if raw_value == value:
            return _text(getattr(spec, "id", ""))'''
if text.count(old_ensure_loop) != 1:
    raise SystemExit(f"ensure separator raw-value block count={text.count(old_ensure_loop)}")
text = text.replace(old_ensure_loop, new_ensure_loop, 1)

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

# The detector must also compare raw separator values, not stripped values.
old_identity = '''        separator_id = ""
        for spec in tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()):
            value = _text(getattr(spec, "value", "")).strip()
            if value == token:
                separator_id = _text(getattr(spec, "id", ""))
                break'''
new_identity = '''        separator_id = ""
        expected_value = f" {token} " if spacing == "spaced" else token
        for spec in tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()):
            raw_value = str(getattr(spec, "value", "") or "")
            if raw_value == expected_value:
                separator_id = _text(getattr(spec, "id", ""))
                break'''
if text.count(old_identity) != 1:
    raise SystemExit(f"separator identity block count={text.count(old_identity)}")
text = text.replace(old_identity, new_identity, 1)

# Normalize the winning majority from its visible token + spacing. This makes
# the result deterministic even after runtime-generated separator specs are
# prepended to the library by an earlier category/test.
old_majority = '''    if sep_mixed:
        separator = {**separator, "spacing": "mixed/unknown", "label": "mixed/unknown"}
    frame = frame_examples.get(frame_id, {"id": "", "kind": "unknown", "label": "mixed/unknown"}) if frame_id else {"id": "", "kind": "unknown", "label": "mixed/unknown"}'''
new_majority = '''    if sep_mixed:
        separator = {**separator, "spacing": "mixed/unknown", "label": "mixed/unknown"}
    else:
        majority_spacing = _text(separator.get("spacing"), "unknown")
        majority_token = _text(separator.get("token"))
        resolved_separator_id = ""
        if majority_spacing == "wrapped":
            candidate = _text(separator.get("separator_id"))
            if candidate in getattr(studio, "SEPARATORS_BY_ID", {}):
                resolved_separator_id = candidate
        elif majority_spacing in {"compact", "spaced", "none"}:
            resolved_separator_id = ensure_separator_spec(studio, majority_token, majority_spacing)
        if resolved_separator_id:
            separator = {**separator, "separator_id": resolved_separator_id}
    frame = frame_examples.get(frame_id, {"id": "", "kind": "unknown", "label": "mixed/unknown"}) if frame_id else {"id": "", "kind": "unknown", "label": "mixed/unknown"}'''
if text.count(old_majority) != 1:
    raise SystemExit(f"majority separator normalization block count={text.count(old_majority)}")
text = text.replace(old_majority, new_majority, 1)

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

print("PASS: preserved raw separator whitespace identity and aligned Smart Auto-Detect contract")
