"""Proof gates for retiring the two stacked picker feature branches.

PR #725 merged into a feature branch, not main. The original feature
branch tips must not be deleted until the forward port (#803) is on
main, exact-main CI passed, and the original regression test survived.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/retire-merged-temporary-branches.yml").read_text()


def test_stacked_retirement_requires_exact_original_refs_and_merge():
    assert "'fix/legacy-use-this-channel-20261025'" in WORKFLOW
    assert "'65992a7c414ea09a9557c47ac2065636d01faa34'" in WORKFLOW
    assert "'fix/shared-use-this-channel-20261025'" in WORKFLOW
    assert "'2416f81a002b3a57da678c6bab1e3c1b84ba3eed'" in WORKFLOW
    assert "stackedPickerHeads.get(branch.name) !== branch.commit.sha" in WORKFLOW
    assert "allPRs.find(pr => pr.number === 803)" in WORKFLOW
    assert "port.base?.ref !== 'main'" in WORKFLOW
    assert "portAncestry.behind_by !== 0" in WORKFLOW


def test_stacked_retirement_checks_exact_main_ci_and_ui_evidence():
    assert "run.name === 'Dank Shield CI'" in WORKFLOW
    assert "run.conclusion === 'success'" in WORKFLOW
    assert "'e3b842b66709667651229cd5e4717a81fedc1f60'" in WORKFLOW
    for marker in ("_CurrentLogTargetMenu", "GuidedUseCurrentChannelButton",
                   "_save_output_channel", "Use This Channel: Pronouns"):
        assert marker in WORKFLOW
    assert "if (!verified) continue;" in WORKFLOW


def test_stacked_retirement_reuses_existing_owner_archive_and_ref_leases():
    assert "if (reviewed)" in WORKFLOW
    assert "if (!isManual || context.actor !== 'UglyGameFace')" in WORKFLOW
    assert "DELETE_REVIEWED_" in WORKFLOW
    assert "archive.object.sha !== item.sha" in WORKFLOW
    assert "lease.object.sha !== item.sha" in WORKFLOW
    assert "github.rest.git.deleteRef" in WORKFLOW

def test_exact_main_ci_lookup_has_read_only_actions_permission():
    relay = (ROOT / ".github/workflows/owner-reviewed-branch-retirement-command.yml").read_text()
    assert "  actions: read" in WORKFLOW
    assert "      actions: read" in relay
