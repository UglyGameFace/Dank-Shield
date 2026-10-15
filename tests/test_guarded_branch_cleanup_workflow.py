"""Regression gates for owner-authorized, GitHub-only branch cleanup.

This workflow is intentionally manual and must not affect Discloud runtime,
production data, or any unmerged feature branch.
"""
from __future__ import annotations

from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/guarded-merged-branch-cleanup.yml"
)


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_branch_cleanup_requires_owner_main_and_manual_dispatch() -> None:
    text = _workflow()
    assert "  workflow_dispatch:" in text
    for event in ("push", "pull_request", "schedule", "workflow_run", "workflow_call"):
        assert f"  {event}:" not in text
    assert "github.ref == 'refs/heads/main'" in text
    assert "github.actor == 'UglyGameFace'" in text
    assert "concurrency:" in text
    assert "cancel-in-progress: false" in text


def test_first_wave_is_preserved_and_second_wave_is_separate() -> None:
    text = _workflow()
    for option in (
        "preview", "delete", "preview-next", "delete-next",
        "preview-reviewed", "delete-reviewed",
    ):
        assert f"          - {option}\n" in text
    assert "const mergedBefore = '2026-10-01T00:00:00Z'" in text
    assert "const nextMergedBefore = '2026-10-14T00:00:00Z'" in text
    assert "DELETE_STALE_MERGED_BRANCHES" in text
    assert "DELETE_VERIFIED_NEXT_BATCH" in text
    assert "DELETE_REVIEWED_HISTORICAL_BRANCHES" in text
    assert "const deleting = mode === 'delete' || mode === 'delete-next' || mode === 'delete-reviewed'" in text
    assert "if (deleting) {" in text
    assert "process.env.CLEANUP_CONFIRMATION !== (" in text


def test_preserves_discloud_and_recovery_references() -> None:
    text = _workflow()
    assert "'aaa-cinema-pre-pregateway-bind'" in text
    assert "'backup/stoneyverify-stable-7fdc331-20261008'" in text
    assert "'backup/'" in text
    assert "'archive/'" in text
    assert "'recovery/'" in text
    assert "'reconcile/'" in text
    assert "'integration/'" in text
    assert "'discloud'" in text
    assert "'migration'" in text


def test_second_wave_requires_exact_merged_head_or_git_ancestry() -> None:
    text = _workflow()
    assert "pr.head.sha === branch.commit.sha" in text
    assert "pr.base?.ref === defaultBranch" in text
    assert "pr.base?.ref !== defaultBranch" in text
    assert "async function ancestorOfMain(sha)" in text
    assert "data.ahead_by === 0" in text
    assert "if (await ancestorOfMain(branch.commit.sha))" in text
    assert "if (!prs.length && checkedNoPR.has(name))" in text


def test_no_active_or_changed_branches_deleted() -> None:
    text = _workflow()
    assert "prs.some(pr => !pr.merged_at)" in text
    assert "inUseByOpenPR.has(name)" in text
    assert "name === defaultBranch || branch.protected" in text
    assert "current.protected || current.commit.sha !== candidate.sha" in text
    assert "github.rest.git.deleteRef" in text
    assert "candidates.length > 250" in text
    assert "if (failed.length)" in text


def test_reviewed_wave_is_frozen_and_requires_supersession_evidence() -> None:
    text = _workflow()
    assert "const reviewedRetirements = new Map([" in text
    assert "review.sha !== branch.commit.sha" in text
    assert "pr.number === review.pr && pr.merged_at" in text
    assert "pr.head.sha === branch.commit.sha" in text
    assert "pr.number === review.pr && !pr.merged_at" in text
    assert "pr.number === review.replacement && pr.merged_at" in text
    assert "if (!original || !replacement) continue;" in text
    assert "if (inUseByOpenPR.has(name)) continue;" in text
    assert "if (reviewedBatch) {" in text
    # These unique-commit or linked-production branches must never be auto-retired.
    assert "'aaa-cinema-pre-pregateway-bind', { kind:" not in text
    assert "'feat/cinema-library-intelligence', { kind:" not in text
    assert "'fix/source-backup-large-toc-stream-20261013', { kind:" not in text
    assert "'fix/ticket-panel-legacy-interaction-recovery-20260924', { kind:" not in text


def test_reviewed_wave_has_exact_frozen_retirement_count() -> None:
    text = _workflow()
    manifest = text.split("const reviewedRetirements = new Map([", 1)[1].split("]);", 1)[0]
    assert manifest.count("kind: 'merged'") == 13
    assert manifest.count("kind: 'superseded'") == 4
    for pr in (94, 171):
        assert f"replacement: {pr}" in manifest
