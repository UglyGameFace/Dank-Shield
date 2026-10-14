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
])
def test_redacted_restore_error_categories_do_not_echo_sql(text, category):
    assert recovery._restore_error_category(text) == category
    assert "supabase_admin" not in recovery._restore_error_category(text)
    assert "pg_net" not in recovery._restore_error_category(text)


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
    assert "postgres:17" in text
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
