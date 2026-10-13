"""Disposable *real* PG17 backup/restore drill. Never touches Discloud or Supabase.

This test requires Github runner Docker and the isolated PostgreSQL 17 service
started in .github/workflows/discloud-postgres17-contracts.yml. It builds an
encrypted custom dump from a test database, authenticates and lists the TOC,
and restores all archived data into a second temporary PG17 test database.
"""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytest.importorskip("cryptography")
from stoney_verify import source_pg_backup as backup

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PG_BACKUP_INTEGRATION") != "1",
    reason="requires disposable localhost PG17 and Github Docker service",
)

PASSWORD = "only-for-disposable-pg17-ci-test-random-passphrase"


def _run_pg(*args, input=None):
    return subprocess.run(
        ["docker", "run", "--rm", "-i", "--network=host",
         "-e", "PGPASSWORD=postgres", "postgres:17",
         *args], input=input, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True, timeout=90,
    )


def test_disposable_real_pg17_custom_dump_restore_and_voice_canary(
    tmp_path, monkeypatch,
):
    if not shutil.which("docker"):
        pytest.skip("docker CLI required for disposable restore test")
    _run_pg("psql", "-h", "127.0.0.1", "-U", "postgres",
            "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c",
            "create table if not exists public.voice_backup_fixture "
            "(id int primary key, text_value text not null)")
    _run_pg("psql", "-h", "127.0.0.1", "-U", "postgres",
            "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c",
            "insert into public.voice_backup_fixture(id,text_value) "
            "values (1,'preserve voice data') on conflict(id) do update "
            "set text_value=excluded.text_value")

    wrapper_bin = tmp_path / "bin"
    wrapper_bin.mkdir()
    wrapper = wrapper_bin / "pg_restore"
    wrapper.write_text(
        '#!/bin/sh\n'
        'exec docker run --rm -i postgres:17 pg_restore "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    monkeypatch.setenv(
        "PATH", f"{wrapper_bin}:{os.environ.get('PATH','')}"
    )
    # Docker's official postgres:17 image provides matching client tools.
    check = subprocess.run(
        ["pg_restore", "--version"], capture_output=True, check=True, timeout=30
    )
    assert b"17." in check.stdout

    dumping = subprocess.Popen(
        ["docker", "run", "--rm", "-i", "--network=host",
         "-e", "PGPASSWORD=postgres", "postgres:17",
         "pg_dump", "-h", "127.0.0.1", "-U", "postgres", "-d", "postgres",
         "--format=custom", "--no-owner", "--no-privileges"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    encrypted_path = tmp_path / "encrypted.dsbak"
    try:
        with encrypted_path.open("wb") as encrypted:
            assert dumping.stdout is not None
            info = backup.encrypt_stream_to_file(
                dumping.stdout, encrypted, PASSWORD
            )
        assert dumping.wait(timeout=90) == 0
    finally:
        if dumping.poll() is None:
            dumping.kill()
            dumping.wait()
    assert info["plaintext_archive_bytes"] > 100
    verified = backup.verify_archive_toc(encrypted_path, PASSWORD)
    assert verified["authenticated"]
    assert verified["archive_toc_readable"]
    assert verified["restore_drill_passed"] is False

    # Verify a REAL restore in a separate disposable database. Never submit
    # unauthenticated plaintext to pg_restore in production. Here the isolated
    # test archive is generated entirely by our own disposable test database.
    _run_pg("psql", "-h", "127.0.0.1", "-U", "postgres", "-d", "postgres",
            "-v", "ON_ERROR_STOP=1", "-c",
            "create database dank_backup_restore_check")
    restorer = subprocess.Popen(
        ["docker", "run", "--rm", "-i", "--network=host",
         "-e", "PGPASSWORD=postgres", "postgres:17",
         "pg_restore", "-h", "127.0.0.1", "-U", "postgres",
         "-d", "dank_backup_restore_check", "--no-owner",
         "--no-privileges", "--exit-on-error"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert restorer.stdin is not None
        for piece in backup._iter_verified_plaintext(encrypted_path, PASSWORD):
            restorer.stdin.write(piece)
        restorer.stdin.close()
        assert restorer.wait(timeout=90) == 0
    finally:
        if restorer.poll() is None:
            restorer.kill()
            restorer.wait()
    voice = _run_pg(
        "psql", "-h", "127.0.0.1", "-U", "postgres",
        "-d", "dank_backup_restore_check",
        "-Atc", "select text_value from public.voice_backup_fixture where id=1"
    )
    assert voice.stdout.strip() == b"preserve voice data"
