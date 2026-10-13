"""Manual owner backup workflow must never become implicit production logic.

Static regression guard: any future edit that schedules data exports, uploads
unencrypted files, or starts a migration from this workflow should fail CI.
"""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github/workflows/manual-encrypted-supabase-backup.yml"


def _workflow():
    assert WF.exists()
    return WF.read_text(encoding="utf-8")


def test_only_manual_dispatch_can_trigger_sensitive_export():
    text = _workflow()
    assert re.search(r"(?m)^on:\s*\n\s+workflow_dispatch:\s*$", text)
    assert not re.search(r"(?m)^\s{2}(push|pull_request|schedule|workflow_run|workflow_call):", text)
    assert "github.ref == 'refs/heads/main'" in text
    assert "BACKUP_ONLY_NO_DELETE" in text
    assert "cancel-in-progress: false" in text


def test_source_password_and_passphrase_only_from_actions_secrets():
    text = _workflow()
    assert "secrets.DANK_SOURCE_DATABASE_URL" in text
    assert "secrets.DANK_BACKUP_PASSPHRASE" in text
    assert "inputs.source_project_ref" in text
    assert "DANK_MIGRATION_SOURCE_REF" in text
    assert "Validate required secrets without displaying them" in text
    assert "::add-mask::" not in text  # never print secret values to mask them
    assert "echo \"\$DANK_SOURCE_DATABASE_URL\"" not in text


def test_only_authenticated_encrypted_backup_can_be_uploaded():
    text = _workflow()
    assert "python -m tools.backup_dankshield_supabase_source" in text
    assert "--create --output-dir" in text
    assert "actions/upload-artifact@v4" in text
    assert "dank-shield-source-backup/*.dsbak" in text
    assert "if-no-files-found: error" in text
    assert "retention-days: 3" in text
    assert "compression-level: 0" in text
    assert "pg_dump" in text and "pg_restore" in text
    assert "postgres:17" in text
    assert "secrets.GITHUB_TOKEN" not in text
    assert "pg_restore -d " not in text  # do not restore the source to Discloud
    assert "SUPABASE_SERVICE_ROLE_KEY" not in text
    assert "DANK_CORE_POSTGRES_DATABASE_URL" not in text


def test_private_output_dir_and_failure_gate():
    text = _workflow()
    assert "install -d -m 0700" in text
    assert "umask 077" in text
    assert "set -euo pipefail" in text
    assert "find \"$RUNNER_TEMP/dank-shield-source-backup\"" in text
    assert "test \"$(find" in text
    assert "bash tools/setup_pg17_backup_clients.sh" in text
    wrapper = (ROOT / "tools/setup_pg17_backup_clients.sh").read_text()
    assert 'postgres:17 %s "$@"' in wrapper
    assert "for cmd in pg_dump pg_restore" in wrapper


def test_operator_pg17_wrappers_are_executable_and_preserve_arguments(tmp_path):
    import os
    import subprocess

    scripts = tmp_path / "private"
    helper = ROOT / "tools/setup_pg17_backup_clients.sh"
    subprocess.run(["bash", str(helper), str(scripts)], check=True)
    assert not (scripts.stat().st_mode & 0o077)
    docker_bin = tmp_path / "bin"
    docker_bin.mkdir()
    fake = docker_bin / "docker"
    fake.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n")
    fake.chmod(0o700)
    env = dict(os.environ, PATH=f"{docker_bin}:{os.environ.get('PATH','')}")
    for executable in ("pg_dump", "pg_restore"):
        command = subprocess.run(
            [str(scripts / executable), "--version"], check=True,
            capture_output=True, text=True, env=env,
        )
        assert "postgres:17" in command.stdout
        assert executable in command.stdout
        assert "--version" in command.stdout
