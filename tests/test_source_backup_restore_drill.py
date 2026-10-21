"""Security and recovery-gate regression checks; never touch real databases."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
import os

import pytest

pytest.importorskip("cryptography")
from stoney_verify import source_pg_backup as backup
from tools import verify_source_backup_restoration as recovery

ROOT = Path(__file__).resolve().parents[1]
PASS = "ONLY-FOR-TEST-very-strong-encryption-passphrase-2026"


def test_disposable_runner_guard_blocks_default_and_remote_instances(monkeypatch):
    for key in (
        "GITHUB_ACTIONS", "CI", "GITHUB_REPOSITORY", "DANK_RECOVERY_DISPOSABLE_PG17",
        "PGHOST", "PGPORT", "PGUSER", "PGDATABASE", "PGPASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(recovery.RecoveryDrillError, match="not_disposable_runner"):
        recovery.validate_disposable_runner()
    checks = {
        "GITHUB_ACTIONS": "true", "CI": "true",
        "GITHUB_REPOSITORY": "UglyGameFace/Dank-Shield",
        "DANK_RECOVERY_DISPOSABLE_PG17": "YES_ISOLATED",
        "PGHOST": "127.0.0.1", "PGPORT": "5432", "PGUSER": "postgres",
        "PGDATABASE": "postgres", "PGPASSWORD": "test-only",
    }
    for k, v in checks.items():
        monkeypatch.setenv(k, v)
    recovery.validate_disposable_runner()
    monkeypatch.setenv("PGUSER", "supabase_admin")
    recovery.validate_disposable_runner()
    monkeypatch.setenv("PGUSER", "unrelated_role")
    with pytest.raises(recovery.RecoveryDrillError, match="not_disposable_runner"):
        recovery.validate_disposable_runner()
    monkeypatch.setenv("PGUSER", "postgres")
    monkeypatch.setenv("PGHOST", "dankbot")
    with pytest.raises(recovery.RecoveryDrillError, match="not_disposable_runner"):
        recovery.validate_disposable_runner()


def test_encrypted_checksum_and_authentication_must_pass_before_restore(
    tmp_path, monkeypatch,
):
    reader = tmp_path / "bin"
    reader.mkdir()
    fake = reader / "pg_restore"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdin.buffer.read(64)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    monkeypatch.setenv("PATH", f"{reader}:{os.environ.get('PATH','')}")
    archive = tmp_path / "private.dsbak"
    with archive.open("wb") as encrypted:
        backup.encrypt_stream_to_file(
            BytesIO(b"PGDMP" + os.urandom(1024 * 1024)), encrypted, PASS
        )
    expected = backup._sha256_file(archive)
    passed = recovery._check_archive(archive, expected, PASS)
    assert passed["authenticated"] and passed["archive_toc_readable"]
    assert passed["encrypted_bytes"] == archive.stat().st_size

    with pytest.raises(recovery.RecoveryDrillError, match="encrypted_archive_checksum_mismatch"):
        recovery._check_archive(archive, "f" * 64, PASS)
    with pytest.raises(recovery.RecoveryDrillError, match="encrypted_archive_auth_or_toc_failed"):
        recovery._check_archive(archive, expected, PASS + "-incorrect")
    tampered = bytearray(archive.read_bytes())
    tampered[-40] ^= 15
    archive.write_bytes(tampered)
    with pytest.raises(recovery.RecoveryDrillError, match="encrypted_archive_checksum_mismatch"):
        recovery._check_archive(archive, expected, PASS)
    with pytest.raises(recovery.RecoveryDrillError, match="encrypted_archive_auth_or_toc_failed"):
        recovery._check_archive(archive, backup._sha256_file(archive), PASS)


@pytest.mark.parametrize("text,category", [
    (b'ERROR: extension "pg_net" is not available',
     "source_extension_not_available_on_plain_pg17"),
    (b'ERROR: role "supabase_admin" does not exist',
     "source_role_not_available_on_plain_pg17"),
    (b"ERROR: permission denied for schema auth",
     "restore_target_permissions_incompatible"),
    (b'ERROR: unrecognized configuration parameter "app.settings"',
     "source_configuration_not_supported_by_plain_pg17"),
    (b"ERROR: connection closed for other reason",
     "pg_restore_sql_failed_unclassified"),
    (b'ERROR: must be superuser to create event trigger on private_schema',
     "restore_target_permissions_incompatible"),
    (b'ERROR: CREATE SUBSCRIPTION cannot run inside a transaction block',
     "restore_transaction_unsupported"),
    (b'ERROR: cannot drop table internal because other objects depend on it',
     "restore_dependency_conflict"),
    (b'ERROR: collation version mismatch in private locale',
     "restore_collation_version_mismatch"),
    (b'ERROR: invalid input syntax for type private_domain: secret',
     "restore_data_constraint_rejected"),
])
def test_redacted_restore_error_categories_do_not_echo_sql(text, category):
    assert recovery._restore_error_category(text) == category
    assert "supabase_admin" not in recovery._restore_error_category(text)
    assert "pg_net" not in recovery._restore_error_category(text)


@pytest.mark.parametrize("stderr, expected", [
    (
        b'pg_restore: from TOC entry 421; 1259 10029 TABLE DATA public.secret_user postgres\n'
        b'pg_restore: error: could not execute query: ERROR: unknown private information',
        "TABLE DATA",
    ),
    (
        b'pg_restore: from TOC entry 44; 2615 9134 SCHEMA secret_schema postgres',
        "SCHEMA",
    ),
    (
        b'pg_restore: from TOC entry 44; 9999 9134 SUPER_SECRET secret_schema',
        "unknown",
    ),
    (b'pg_restore: error: permission denied for secret_schema', "unknown"),
    (b'pg_restore: creating TABLE "public.private_table"\n'
     b'pg_restore: error: could not execute query: ERROR: synthetic failure',
     "TABLE"),
    (b'pg_restore: processing data for table "public.private_table"\n'
     b'pg_restore: error: could not execute query: ERROR: synthetic failure',
     "TABLE DATA"),
    (b'pg_restore: creating PUBLICATION TABLE "public.private_table"\n'
     b'pg_restore: error: could not execute query: ERROR: synthetic failure',
     "PUBLICATION TABLE"),
])
def test_restore_toc_kind_never_discloses_object_names(stderr, expected):
    kind = recovery._restore_failed_object_kind(stderr)
    assert kind == expected
    assert "secret" not in kind.lower()
    assert "public" not in kind.lower()


@pytest.mark.parametrize("stderr, category", [
    (b'pg_restore: error: connection to server at localhost failed: Connection refused',
     "pg_restore_connection_failed"),
    (b'pg_restore: error: input file is too short',
     "pg_restore_archive_read_failed"),
    (b'pg_restore: error: unrecognized option --malformed',
     "pg_restore_invocation_failed"),
    (b'pg_restore: error: unexpected client-side failure',
     "pg_restore_process_failed_unclassified"),
    (b'pg_restore: error: could not execute query: ERROR: private SQL failed',
     "pg_restore_sql_failed_unclassified"),
])
def test_restore_client_and_sql_failures_are_distinct(stderr, category):
    assert recovery._restore_error_category(stderr) == category


def test_restore_preflight_refuses_broken_docker_transport(monkeypatch):
    class Failed:
        returncode = 1
        stdout = b""
    monkeypatch.setenv("PGUSER", "supabase_admin")
    monkeypatch.setenv("PGPASSWORD", "synthetic-test-only")
    monkeypatch.setattr(recovery.subprocess, "run", lambda *a, **kw: Failed())
    with pytest.raises(recovery.RecoveryDrillError,
                       match="restore_container_connection_unavailable"):
        recovery._validate_container_restore_connection()


def test_restore_failure_reports_static_code_and_never_plaintext(monkeypatch, capsys):
    def deny():
        raise recovery.RecoveryDrillError("not_disposable_runner")
    monkeypatch.setattr(recovery, "validate_disposable_runner", deny)
    monkeypatch.setattr("sys.argv", [
        "verify_source_backup_restoration", "--encrypted-archive",
        "/tmp/never-exists.dsbak", "--sha256", "a" * 64,
    ])
    assert recovery.main() == 2
    log = json.loads(capsys.readouterr().out)
    assert log["status"] == "not_recovered"
    assert log["failure_code"] == "not_disposable_runner"
    assert log["source_data_modified"] is False
    assert "never-exists" not in str(log)


def test_restore_workflow_never_exposes_shared_database_or_plaintext_artifacts():
    text = (
        ROOT / ".github/workflows/verify-source-backup9-restore.yml"
    ).read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "38501909516" in text
    assert "84acce40549855387ad04ddaaec41cf845fe12cf73a5e5e88282422eddb1f1c0" in text
    assert "actions/download-artifact@v4" in text
    assert "VERIFY_BACKUP_9_ISOLATED_ONLY" in text
    assert "supabase/postgres:17.6.1.136" in text
    assert "POSTGRES_USER: supabase_admin" in text
    assert "Supabase Vault is missing" in text
    assert "log_min_error_statement = 'panic'" in text
    assert "log_min_messages = 'fatal'" in text
    assert "DANK_BACKUP_PASSPHRASE: ${{ secrets.DANK_BACKUP_PASSPHRASE }}" in text
    assert "DANK_SOURCE_DATABASE_URL" not in text
    assert "DANK_CORE_POSTGRES_DATABASE_URL" not in text
    assert "SUPABASE_SERVICE_ROLE_KEY" not in text
    assert "actions/upload-artifact" not in text
    assert "push:" not in text
    assert "schedule:" not in text
    code = (ROOT / "tools/verify_source_backup_restoration.py").read_text()
    assert "validate_disposable_runner()" in code
    assert "authenticate" in code.lower()
    assert "--single-transaction" in code and "--exit-on-error" in code
    assert 'stdout=subprocess.DEVNULL' in code


def test_sql_error_behind_broken_restore_pipe_is_classified_without_raw_output(
    tmp_path, monkeypatch,
):
    """A real restore SQL failure may close stdin before the archive is streamed."""
    archive = tmp_path / "offline-only.dsbak"
    archive.write_bytes(b"no user data: synthetic fixture")
    digest = backup._sha256_file(archive)
    seen_chunks = []

    def synthetic_verified_archive(_path, _passphrase):
        for chunk in (b"first", b"second", b"authenticated-trailer"):
            seen_chunks.append(chunk)
            yield chunk

    class ClosedInput:
        def write(self, _chunk):
            raise BrokenPipeError()
        def close(self):
            pass

    class FailedRestore:
        stdin = ClosedInput()
        stderr = BytesIO(
            b'pg_restore: from TOC entry 42; 3079 2132 '
            b'EXTENSION supabase_vault postgres\n'
            b'pg_restore: error: could not execute query: ERROR: '
            b'extension "supabase_vault" is not available'
        )
        def wait(self, timeout=None):
            return 1
        def poll(self):
            return 1
        def kill(self):
            raise AssertionError("already stopped")

    class Runner:
        def __call__(self, command, **kwargs):
            assert command[0:2] == ["docker", "run"]
            assert "--single-transaction" in command
            assert "--exit-on-error" in command
            assert "--verbose" in command
            assert kwargs["env"]["PGUSER"] == "supabase_admin"
            return FailedRestore()

    monkeypatch.setenv("PGUSER", "supabase_admin")
    monkeypatch.setenv("PGPASSWORD", "postgres")
    monkeypatch.setattr(recovery, "_iter_verified_plaintext", synthetic_verified_archive)
    monkeypatch.setattr(recovery, "_validate_container_restore_connection", lambda: None)
    monkeypatch.setattr(recovery.subprocess, "Popen", Runner())
    with pytest.raises(
        recovery.RecoveryDrillError,
        match="source_extension_not_available_on_plain_pg17",
    ) as failure:
        recovery._stream_restore(archive, PASS, digest)
    assert failure.value.object_kind == "EXTENSION"
    # Even when the consumer exits, the encrypted reader must reach its end.
    assert len(seen_chunks) == 3
