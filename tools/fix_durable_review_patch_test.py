from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "apply_durable_invite_stats_review_fixes.py"
OLD = '''def test_outbox_persistence_moves_json_and_file_work_off_event_loop(monkeypatch) -> None:\\n    _clear_runtime_state(monkeypatch)\\n    event = durable_invite_stats.PendingInviteEvent('''
NEW = '''def test_outbox_persistence_moves_json_and_file_work_off_event_loop(monkeypatch) -> None:\\n    durable_invite_stats._PENDING.clear()\\n    event = durable_invite_stats.PendingInviteEvent('''

text = TARGET.read_text(encoding="utf-8")
if text.count(OLD) != 1:
    raise RuntimeError(f"expected one outbox test harness match, found {text.count(OLD)}")
TARGET.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
Path(__file__).unlink(missing_ok=True)
