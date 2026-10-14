from __future__ import annotations

from io import BytesIO
from pathlib import Path
import os

import pytest

pytest.importorskip("cryptography")

from stoney_verify import source_pg_backup as backup


REF = "hyncpbvqyjojxhjzcdzg"
PASSPHRASE = "A-uniquely-long-random-test-secret-2026-and-not-production"


def test_strict_direct_supabase_source_identity():
    vars = backup.validate_source_dsn(
        f"postgresql://postgres:secret123@db.{REF}.supabase.co:5432/postgres",
        REF,
    )
    assert vars["PGHOST"] == f"db.{REF}.supabase.co"
    assert vars["PGSSLMODE"] == "require"
    assert vars["PGPASSWORD"] == "secret123"
    assert vars["PGDATABASE"] == "postgres"


def test_pooler_session_connection_bound_to_source_reference():
    vars = backup.validate_source_dsn(
        f"postgresql://postgres.{REF}:secret@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
        REF,
    )
    assert vars["PGUSER"] == f"postgres.{REF}"


@pytest.mark.parametrize("url", [
    f"postgresql://postgres:pass@evil.example.org:5432/postgres",
    f"postgresql://postgres:pass@db.otherprojectaaaaaaaa.supabase.co:5432/postgres",
    f"postgresql://postgres:pass@db.{REF}.supabase.co:6543/postgres",
    f"postgresql://postgres:pass@db.{REF}.supabase.co:5432/template1",
    f"postgresql://postgres:pass@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
    f"http://postgres:pass@db.{REF}.supabase.co:5432/postgres",
    f"postgresql://postgres@db.{REF}.supabase.co:5432/postgres",
    f"postgresql://postgres:pass@db.{REF}.supabase.co:5432/postgres?sslmode=disable&bad=1",
])
def test_unapproved_or_mismatched_pg_source_ref_blocked(url):
    with pytest.raises(ValueError):
        backup.validate_source_dsn(url, REF)


def test_encryption_streams_in_constant_memory_and_roundtrips():
    original = b"PGDMP" + os.urandom(3 * 1024 * 1024)
    encrypted = BytesIO()
    info = backup.encrypt_stream_to_file(BytesIO(original), encrypted, PASSPHRASE)
    assert info["plaintext_archive_bytes"] == len(original)
    ciphertext = encrypted.getvalue()
    assert original[:128] not in ciphertext
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        file = Path(tmp) / "archive.dsbak"
        file.write_bytes(ciphertext)
        assert b"".join(backup._iter_verified_plaintext(file, PASSPHRASE)) == original


def test_bad_passphrase_and_modified_ciphertext_rejected(tmp_path):
    archive = tmp_path / "good.dsbak"
    with archive.open("wb") as out:
        backup.encrypt_stream_to_file(
            BytesIO(b"PGDMP" + b"x" * 80), out, PASSPHRASE
        )
    with pytest.raises(RuntimeError, match="authentication"):
        b"".join(backup._iter_verified_plaintext(archive, "wrong-password-but-still-24-chars"))
    content = bytearray(archive.read_bytes())
    content[len(backup.MAGIC) + backup.SALT_LEN + backup.NONCE_LEN + 6] ^= 0x4F
    archive.write_bytes(content)
    with pytest.raises(RuntimeError, match="authentication"):
        b"".join(backup._iter_verified_plaintext(archive, PASSPHRASE))


def test_unknown_version_truncated_and_oversized_rejected(tmp_path):
    archive = tmp_path / "bad.dsbak"
    archive.write_bytes(b"not_an_archive")
    with pytest.raises(RuntimeError, match="incomplete"):
        b"".join(backup._iter_verified_plaintext(archive, PASSPHRASE))
    archive.write_bytes(b"x" * 120)
    with pytest.raises(RuntimeError, match="unknown"):
        b"".join(backup._iter_verified_plaintext(archive, PASSPHRASE))
    with pytest.raises(RuntimeError, match="safety ceiling"):
        backup.encrypt_stream_to_file(
            BytesIO(b"PGDMP" + b"x" * 99), BytesIO(),
            PASSPHRASE, max_archive_bytes=80,
        )


def test_private_backup_folder_and_symlink_rejected(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    assert backup._output_directory(private) == private
    private.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        backup._output_directory(private)
    private.chmod(0o700)
    symlink = tmp_path / "link"
    symlink.symlink_to(private, target_is_directory=True)
    with pytest.raises(ValueError, match="non-symlink"):
        backup._output_directory(symlink)


def test_no_leaked_application_secrets_to_pg_dump(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "neverexpose")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "privatekey")
    out = backup._sanitized_pg_environment({
        "PGHOST": "db.example.org", "PGPASSWORD": "only-postgres-secret"
    })
    assert out["PGPASSWORD"] == "only-postgres-secret"
    assert "DISCORD_TOKEN" not in out
    assert "SUPABASE_SERVICE_ROLE_KEY" not in out


