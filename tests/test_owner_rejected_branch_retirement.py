"""Safety contract for owner-rejected, archive-first historical branch retirement."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEANUP = (ROOT / ".github/workflows/retire-merged-temporary-branches.yml").read_text()
RELAY = (ROOT / ".github/workflows/owner-reviewed-branch-retirement-command.yml").read_text()


def test_rejected_work_is_exact_sha_pinned_and_not_scheduled():
    # Both owner-rejected and reconciled retirements share the same gated path.
    assert "'preview-rejected', 'delete-rejected'" in CLEANUP
    assert "if (!isManual || !ownerCommentRelay || context.actor !== 'UglyGameFace'" in CLEANUP
    assert "const rejectedHeads = reconciled ? [" in CLEANUP
    assert "!isManual || !ownerCommentRelay" in CLEANUP
    assert "!deploymentBranchConfigured" in CLEANUP
    assert "feat/cinema-rolling-supervised-worker-20261014" in CLEANUP
    assert "8bccd1ee9a5203cebc7862eec8addd48ba9706a6" in CLEANUP
    assert "fix/517-permanent-optout-20261013" in CLEANUP
    assert "96117053c1b4c24a9d3e7cf6e914e334c9398a85" in CLEANUP
    assert "protectedHeads.has(target.name)" in CLEANUP
    assert "openPRs.some(pr => pr.head.ref === target.name" in CLEANUP


def test_rejected_work_uses_archive_before_delete_and_ref_leases():
    section = CLEANUP.split("// Explicit owner rejection:", 1)[1].split(
        "const allBranches = await", 1
    )[0]
    assert section.index("github.rest.git.createRef") < section.index(
        "github.rest.git.deleteRef"
    )
    assert section.index("archive.object.sha !== item.sha") < section.index(
        "github.rest.git.deleteRef"
    )
    assert section.index("lease.object.sha !== item.sha") < section.index(
        "github.rest.git.deleteRef"
    )
    assert "currentMain !== mainSha" in section
    assert "current.commit.sha !== item.sha" in section
    assert "DELETE_REJECTED_" in section
    assert "process.env.CLEANUP_CONFIRMATION !== token" in section


def test_owner_relay_accepts_only_bound_rejected_delete_token():
    assert "'preview-rejected','delete-rejected'" in RELAY
    assert "input.mode === 'delete-rejected'" in RELAY
    assert "/^DELETE_REJECTED_[0-9A-F]{16}$/" in RELAY
    assert "I_VERIFIED_LIVE_DISCLOUD_BRANCH" in RELAY
    assert "context.actor !== 'UglyGameFace'" in RELAY
