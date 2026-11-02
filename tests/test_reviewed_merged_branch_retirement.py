"""Guardrails for owner-reviewed merged-branch retirement.

The scheduled job may retire only opt-in exact-head merged branches. The
broader historical wave always requires a SHA-bound owner preview token.
"""
from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/retire-merged-temporary-branches.yml"
)


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_five_minute_schedule_is_serialized_and_not_nightly_gated() -> None:
    text = _workflow()
    assert "cron: '*/5 * * * *'" in text
    assert "group: guarded-merged-branch-cleanup" in text
    assert "cancel-in-progress: false" in text
    assert "if (localHour !== '03')" not in text
    assert "timeZone: 'America/New_York'" not in text
    # Scheduled runs may retire only narrowly opted-in, verified merged heads.
    assert "const mode = isManual ? process.env.REQUESTED_MODE : 'delete';" in text
    assert "const reviewed = mode === 'preview-reviewed-merged' || mode === 'delete-reviewed-merged';" in text
    assert "if (!isManual || context.actor !== 'UglyGameFace')" in text


def test_broad_retirement_is_manual_and_sha_leased() -> None:
    text = _workflow()
    assert "preview-reviewed-merged" in text
    assert "delete-reviewed-merged" in text
    assert "context.actor !== 'UglyGameFace'" in text
    assert "DANK_DISCLOUD_DEPLOY_BRANCH" in text
    assert "CLEANUP_CONFIRMATION" in text
    assert "createHash('sha256')" in text
    assert "JSON.stringify({mainSha,batch,effectiveDeployBranch})" in text
    assert "DELETE_REVIEWED_" in text
    assert "eligible.slice(0, 12)" in text



def test_owner_preview_exposes_sha_bound_plan_in_actions_logs() -> None:
    text = _workflow()
    reviewed = text.split("if (reviewed) {", 1)[1].split("const allBranches =", 1)[0]
    assert "if (!deleteReviewed) {" in reviewed
    assert "core.info('Reviewed retirement preview: ' + JSON.stringify({" in reviewed
    assert "mainSha, effectiveDeployBranch, eligible: eligible.length," in reviewed
    assert "before: branches.length, batch" in reviewed
    assert "core.info('Reviewed retirement confirmation token: ' + token);" in reviewed
    assert reviewed.index("const token = 'DELETE_REVIEWED_'") < reviewed.index(
        "core.info('Reviewed retirement confirmation token:"
    )
    assert reviewed.index(
        "core.info('Reviewed retirement confirmation token:"
    ) < reviewed.index(
        "if (deleteReviewed && process.env.CLEANUP_CONFIRMATION !== token)"
    )
    assert "JSON.stringify({mainSha,batch,effectiveDeployBranch})" in reviewed
    assert "const batch = eligible.slice(0, 12);" in reviewed



def test_reviewed_merged_branch_requires_actual_integration() -> None:
    text = _workflow()
    for proof in (
        "prs.length !== 1",
        "pr.merged_at",
        "pr.head.sha !== branch.commit.sha",
        "pr.base?.ref !== 'main'",
        "pr.merge_commit_sha",
        "github.rest.repos.compareCommits",
        "branchGraph.ahead_by !== 0",
        "base: mainSha, head: branch.commit.sha",
        "graph.behind_by !== 0",
        "Date.parse(pr.merged_at) > Date.now()",
        "currentMain !== mainSha",
        "current.commit.sha !== item.sha",
        "nowOpen.some(pr => pr.head.ref === item.name || pr.base.ref === item.name)",
    ):
        assert proof in text


def test_no_unpreserved_work_or_production_refs_deleted() -> None:
    text = _workflow()
    for hold in (
        "aaa-cinema-pre-pregateway-bind",
        "backup/",
        "archive/",
        "recovery/",
        "integration/",
        "migration/",
        "'discloud'",
        "'supabase'",
        "'postgres'",
        "'rollback'",
        "scale/production-readiness-10k",
    ):
        if hold == "scale/production-readiness-10k":
            continue  # The broad reviewed allowlist excludes scale/ by construction.
        assert hold in text
    assert text.index("branchGraph.ahead_by !== 0") < text.index("eligible.push({name: branch.name")
    assert "archive.object.sha !== item.sha" in text
    assert "lease.object.sha !== item.sha" in text
    assert text.index("archive.object.sha !== item.sha") < text.index(
        "ref:'heads/' + item.name"
    )
    assert "github.rest.git.deleteRef({owner,repo,ref:'heads/' + item.name})" in text


def test_opt_in_deletion_is_archived_and_high_branch_count_is_not_failure() -> None:
    text = _workflow()
    assert "branch-retirement-opt-in/" in text
    assert "archive.object.sha !== branch.commit.sha" in text
    assert "core.warning('Branch count is '" in text
    assert "core.setFailed('Branch count is '" not in text
    assert "Math.ceil(eligibleCandidates.length / 20)" in text
    assert "eligibleCandidates.slice(candidateStart, candidateStart + 20)" in text
    assert "eligibleCandidates.length > candidates.length" in text


