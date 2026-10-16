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
    assert manifest.count("kind: 'merged'") == 45
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
    assert manifest.count("kind:") == 59
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


def test_sixth_wave_exact_sha_and_discloud_dual_ancestry() -> None:
    text = _workflow()
    manifest = text.split("const reviewedRetirements = new Map([", 1)[1].split("]);", 1)[0]
    assert manifest.count("kind: 'linked-ancestor'") == 5
    assert manifest.count("kind: 'merged'") == 45
    assert manifest.count("kind:") == 59
    assert "async function ancestorOfLinkedDiscloud(sha)" in text
    assert "if (!await ancestorOfMain(branch.commit.sha)) continue;" in text
    assert "if (!await ancestorOfLinkedDiscloud(branch.commit.sha)) continue;" in text
    assert "pr.base?.ref === 'aaa-cinema-pre-pregateway-bind'" in text
    assert "if (name === 'aaa-cinema-pre-pregateway-bind') continue;" in text
    expected = {
        "fix/cinema-fullscreen-portrait-recovery-20261010": (499, "1885679d61f212108081e600b4670aed4c9a72fd", "linked-ancestor"),
        "fix/cinema-api-html-json-contract-discloud-20261010": (501, "c00dc081b2dc47d999351e2caec3db669033db07", "linked-ancestor"),
        "fix/share-router-prod-branch-reconcile-20261010": (504, "dbc607a8bf8f39adeb3b4cf7552a6b9922f8a044", "linked-ancestor"),
        "fix/verify-reminders-discloud-postgres-20261011": (511, "e7e5b67dcd2ae0ee085e6e104a7760766caf677c", "linked-ancestor"),
        "fix/ticket-panel-production-parity-20261012": (528, "99a915d169aaf48667e6e82fe526ee440d89873f", "linked-ancestor"),
        "feat/toke-lifecycle-and-coordination-20261014": (560, "4545b88395ef3837e7b48e5176a2a420ecb1ed01", "merged"),
        "feat/toke-host-handoff-20261014": (561, "5de1f9dd06c6e45d42074b9f2167b1a8dafd1e8e", "merged"),
        "feat/toke-voice-lifecycle-20261014": (565, "db0f68fbbe79e10d4cbdda5d50d9812017ddd44b", "merged"),
        "feat/toke-opt-in-reminders-20261014": (566, "161bb5b0946f7be3f45be08f9b7e836b059ba165", "merged"),
        "feat/toke-voice-index-and-noop-20261014": (567, "a7b05a70b5a6cdfaf1139695e96e2f962ad874b0", "merged"),
        "fix/torrent-cache-crash-orphans-shared-storage-20261014": (569, "160cc384a41c6677b8355199c9ba44dd2a1aec2f", "merged"),
        "test/cinema-real-engine-50gib-rolling-proof-20261014": (570, "8f107c1a282faba7e4006173c8e8e1afe2d32a29", "merged"),
        "stabilization/cinema-public-route-smoke-20261015": (574, "363f6677270cd8c6ccc08a904bc5776836f7c596", "merged"),
        "stabilization/cinema-friendly-feed-profile-20261015": (575, "6f1ed13517556813db5a79328b6c548675e07c88", "merged"),
        "stabilization/ticket-topic-privacy-20261015": (577, "e8bbbcd053d8ca56a078dbc2f72ceed8d28804b8", "merged"),
        "stabilization/ticket-topic-repair-20261015": (578, "49f8e9fdca543a742a51e99ec523337ef053c166", "merged"),
        "stabilization/member-center-history-universal-purge-20261015": (579, "fddcb09e6d9113a8ad7a23fce3d4ba2a2a4b8c16", "merged"),
        "feat/embed-studio-mobile-20261015": (580, "0fd37ca813ee16ee30508fdca9355ebf5ac4336b", "merged"),
        "feat/embed-studio-persistent-role-actions-20261015": (581, "335c58f532d7534214bb33eaf0cf755ef045b031", "merged"),
        "chore/guarded-branch-cleanup-fifth-wave-20261015": (586, "702929cd9d6ea3a92ef2637c0f397c9ca240fec7", "merged"),
        "fix/antinuke-panic-containment-respects-alert-and-trust-20261015": (587, "030a12b5834ec0078f356e30cd942dacc73169c7", "merged"),
        "fix/antinuke-gateway-trusted-role-thresholds-20261015": (588, "8f2ef03fcd0ccae1afaebaeeb0842a554b90bfc0", "merged"),
        "fix/antinuke-sparse-bot-add-attribution-20261015": (589, "41cc258097debb10bab6f8b94b4fd2733d3df58f", "merged"),
        "fix/antinuke-invalid-self-marker-20261015": (590, "ca42ebb8b10a9d0d8327784eef11d7b3c28bb980", "merged"),
        "fix/antinuke-malformed-self-marker-receipt-20261015": (591, "2a1b64d19b94413f188508b6b14ad107b6d7b50e", "merged"),
        "fix/antinuke-gateway-panic-trust-consistency-20261015": (592, "f6ea474d25c81db5150fb58112be0a704af04677", "merged"),
        "fix/antinuke-harmless-role-create-panic-20261015": (593, "67b7cd0f1a72b1809eba92f7f73e0127d7ae7580", "merged"),
        "fix/antinuke-harmless-role-create-containment-20261015": (594, "66804ff0cf04cc0a18595e5d82fa2e4ca6341dee", "merged"),
        "fix/antinuke-safe-creation-containment-20261015": (595, "dba7d1d4ba26d2f2969de6b3136508169f167043", "merged"),
    }
    assert len(expected) == 29
    for name, (pr, sha, kind) in expected.items():
        record = f"['{name}', {{ kind: '{kind}', pr: {pr}, sha: '{sha}' }}]"
        assert manifest.count(record) == 1
    for excluded in (
        "main", "aaa-cinema-pre-pregateway-bind",
        "backup/stoneyverify-stable-7fdc331-20261008",
        "fix/source-backup-large-toc-stream-20261013",
        "feat/cinema-library-intelligence",
        "feat/discloud-pg-atomic-guild-config-20261013",
        "fix/antinuke-self-provenance-guard",
    ):
        assert f"['{excluded}', {{ kind:" not in manifest
