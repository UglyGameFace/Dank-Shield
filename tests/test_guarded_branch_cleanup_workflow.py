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
    assert manifest.count("kind: 'merged'") == 21
    assert manifest.count("kind: 'superseded'") == 5
    assert manifest.count("kind: 'closed-record'") == 3
    assert manifest.count("kind: 'ancestor-only'") == 1
    for pr in (46, 94, 171, 214, 216, 229):
        assert f"replacement: {pr}" in manifest


def test_fourth_wave_requires_live_git_ancestry_and_exact_records() -> None:
    text = _workflow()
    assert "review.kind === 'superseded' || review.kind === 'closed-record'" in text
    assert "if (prs.length || !await ancestorOfMain(branch.commit.sha)) continue;" in text
    assert "review.sha !== branch.commit.sha" in text
    assert "if (!original || !replacement) continue;" in text
    assert "pr.head.sha === branch.commit.sha" in text
    assert "inUseByOpenPR.has(name)" in text
    assert "current.protected || current.commit.sha !== candidate.sha" in text
    manifest = text.split("const reviewedRetirements = new Map([", 1)[1].split("]);", 1)[0]
    assert manifest.count("kind:") == 30
    for branch in (
        "aaa-cinema-pre-pregateway-bind",
        "feat/cinema-library-intelligence",
        "fix/prod-shard-guild-loops-no-batching-20261013",
        "fix/source-backup-large-toc-stream-20261013",
        "fix/ticket-panel-legacy-interaction-recovery-20260924",
    ):
        assert "'" + branch + "', { kind:" not in manifest


def test_fifth_wave_only_audited_merged_verifier_and_workflow_branches() -> None:
    text = _workflow()
    manifest = text.split("const reviewedRetirements = new Map([", 1)[1].split("]);", 1)[0]
    exact = {
        "chore/verify-supabase-sync": (154, "79f78cba5d23105869e0debde94029a91bab7ff4"),
        "chore/remove-supabase-sync-verifier": (155, "ab41128b4335af29f21d9766f78bc69abee32695"),
        "chore/verify-ticket-counter-production": (159, "4ccbf727eb69f2ca3e11bf78a4567e6865bcbe3e"),
        "chore/guarded-branch-cleanup-fourth-wave-20261015": (585, "c92fe20e89f80698f5c7b3f20dd070820853d963"),
    }
    for name, (pr, sha) in exact.items():
        entry = f"['{name}', {{ kind: 'merged', pr: {pr}, sha: '{sha}' }}]"
        assert manifest.count(entry) == 1
    # No additional cleanup should target live backup, storage migration,
    # deployment branches, or any source branch of a closed-unmerged PR.
    for name in (
        "chore/automate-supabase-migrations",
        "fix/supabase-out-of-order-migrations",
        "cleanup/schema-authority-consolidation",
        "fix/live-captions-stable-interim-recovery-20260927",
        "feat/native-welcome-cards",
    ):
        assert f"'{name}', {{ kind:" not in manifest
