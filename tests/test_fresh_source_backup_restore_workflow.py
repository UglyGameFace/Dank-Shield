"""Guard the exact fresh backup restore; no production connections or exports."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_fresh_restore_is_owner_only_manual_and_checksum_pinned():
    f = ROOT / ".github/workflows/verify-fresh-source-backup-restore.yml"
    code = f.read_text("utf-8")
    assert "  workflow_dispatch:" in code
    for auto in ("  push:", "  workflow_run:", "  schedule:", "  pull_request:"):
        assert auto not in code
    assert "github.actor == 'UglyGameFace'" in code
    assert "github.ref == 'refs/heads/main'" in code
    assert "VERIFY_FRESH_SOURCE_BACKUP_ISOLATED_ONLY" in code
    assert "run-id: 39249241539" in code
    assert "df43be1c3a472c9c05a97f4a0ee0ad25e59c443481d76ce2db7fadfd82cb30f7" in code
    assert "supabase/postgres:17.6.1.136" in code
    assert "expected exactly one" in code.lower()
    assert "sha256sum" in code
    assert "DANK_BACKUP_PASSPHRASE: ${{ secrets.DANK_BACKUP_PASSPHRASE }}" in code
    assert "tools.verify_source_backup_restoration" in code
    assert "DANK_RECOVERY_DISPOSABLE_PG17: YES_ISOLATED" in code
    assert "log_min_error_statement = 'panic'" in code
    for unsafe in ("actions/upload-artifact", "DANK_SOURCE_DATABASE_URL", "AIVEN_POSTGRES_URI", "DISCLOUD_TOKEN", "SUPABASE_DB_PASSWORD", "supabase db push"):
        assert unsafe not in code


def test_historical_backup9_workflow_is_untouched():
    original = (ROOT / ".github/workflows/verify-source-backup9-restore.yml").read_text("utf-8")
    assert "38501909516" in original
    assert "84acce40549855387ad04ddaaec41cf845fe12cf73a5e5e88282422eddb1f1c0" in original
