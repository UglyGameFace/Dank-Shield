"""Owner-reviewed retirement of PR #745's fully merged documentation branch."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/retire-merged-temporary-branches.yml").read_text()


def test_exact_merged_audit_ref_allowed_without_opening_backup_namespaces():
    start = WORKFLOW.index("const integratedCleanupRefs = new Set([")
    end = WORKFLOW.index("const safeName = name =>", start)
    allowlist = WORKFLOW[start:end]
    assert allowlist.count("'audit/idle-grow-discloud-backup-retire-aiven-20261026'") == 1
    assert "'backup/'" in WORKFLOW
    assert "'archive/'" in WORKFLOW
    assert "'recovery/'" in WORKFLOW
    assert "integratedCleanupRefs.has(name)" in WORKFLOW
    assert "branchGraph.ahead_by !== 0" in WORKFLOW
    assert "['behind', 'identical'].includes(branchGraph.status)" in WORKFLOW


def test_retirement_requires_owner_merge_proof_and_archival():
    reviewed = WORKFLOW.split("if (reviewed) {", 1)[1].split(
        "const allBranches =", 1
    )[0]
    assert "if (!isManual || context.actor !== 'UglyGameFace')" in reviewed
    assert "if (prs.length !== 1) continue;" in reviewed
    assert "pr.head.sha !== branch.commit.sha" in reviewed
    assert "graph.behind_by !== 0" in reviewed
    assert "nowOpen.some(pr => pr.head.ref === item.name || pr.base.ref === item.name)" in reviewed
    assert "archive.object.sha !== item.sha" in reviewed
    assert "lease.object.sha !== item.sha" in reviewed
    assert reviewed.index("archive.object.sha !== item.sha") < reviewed.index(
        "github.rest.git.deleteRef({owner,repo,ref:'heads/' + item.name})"
    )
    assert "process.env.CLEANUP_CONFIRMATION !== token" in reviewed
