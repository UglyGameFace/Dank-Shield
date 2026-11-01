"""Regression gates for current zero-ahead branch retirement authority.

The frozen October 22 manifest lived in a retired workflow. Its historical
commits/tags remain in Git; current deletion requires live ancestry and leases.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CANONICAL = WORKFLOWS / "retire-merged-temporary-branches.yml"


def test_legacy_zero_ahead_delete_path_is_gone() -> None:
    assert not (WORKFLOWS / "guarded-merged-branch-cleanup.yml").exists()
    assert not (WORKFLOWS / "guarded-final-two-branches.yml").exists()


def test_canonical_zero_ahead_requires_merged_pr_and_live_sha() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    assert "graph.ahead_by !== 0" in text
    assert "prs.length !== 1" in text
    assert "pr.head?.sha !== sha" in text
    assert "Date.parse(pr.merged_at) > cutoff" in text
    assert "latestOpen.some(pr =>" in text
    assert "mainLease !== startMain || lease.object.sha !== sha" in text
    assert "branch-retirement-integrated/" in text
    assert "archive.object.sha !== sha" in text


def test_owner_reviewed_squash_reconciliation_fails_closed() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    assert "equivalentAtMerge" in text
    assert "branchGraph.ahead_by !== 0" in text
    assert "merged.data.truncated || branchTree.data.truncated" in text
    assert "a.sha === b.sha && a.mode === b.mode && a.type === b.type" in text
    assert "const token = 'DELETE_REVIEWED_' + fingerprint;" in text
    assert "if (deleteReviewed && process.env.CLEANUP_CONFIRMATION !== token)" in text
    assert "await github.rest.git.deleteRef({owner,repo,ref:'heads/' + item.name})" in text
