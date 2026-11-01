"""Contract and runnable command tests for the owner-only cleanup relay."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/owner-reviewed-branch-retirement-command.yml"
)


def _script():
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line == "          script: |") + 1
    return "\n".join(
        line[12:] for line in lines[start:]
        if line.startswith("            ") or not line.strip()
    )


def test_relay_never_deletes_branches_or_dispatches_another_workflow():
    contents = WORKFLOW.read_text(encoding="utf-8")
    assert "issue_comment:" in contents
    assert "github.actor == 'UglyGameFace'" in contents
    assert "github.event.issue.number == 788" in contents
    assert "contents: read" in contents
    assert "contents: write" in contents
    assert "uses: ./.github/workflows/retire-merged-temporary-branches.yml" in contents
    assert "createWorkflowDispatch" not in contents
    target = (WORKFLOW.parent / "retire-merged-temporary-branches.yml").read_text(encoding="utf-8")
    assert "workflow_call:" in target
    assert "const ownerCommentRelay = context.eventName === 'issue_comment'" in target
    assert "context.payload.comment?.user?.login === 'UglyGameFace'" in target
    assert "const isManual = context.eventName === 'workflow_dispatch' || ownerCommentRelay" in target
    assert "deleteRef" not in contents
    assert "createRef" not in contents
    assert "updateRef" not in contents
    assert "retire-merged-temporary-branches.yml" in contents
    assert "guarded-merged-branch-cleanup.yml" not in contents


NODE_DRIVER = r"""
const fs = require('node:fs');
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const outputs = {};
const context = {
  repo: {owner: 'UglyGameFace', repo: 'Dank-Shield'},
  actor: spec.actor || 'UglyGameFace',
  payload: {
    issue: {number: spec.issue || 788, pull_request: {}},
    comment: {body: spec.body, user: {login: spec.author || 'UglyGameFace'}}
  }
};
const github = {};
const core = {info: () => {}, setOutput: (name,value) => {outputs[name]=value;}};
(async () => {
  try {
    await new AsyncFunction('context','github','core',spec.script)(context,github,core);
    process.stdout.write(JSON.stringify({success:true,outputs}));
  } catch (error) {
    process.stdout.write(JSON.stringify({success:false,error:String(error.message),outputs}));
  }
})().catch(err => {process.stderr.write(String(err));process.exitCode=1;});
"""


def _run(input_json, **overrides):
    if not shutil.which("node"):
        pytest.skip("Node.js not available")
    command = "DANK_SHIELD_CLEANUP_V1 " + json.dumps(input_json, separators=(",", ":"))
    payload = {"script": _script(), "body": command, **overrides}
    result = subprocess.run(
        ["node", "-e", NODE_DRIVER],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return json.loads(result.stdout)


def test_owner_preview_passes_validated_inputs_to_reusable_workflow():
    out = _run({"mode": "preview-reviewed-merged"})
    assert out["success"]
    assert out["outputs"] == {
        "mode": "preview-reviewed-merged",
        "confirmation": "",
        "live_discloud_branch": "",
        "live_discloud_branch_ack": "",
    }


@pytest.mark.parametrize("override", [
    {"actor": "outsider"},
    {"author": "outsider"},
    {"issue": 789},
])
def test_spoofed_owner_and_wrong_issue_never_dispatch(override):
    out = _run({"mode": "preview-reviewed-merged"}, **override)
    assert not out["success"]
    assert not out["outputs"]


@pytest.mark.parametrize("input_json", [
    {"mode": "delete-reviewed-merged"},
    {"mode": "delete-reviewed-merged", "live_discloud_branch": "main",
     "live_discloud_branch_ack": "I_VERIFIED_LIVE_DISCLOUD_BRANCH"},
    {"mode": "preview-reviewed-merged", "confirmation": "DELETE_REVIEWED_FAKE"},
    {"mode": "preview-reviewed-merged", "extra": "unreviewed"},
    {"mode": "delete"},
    {"mode": "preview-reviewed-merged", "live_discloud_branch": "../main"},
])
def test_incomplete_or_unsafe_requests_do_not_dispatch(input_json):
    out = _run(input_json)
    assert not out["success"]
    assert not out["outputs"]


def test_well_formed_delete_delegates_all_remaining_guards():
    out = _run({
        "mode": "delete-reviewed-merged",
        "live_discloud_branch": "verified-production-ref",
        "live_discloud_branch_ack": "I_VERIFIED_LIVE_DISCLOUD_BRANCH",
        "confirmation": "DELETE_REVIEWED_" + "A" * 16,
    })
    assert out["success"]
    assert out["outputs"] == {
        "mode": "delete-reviewed-merged",
        "confirmation": "DELETE_REVIEWED_" + "A" * 16,
        "live_discloud_branch": "verified-production-ref",
        "live_discloud_branch_ack": "I_VERIFIED_LIVE_DISCLOUD_BRANCH",
    }
    # This is a relay-only test: the protected target workflow still validates
    # that the branch is truly the owner-verified live one, and that the SHA-
    # bound token matches the latest preview. No real GitHub refs are deleted.


def test_owner_opt_in_preview_delegates_to_same_guarded_workflow():
    out = _run({
        "mode": "preview",
        "live_discloud_branch": "main",
        "live_discloud_branch_ack": "I_VERIFIED_LIVE_DISCLOUD_BRANCH",
    })
    assert out["success"]
    assert out["outputs"] == {
        "mode": "preview",
        "confirmation": "",
        "live_discloud_branch": "main",
        "live_discloud_branch_ack": "I_VERIFIED_LIVE_DISCLOUD_BRANCH",
    }


def test_owner_opt_in_delete_requires_exact_token_and_verified_branch():
    token = "DELETE_OPT_IN_" + "A" * 16
    out = _run({
        "mode": "delete",
        "live_discloud_branch": "main",
        "live_discloud_branch_ack": "I_VERIFIED_LIVE_DISCLOUD_BRANCH",
        "confirmation": token,
    })
    assert out["success"]
    assert out["outputs"]["mode"] == "delete"
    assert out["outputs"]["confirmation"] == token


@pytest.mark.parametrize("input_json", [
    {"mode": "delete", "live_discloud_branch": "main",
     "live_discloud_branch_ack": "I_VERIFIED_LIVE_DISCLOUD_BRANCH",
     "confirmation": "DELETE_REVIEWED_" + "A" * 16},
    {"mode": "delete", "live_discloud_branch": "main",
     "live_discloud_branch_ack": "I_VERIFIED_LIVE_DISCLOUD_BRANCH",
     "confirmation": "DELETE_OPT_IN_" + "A" * 15},
    {"mode": "preview", "confirmation": "DELETE_OPT_IN_" + "A" * 16},
    {"mode": "delete", "live_discloud_branch": "main",
     "confirmation": "DELETE_OPT_IN_" + "A" * 16},
])
def test_opt_in_relay_rejects_missing_or_mismatched_proof(input_json):
    out = _run(input_json)
    assert not out["success"]
    assert not out["outputs"]