def test_no_opt_in_delete_when_deployment_reference_is_unknown() -> None:
    text = _workflow()
    assert "if (isManual && mode === 'delete' && !deploymentBranchConfigured)" in text
    assert "const deleting = mode === 'delete' && deploymentBranchConfigured;" in text
    assert text.index("const deleting = mode === 'delete' && deploymentBranchConfigured;") < text.index("if (deleting) {", text.index("const allBranches ="))


def test_reviewed_inventory_uses_one_paged_pr_index_without_loosening_guards() -> None:
    text = _workflow()
    reviewed = text.split("if (reviewed) {", 1)[1].split("const allBranches =", 1)[0]
    assert "const allPRs = await github.paginate(github.rest.pulls.list" in reviewed
    assert "const prsByHead = new Map();" in reviewed
    assert "pr.head?.repo?.full_name !== fullName" in reviewed
    assert "const prs = prsByHead.get(branch.name) || [];" in reviewed
    assert "prs.length !== 1" in reviewed
    assert "head: owner + ':' + branch.name" not in reviewed
    assert "branchGraph.ahead_by !== 0" in reviewed
    assert "graph.behind_by !== 0" in reviewed
    assert "Date.parse(pr.merged_at) > cutoff" not in reviewed
    assert "Date.parse(pr.merged_at) > Date.now()" in reviewed
    assert "Date.parse(pr.merged_at) > cutoff" in text.split("const allBranches =", 1)[1]
    assert "deleteReviewed && process.env.CLEANUP_CONFIRMATION !== token" in reviewed


