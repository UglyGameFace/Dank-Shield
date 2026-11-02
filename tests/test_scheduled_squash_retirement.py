"""Exercise the scheduled historical cleanup's squash-merge proof gates.

This tests the real JavaScript decision block extracted from the authoritative
workflow, rather than a second Python reimplementation of retirement policy.
"""
import json
import shutil
import subprocess
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/retire-merged-temporary-branches.yml"


def test_scheduled_squash_archive_requires_exact_merge_and_ancestry() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    historical = source.index("const historical = allBranches.filter")
    start = source.index("const graph = (await github.rest.repos.compareCommits({", historical)
    end = source.index("const currentMain = (await github.rest.repos.getBranch({", start)
    decision = source[start:end]

    assert "await equivalentAtMerge(item, pr, graph)" in decision
    assert "base: pr.merge_commit_sha, head: startMain" in decision
    assert "pr.head?.sha !== sha" in decision
    assert "Date.parse(pr.merged_at) > cutoff" in decision
    assert "prs.length !== 1" in decision
    assert decision.index("await equivalentAtMerge(item, pr, graph)") < decision.index(
        "base: pr.merge_commit_sha, head: startMain"
    )
    if shutil.which("node") is None:
        return

    script = r"""
const owner = 'UglyGameFace', repo = 'Dank-Shield';
const fullName = owner + '/' + repo;
const startMain = 'pinned-main';
const cutoff = Date.now() - 48 * 60 * 60 * 1000;
const cases = [
  {name:'direct-good', ahead:0, status:'behind', equivalent:false, merged:true},
  {name:'squash-good', ahead:2, status:'diverged', equivalent:true, merged:true},
  {name:'squash-bad', ahead:2, status:'diverged', equivalent:false, merged:true},
  {name:'merge-not-main', ahead:2, status:'diverged', equivalent:true, merged:false},
  {name:'head-changed', ahead:0, status:'behind', equivalent:false, merged:true, wrongHead:true},
  {name:'recent', ahead:0, status:'behind', equivalent:false, merged:true, recent:true},
  {name:'wrong-base', ahead:0, status:'behind', equivalent:false, merged:true, wrongBase:true},
  {name:'multiple-prs', ahead:0, status:'behind', equivalent:false, merged:true, multiple:true},
];
let active, proofCalls = [];
const github = {
  paginate: async () => {
    const pr = {
      number:17, state:'closed',
      merged_at:active.recent ? new Date().toISOString() : '2026-10-01T00:00:00Z',
      base:{ref:active.wrongBase ? 'staging' : 'main',repo:{full_name:fullName}},
      head:{ref:active.name,sha:active.wrongHead ? 'different' : active.name,
            repo:{full_name:fullName}},
      merge_commit_sha:'merge-' + active.name
    };
    return active.multiple ? [pr,pr] : [pr];
  },
  rest: {
    pulls:{list:()=>{}},
    repos:{compareCommits:async ({base}) => {
      if (base === 'main') return {data:{ahead_by:active.ahead,status:active.status}};
      return {data:{behind_by:active.merged ? 0 : 1,status:'ahead'}};
    }}
  }
};
const equivalentAtMerge = async (item, pr, graph) => {
  proofCalls.push(item.name);
  return active.equivalent;
};
const accepted = [];
(async () => {
  for (active of cases) {
    const item = {name:active.name,commit:{sha:active.name}};
    const name = item.name, sha = item.commit.sha;
""" + json.dumps(decision) + r"""
  }
})().catch(e => {console.error(e.stack);process.exitCode=1});
"""
    # Insert the actual extracted workflow block as executable JS, not a string.
    script = script.replace(json.dumps(decision), decision + "\n    accepted.push(name);")
    script = script.replace(
        "})().catch(e =>",
        "  console.log(JSON.stringify({accepted,proofCalls}));\n})().catch(e =>",
    )
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual["accepted"] == ["direct-good", "squash-good"]
    assert actual["proofCalls"] == ["squash-good", "squash-bad", "merge-not-main"]
