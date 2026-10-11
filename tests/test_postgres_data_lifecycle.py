from __future__ import annotations

"""Prevent new private PostgreSQL tables without a deliberate lifecycle owner."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "postgres/data_lifecycle.json"
MIGRATIONS = ROOT / "postgres/migrations"

CREATE_TABLE = re.compile(
    r"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?"
    r"(?:(?:public\.)?[a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)


def test_every_postgres_table_declares_owner_and_retention() -> None:
    policy = json.loads(REGISTRY.read_text("utf-8"))
    assert policy["version"] == 1
    tables = policy["tables"]
    assert tables

    created: set[str] = set()
    for migration in MIGRATIONS.glob("*.sql"):
        # Only DDL CREATE TABLE registrations, not reads/updates, need this
        # schema-level lifecycle guard. Existing Supabase migrations are separate.
        sql = re.sub(r"--[^\n]*", "", migration.read_text("utf-8"))
        for match in CREATE_TABLE.finditer(sql):
            name = match.group().lower().split()[-1]
            created.add(name if "." in name else "public." + name)

    assert created == set(tables), (
        f"PostgreSQL lifecycle registrations do not match created tables: "
        f"missing={sorted(created - set(tables))} "
        f"orphan={sorted(set(tables) - created)}"
    )

    required = {
        "feature",
        "owner",
        "purpose",
        "stored_fields",
        "contains_message_content",
        "retention_rule",
        "cleanup_interval_seconds",
        "max_candidates_per_cleanup",
        "expiry_index",
        "preserve_when",
        "backup_class",
        "migration",
    }
    for table_name, settings in tables.items():
        assert not (required - set(settings)), f"{table_name} missing retention controls"
        assert all(settings[name] for name in required if name != "contains_message_content")
        assert isinstance(settings["contains_message_content"], bool)
        assert isinstance(settings["cleanup_interval_seconds"], int)
        assert 60 <= settings["cleanup_interval_seconds"] <= 86400 * 30
        assert isinstance(settings["max_candidates_per_cleanup"], int)
        assert 1 <= settings["max_candidates_per_cleanup"] <= 1000
        assert len(settings["stored_fields"]) == len(set(settings["stored_fields"]))
        assert (ROOT / settings["migration"]).is_file()


def test_reminder_retention_uses_one_authoritative_owner() -> None:
    lifecycle = json.loads(REGISTRY.read_text("utf-8"))["tables"][
        "public.dank_verify_reminder_delivery"
    ]
    from stoney_verify import verification_reminders as reminders
    from stoney_verify import verification_reminder_postgres as storage

    assert lifecycle["cleanup_interval_seconds"] == reminders.CLEANUP_INTERVAL_SECONDS
    assert lifecycle["max_candidates_per_cleanup"] == reminders.CLEANUP_BATCH_SIZE
    assert "90 days" in lifecycle["retention_rule"]
    assert reminders.RETENTION_DAYS == 90
    assert callable(storage.delete_departed_sync)
