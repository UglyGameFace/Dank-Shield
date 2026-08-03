from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parent / "apply_dank_stats_live_recovery.py"
text = TARGET.read_text(encoding="utf-8")
old = '''    rows = _rows(response)\n    return rows[0] if rows else None\n\n\ndef _merged_config_payload'''
new = '''    rows = _rows(response)\n    return rows[0] if rows else None\n\n\ndef _fallback_event_hashes(settings: Mapping[str, Any]) -> list[str]:\n    raw = settings.get(FALLBACK_EVENTS_KEY)\n    values = list(raw or []) if isinstance(raw, (list, tuple)) else []\n    result: list[str] = []\n    for value in values:\n        token = str(value or "").strip().lower()\n        if len(token) == 64 and token not in result:\n            result.append(token)\n    return result[-_MAX_FALLBACK_EVENT_HASHES:]\n\n\ndef _merged_config_payload'''
if text.count(old) != 1:
    raise RuntimeError(f"expected one insertion point, found {text.count(old)}")
TARGET.write_text(text.replace(old, new, 1), encoding="utf-8")
Path(__file__).unlink(missing_ok=True)
