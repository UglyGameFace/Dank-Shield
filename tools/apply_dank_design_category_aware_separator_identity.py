from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAJORITY = ROOT / "stoney_verify/services/server_design_majority_layout.py"

text = MAJORITY.read_text(encoding="utf-8")
old = '''        separator_id = ""
        for spec in tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()):
            value = _text(getattr(spec, "value", "")).strip()
            if value == token:
                separator_id = _text(getattr(spec, "id", ""))
                break'''
new = '''        separator_id = ""
        expected_value = f" {token} " if spacing == "spaced" else token
        for spec in tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()):
            value = _text(getattr(spec, "value", ""))
            if value == expected_value:
                separator_id = _text(getattr(spec, "id", ""))
                break'''
if text.count(old) != 1:
    raise SystemExit(f"separator identity block count={text.count(old)}")
text = text.replace(old, new, 1)
MAJORITY.write_text(text, encoding="utf-8")
compile(text, str(MAJORITY), "exec")
print("PASS: compact and spaced separators keep distinct identities")
