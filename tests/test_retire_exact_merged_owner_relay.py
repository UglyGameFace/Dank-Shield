"""The obsolete owner relay is the only additional work/* ref eligible for review."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/retire-merged-temporary-branches.yml").read_text()


def test_only_exact_original_relay_head_can_enter_reviewed_cleanup():
    start = WORKFLOW.index("const reconciledWorkHeads = new Map([")
    end = WORKFLOW.index("const safeName = name =>", start)
    allowlist = WORKFLOW[start:end]
    assert "work/dankshield-owner-cleanup-relay-20261101" in allowlist
    assert "bafe17a86225d71700f65496fb8339190418792f" in allowlist
    assert allowlist.count("['work/") == 1
    assert "reconciledWorkHeads.has(name)" in WORKFLOW
    assert "reconciledWorkHeads.get(branch.name) !== branch.commit.sha" in WORKFLOW


def test_original_merged_tree_and_archive_guards_are_still_mandatory():
    assert "if (prs.length !== 1) continue;" in WORKFLOW
    assert "pr.head.sha !== branch.commit.sha" in WORKFLOW
    assert "equivalentAtMerge(branch, pr, branchGraph)" in WORKFLOW
    assert "graph.behind_by !== 0" in WORKFLOW
    assert "nowOpen.some(pr => pr.head.ref === item.name" in WORKFLOW
    assert "archive.object.sha !== item.sha" in WORKFLOW
    assert "lease.object.sha !== item.sha" in WORKFLOW
    assert "process.env.CLEANUP_CONFIRMATION !== token" in WORKFLOW
    assert "if (!isManual || context.actor !== 'UglyGameFace')" in WORKFLOW
