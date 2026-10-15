"""Guard the existing-app GitHub deployment without contacting production services."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/discloud-existing-app-manual-commit.yml"
RISK_PHRASE = "I_ACCEPT_STONEYVERIFY_DATA_LOSS"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _preflight_guard() -> str:
    section = _workflow().split(
        "      - name: Reject wrong ref, branch drift or unsafe archive\n", 1
    )[1].split("\n      - name: Confirm deployment manifest", 1)[0]
    return textwrap.dedent(section.split("        run: |\n", 1)[1])


@pytest.fixture
def sandbox(tmp_path: Path):
    (tmp_path / "discloud.config").write_text(
        "ID=stoneyverify\nMAIN=main.py\nTYPE=site\n", encoding="utf-8"
    )
    (tmp_path / "main.py").write_text("print('test')\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("discord.py\n", encoding="utf-8")
    (tmp_path / ".discloudignore").write_text(
        "\n".join((".git", ".github", ".env", ".env.production",
                   ".env.local", "data", "tests")) + "\n",
        encoding="utf-8",
    )
    for args in (
        ("init", "-q"),
        ("config", "user.email", "ci@example.invalid"),
        ("config", "user.name", "Workflow test"),
        ("add", "-A"),
        ("commit", "-qm", "fixture"),
    ):
        subprocess.run(("git", *args), cwd=tmp_path, check=True, capture_output=True)
    sha = subprocess.check_output(
        ("git", "rev-parse", "HEAD"), cwd=tmp_path, text=True
    ).strip()
    env = os.environ.copy()
    env.update({
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_OUTPUT": str(tmp_path / "outputs.txt"),
        "REQUESTED_SHA": sha,
        "OPERATION": "deploy_existing_app",
        "RECOVERY_POLICY": "owner_accepts_unverified_recovery",
        "OWNER_RISK_ACK": RISK_PHRASE,
        "OFF_HOST_BACKUP_RECEIPT": "bkp-stoneyverify-20261014-phone",
        "RPC_VERIFIED": "true",
        "FILE_PERSISTENCE_VERIFIED": "false",
        "ENV_RECOVERY_VERIFIED": "false",
    })
    return tmp_path, env


def _run_guard(sandbox, **overrides):
    folder, env = sandbox
    env = {**env, **overrides}
    return subprocess.run(
        ("bash", "-c", _preflight_guard()), cwd=folder, env=env,
        text=True, capture_output=True,
    )


def test_risk_mode_accepts_only_explicit_accurate_owner_acknowledgement(sandbox):
    result = _run_guard(sandbox)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OWNER-ACCEPTED RISK" in result.stdout
    assert "not" in result.stdout.lower()  # no pretense of verified recovery


@pytest.mark.parametrize("overrides,expected_message", [
    ({"OWNER_RISK_ACK": ""}, "Owner risk acknowledgement"),
    ({"OWNER_RISK_ACK": "YES"}, "Owner risk acknowledgement"),
    ({"OFF_HOST_BACKUP_RECEIPT": ""}, "backup actually stored off Discloud"),
    ({"RPC_VERIFIED": "false"}, "production member-activity RPC"),
    ({"RECOVERY_POLICY": "not-a-valid-mode"}, "Unknown recovery policy"),
    ({"GITHUB_REF": "refs/heads/feature"}, "Only workflow_dispatch on main"),
    ({"REQUESTED_SHA": "0" * 40}, "Main advanced"),
])
def test_deploy_denies_missing_evidence_and_bad_source(sandbox, overrides, expected_message):
    result = _run_guard(sandbox, **overrides)
    assert result.returncode != 0
    assert expected_message in result.stdout + result.stderr


def test_verified_recovery_path_keeps_both_independent_requirements(sandbox):
    verified = _run_guard(
        sandbox, RECOVERY_POLICY="verified_recovery", OWNER_RISK_ACK="",
        FILE_PERSISTENCE_VERIFIED="true", ENV_RECOVERY_VERIFIED="true",
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    for key, msg in (
        ("FILE_PERSISTENCE_VERIFIED", "confirmed app-file persistence"),
        ("ENV_RECOVERY_VERIFIED", "tested environment recovery"),
    ):
        flags = {"FILE_PERSISTENCE_VERIFIED": "true", "ENV_RECOVERY_VERIFIED": "true"}
        flags[key] = "false"
        denied = _run_guard(
            sandbox, RECOVERY_POLICY="verified_recovery", OWNER_RISK_ACK="", **flags
        )
        assert denied.returncode != 0
        assert msg in denied.stdout + denied.stderr


def test_preflight_without_deploy_evidence_remains_read_only(sandbox):
    result = _run_guard(
        sandbox, OPERATION="preflight", RPC_VERIFIED="false",
        OWNER_RISK_ACK="", OFF_HOST_BACKUP_RECEIPT="",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_tracked_secret_artifact_stops_preflight(sandbox):
    folder, _ = sandbox
    (folder / ".env").write_text("DUMMY=nonsecret\n", encoding="utf-8")
    denied = _run_guard(sandbox)
    assert denied.returncode != 0
    assert "Unsafe or mutable local artifact" in denied.stdout + denied.stderr


def test_workflow_never_auto_deploys_or_uploads_env_data():
    text = _workflow()
    assert "  workflow_dispatch:\n" in text
    assert not any(f"  {event}:" in text for event in ("push", "pull_request", "schedule"))
    assert "cancel-in-progress: false" in text
    assert "environment: discloud-production" in text
    assert "if: ${{ inputs.operation == 'deploy_existing_app' }}" in text
    assert "app_id: stoneyverify" in text
    assert "uses: discloud/deploy-action@fae7024653d941a19daa2b6f56d2ae208e008c54" in text
    deploy_with = text.split("        uses: discloud/deploy-action@", 1)[1].split(
        "      - name: Summarize submitted update", 1
    )[0]
    assert "\n          env:" not in deploy_with
    assert "\n          env_file:" not in deploy_with
    assert "secrets.DISCLOUD_TOKEN" in text
    assert "FILE_PERSISTENCE_VERIFIED" in text
    assert "ENV_RECOVERY_VERIFIED" in text
    assert "owner_accepts_unverified_recovery" in text
    assert RISK_PHRASE in text


def test_deploy_requires_current_main_and_canonical_ci():
    text = _workflow()
    assert "  actions: read" in text
    assert "git fetch --quiet origin main" in text
    assert 'git rev-parse origin/main' in text
    assert "/actions/workflows/ci.yml/runs" in text
    assert ".head_branch == \"main\"" in text
    assert '.event == "push"' in text
    assert '.conclusion == "success"' in text
    assert text.index("Require current main and successful exact-SHA CI") < text.index(
        "Commit approved main code to EXISTING Discloud app"
    )
