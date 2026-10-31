from __future__ import annotations

from pathlib import Path

import pytest

from tools.verify_live_supabase_migrations import (
    MigrationHistoryMismatch,
    stage_live_migrations,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/deploy-supabase-migrations.yml"


def _fixture(tmp_path: Path) -> tuple[Path, Path, set[str]]:
    repo = tmp_path / "repo"
    workdir = tmp_path / "runner"
    (repo / "supabase_live/migrations").mkdir(parents=True)
    (workdir / "supabase/migrations").mkdir(parents=True)
    baseline_lines = (
        ROOT / "supabase_live/production_baseline_versions.txt"
    ).read_text(encoding="utf-8").splitlines()
    original_versions = {line for line in baseline_lines if line and not line.startswith("#")}
    approved_lines = (
        ROOT / "supabase_live/approved_postcutover_versions.txt"
    ).read_text(encoding="utf-8").splitlines()
    approved_versions = {line for line in approved_lines if line and not line.startswith("#")}
    (repo / "supabase_live/approved_postcutover_versions.txt").write_text(
        "\n".join(sorted(approved_versions)) + "\n", encoding="utf-8"
    )
    versions = original_versions | approved_versions
    (repo / "supabase_live/production_baseline_versions.txt").write_text(
        "\n".join(sorted(original_versions)) + "\n", encoding="utf-8"
    )
    for version in versions:
        (workdir / "supabase/migrations" / f"{version}_applied.sql").write_text(
            "-- remote already applied\n", encoding="utf-8"
        )
    return repo, workdir, versions


def test_live_workflow_hard_rejects_old_project_and_never_uses_legacy_chain() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'expected_live_project="viamriafkbqhquvrsnua"' in text
    assert 'if [ "$SUPABASE_PROJECT_ID" != "$expected_live_project" ]; then' in text
    assert "supabase link --project-ref" in text
    assert text.index("expected_live_project=") < text.index("supabase link --project-ref")
    assert "supabase migration fetch --linked" in text
    assert "tools/verify_live_supabase_migrations.py" in text
    assert "supabase db push --dry-run --include-all" in text
    assert "supabase db push --include-all" in text
    assert 'cd "$LIVE_WORKDIR"' in text
    assert 'group: supabase-production-migrations' in text
    assert 'environment: production' in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "supabase migration repair" not in text
    assert "supabase/migrations/*" not in text


def test_baseline_matches_verified_live_database_snapshot() -> None:
    content = (ROOT / "supabase_live/production_baseline_versions.txt").read_text()
    versions = [
        line.strip() for line in content.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(versions) == len(set(versions)) == 27
    assert "20261023014508" in versions
    assert "20261023014518" in versions
    assert versions == sorted(versions)
    approved = [
        line for line in (ROOT / "supabase_live/approved_postcutover_versions.txt")
        .read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert approved == ["20261028232821", "20261028234027", "20261028235128"]


def test_verified_baseline_is_noop_without_new_reviewed_migrations(tmp_path: Path) -> None:
    repo, workdir, _ = _fixture(tmp_path)
    assert stage_live_migrations(repo, workdir) == (30, 0, 0)


def test_stages_only_future_migrations_and_blocks_unreviewed_history(tmp_path: Path) -> None:
    repo, workdir, versions = _fixture(tmp_path)
    planned = repo / "supabase_live/migrations/20261024000100_add_new_safe_column.sql"
    planned.write_text("select 1;\n")
    assert stage_live_migrations(repo, workdir) == (30, 1, 1)
    staged = workdir / "supabase/migrations" / planned.name
    assert staged.read_text() == planned.read_text()
    assert stage_live_migrations(repo, workdir) == (30, 1, 0)
    planned.write_text("select 2;\n")
    with pytest.raises(MigrationHistoryMismatch, match="differs"):
        stage_live_migrations(repo, workdir)


def test_missing_remote_baseline_or_unknown_remote_version_stops_deploy(tmp_path: Path) -> None:
    repo, workdir, versions = _fixture(tmp_path)
    v = sorted(versions)[0]
    (workdir / "supabase/migrations" / f"{v}_applied.sql").unlink()
    with pytest.raises(MigrationHistoryMismatch, match="missing"):
        stage_live_migrations(repo, workdir)
    (workdir / "supabase/migrations" / f"{v}_applied.sql").write_text("select 1;")
    (workdir / "supabase/migrations/20261024000200_unreviewed.sql").write_text(
        "select 1;"
    )
    with pytest.raises(MigrationHistoryMismatch, match="unreviewed_remote"):
        stage_live_migrations(repo, workdir)


def test_past_and_collision_versions_do_not_replay(tmp_path: Path) -> None:
    repo, workdir, versions = _fixture(tmp_path)
    for value in (sorted(versions)[-1], "20261022080000"):
        path = repo / "supabase_live/migrations" / f"{value}_unsafe.sql"
        path.write_text("select 1;")
        with pytest.raises(MigrationHistoryMismatch, match="predates"):
            stage_live_migrations(repo, workdir)
        path.unlink()


def test_invalid_migration_filename_fails_closed(tmp_path: Path) -> None:
    repo, workdir, _ = _fixture(tmp_path)
    path = repo / "supabase_live/migrations/20261024_unsafe.sql"
    path.write_text("select 1;")
    with pytest.raises(MigrationHistoryMismatch, match="Invalid migration filename"):
        stage_live_migrations(repo, workdir)


def test_postcutover_history_is_required_and_cannot_be_silently_extended(tmp_path: Path) -> None:
    repo, workdir, versions = _fixture(tmp_path)
    approved = repo / "supabase_live/approved_postcutover_versions.txt"
    approved.unlink()
    with pytest.raises(MigrationHistoryMismatch, match="missing"):
        stage_live_migrations(repo, workdir)
    approved.write_text("20261028232821\n20261028234027\n20261028235128\n20261031000000\n")
    with pytest.raises(MigrationHistoryMismatch, match="Unreviewed"):
        stage_live_migrations(repo, workdir)
    approved.write_text("20261028232821\n20261028234027\n20261028235128\n")
    (workdir / "supabase/migrations/20261031000000_unapproved.sql").write_text("select 1;")
    with pytest.raises(MigrationHistoryMismatch, match="unreviewed_remote"):
        stage_live_migrations(repo, workdir)


def test_missing_approved_remote_history_stops_deploy(tmp_path: Path) -> None:
    repo, workdir, _ = _fixture(tmp_path)
    (workdir / "supabase/migrations/20261028234027_applied.sql").unlink()
    with pytest.raises(MigrationHistoryMismatch, match="20261028234027"):
        stage_live_migrations(repo, workdir)
