"""Reconciled branch retirement must preserve original SHA and prove replacement merge."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEANUP = (ROOT / ".github/workflows/retire-merged-temporary-branches.yml").read_text()
RELAY = (ROOT / ".github/workflows/owner-reviewed-branch-retirement-command.yml").read_text()


def test_reconciled_cleanup_is_explicit_and_sha_pinned() -> None:
    assert "preview-reconciled" in CLEANUP
    assert "delete-reconciled" in CLEANUP
    assert "fix/trace-application-command-ingress-20261007" in CLEANUP
    assert "be80019ddcce56dd89daa89aef480c499107f373" in CLEANUP
    assert "const reconciled = mode.endsWith('-reconciled')" in CLEANUP
    assert "pull_number:806" in CLEANUP
    assert "replacement.merged_at" in CLEANUP
    assert "replacement.merge_commit_sha" in CLEANUP
    assert "ancestry.behind_by !== 0" in CLEANUP
    assert "DELETE_RECONCILED_" in CLEANUP


def test_reconciled_cleanup_reuses_archive_first_owner_guards() -> None:
    section = CLEANUP.split("// Explicit owner rejection:", 1)[1].split(
        "const allBranches = await", 1
    )[0]
    assert "!isManual || !ownerCommentRelay" in section
    assert "!deploymentBranchConfigured" in section
    assert "protectedHeads.has(target.name)" in section
    assert "currentMain !== mainSha" in section
    assert "current.commit.sha !== item.sha" in section
    assert "lease.object.sha !== item.sha" in section
    assert "archive.object.sha !== item.sha" in section
    assert section.index("github.rest.git.createRef") < section.index(
        "github.rest.git.deleteRef"
    )


def test_owner_relay_requires_bound_reconciled_confirmation() -> None:
    assert "'preview-reconciled','delete-reconciled'" in RELAY
    assert "input.mode === 'delete-reconciled'" in RELAY
    assert "/^DELETE_RECONCILED_[0-9A-F]{16}$/" in RELAY
    assert "I_VERIFIED_LIVE_DISCLOUD_BRANCH" in RELAY
    assert "context.actor !== 'UglyGameFace'" in RELAY