def test_plan_only_command_does_not_open_database(capsys, monkeypatch):
    from tools.backup_dankshield_supabase_source import main
    monkeypatch.setattr("sys.argv", ["backup_dankshield_supabase_source"])
    assert main() == 0
    output = capsys.readouterr().out
    assert "plan_only" in output
    assert "No database contacted" in output
    assert "neverexpose" not in output


@pytest.mark.parametrize("stderr,expected", [
    (b'pg_dump: error: connection to server failed: FATAL: password authentication failed for user "postgres"',
     "database_authentication_rejected"),
    (b'FATAL: Tenant or user not found', "pooler_project_or_user_rejected"),
    (b'could not translate host name "db.sample.supabase.co" to address',
     "database_dns_lookup_failed"),
    (b'connection to server failed: Network is unreachable',
     "database_network_unreachable"),
    (b'connection refused', "database_connection_refused"),
    (b'ssl error: certificate verify failed', "database_tls_failed"),
    (b'remaining connection slots are reserved', "database_connection_limit"),
    (b'permission denied for schema auth', "database_dump_permission_denied"),
    (b'server version mismatch', "pg_client_server_version_mismatch"),
    (b'server closed the connection unexpectedly', "database_connection_dropped"),
    (b'connection to server at "host" failed for unknown reason',
     "database_connection_failed_unclassified"),
    (b'pg_dump: unrecognized failure', "pg_dump_failed_unclassified"),
])
def test_backup_errors_are_classified_without_untrusted_stderr(stderr, expected):
    code = backup.classify_pg_dump_failure(stderr + b' secret-database-password')
    assert code == expected
    assert "password" not in code
    assert "db.sample" not in code


def test_actual_pg_dump_failure_reports_safe_code_without_leaking_secrets(tmp_path, monkeypatch, capsys):
    """One CLI run with fake pg_dump that simulates a rejected login.

    Source credentials are test data. The fake process must never attempt
    network access. It verifies the prior generic RuntimeError is replaced
    by an actionable, fixed code while partial archives are erased.
    """
    from tools.backup_dankshield_supabase_source import main
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    for tool in ("pg_dump", "pg_restore"):
        script = fake_bin / tool
        script.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ \"$1\" == '--version' ]]; then\n"
            f"  echo '{tool} (PostgreSQL) 17.11'\n"
            "  exit 0\n"
            "fi\n"
            "echo 'pg_dump: error: password authentication failed for user demo "
            "private-password-is-never-printed' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )
        script.chmod(0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv(
        "DANK_SOURCE_DATABASE_URL",
        f"postgresql://postgres:verySecretTestPassword@db.{REF}.supabase.co:5432/postgres",
    )
    monkeypatch.setenv("DANK_MIGRATION_SOURCE_REF", REF)
    monkeypatch.setenv("DANK_BACKUP_PASSPHRASE", PASSPHRASE)
    monkeypatch.setattr("sys.argv", [
        "backup_dankshield_supabase_source", "--create", "--output-dir", str(private)
    ])
    assert main() == 2
    line = capsys.readouterr().out
    assert '"failure_code": "database_authentication_rejected"' in line
    assert "verySecretTestPassword" not in line
    assert "private-password-is-never-printed" not in line
    assert "postgresql://" not in line
    assert list(private.iterdir()) == []


def test_diagnostic_phase_for_source_auth_rejection_is_redacted(
    tmp_path, monkeypatch, capsys,
):
    """The reported phase is evidence, not an inferred cause."""
    from tools.backup_dankshield_supabase_source import main
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for exe in ("pg_dump", "pg_restore"):
        script = fake_bin / exe
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "if '--version' in sys.argv:\n"
            f"    print('{exe} (PostgreSQL) 17.11')\n"
            "else:\n"
            "    print('password authentication failed for confidential-user', "
            "file=sys.stderr)\n"
            "    sys.exit(1)\n",
            encoding="utf-8",
        )
        script.chmod(0o700)
    out = tmp_path / "private"
    out.mkdir(mode=0o700)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv(
        "DANK_SOURCE_DATABASE_URL",
        f"postgresql://postgres:secret-keep-private@db.{REF}.supabase.co:5432/postgres",
    )
    monkeypatch.setenv("DANK_MIGRATION_SOURCE_REF", REF)
    monkeypatch.setenv("DANK_BACKUP_PASSPHRASE", PASSPHRASE)
    monkeypatch.setattr(
        "sys.argv", ["backup", "--create", "--output-dir", str(out)]
    )
    assert main() == 2
    output = capsys.readouterr().out
    assert '"failure_code": "database_authentication_rejected"' in output
    assert '"error_kind": "controlled_backup_failure"' in output
    assert '"phase_at_failure": "encrypted_stream_started"' in output
    assert "secret-keep-private" not in output
    assert "confidential-user" not in output
    assert list(out.iterdir()) == []


