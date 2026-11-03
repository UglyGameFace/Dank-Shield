"""Archive-first retirement of historical PR #113/#126 integration aliases.

These refs contain divergent commit IDs, but all changed paths are byte-identical
to the immutable merged PR trees. They are NOT eligible for generic ancestor
pruning, and no protected backup or deployment ref is eligible here.
"""
from pathlib import Path
import shutil
import subprocess

import pytest

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


def test_archive_readback_retries_only_transient_404_after_creation() -> None:
    # GitHub accepted a tag create but immediately returned 404 on readback.
    # All four cleanup paths must wait for exact-SHA verification, fail closed
    # on mismatch, and never delete an unverified original ref.
    assert "const readCreatedArchive = async (tag, expectedSha) =>" in WORKFLOW
    assert "attempt < 5" in WORKFLOW
    assert "error.status !== 404 || attempt === 4" in WORKFLOW
    assert "archive.object.sha !== expectedSha" in WORKFLOW
    assert "throw new Error('Archive SHA mismatch after creation:" in WORKFLOW
    assert "await new Promise(resolve => setTimeout(resolve," in WORKFLOW
    assert WORKFLOW.count("await readCreatedArchive(") == 4
    for expected in (
        "readCreatedArchive(tag, item.sha)",
        "readCreatedArchive(tag, branch.commit.sha)",
        "readCreatedArchive(tagRef, sha)",
    ):
        assert expected in WORKFLOW
    assert "github.rest.git.deleteRef" in WORKFLOW


def test_archive_readback_behavior_with_mocked_github_api() -> None:
    if not shutil.which("node"):
        pytest.skip("Node.js is required for the workflow's JavaScript behavior test")
    helper = "const readCreatedArchive = async (tag, expectedSha) =>" + WORKFLOW.split(
        "const readCreatedArchive = async (tag, expectedSha) =>", 1
    )[1].split("            if (fullName !==", 1)[0]
    script = (
        "const owner='UglyGameFace', repo='Dank-Shield';\n"
        "const setTimeout = fn => { fn(); };\n"
        "let replies = [], calls = 0;\n"
        "const github = {rest:{git:{getRef:async()=>{\n"
        "  calls++; const next = replies.shift();\n"
        "  if (next instanceof Error) throw next; return {data:next};\n"
        "}}}};\n"
        + helper
        + """
(async () => {
  const missing = () => Object.assign(new Error('Not Found'), {status:404});
  replies = [missing(), {object:{type:'commit',sha:'expected'}}];
  calls = 0;
  const recovered = await readCreatedArchive('tags/example', 'expected');
  if (calls !== 2 || recovered.object.sha !== 'expected')
    throw new Error('Transient 404 was not retried and verified');
  replies = [{object:{type:'commit',sha:'wrong'}}]; calls = 0;
  try { await readCreatedArchive('tags/example', 'expected'); throw new Error('accepted wrong SHA'); }
  catch (error) {
    if (!String(error.message).includes('Archive SHA mismatch') || calls !== 1) throw error;
  }
  replies = [Object.assign(new Error('Forbidden'), {status:403})]; calls = 0;
  try { await readCreatedArchive('tags/example', 'expected'); throw new Error('accepted 403'); }
  catch (error) {
    if (error.status !== 403 || calls !== 1) throw error;
  }
  replies = Array.from({length:5}, missing); calls = 0;
  try { await readCreatedArchive('tags/example', 'expected'); throw new Error('accepted missing tag'); }
  catch (error) {
    if (error.status !== 404 || calls !== 5) throw error;
  }
})().catch(error => { console.error(error); process.exit(1); });
"""
    )
    result = subprocess.run(
        ["node", "-e", script], text=True, capture_output=True, timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
