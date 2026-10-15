"""Nonmutating regression gates for one-approval Discloud main deployment."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / ".github/workflows/discloud-quick-main-deploy.yml"
GUARDED = ROOT / ".github/workflows/discloud-existing-app-manual-commit.yml"
RISK_PHRASE = "I_ACCEPT_STONEYVERIFY_DATA_LOSS"


def test_quick_release_only_runs_for_explicit_owner_dispatch() -> None:
    text = QUICK.read_text(encoding="utf-8")
    assert "  workflow_dispatch:" in text
    for event in ("push", "pull_request", "workflow_run", "schedule"):
        assert f"  {event}:" not in text
    assert "github.ref == 'refs/heads/main'" in text
    assert "github.actor == 'UglyGameFace'" in text
    assert "off_host_backup_receipt:" in text
    assert "owner_risk_acknowledgement:" in text
    assert text.count("        required: true") == 2
    assert "  contents: read" in text
    assert "  actions: read" in text


def test_quick_release_reuses_hardened_main_deployment() -> None:
    text = QUICK.read_text(encoding="utf-8")
    assert "uses: ./.github/workflows/discloud-existing-app-manual-commit.yml" in text
    assert "expected_main_sha: ${{ github.sha }}" in text
    assert "operation: deploy_existing_app" in text
    assert "recovery_policy: owner_accepts_unverified_recovery" in text
    assert "off_host_backup_receipt: ${{ inputs.off_host_backup_receipt }}" in text
    assert "owner_risk_acknowledgement: ${{ inputs.owner_risk_acknowledgement }}" in text
    assert "database_rpc_verified: ${{ vars.DANK_PRODUCTION_MEMBER_RPC_VERIFIED == 'true' }}" in text
    assert "app_files_persistence_verified: false" in text
    assert "env_recovery_verified: false" in text
    assert "secrets: inherit" in text


def test_quick_workflow_cannot_silently_accept_data_loss() -> None:
    text = QUICK.read_text(encoding="utf-8")
    assert "owner_risk_acknowledgement: " + RISK_PHRASE not in text
    assert RISK_PHRASE in text  # only visible input instruction
    assert "DISCLOUD_TOKEN" not in text
    assert "discloud/deploy-action@" not in text
    assert "curl " not in text
    assert "env_file:" not in text
    assert "DANK_CINEMA_ROLLING_ENABLED" not in text


def test_shared_deployer_keeps_unchanged_production_guards() -> None:
    text = GUARDED.read_text(encoding="utf-8")
    assert "  workflow_dispatch:" in text
    assert "  workflow_call:" in text
    for event in ("push", "pull_request", "workflow_run", "schedule"):
        assert f"  {event}:" not in text
    assert "if: ${{ inputs.operation == 'deploy_existing_app' }}" in text
    assert "environment: discloud-production" in text
    assert "cancel-in-progress: false" in text
    assert 'Only the repository owner may accept unverified Discloud recovery risk.' in text
    assert 'Owner risk acknowledgement must match the exact required phrase.' in text
    assert "app_id: stoneyverify" in text
    assert "uses: discloud/deploy-action@fae7024653d941a19daa2b6f56d2ae208e008c54" in text
    assert "git fetch --quiet origin main" in text
    assert '.conclusion == "success"' in text
    assert "      app_files_persistence_verified:\n        type: boolean" in text
    assert "      env_recovery_verified:\n        type: boolean" in text
