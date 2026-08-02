from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "stoney_verify" / "startup_guards" / "auto_schema_bootstrap.py"
OLD = '"202608020001_durable_invite_stats.sql"'
NEW = '"20260802225500_durable_invite_stats.sql"'

text = TARGET.read_text(encoding="utf-8")
if text.count(OLD) != 1:
    raise RuntimeError(f"expected exactly one old migration reference, found {text.count(OLD)}")
TARGET.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")

(ROOT / "tools" / "rename_durable_invite_stats_bootstrap.py").unlink(missing_ok=True)
(ROOT / ".github" / "workflows" / "rename-durable-invite-stats-bootstrap.yml").unlink(missing_ok=True)
