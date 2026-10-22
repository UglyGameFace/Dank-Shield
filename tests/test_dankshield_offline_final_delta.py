"""Credential-free guardrails for the manual offline 34-table catch-up."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from tools import audit_dankshield_cutover_delta as audit
from tools import stage_dankshield_offline_final_delta as catchup


def test_default_is_preview_and_requires_exact_separate_stage_approval():
    assert catchup.validate_mode("preview", "", "", "") == "preview"
    for args in [
        ("stage", "", catchup.FROZEN, "backup-receipt"),
        ("stage", catchup.CONFIRM, "", "backup-receipt"),
        ("stage", catchup.CONFIRM, catchup.FROZEN, ""),
        ("stage", catchup.CONFIRM, catchup.FROZEN, "x"),
        ("production", catchup.CONFIRM, catchup.FROZEN, "backup-receipt"),
    ]:
        with pytest.raises(ValueError):
            catchup.validate_mode(*args)
    assert catchup.validate_mode(
        "stage", catchup.CONFIRM, catchup.FROZEN, "backup-receipt"
    ) == "stage"


def test_delta_accounts_for_new_changed_and_candidate_only_rows():
    source = {
        ("g", "u1"): ("hash1", "payload1"),
        ("g", "u2"): ("hash2", "payload2"),
        ("g", "u3"): ("hash3", "payload3"),
    }
    target = {
        ("g", "u1"): ("hash1", None),
        ("g", "u2"): ("old", None),
        ("g", "other"): ("target-only", None),
    }
    result = catchup.describe_delta(source, target)
    assert result["source_only"] == 1
    assert result["target_only"] == 1
    assert result["changed"] == 1
    assert result["write_keys"] == {("g", "u2"), ("g", "u3")}


def test_query_builder_only_writes_approved_tables_and_preserves_existing_pk():
    pytest.importorskip("psycopg")
    insert = catchup.upsert_query(
        "guild_members",
        ("guild_id", "user_id", "roles"),
        ("guild_id", "user_id"),
    )
    assert "on conflict" in insert.as_string().lower()
    assert "excluded" in insert.as_string().lower()
    assert "delete" not in insert.as_string().lower()
    assert "roles" in insert.as_string()
    with pytest.raises(ValueError):
        catchup.upsert_query("member_secrets", ("id",), ("id",))
    with pytest.raises(ValueError):
        catchup.upsert_query("guild_members", ("guild_id", "roles"), ("guild_id", "user_id"))


def test_select_builder_strips_only_documented_candidate_extra_columns():
    pytest.importorskip("psycopg")
    source = catchup.row_query("guild_members", ("guild_id", "user_id"), excluded=frozenset())
    query = source.as_string()
    assert "order by" in query.lower()
    assert "to_jsonb" in query
    assert "guild_id" in query and "user_id" in query
    with pytest.raises(ValueError):
        catchup.row_query("secrets", ("id",), excluded=frozenset())


def test_stage_invariants_are_explicit_and_fail_closed():
    source = inspect.getsource(catchup)
    assert "audit.validate_readonly_sql_and_inputs()" in source
    assert "audit.table_contract(source, target, table)" in source
    assert "default_transaction_read_only=on" in inspect.getsource(audit.connection_params)
    assert "lock table {} in share row exclusive mode" in source
    assert "candidate_only_rows_need_review" in source
    assert "offline_catchup_row_mismatch" in source
    assert "recheck_original_snapshot(source_params, source_snapshots, keys)" in source
    assert "jsonb_populate_record" in source
    assert "production_cutover_authorized" in source
    assert "no automatic" not in source  # No Discord moderation side effects


def test_script_is_unwired_from_bot_startup():
    path = Path("tools/stage_dankshield_offline_final_delta.py")
    assert path.is_file()
    assert not any(
        code in path.read_text(encoding="utf-8")
        for code in ("bot.add_listener", "on_ready", "os.environ['SUPABASE_URL'] =")
    )
