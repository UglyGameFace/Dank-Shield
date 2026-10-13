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
