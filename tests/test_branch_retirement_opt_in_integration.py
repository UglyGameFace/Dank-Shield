"""Execute the real opt-in retirement loop with mocked GitHub APIs.

Squash-merged commits and branches used as a PR base must never be retired.
No GitHub token or real refs are touched by these tests.
"""
from pathlib import Path
import json
import shutil
import subprocess
import textwrap

import pytest


WORKFLOW = (Path(__file__).resolve().parents[1] /
            ".github/workflows/retire-merged-temporary-branches.yml")


def _opt_in_loop():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("            const rows = [];")
    end = workflow.index("            // The opt-in namespace alone", start)
    return textwrap.dedent(workflow[start:end])


def _run(*, unique=False, used_as_base=False, main_moves=False):
    if not shutil.which("node"):
        pytest.skip("Node.js is needed for the real cleanup JS")
    script = """
const fullName = 'UglyGameFace/Dank-Shield', owner = 'UglyGameFace';
const repo = 'Dank-Shield', defaultBranch = 'main';
const name = 'work/dankshield-branch-cleanup-20261101';
const branch = {name, commit: {sha: 'branchsha'}, protected: false};
const candidates = [branch], protectedHeads = new Set(), deleting = true;
const isManual = false, optInPreviewMainSha = 'mainsha';
const calls = [];
let mainReads = 0;
const mergedPR = {
  number: 7, state: 'closed', merged_at: '2026-10-20T00:00:00Z',
  head: {sha: 'branchsha', ref: name, repo: {full_name: fullName}},
  base: {ref: 'main', repo: {full_name: fullName}},
  merge_commit_sha: 'mergesha'
};
const openPR = {
  head: {ref: 'another-branch', repo: {full_name: fullName}},
  base: {ref: name, repo: {full_name: fullName}}
};
const github = {rest: {
  pulls: {list: () => {}},
  repos: {
    getBranch: async ({branch: ref}) => ({
      data: {protected: false, commit: {sha: ref === 'main'
        ? ((++mainReads > 1 && %s) ? 'moved-main' : 'mainsha')
        : 'branchsha'}}
    }),
    compareCommits: async ({base, head}) => ({data: base === 'mainsha'
      ? {ahead_by: %s, status: %s ? 'diverged' : 'behind'}
      : {behind_by: 0, status: 'ahead'}})
  },
  git: {
    getRef: async ({ref}) => {calls.push('get:' + ref);
      return {data: {object: {type: 'commit', sha: 'branchsha'}}};},
    createRef: async () => {calls.push('create');},
    deleteRef: async ({ref}) => {calls.push('delete:' + ref);}
  }
}};
github.paginate = async (method, args) => args.state === 'all'
  ? [mergedPR] : (%s ? [openPR] : []);
(async () => {
%s
return {rows, removed, calls};
})().then(result => console.log(JSON.stringify(result)))
  .catch(error => {console.error(error.stack); process.exitCode = 3;});
""" % (json.dumps(main_moves), int(unique), json.dumps(unique),
       json.dumps(used_as_base), _opt_in_loop())
    return subprocess.run(["node", "-e", script], capture_output=True,
                          text=True, check=False)


@pytest.mark.parametrize("case,reason", [
    ({"unique": True}, "Unique commits require reconciliation"),
    ({"used_as_base": True}, "Head or base of an open PR"),
    ({"main_moves": True}, "Main or active PR changed"),
])
def test_opt_in_retirement_holds_unsafe_ref(case, reason):
    result = _run(**case)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["removed"] == 0
    assert not any(call.startswith("delete:") for call in payload["calls"])
    assert any(reason in row[2] for row in payload["rows"])


def test_integrated_unused_ref_archived_before_retirement():
    result = _run()
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["removed"] == 1
    assert payload["calls"] == [
        "get:heads/work/dankshield-branch-cleanup-20261101",
        "get:tags/branch-retirement-opt-in/work/dankshield-branch-cleanup-20261101",
        "delete:heads/work/dankshield-branch-cleanup-20261101",
    ]


def test_opt_in_checks_ancestry_and_both_pr_directions():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    segment = _opt_in_loop()
    assert "usedByOpenPR(openPRs)" in segment
    assert "usedByOpenPR(latestOpenPRs)" in segment
    assert "integrated.ahead_by !== 0" in segment
    assert "base: optInMainSha, head: branch.commit.sha" in segment
    assert "merged.behind_by !== 0" in segment
    assert "latestMain !== optInMainSha" in segment
    assert workflow.index("Unique commits require reconciliation") < workflow.index(
        "await github.rest.git.deleteRef({ owner, repo, ref: 'heads/' + name })")


def test_manual_batch_selection_is_stable_not_clock_bound() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "const requestedBatch = String(process.env.CLEANUP_BATCH_INDEX || '0');" in workflow
    assert "const candidateBatch = isManual ? Number(requestedBatch) :" in workflow
    assert "Math.floor(Date.now() / 300000) % candidateBatchCount" in workflow
    assert "batchIndex: candidateBatch" in workflow
    assert "candidateBatch >= candidateBatchCount" in workflow
