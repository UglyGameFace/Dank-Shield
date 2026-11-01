"""Exercise the owner-attested cleanup override without GitHub credentials.

This tests the actual JavaScript guard extracted from the workflow, rather
than a Python reimplementation of its decision logic.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/retire-merged-temporary-branches.yml"


def _guard():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("            const configuredDeployBranch =")
    end = workflow.index("            if (!['preview', 'delete', 'preview-reviewed-merged', 'delete-reviewed-merged']", start)
    return textwrap.dedent(workflow[start:end])


def _simulate(*, event="workflow_dispatch", actor="UglyGameFace",
              configured="", manual="", ack="", exists=True):
    if not shutil.which("node"):
        pytest.skip("Node.js is required to execute the workflow's JavaScript guard")
    script = """
const context = {eventName: %s, actor: %s};
const core = {warning: () => {}};
const github = {rest: {repos: {getBranch: async ({branch}) => {
  if (%s || !branch) throw new Error('missing branch');
  return {data: {commit: {sha: '0123456789abcdef'}}};
}}}};
(async () => {
%s
  return {effectiveDeployBranch, deploymentBranchConfigured,
          protectedHeads: [...protectedHeads], mode};
})().then(value => console.log(JSON.stringify(value)))
    .catch(error => {console.error(error.message); process.exitCode = 3;});
""" % (json.dumps(event), json.dumps(actor), json.dumps(not exists), _guard())
    env = dict(os.environ, REQUESTED_MODE="preview-reviewed-merged",
               LINKED_PRODUCTION_BRANCH=configured, MANUAL_DEPLOY_BRANCH=manual,
               MANUAL_DEPLOY_ACK=ack)
    return subprocess.run(["node", "-e", script], env=env, capture_output=True,
                          text=True, check=False)


def test_scheduled_run_cannot_accept_manual_override():
    result = _simulate(event="schedule", manual="feat/retained-live-branch",
                       ack="I_VERIFIED_LIVE_DISCLOUD_BRANCH")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["effectiveDeployBranch"] == ""
    assert payload["deploymentBranchConfigured"] is False
    assert payload["mode"] == "delete"


def test_owner_manual_attestation_resolves_and_protects_live_branch():
    result = _simulate(manual="feat/retained-live-branch",
                       ack="I_VERIFIED_LIVE_DISCLOUD_BRANCH")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["deploymentBranchConfigured"] is True
    assert "feat/retained-live-branch" in payload["protectedHeads"]


@pytest.mark.parametrize("kwargs", [
    {"actor": "someone-else", "manual": "feat/live", "ack": "I_VERIFIED_LIVE_DISCLOUD_BRANCH"},
    {"manual": "feat/live", "ack": ""},
    {"configured": "feat/old-live", "manual": "feat/new-live",
     "ack": "I_VERIFIED_LIVE_DISCLOUD_BRANCH"},
])
def test_untrusted_or_conflicting_override_fails_closed(kwargs):
    result = _simulate(**kwargs)
    assert result.returncode != 0


def test_nonexistent_attested_branch_cannot_authorize_deletion():
    result = _simulate(manual="feat/not-found",
                       ack="I_VERIFIED_LIVE_DISCLOUD_BRANCH", exists=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["deploymentBranchConfigured"] is False


def test_owner_preview_token_binds_live_branch_and_archives_before_delete():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "JSON.stringify({mainSha,batch,effectiveDeployBranch})" in workflow
    assert "if (deleteReviewed && process.env.CLEANUP_CONFIRMATION !== token)" in workflow
    assert "const canRetire = deploymentBranchConfigured;" in workflow
    assert "const deleting = mode === 'delete' && deploymentBranchConfigured;" in workflow
    assert workflow.index("archive.object.sha !== sha") < workflow.index(
        "await github.rest.git.deleteRef({owner, repo, ref: 'heads/' + name})"
    )


def test_workflow_inline_javascript_compiles(tmp_path):
    if not shutil.which("node"):
        pytest.skip("Node.js is required to syntax-check the workflow script")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    script = textwrap.dedent(workflow.split("          script: |\n", 1)[1])
    path = tmp_path / "cleanup.cjs"
    path.write_text("(async function cleanup() {\n" + script + "\n})\n",
                    encoding="utf-8")
    result = subprocess.run(["node", "--check", str(path)], capture_output=True,
                            text=True, check=False)
    assert result.returncode == 0, result.stderr
