"""Ensure historical and recent merged heads share one guarded retirement authority.

The old post-merge auto-archive job could delete temporary refs even when the
Discloud deployment ref was unknown. The scheduled/push cleanup must be the
sole automatic retirement implementation, and must fail closed on deletion.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RETIRE = WORKFLOWS / "retire-merged-temporary-branches.yml"
OLD_AUTO = WORKFLOWS / "auto-archive-merged-pr-branches.yml"
LEGACY_REVIEWED = WORKFLOWS / "guarded-merged-branch-cleanup.yml"
LEGACY_FINAL_PAIR = WORKFLOWS / "guarded-final-two-branches.yml"


def test_duplicate_post_merge_deletion_workflow_is_removed() -> None:
    assert not OLD_AUTO.exists(), "Unsafe overlapping auto-archive authority returned"
    text = RETIRE.read_text(encoding="utf-8")
    assert "  push:\n    branches:\n      - main" in text
    assert "cron: '*/5 * * * *'" in text
    assert "group: guarded-merged-branch-cleanup" in text
    assert "cancel-in-progress: false" in text
    assert not LEGACY_REVIEWED.exists(), "Duplicate historical deletion workflow returned"
    assert not LEGACY_FINAL_PAIR.exists(), "Duplicate final-pair deletion workflow returned"
    assert "  workflow_call:" in text
    assert "delete-reviewed-merged" in text


def test_automatic_historical_retirement_fails_closed_without_deploy_ref() -> None:
    text = RETIRE.read_text(encoding="utf-8")
    assert "deploymentBranchConfigured = Boolean(deployed.commit?.sha);" in text
    assert "const canRetire = deploymentBranchConfigured;" in text
    assert "if (!canRetire) {" in text
    assert "archivedOnly++;" in text
    assert "graph.ahead_by !== 0" in text
    assert "prs.length !== 1" in text
    assert "pr.head?.sha !== sha" in text
    assert "archive.object.sha !== sha" in text
    assert "latestOpen.some(pr =>" in text
    assert "mainLease !== startMain || lease.object.sha !== sha" in text
    assert text.index("if (!canRetire) {") < text.index(
        "await github.rest.git.deleteRef({owner, repo, ref: 'heads/' + name})"
    )


def test_previous_auto_archive_hotfix_scope_is_preserved_safely() -> None:
    text = RETIRE.read_text(encoding="utf-8")
    assert "const historicalPrefixes = ['feat/', 'fix/', 'hotfix/', 'extract/', 'docs/', 'audit/', 'chore/']" in text
    assert "const prefixes = ['feat/', 'fix/', 'hotfix/', 'extract/', 'docs/', 'audit/', 'chore/']" in text
    for excluded in ("'backup/'", "'recovery/'", "'migration/'",
                     "'supabase'", "'database'", "'discloud'",
                     "'rollback'"):
        assert excluded in text