def test_reviewed_pr_inventory_executable_keeps_only_integrated_unique_head() -> None:
    import json
    import shutil
    import subprocess
    import textwrap

    if shutil.which("node") is None:
        return  # Node is also exercised in dedicated CI workflow tests.
    text = _workflow()
    segment = text.split("              const allPRs = await", 1)[1]
    segment = "const allPRs = await" + segment.split("              eligible.sort(", 1)[0]
    segment = textwrap.dedent(segment)
    equivalent = text.split("            const equivalentAtMerge = ", 1)[1]
    equivalent = "const equivalentAtMerge = " + equivalent.split(
        "            const reviewed = ", 1
    )[0]
    equivalent = textwrap.dedent(equivalent)
    js = r"""
const owner = 'UglyGameFace', repo = 'Dank-Shield';
const fullName = owner + '/' + repo, mainSha = 'mainsha';
const inUse = new Set(['feat/open']);
const safeName = name => name.startsWith('feat/');
const cutoff = Date.now() - 48 * 60 * 60 * 1000;
const branches = [
  {name:'feat/integrated',protected:false,commit:{sha:'oldsha'}},
  {name:'feat/recent-merged',protected:false,commit:{sha:'recentsha'}},
  {name:'feat/unique',protected:false,commit:{sha:'uniquesha'}},
  {name:'feat/unique-bad',protected:false,commit:{sha:'badsha'}},
  {name:'feat/mode-bad',protected:false,commit:{sha:'modesha'}},
  {name:'feat/truncated',protected:false,commit:{sha:'truncatedsha'}},
  {name:'feat/deleted',protected:false,commit:{sha:'deletedsha'}},
  {name:'feat/multi',protected:false,commit:{sha:'multisha'}},
  {name:'feat/open',protected:false,commit:{sha:'opensha'}},
  {name:'backup/retained',protected:false,commit:{sha:'backupsha'}}
];
const mkpr = (ref, sha, n) => ({
  number:n, state:'closed', merged_at:'2026-10-01T00:00:00Z',
  head:{repo:{full_name:fullName},ref,sha},
  base:{repo:{full_name:fullName},ref:'main'},
  merge_commit_sha:'mergedsha' + n
});
const records = [
  mkpr('feat/integrated','oldsha',1),
  {...mkpr('feat/recent-merged','recentsha',12), merged_at:new Date().toISOString()},
  mkpr('feat/unique','uniquesha',2),
  mkpr('feat/unique-bad','badsha',8),
  mkpr('feat/mode-bad','modesha',9),
  mkpr('feat/truncated','truncatedsha',10),
  mkpr('feat/deleted','deletedsha',11),
  mkpr('feat/multi','multisha',3),
  mkpr('feat/multi','multisha',4),
  mkpr('feat/open','opensha',5),
  mkpr('backup/retained','backupsha',6),
  { ...mkpr('feat/integrated','oldsha',7),
    head:{repo:{full_name:'other/repo'},ref:'feat/integrated',sha:'oldsha'}}
];
let paginated = 0, compared = 0;
const github = {
  paginate: async () => { paginated++; return records; },
  rest: {
    pulls: {list: () => {}},
    repos: {compareCommits: async ({base,head}) => {
      compared++;
      if (base === mainSha) return {data:{
        ahead_by:['uniquesha','badsha','modesha','truncatedsha','deletedsha'].includes(head) ? 1 : 0,
        status:['uniquesha','badsha','modesha','truncatedsha','deletedsha'].includes(head) ? 'diverged' : 'behind',
        merge_base_commit:{sha:'forksha'}}};
      if (base === 'forksha') return {data:{
        status:'ahead',ahead_by:1,total_commits:1,
        files:[{filename:head === 'deletedsha' ? 'old.py' : 'feature.py',
          status:head === 'deletedsha' ? 'removed' : 'modified'}]}};
      return {data:{behind_by:0,status:'ahead'}};
    }},
    git: {getTree: async ({tree_sha}) => {
      if (tree_sha === 'truncatedsha') return {data:{truncated:true,tree:[]}};
      if (tree_sha === 'deletedsha' || tree_sha === 'mergedsha11')
        return {data:{truncated:false,tree:[]}};
      const blob = tree_sha === 'badsha' ? 'badblob' : 'goodblob';
      const mode = tree_sha === 'modesha' ? '100755' : '100644';
      return {data:{truncated:false,tree:[
        {path:'feature.py',type:'blob',mode,sha:blob}
      ]}};
    }}
  }
};
""" + equivalent + r"""
(async () => {
""" + segment + r"""
console.log(JSON.stringify({eligible,paginated,compared}));
})().catch(error => {console.error(error.stack);process.exitCode=1});
"""
    result = subprocess.run(["node", "-e", js], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual["paginated"] == 1
    assert actual["eligible"] == [
        {"name": "feat/integrated", "sha": "oldsha", "pr": 1},
        {"name": "feat/recent-merged", "sha": "recentsha", "pr": 12},
        {"name": "feat/unique", "sha": "uniquesha", "pr": 2},
        {"name": "feat/deleted", "sha": "deletedsha", "pr": 11},
    ]
    assert actual["compared"] >= 13


def test_squash_reconciliation_requires_complete_fork_delta_and_merge_tree_parity() -> None:
    text = _workflow()
    for proof in (
        "const equivalentAtMerge = async (branch, pr, graph) =>",
        "graph.merge_base_commit?.sha",
        "graph.ahead_by > 100",
        "Number.isInteger(graph.ahead_by)",
        "Number.isInteger(delta.total_commits)",
        "Array.isArray(merged.data.tree)",
        "delta.total_commits > 100",
        "delta.files.length >= 300",
        "tree_sha: pr.merge_commit_sha",
        "tree_sha: branch.commit.sha",
        "merged.data.truncated || branchTree.data.truncated",
        "file.previous_filename",
        "a.sha === b.sha && a.mode === b.mode && a.type === b.type",
        "await equivalentAtMerge(branch, pr, branchGraph)",
        "archive.object.sha !== item.sha",
        "current.commit.sha !== item.sha",
    ):
        assert proof in text
    assert "reconciledSquash" not in text
    assert "tree_sha: mainSha" not in text


def test_feature_recovery_names_are_not_mistaken_for_backup_refs() -> None:
    """Run the actual workflow name classifier against feature and safety refs."""
    import json
    import shutil
    import subprocess
    import textwrap

    text = _workflow()
    assert "!hasHeldHistoricalSegment(name, sensitive)" in text
    assert "!hasHeldHistoricalSegment(branch.name, heldSegments)" in text
    if shutil.which("node") is None:
        return
    classifier = "const hasHeldHistoricalSegment = " + textwrap.dedent(
        text.split("            const hasHeldHistoricalSegment = ", 1)[1]
            .split("            const reviewed = ", 1)[0]
    )
    js = classifier + """
const held = new Set([
  'backup', 'restore', 'recovery', 'archive', 'release', 'deploy',
  'deployment', 'discloud', 'staging', 'migration', 'supabase',
  'database', 'postgres', 'production', 'rollback', 'revert',
  'snapshot', 'stable', 'known', 'good'
]);
const names = [
  'feat/embed-studio-durable-draft-recovery-20261024',
  'fix/ticket-panel-legacy-interaction-recovery-20260924',
  'recovery/cinema-pr436-known-good',
  'backup/rollback-recovery',
  'feat/discloud-recovery-20261024',
  'feat/backup-recovery-20261024',
  'fix/rollback-recovery-20261024',
  'feat/action-center-release-lock-20261023',
  'release/stable-20261023',
  'fix/release-hotfix-20261023',
  'feat/production-release-20261023',
  'feat/embed-studio-recurring-campaigns-20261024'
];
console.log(JSON.stringify(names.map(name => hasHeldHistoricalSegment(name, held))));
"""
    result = subprocess.run(["node", "-e", js], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [False, False, True, True, True, True, True, False, True, True, True, False]