def test_backup_recovers_when_archive_toc_reader_exits_early(
    tmp_path, monkeypatch, capsys,
):
    """Reproduce the confirmed Backup #8 pipe close through the actual CLI.

    The early-closed pg_restore process exits zero; the full ciphertext
    must still authenticate and be committed, without exposing credentials.
    """
    from tools.backup_dankshield_supabase_source import main
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    dump = fake_bin / "pg_dump"
    dump.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('pg_dump (PostgreSQL) 17.11')\n"
        "else:\n"
        "    sys.stdout.buffer.write(b'PGDMP' + os.urandom(8*1024*1024))\n",
        encoding="utf-8",
    )
    dump.chmod(0o700)
    restore = fake_bin / "pg_restore"
    restore.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--version' in sys.argv:\n"
        "    print('pg_restore (PostgreSQL) 17.11')\n"
        "else:\n"
        "    sys.stdin.buffer.read(64)\n"
        "    sys.exit(0)\n",
        encoding="utf-8",
    )
    restore.chmod(0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv(
        "DANK_SOURCE_DATABASE_URL",
        f"postgresql://postgres:secret-keep-private@db.{REF}.supabase.co:5432/postgres",
    )
    monkeypatch.setenv("DANK_MIGRATION_SOURCE_REF", REF)
    monkeypatch.setenv("DANK_BACKUP_PASSPHRASE", PASSPHRASE)
    monkeypatch.setattr(
        "sys.argv", ["backup", "--create", "--output-dir", str(private)]
    )
    assert main() == 0
    output = capsys.readouterr().out
    assert '"status": "encrypted_archive_toc_verified"' in output
    assert '"restore_drill_passed": false' in output
    assert "secret-keep-private" not in output
    assert len(list(private.glob("*.dsbak"))) == 1


def test_large_archive_when_nonexecuting_toc_reader_exits_early(tmp_path, monkeypatch):
    """A TOC-only pg_restore can exit before consuming a large archive.

    The rest of the ciphertext must STILL be GCM-authenticated. Simulates
    the allowed early-stop behavior without reaching a real database.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_restore = fake_bin / "pg_restore"
    fake_restore.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdin.buffer.read(64)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    fake_restore.chmod(0o700)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH','')}")
    archive = tmp_path / "large.dsbak"
    payload = b"PGDMP" + os.urandom(7 * 1024 * 1024)
    with archive.open("wb") as output:
        backup.encrypt_stream_to_file(BytesIO(payload), output, PASSPHRASE)
    result = backup.verify_archive_toc(archive, PASSPHRASE)
    assert result["authenticated"]
    assert result["archive_toc_readable"]
    assert result["plaintext_archive_bytes"] == len(payload)
    assert result["restore_drill_passed"] is False

    # An exit-zero TOC reader MUST NOT be enough if later ciphertext was
    # tampered with. Authenticate everything even after the early EPIPE.
    tampered = bytearray(archive.read_bytes())
    tampered[-backup.TAG_LEN-1024] ^= 0x29
    archive.write_bytes(tampered)
    with pytest.raises(RuntimeError, match="authentication failed"):
        backup.verify_archive_toc(archive, PASSPHRASE)


def test_early_toc_reader_failure_still_rejects_archive(tmp_path, monkeypatch):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_restore = fake_bin / "pg_restore"
    fake_restore.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdin.buffer.read(64)\n"
        "sys.exit(4)\n",
        encoding="utf-8",
    )
    fake_restore.chmod(0o700)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH','')}")
    archive = tmp_path / "invalid.dsbak"
    with archive.open("wb") as output:
        backup.encrypt_stream_to_file(
            BytesIO(b"PGDMP" + os.urandom(6 * 1024 * 1024)),
            output, PASSPHRASE
        )
    with pytest.raises(RuntimeError, match="rejected custom-format archive TOC"):
        backup.verify_archive_toc(archive, PASSPHRASE)
