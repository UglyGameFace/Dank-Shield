"""Archive-first retirement of historical PR #113/#126 integration aliases.

These refs contain divergent commit IDs, but all changed paths are byte-identical
to the immutable merged PR trees. They are NOT eligible for generic ancestor
pruning, and no protected backup or deployment ref is eligible here.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/retire-merged-temporary-branches.yml").read_text()
RELAY = (ROOT / ".github/workflows/owner-reviewed-branch-retirement-command.yml").read_text()


def test_only_three_exact_historical_integration_heads_are_opted_in() -> None:
    pinned = {
        "integration/pr-113-current-main": "e55228c38e9ebcc26bc79718058d44fd31352875",
        "integration/pr-113-squash-current-main": "c3779d8fbe1b87fff99ca44d595e91324541dac3",
        "integration/pr-126-current-main": "e51fe07cd33233860aa96c35bc1a79109d73677d",
    }
    section = WORKFLOW.split("const historicalIntegrationRefs = reconciled ? [", 1)[1].split("] : [];", 1)[0]
    assert section.count("{name:'integration/") == len(pinned)
    for name, sha in pinned.items():
        assert name in section
        assert sha in section
    assert "expectedAhead:3, expectedFiles:9" in section
    assert "expectedAhead:1, expectedFiles:9" in section
    assert "expectedAhead:1, expectedFiles:32" in section


def test_original_pr_merges_and_exact_heads_must_be_in_main() -> None:
    for value in (
        "pull:113", "pull:126",
        "53524b368682a33c0149a818a2e336c8640d2c86",
        "a3ba33007b9ef542460ab1b210f9ce8c3ec3db08",
        "originalRef:'fix/member-risk-card-and-update-noise'",
        "originalRef:'feature/live-profile-cards'",
        "pr.head?.ref !== item.originalRef",
        "pr.head?.sha !== item.prHead",
        "pr.merge_commit_sha !== item.merge",
        "pr.base?.ref !== 'main'",
        "pr.base?.repo?.full_name !== owner + '/' + repo",
        "pr.head?.repo?.full_name !== owner + '/' + repo",
        "base:item.merge,head:currentMain",
        "ancestry.behind_by !== 0",
    ):
        assert value in WORKFLOW


def test_every_unique_changed_path_must_match_immutable_merge_tree() -> None:
    for value in (
        "github.paginate(github.rest.pulls.listFiles",
        "files.length !== item.expectedFiles",
        "new Set(files.map(f => f.filename)).size !== files.length",
        "uniqueDelta.status !== 'ahead'",
        "uniqueDelta.ahead_by !== item.expectedAhead",
        "uniqueDelta.behind_by !== 0",
        "uniqueDelta.files.length !== item.expectedFiles",
        "uniqueDelta.files.length >= 300",
        "JSON.stringify(uniqueDelta.files.map(f => f.filename).sort())",
        "JSON.stringify(record.files.map(f => f.filename).sort())",
        "mergedTree.truncated",
        "oldTree.truncated",
        "file.previous_filename",
        "mergedPaths.get(path) || null",
        "originalPaths.get(path) || null",
        "Integration branch has unmerged content:",
    ):
        assert value in WORKFLOW


def test_retirement_stays_owner_only_sha_leased_and_archive_first() -> None:
    section = WORKFLOW.split("// Explicit owner rejection:", 1)[1].split(
        "const allBranches = await", 1
    )[0]
    for value in (
        "if (!isManual || !ownerCommentRelay || context.actor !== 'UglyGameFace'",
        "protectedHeads.has(target.name)",
        "openPRs.some(pr => pr.head.ref === target.name",
        "currentMain !== mainSha",
        "current.commit.sha !== item.sha",
        "archive.object.sha !== item.sha",
        "lease.object.sha !== item.sha",
        "process.env.CLEANUP_CONFIRMATION !== token",
        "...historicalIntegrationRefs.map(({name,sha}) => ({name,sha}))",
    ):
        assert value in section
    assert section.index("github.rest.git.createRef") < section.index(
        "github.rest.git.deleteRef"
    )
    assert "'preview-reconciled','delete-reconciled'" in RELAY
    assert "/^DELETE_RECONCILED_[0-9A-F]{16}$/" in RELAY
    assert "I_VERIFIED_LIVE_DISCLOUD_BRANCH" in RELAY
