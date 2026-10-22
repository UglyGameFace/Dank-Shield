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


def test_guild_config_catchup_disables_only_two_known_candidate_triggers():
    class Candidate:
        state = "O"
        def __init__(self):
            self.actions = []

        def execute(self, query, *args):
            sql = str(query.as_string()) if hasattr(query, "as_string") else str(query)
            if sql.lower().startswith("alter table"):
                self.actions.append(sql)
                self.state = "D" if "DISABLE" in sql else "O"
            return self

        def fetchall(self):
            return [(name, self.state) for name in catchup._CONFIG_COPY_TRIGGERS]

    db = Candidate()
    catchup._candidate_config_trigger_guard(db, enable=False)
    assert db.state == "D"
    catchup._candidate_config_trigger_guard(db, enable=True)
    assert db.state == "O"
    assert len(db.actions) == 4
    assert all('public."guild_configs"' in x for x in db.actions)
    assert all("trigger" in x.lower() and "ALL" not in x for x in db.actions)
    assert not any("DISABLE TRIGGER USER" in x for x in db.actions)


def test_config_trigger_guard_fails_closed_on_unapproved_triggers():
    class Unexpected:
        def execute(self, _query, *args): return self
        def fetchall(self):
            return [("unknown_trigger", "O")]

    with pytest.raises(RuntimeError, match="unapproved_candidate_config_triggers"):
        catchup._candidate_config_trigger_guard(Unexpected(), enable=False)


def test_stage_error_output_is_allowlisted_and_never_exposes_db_secrets(
    monkeypatch, capsys,
):
    monkeypatch.setenv("DANK_DELTA_CATCHUP_MODE", "preview")
    monkeypatch.setattr(
        catchup, "reconcile", lambda mode: (_ for _ in ()).throw(
            RuntimeError("password=secret private-guild-user")
        )
    )
    assert catchup.main() == 2
    out = capsys.readouterr()
    assert "unexpected_catchup_failure" in out.err
    assert "secret" not in out.err and "private-guild" not in out.err

    monkeypatch.setattr(
        catchup, "reconcile", lambda mode: (_ for _ in ()).throw(
            RuntimeError("offline_catchup_row_mismatch")
        )
    )
    assert catchup.main() == 2
    assert "offline_catchup_row_mismatch" in capsys.readouterr().err


def test_historical_member_events_must_not_generate_new_feed_records():
    """The actual candidate has AFTER INSERT triggers on both source-only tables."""
    assert catchup._CANDIDATE_COPY_TRIGGERS["member_joins"] == (
        "member_joins_to_activity_feed_ai",
    )
    assert catchup._CANDIDATE_COPY_TRIGGERS["member_events"] == (
        "member_events_to_activity_feed_ai",
    )
    assert catchup._CANDIDATE_COPY_TRIGGERS["activity_feed_events"] == (
        "trg_sync_activity_feed_meta_columns",
    )
    assert "tickets" in catchup._CANDIDATE_COPY_TRIGGERS


def test_replay_trigger_guards_enforce_explicit_candidate_trigger_list():
    class Candidate:
        def __init__(self, names):
            self.names = names
            self.state = "O"
            self.actions = []
        def execute(self, query, *args):
            statement = str(query.as_string()) if hasattr(query, "as_string") else str(query)
            if statement.lower().startswith("alter table"):
                self.actions.append(statement)
                self.state = "D" if "DISABLE" in statement else "O"
            return self
        def fetchall(self):
            return [(name, self.state) for name in self.names]

    names = catchup._CANDIDATE_COPY_TRIGGERS["member_joins"]
    db = Candidate(names)
    catchup._candidate_copy_trigger_guard(db, "member_joins", enable=False)
    assert db.state == "D" and len(db.actions) == 1
    catchup._candidate_copy_trigger_guard(db, "member_joins", enable=True)
    assert db.state == "O" and len(db.actions) == 2
    assert all("public.\"member_joins\"" in x for x in db.actions)
    assert all("TRIGGER USER" not in x and "TRIGGER ALL" not in x for x in db.actions)
    assert all("member_joins_to_activity_feed_ai" in x for x in db.actions)

    with pytest.raises(RuntimeError, match="unapproved_candidate_copy_triggers"):
        catchup._candidate_copy_trigger_guard(Candidate(("unexpected_trigger",)),
                                             "member_events", enable=False)
    with pytest.raises(RuntimeError, match="unapproved_candidate_copy_triggers"):
        catchup._candidate_copy_trigger_guard(Candidate(()), "guild_secrets",
                                             enable=False)


def test_candidate_only_failures_report_approved_table_but_never_identifier(
    monkeypatch, capsys,
):
    monkeypatch.setenv("DANK_DELTA_CATCHUP_MODE", "preview")
    monkeypatch.setattr(
        catchup, "reconcile",
        lambda _: (_ for _ in ()).throw(
            catchup.StageBlocker("candidate_only_rows_need_review", "activity_feed_events")
        ),
    )
    assert catchup.main() == 2
    result = capsys.readouterr().err
    assert "candidate_only_rows_need_review table=activity_feed_events" in result
    assert "password" not in result


def test_transactional_stage_toggles_only_approved_candidate_user_triggers():
    code = inspect.getsource(catchup.reconcile)
    assert "_candidate_copy_trigger_guard(target, table, enable=False)" in code
    assert "_candidate_copy_trigger_guard(target, table, enable=True)" in code
    assert "if writes and table in _CANDIDATE_COPY_TRIGGERS:" in code
    assert "if difference[\"target_only\"] and mode == \"stage\":" in code
    assert "final_offline_snapshot_not_equal" in code
    assert "recheck_original_snapshot(source_params, source_snapshots, keys)" in code
