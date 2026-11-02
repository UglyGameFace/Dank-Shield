"""Retire only the proven-superseded historical ticket panel branch.

PR #310 was closed unmerged in favor of merged PR #311. Keep this test
independent of Discord/DB imports: it protects the branch-hygiene authority.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/retire-merged-temporary-branches.yml").read_text()


def test_exact_historical_ticket_branch_is_pinned():
    assert "fix/ticket-panel-legacy-interaction-recovery-20260924" in WORKFLOW
    assert "51d28415d13ba5b08b6632d04ea4f48e7ab29e6e" in WORKFLOW
    assert "pull_number:310" in WORKFLOW
    assert "historical.head?.sha !== '51d28415d13ba5b08b6632d04ea4f48e7ab29e6e'" in WORKFLOW
    assert "historical.state !== 'closed' || historical.merged_at" in WORKFLOW
    assert "Superseded by PR #311" in WORKFLOW


def test_merged_replacement_and_current_runtime_contract_are_required():
    assert "pull_number:311" in WORKFLOW
    assert "!ticketReplacement.merged_at" in WORKFLOW
    assert "ticketReplacement.base?.ref !== 'main'" in WORKFLOW
    assert "base:ticketReplacement.merge_commit_sha,head:currentMain" in WORKFLOW
    assert "ticketAncestry.behind_by !== 0" in WORKFLOW
    assert "LEGACY_PANEL_BUTTON_CUSTOM_IDS" in WORKFLOW
    assert "sv:ticket:panel:create:v6" in WORKFLOW
    assert "ticket_create" in WORKFLOW
    assert "test_fallback_recovers_all_known_public_ticket_panel_ids" in WORKFLOW
    assert "test_clean_registrar_reuses_runtime_without_duplicate_discord_bindings" in WORKFLOW
    assert "test_legacy_v6_current_bot_panel_is_upgraded_to_canonical_view" in WORKFLOW
    assert "needle => source.includes(needle)" in WORKFLOW


def test_no_automatic_retirement_and_archived_exact_sha_lease():
    section = WORKFLOW.split("// Explicit owner rejection:", 1)[1].split(
        "const allBranches = await", 1
    )[0]
    assert "!isManual || !ownerCommentRelay || context.actor !== 'UglyGameFace'" in section
    assert "!deploymentBranchConfigured" in section
    assert "protectedHeads.has(target.name)" in section
    assert "openPRs.some(pr => pr.head.ref === target.name" in section
    assert "currentMain !== mainSha" in section
    assert "current.commit.sha !== item.sha" in section
    assert "DELETE_RECONCILED_" in section
    assert "process.env.CLEANUP_CONFIRMATION !== token" in section
    assert section.index("github.rest.git.createRef") < section.index(
        "github.rest.git.deleteRef"
    )
    assert section.index("archive.object.sha !== item.sha") < section.index(
        "github.rest.git.deleteRef"
    )
    assert section.index("lease.object.sha !== item.sha") < section.index(
        "github.rest.git.deleteRef"
    )
