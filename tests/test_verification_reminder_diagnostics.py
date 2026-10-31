from __future__ import annotations

from pathlib import Path

import pytest

from stoney_verify import verification_reminder_postgres as ledger


class FakePgError(Exception):
    def __init__(self, sqlstate: str, text: str = "private://do-not-show"):
        super().__init__(text)
        self.sqlstate = sqlstate


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.current_table = ""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split()).lower()
        self.connection.queries.append((normalized, params))
        assert normalized.startswith("select "), "Diagnostics must never write database state"
        if " limit 0" in normalized:
            self.current_table = normalized.split(" from ")[1].split(" limit ")[0]
            code = self.connection.failures.get(self.current_table)
            if code:
                raise FakePgError(code)
        elif "has_table_privilege" in normalized:
            self.current_table = params[0]

    def fetchone(self):
        return (self.current_table not in self.connection.denied,)


class FakeConnection:
    def __init__(self, *, failures=None, denied=()):
        self.failures = failures or {}
        self.denied = set(denied)
        self.queries = []
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rollbacks += 1


def test_missing_config_reports_exact_blocker_without_connecting(monkeypatch):
    monkeypatch.delenv(ledger.DATABASE_URL_ENV, raising=False)
    monkeypatch.setattr(ledger, "_connect", lambda: pytest.fail("must not connect"))
    report = ledger.diagnose_reminder_ledgers_sync()
    assert not report["all_ready"]
    assert report["status"]["connection"] == "not_configured"
    text = ledger.format_reminder_ledger_diagnostics(report)
    assert "DANK_VERIFY_REMINDER_DATABASE_URL" in text
    assert "Not checked" in text
    assert "No reminders were sent" in text


def test_success_checks_all_three_ledgers_without_reading_rows_or_writing(monkeypatch):
    monkeypatch.setenv(ledger.DATABASE_URL_ENV, "private-url")
    db = FakeConnection()
    monkeypatch.setattr(ledger, "_connect", lambda: db)
    report = ledger.diagnose_reminder_ledgers_sync()
    assert report["all_ready"] is True
    assert len(db.queries) == 6
    assert sum(" limit 0" in sql for sql, _ in db.queries) == 3
    assert sum("has_table_privilege" in sql for sql, _ in db.queries) == 3
    assert db.rollbacks == 0
    message = ledger.format_reminder_ledger_diagnostics(report)
    assert "Scheduled reminder ledger" in message
    assert "Manual reminder budget" in message
    assert "Manual member history" in message
    assert "no reminders were sent" in message.lower()


def test_missing_manual_table_keeps_scheduled_result_and_checks_rest(monkeypatch):
    monkeypatch.setenv(ledger.DATABASE_URL_ENV, "private-url")
    db = FakeConnection(failures={ledger.MANUAL_GUILD_TABLE: "42P01"})
    monkeypatch.setattr(ledger, "_connect", lambda: db)
    report = ledger.diagnose_reminder_ledgers_sync()
    assert report["status"] == {
        "connection": "ok",
        "scheduled": "ok",
        "manual_budget": "missing_table",
        "manual_member": "ok",
    }
    assert not report["all_ready"]
    assert db.rollbacks == 1
    text = ledger.format_reminder_ledger_diagnostics(report)
    assert "Required table is missing" in text
    assert "committed PostgreSQL reminder migration" in text


def test_missing_column_and_permissions_are_distinct(monkeypatch):
    monkeypatch.setenv(ledger.DATABASE_URL_ENV, "private-url")
    db = FakeConnection(
        failures={ledger.TABLE: "42703"},
        denied={ledger.MANUAL_MEMBER_TABLE},
    )
    monkeypatch.setattr(ledger, "_connect", lambda: db)
    report = ledger.diagnose_reminder_ledgers_sync()
    assert report["status"]["scheduled"] == "missing_column"
    assert report["status"]["manual_budget"] == "ok"
    assert report["status"]["manual_member"] == "permission_denied"


@pytest.mark.parametrize("code,expected", [
    ("28P01", "authentication_failed"),
    ("3D000", "database_missing"),
    ("53300", "service_unavailable"),
    ("42501", "permission_denied"),
    ("42P01", "missing_table"),
    ("42703", "missing_column"),
    ("unexpected", "connection_failed"),
])
def test_error_classification_never_echoes_exception_text(code, expected):
    error = FakePgError(code, "postgresql://user:secret@internal/database")
    assert ledger._diagnostic_error_code(error, connecting=True) == expected


def test_driver_absent_is_distinguishable():
    assert ledger._diagnostic_error_code(ModuleNotFoundError("psycopg"), connecting=True) == "driver_missing"


def test_connection_failures_avoid_leaking_secrets(monkeypatch):
    secret = "postgresql://alice:example-password@private.example/db"
    monkeypatch.setenv(ledger.DATABASE_URL_ENV, secret)

    def broken():
        raise FakePgError("28P01", secret)

    monkeypatch.setattr(ledger, "_connect", broken)
    report = ledger.diagnose_reminder_ledgers_sync()
    message = ledger.format_reminder_ledger_diagnostics(report)
    assert report["status"]["connection"] == "authentication_failed"
    assert all(report["status"][key] == "not_checked" for key in ("scheduled", "manual_budget", "manual_member"))
    assert "PostgreSQL rejected" in message
    assert secret not in message
    assert "example-password" not in repr(report)


def test_staff_button_uses_read_only_diagnostics_and_preserves_senders():
    ui = (Path(__file__).resolve().parents[1] /
          "stoney_verify/commands_ext/public_verify_reminder_ui.py").read_text()
    marker = ui.index('    @discord.ui.button(label="Check Database"')
    button = ui[marker:ui.index('    @discord.ui.button(label="Refresh"', marker)]
    assert "await _require_staff(interaction)" in button
    assert "asyncio.to_thread(diagnose_reminder_ledgers_sync)" in button
    assert "format_reminder_ledger_diagnostics(report)" in button
    assert "send_due" not in button and "send_previewed" not in button
