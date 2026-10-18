"""Regression gates for owner-authorized, GitHub-only branch cleanup.

This workflow is intentionally manual and must not affect Discloud runtime,
production data, or any unmerged feature branch.
"""
from __future__ import annotations

from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/guarded-merged-branch-cleanup.yml"
)


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_branch_cleanup_requires_owner_main_and_manual_dispatch() -> None:
    text = _workflow()
    assert "  workflow_dispatch:" in text
    for event in ("push", "pull_request", "schedule", "workflow_run", "workflow_call"):
        assert f"  {event}:" not in text
    assert "github.ref == 'refs/heads/main'" in text
    assert "github.actor == 'UglyGameFace'" in text
    assert "concurrency:" in text
    assert "cancel-in-progress: false" in text


def test_first_wave_is_preserved_and_second_wave_is_separate() -> None:
    text = _workflow()
    for option in (
        "preview", "delete", "preview-next", "delete-next",
        "preview-reviewed", "delete-reviewed",
    ):
        assert f"          - {option}\n" in text
    assert "const mergedBefore = '2026-10-01T00:00:00Z'" in text
    assert "const nextMergedBefore = '2026-10-14T00:00:00Z'" in text
    assert "DELETE_STALE_MERGED_BRANCHES" in text
    assert "DELETE_VERIFIED_NEXT_BATCH" in text
    assert "DELETE_REVIEWED_HISTORICAL_BRANCHES" in text
    assert "const deleting = mode === 'delete' || mode === 'delete-next' || mode === 'delete-reviewed'" in text
    assert "if (deleting) {" in text
    assert "process.env.CLEANUP_CONFIRMATION !== (" in text


def test_preserves_discloud_and_recovery_references() -> None:
    text = _workflow()
    assert "'aaa-cinema-pre-pregateway-bind'" in text
    assert "'backup/stoneyverify-stable-7fdc331-20261008'" in text
    assert "'backup/'" in text
    assert "'archive/'" in text
    assert "'recovery/'" in text
    assert "'reconcile/'" in text
    assert "'integration/'" in text
    assert "'discloud'" in text
    assert "'migration'" in text


def test_second_wave_requires_exact_merged_head_or_git_ancestry() -> None:
    text = _workflow()
    assert "pr.head.sha === branch.commit.sha" in text
    assert "pr.base?.ref === defaultBranch" in text
    assert "pr.base?.ref !== defaultBranch" in text
    assert "async function ancestorOfMain(sha)" in text
    assert "data.ahead_by === 0" in text
    assert "if (await ancestorOfMain(branch.commit.sha))" in text
    assert "if (!prs.length && checkedNoPR.has(name))" in text


def test_no_active_or_changed_branches_deleted() -> None:
    text = _workflow()
    assert "prs.some(pr => !pr.merged_at)" in text
    assert "inUseByOpenPR.has(name)" in text
    assert "name === defaultBranch || branch.protected" in text
    assert "current.protected || current.commit.sha !== candidate.sha" in text
    assert "github.rest.git.deleteRef" in text
    assert "candidates.length > 250" in text
    assert "if (failed.length)" in text


def test_reviewed_wave_is_frozen_and_requires_supersession_evidence() -> None:
    text = _workflow()
    assert "const reviewedRetirements = new Map([" in text
    assert "review.sha !== branch.commit.sha" in text
    assert "pr.number === review.pr && pr.merged_at" in text
    assert "pr.head.sha === branch.commit.sha" in text
    assert "pr.number === review.pr && !pr.merged_at" in text
    assert "pr.number === review.replacement && pr.merged_at" in text
    assert "if (!original || !replacement) continue;" in text
    assert "if (inUseByOpenPR.has(name)) continue;" in text
    assert "if (reviewedBatch) {" in text
    # These unique-commit or linked-production branches must never be auto-retired.
    assert "'aaa-cinema-pre-pregateway-bind', { kind:" not in text
    assert "'feat/cinema-library-intelligence', { kind:" not in text
    assert "'fix/source-backup-large-toc-stream-20261013', { kind:" not in text
    assert "'fix/ticket-panel-legacy-interaction-recovery-20260924', { kind:" not in text


def test_reviewed_wave_has_exact_frozen_retirement_count() -> None:
    text = _workflow()
    manifest = text.split("const reviewedRetirements = new Map([", 1)[1].split("]);", 1)[0]
    assert manifest.count("kind: 'merged'") == 91
    assert manifest.count("kind: 'superseded'") == 7
    assert manifest.count("kind: 'closed-record'") == 3
    assert manifest.count("kind: 'ancestor-only'") == 7
    for pr in (46, 94, 171, 214, 216, 229):
        assert f"replacement: {pr}" in manifest


def test_fourth_wave_requires_live_git_ancestry_and_exact_records() -> None:
    text = _workflow()
    assert "review.kind === 'superseded' || review.kind === 'closed-record'" in text
    assert "if (prs.length || !await ancestorOfMain(branch.commit.sha)) continue;" in text
    assert "review.sha !== branch.commit.sha" in text
    assert "if (!original || !replacement) continue;" in text
    assert "pr.head.sha === branch.commit.sha" in text
    assert "inUseByOpenPR.has(name)" in text
    assert "current.protected || current.commit.sha !== candidate.sha" in text
    manifest = text.split("const reviewedRetirements = new Map([", 1)[1].split("]);", 1)[0]
    assert manifest.count("kind:") == 114
    for branch in (
        "aaa-cinema-pre-pregateway-bind",
        "feat/cinema-library-intelligence",
        "fix/prod-shard-guild-loops-no-batching-20261013",
        "fix/source-backup-large-toc-stream-20261013",
        "fix/ticket-panel-legacy-interaction-recovery-20260924",
    ):
        assert "'" + branch + "', { kind:" not in manifest


def test_fifth_wave_only_audited_merged_verifier_and_workflow_branches() -> None:
    text = _workflow()
    manifest = text.split("const reviewedRetirements = new Map([", 1)[1].split("]);", 1)[0]
    exact = {
        "chore/verify-supabase-sync": (154, "79f78cba5d23105869e0debde94029a91bab7ff4"),
        "chore/remove-supabase-sync-verifier": (155, "ab41128b4335af29f21d9766f78bc69abee32695"),
        "chore/verify-ticket-counter-production": (159, "4ccbf727eb69f2ca3e11bf78a4567e6865bcbe3e"),
        "chore/guarded-branch-cleanup-fourth-wave-20261015": (585, "c92fe20e89f80698f5c7b3f20dd070820853d963"),
    }
    for name, (pr, sha) in exact.items():
        entry = f"['{name}', {{ kind: 'merged', pr: {pr}, sha: '{sha}' }}]"
        assert manifest.count(entry) == 1
    # No additional cleanup should target live backup, storage migration,
    # deployment branches, or any source branch of a closed-unmerged PR.
    for name in (
        "chore/automate-supabase-migrations",
        "fix/supabase-out-of-order-migrations",
        "cleanup/schema-authority-consolidation",
        "fix/live-captions-stable-interim-recovery-20260927",
        "feat/native-welcome-cards",
    ):
        assert f"'{name}', {{ kind:" not in manifest


def test_sixth_wave_exact_sha_and_discloud_dual_ancestry() -> None:
    text = _workflow()
    manifest = text.split("const reviewedRetirements = new Map([", 1)[1].split("]);", 1)[0]
    assert manifest.count("kind: 'linked-ancestor'") == 5
    assert manifest.count("kind: 'merged'") == 91
    assert manifest.count("kind:") == 114
    assert "async function ancestorOfLinkedDiscloud(sha)" in text
    assert "if (!await ancestorOfMain(branch.commit.sha)) continue;" in text
    assert "if (!await ancestorOfLinkedDiscloud(branch.commit.sha)) continue;" in text
    assert "pr.base?.ref === 'aaa-cinema-pre-pregateway-bind'" in text
    assert "if (name === 'aaa-cinema-pre-pregateway-bind') continue;" in text
    expected = {
        "fix/cinema-fullscreen-portrait-recovery-20261010": (499, "1885679d61f212108081e600b4670aed4c9a72fd", "linked-ancestor"),
        "fix/cinema-api-html-json-contract-discloud-20261010": (501, "c00dc081b2dc47d999351e2caec3db669033db07", "linked-ancestor"),
        "fix/share-router-prod-branch-reconcile-20261010": (504, "dbc607a8bf8f39adeb3b4cf7552a6b9922f8a044", "linked-ancestor"),
        "fix/verify-reminders-discloud-postgres-20261011": (511, "e7e5b67dcd2ae0ee085e6e104a7760766caf677c", "linked-ancestor"),
        "fix/ticket-panel-production-parity-20261012": (528, "99a915d169aaf48667e6e82fe526ee440d89873f", "linked-ancestor"),
        "feat/toke-lifecycle-and-coordination-20261014": (560, "4545b88395ef3837e7b48e5176a2a420ecb1ed01", "merged"),
        "feat/toke-host-handoff-20261014": (561, "5de1f9dd06c6e45d42074b9f2167b1a8dafd1e8e", "merged"),
        "feat/toke-voice-lifecycle-20261014": (565, "db0f68fbbe79e10d4cbdda5d50d9812017ddd44b", "merged"),
        "feat/toke-opt-in-reminders-20261014": (566, "161bb5b0946f7be3f45be08f9b7e836b059ba165", "merged"),
        "feat/toke-voice-index-and-noop-20261014": (567, "a7b05a70b5a6cdfaf1139695e96e2f962ad874b0", "merged"),
        "fix/torrent-cache-crash-orphans-shared-storage-20261014": (569, "160cc384a41c6677b8355199c9ba44dd2a1aec2f", "merged"),
        "test/cinema-real-engine-50gib-rolling-proof-20261014": (570, "8f107c1a282faba7e4006173c8e8e1afe2d32a29", "merged"),
        "stabilization/cinema-public-route-smoke-20261015": (574, "363f6677270cd8c6ccc08a904bc5776836f7c596", "merged"),
        "stabilization/cinema-friendly-feed-profile-20261015": (575, "6f1ed13517556813db5a79328b6c548675e07c88", "merged"),
        "stabilization/ticket-topic-privacy-20261015": (577, "e8bbbcd053d8ca56a078dbc2f72ceed8d28804b8", "merged"),
        "stabilization/ticket-topic-repair-20261015": (578, "49f8e9fdca543a742a51e99ec523337ef053c166", "merged"),
        "stabilization/member-center-history-universal-purge-20261015": (579, "fddcb09e6d9113a8ad7a23fce3d4ba2a2a4b8c16", "merged"),
        "feat/embed-studio-mobile-20261015": (580, "0fd37ca813ee16ee30508fdca9355ebf5ac4336b", "merged"),
        "feat/embed-studio-persistent-role-actions-20261015": (581, "335c58f532d7534214bb33eaf0cf755ef045b031", "merged"),
        "chore/guarded-branch-cleanup-fifth-wave-20261015": (586, "702929cd9d6ea3a92ef2637c0f397c9ca240fec7", "merged"),
        "fix/antinuke-panic-containment-respects-alert-and-trust-20261015": (587, "030a12b5834ec0078f356e30cd942dacc73169c7", "merged"),
        "fix/antinuke-gateway-trusted-role-thresholds-20261015": (588, "8f2ef03fcd0ccae1afaebaeeb0842a554b90bfc0", "merged"),
        "fix/antinuke-sparse-bot-add-attribution-20261015": (589, "41cc258097debb10bab6f8b94b4fd2733d3df58f", "merged"),
        "fix/antinuke-invalid-self-marker-20261015": (590, "ca42ebb8b10a9d0d8327784eef11d7b3c28bb980", "merged"),
        "fix/antinuke-malformed-self-marker-receipt-20261015": (591, "2a1b64d19b94413f188508b6b14ad107b6d7b50e", "merged"),
        "fix/antinuke-gateway-panic-trust-consistency-20261015": (592, "f6ea474d25c81db5150fb58112be0a704af04677", "merged"),
        "fix/antinuke-harmless-role-create-panic-20261015": (593, "67b7cd0f1a72b1809eba92f7f73e0127d7ae7580", "merged"),
        "fix/antinuke-harmless-role-create-containment-20261015": (594, "66804ff0cf04cc0a18595e5d82fa2e4ca6341dee", "merged"),
        "fix/antinuke-safe-creation-containment-20261015": (595, "dba7d1d4ba26d2f2969de6b3136508169f167043", "merged"),
    }
    assert len(expected) == 29
    for name, (pr, sha, kind) in expected.items():
        record = f"['{name}', {{ kind: '{kind}', pr: {pr}, sha: '{sha}' }}]"
        assert manifest.count(record) == 1
    for excluded in (
        "main", "aaa-cinema-pre-pregateway-bind",
        "backup/stoneyverify-stable-7fdc331-20261008",
        "fix/source-backup-large-toc-stream-20261013",
        "feat/cinema-library-intelligence",
        "feat/discloud-pg-atomic-guild-config-20261013",
        "fix/antinuke-self-provenance-guard",
    ):
        assert f"['{excluded}', {{ kind:" not in manifest


def test_unmerged_unicode_263_has_exact_proven_replacement() -> None:
    text = _workflow()
    manifest = text.split("const reviewedRetirements = new Map([", 1)[1].split("]);", 1)[0]
    record = (
        "['fix/welcome-card-long-tail-unicode', "
        "{ kind: 'superseded', pr: 263, replacement: 264, "
        "sha: '25047b33bd51f7f066992802ef466276ceee7056' }]"
    )
    assert manifest.count(record) == 1
    assert "if (!original || !replacement) continue;" in text
    assert "review.sha !== branch.commit.sha" in text
    assert "if (inUseByOpenPR.has(name)) continue;" in text
    # No old-build font downloads, unrelated branches, or blanket cleanup.
    assert "tools/provision_unicode_fonts.py" not in text
    assert "'aaa-cinema-pre-pregateway-bind', { kind:" not in manifest


def test_eighth_wave_exact_merged_cleanup_heads_preserved() -> None:
    text = _workflow()
    manifest = text.split("const reviewedRetirements = new Map([", 1)[1].split("]);", 1)[0]
    expected = {
        "chore/guarded-branch-cleanup-sixth-wave-20261015": (
            596, "84bf0510ff971f8708c56328b5eaec62734bbbfb",
        ),
        "chore/reconcile-unicode-263-20261016": (
            598, "48662fdb88addadb5ace05606fda23113976c328",
        ),
    }
    for name, (pr, sha) in expected.items():
        record = f"['{name}', {{ kind: 'merged', pr: {pr}, sha: '{sha}' }}]"
        assert manifest.count(record) == 1
    # These refs and prior closed-unmerged holds are never automatically swept.
    for retained in (
        "main",
        "aaa-cinema-pre-pregateway-bind",
        "feat/cinema-library-intelligence",
        "fix/source-backup-large-toc-stream-20261013",
        "backup/stoneyverify-stable-7fdc331-20261008",
    ):
        assert f"['{retained}', {{ kind:" not in manifest
    assert "pr.head.sha === branch.commit.sha" in text
    assert "inUseByOpenPR.has(name)" in text
    assert "current.protected || current.commit.sha !== candidate.sha" in text


def test_ninth_wave_retains_current_setup_and_exact_historical_heads() -> None:
    text = _workflow()
    manifest = text.split("const reviewedRetirements = new Map([", 1)[1].split("]);", 1)[0]
    expected = {
        "feature/setup-picker-wizard": (
            "superseded", 9, "a79ec6441a66de41bdab5110c5ebac2a9ab0fd81",
        ),
        "chore/guarded-branch-cleanup-eighth-wave-20261016": (
            "merged", 599, "16a7292cecfb8ff73aab2ab50c108287280da940",
        ),
    }
    for branch, (kind, pr, sha) in expected.items():
        if kind == "superseded":
            expected_entry = (
                f"['{branch}', {{ kind: 'superseded', pr: {pr}, "
                f"replacement: 241, sha: '{sha}' }}]"
            )
        else:
            expected_entry = (
                f"['{branch}', {{ kind: 'merged', pr: {pr}, sha: '{sha}' }}]"
            )
        assert manifest.count(expected_entry) == 1
    # Do not widen the manifest to held migration, production or unique-commit refs.
    for retained in (
        "aaa-cinema-pre-pregateway-bind",
        "main",
        "feat/native-welcome-cards",
        "feat/cinema-library-intelligence",
        "fix/ticket-panel-legacy-interaction-recovery-20260924",
        "fix/source-backup-large-toc-stream-20261013",
        "backup/stoneyverify-stable-7fdc331-20261008",
    ):
        assert f"['{retained}', {{ kind:" not in manifest


def test_current_setup_picker_preserves_closed_pr9_configuration_contract() -> None:
    # PR #9's old /stoney wizard is replaced by the current /dank setup picker.
    # Do not reintroduce the older command group or its duplicate registration.
    root = Path(__file__).resolve().parents[1]
    source = (root / "stoney_verify/commands_ext/public_setup_picker.py").read_text(
        encoding="utf-8"
    )
    command_modules = (root / "stoney_verify/commands_ext/__init__.py").read_text(
        encoding="utf-8"
    )
    assert '@dank_group.command(name="setup-picker"' in source
    assert '("public_setup_picker", "register_public_setup_picker_commands",' in command_modules
    assert "stoney_group.add_command" not in source
    for key in (
        "ticket_category_id", "ticket_archive_category_id", "staff_role_id",
        "vc_staff_role_id", "transcripts_channel_id",
        "verify_channel_id", "unverified_role_id", "verified_role_id",
        "resident_role_id", "vc_verify_channel_id", "vc_verify_queue_channel_id",
        "modlog_channel_id", "raidlog_channel_id", "join_log_channel_id",
        "force_verify_log_channel_id", "configured_by_id",
        "configured_by_name", "configured_at",
    ):
        assert f'"{key}"' in source
    for required in (
        "_require_setup_permission", "discord.ui.ChannelSelect",
        "discord.ui.RoleSelect", "_validate_ticket_setup",
        "_validate_verify_setup", "_validate_log_setup",
        "upsert_guild_config", "invalidate_guild_config",
        "class SetupPickerHome", "class TicketPicker",
        "class VerifyPicker", "class VCPicker", "class LogPicker",
    ):
        assert required in source


def test_tenth_wave_merged_heads_and_archive_guard() -> None:
    text = _workflow()
    manifest = text.split("const reviewedRetirements = new Map([", 1)[1].split("]);", 1)[0]
    expected_merged = [
        ("chore/reconcile-setup-picker-9-retire-20261016", 600, "129004553ba30aec8381c97ecbd74faf024e1e75"),
        ("ci/safe-discloud-main-release-20261015", 576, "94ef4408d6b4fadb3cbc62141d055786ae740c2d"),
        ("feat/backup9-staging-candidate-inventory-20261014", 563, "a718edfcd281be7891a84a359061a527ad0ef77d"),
        ("feat/cinema-rolling-engine-gateway-20261014", 571, "0ed28b86bc991274749187a4d9415dcbd723817d"),
        ("feat/discloud-config-parity-gate-20261013", 551, "384d8058992b658618ac13111e41776bd5a29551"),
        ("feat/discloud-core-config-shadow-20261013", 548, "11b858747e5776badd0f682aeb607b8f38ff64b0"),
        ("feat/discloud-core-staging-phase1-20261013", 547, "b47901de4e2e0ac0f6c251e0a0ad26b58cb115fe"),
        ("feat/discloud-postgres-storage-preflight-20261013", 546, "f8e9cd9cd1cc128438d157c23f3e1325c1b32308"),
        ("feat/discloud-source-encrypted-backup-20261013", 552, "e6cefc0a93ba1f8a68616426fc55f4802d8fd101"),
        ("feat/manual-shared-supabase-encrypted-backup-20261013", 553, "d9ca49900679c86fa9178ba20fd8c6dedd2bbd17"),
        ("feat/verify-source-backup9-isolated-pg17-20261013", 558, "253cd70803b7cbb77e01f89e9d76f230594fc742"),
        ("fix/access-repair-emergency-recovery-20260925", 321, "666790eef6a8635ac18cb239d2e1f16ccb329be1"),
        ("fix/activity-batch-rpc-quoted-bodies-pg17-20261014", 568, "d32c83f1bffda100ec501652ea87e804c9db88a0"),
        ("fix/apply-supabase-history-repair", 152, "4858bf3be52206f253d6c8dc26ca3feb41e6b562"),
        ("fix/backup-phase-diagnostics-20261013", 556, "bd0e0eab79a0b420b3947d9b6cac5236a291b62f"),
        ("fix/backup-safe-pgdump-errors-20261013", 554, "4b8d838114ac3f75d77a3e5e58df6eb468e7ca6c"),
        ("fix/cold-start-recovery-quiet-window-20261007", 476, "52642543b9b6160d5402decc6640398af12328ec"),
        ("fix/confirmed-pgrestore-toc-broken-pipe-20261013", 557, "50724b5128be5be066c99bda0a57259d5752303c"),
        ("fix/defer-activity-recovery-outside-discloud-startup-window-20261007", 473, "85777825a29fceeda009b6a9fc1f19fcae9df22c"),
        ("fix/discloud-explicit-owner-recovery-risk-20261014", 573, "9c1f8889fb299373090ffdca9a55284a9137a3d0"),
        ("fix/discloud-global-rate-limit-20260921", 286, "a3d1a71f312605f2771c15810931a7aee85972b2"),
        ("fix/discloud-shared-10gb-migration-budget-20261014", 562, "3f9c0c67ff232fe3d01fb7b70ba184859748640b"),
        ("fix/invite-shield-generalized-recovery-20260925", 329, "16841f44f4ee302824d963b766aa3860cb72cd9b"),
        ("fix/live-captions-revert-354-regression-20260927", 356, "52851ca4bdc7dfc13b2261c091941fb745a3060c"),
        ("fix/migration-storage-audit-budget-parity-20261014", 564, "0a4074407b6963d1bebbc61cf4e8a513ee67a1ca"),
        ("fix/normalize-legacy-supabase-versions", 153, "7267f3289532aea17396b3d8c041b90e28100985"),
        ("fix/owner-authority-verify-recovery-20260925", 325, "1fc726b853b1f02ff6d5f22feb88b06d54ab8d92"),
        ("fix/protect-live-interactions-discloud-rest-20261007", 474, "85533289ccc61e027c7a95aab6e0774754378dd2"),
        ("fix/source-backup9-supabase-restore-20261013", 559, "c8bbcd1211d6ddf3c895b7dc9ecc3b7d4919a3a7"),
        ("fix/supabase-history-repair-final", 151, "176633375eab8b39ade5cb66e125f7eeb218ce6d"),
        ("fix/supabase-migration-history-drift", 150, "468aac934a51cd10dd7e9b66e6456a482c5c6200"),
        ("fix/ticket-counter-durable-migration", 157, "71012873d8d43158b71356cb4b7c13bc3f9e4172"),
        ("ops/discloud-existing-app-safe-main-commit-20261014", 572, "8c3acc431421fa13511aa9b57bf87b404873ccaf"),
        ("rebuild/cinema-library-intelligence-clean", 459, "0547b354fce64a1512f018600a4d50a2d87110f9"),
        ("reconcile/main-production-toke-20261013", 545, "33385349e268be6bfc74d90482c278da82f032e4"),
        ("reconcile/main-reunification-20261013", 535, "eb8b6e311859d4572b02d11753c2c1180244ecb9"),
        ("test/discloud-postgres17-real-contract-20261013", 550, "c97e86c4363c1d3a56d305df9165c82f3b5ecb28"),
    ]
    expected_ancestors = [
        ("audit-000-cinema-pr436-known-good", "c785c5eefc9e9f0b1c6792e7546eca3c8f9703a5"),
        ("fix/discloud-rest-window-recovery-current-main-20261007", "94468b19ec2b44da083de70efd87f370e7ecab1f"),
        ("rebuild/pr460-standalone-current-main", "f68d9cb973196d57b21b041a5daf5932ce2d8765"),
        ("reconcile/live-captions-352-c055-20260927", "b3ce99c71f0d3f98bcd26955348e44fac8b117c9"),
        ("reconcile/405-movie-night-viewer-sync", "6a31cc9b7e154b3047a5432b45fa3a999d8bac5c"),
        ("reconcile/410-main", "3718ef3a5c8c90c575585418f719ab74d45e48b7"),
    ]
    assert len(expected_merged) == 37
    assert len(expected_ancestors) == 6
    for name, pr, sha in expected_merged:
        assert manifest.count(f"['{name}', {{ kind: 'merged', pr: {pr}, sha: '{sha}' }}]") == 1
    for name, sha in expected_ancestors:
        assert manifest.count(f"['{name}', {{ kind: 'ancestor-only', sha: '{sha}' }}]") == 1
    assert "tags/branch-retirement-20261016/${candidate.name}" in text
    assert "github.rest.git.createRef" in text
    assert "github.rest.git.getRef" in text
    assert "archive.object?.type !== 'commit'" in text
    assert "archive.object.sha !== candidate.sha" in text
    assert "if (error.status !== 404) throw error;" in text
    assert text.index("github.rest.git.createRef") < text.index("github.rest.git.deleteRef")
    assert text.index("archive.object.sha !== candidate.sha") < text.index("github.rest.git.deleteRef")
    assert "current.protected || current.commit.sha !== candidate.sha" in text
    for held in (
        "main", "aaa-cinema-pre-pregateway-bind",
        "backup/stoneyverify-stable-7fdc331-20261008",
        "chore/automate-supabase-migrations",
        "fix/supabase-out-of-order-migrations",
        "cleanup/schema-authority-consolidation",
        "fix/live-captions-stable-interim-recovery-20260927",
        "feat/discloud-pg-atomic-guild-config-20261013",
        "feat/cinema-library-intelligence",
        "fix/source-backup-large-toc-stream-20261013",
        "fix/ticket-panel-legacy-interaction-recovery-20260924",
    ):
        assert f"['{held}', {{ kind:" not in manifest


def test_eleventh_wave_completed_master_audit_branches_are_exact_frozen_merged_heads() -> None:
    text = _workflow()
    manifest = text.split("const reviewedRetirements = new Map([", 1)[1].split("]);", 1)[0]
    expected = [
        ("audit/production-command-contract-stoney-legacy-20261016", 603, "5aad13f4fd158083d1d1852f94afba0766bac1f0"),
        ("audit/retire-duplicate-usercustomize-20261016", 604, "b68733954c8ed5abc06c2abef758bc8f777cc382"),
        ("audit/verify-sync-native-ownership-20261016", 605, "ff7e6868d3ce0d9302c3edf8b93db8d64ff2dbc0"),
        ("chore/tag-archive-retired-merged-branches-20261016", 601, "f5bc96f3040308df17613a2a193f5b0bf2a0fe03"),
    ]
    assert len(expected) == 4
    for name, pr, sha in expected:
        assert manifest.count(
            f"['{name}', {{ kind: 'merged', pr: {pr}, sha: '{sha}' }}]"
        ) == 1
    for held in (
        "main", "aaa-cinema-pre-pregateway-bind",
        "backup/stoneyverify-stable-7fdc331-20261008",
        "chore/automate-supabase-migrations",
        "fix/supabase-out-of-order-migrations",
        "cleanup/schema-authority-consolidation",
        "fix/live-captions-stable-interim-recovery-20260927",
        "feat/discloud-pg-atomic-guild-config-20261013",
        "feature/shared-dank-picker",
        "fix/ticket-panel-legacy-interaction-recovery-20260924",
        "feat/cinema-library-intelligence",
    ):
        assert f"['{held}', {{ kind:" not in manifest
    assert "review.sha !== branch.commit.sha" in text
    assert "prs.some(pr => !pr.merged_at)" in text
    assert "pr.head.sha === branch.commit.sha" in text
    assert "inUseByOpenPR.has(name)" in text
    assert "current.protected || current.commit.sha !== candidate.sha" in text
    assert "tags/branch-retirement-20261016/${candidate.name}" in text
    assert "archive.object.sha !== candidate.sha" in text
    assert text.index("github.rest.git.createRef") < text.index("github.rest.git.deleteRef")


def test_twelfth_wave_superseded_single_file_and_merged_head_retirements() -> None:
    text = _workflow()
    manifest = text.split("const reviewedRetirements = new Map([", 1)[1].split("]);", 1)[0]
    merged = [
        ("chore/master-audit-completed-branch-retirement-20261016", 606, "55c56012680fe9f73f655f0d0583b36bcf631612"),
        ("audit/public-launch-readiness-contract-20261016", 607, "773e0a5f4eddc936eba660de821e4fe2b09d4a71"),
    ]
    for name, pr, sha in merged:
        assert manifest.count(
            f"['{name}', {{ kind: 'merged', pr: {pr}, sha: '{sha}' }}]"
        ) == 1
    unique_old = (
        "['claude/readiness-audit-dank-branding', "
        "{ kind: 'superseded-no-pr', replacement: 607, "
        "replacementSha: '773e0a5f4eddc936eba660de821e4fe2b09d4a71', "
        "onlyFile: 'tools/audit_public_launch_readiness.py', "
        "sha: '375a4dbcbc1604f3b23d09d33ab5f9fa8ef9eb4c' }]"
    )
    assert manifest.count(unique_old) == 1
    assert manifest.count("kind: 'superseded-no-pr'") == 1
    assert "if (prs.length) continue;" in text
    assert "original.parents?.length !== 1" in text
    assert "original.files?.length !== 1" in text
    assert "original.files[0].filename !== review.onlyFile" in text
    assert "pr.head.sha === review.replacementSha" in text
    assert "github.rest.pulls.listFiles" in text
    assert "f.filename === review.onlyFile && f.status === 'modified'" in text
    assert "archive.object.sha !== candidate.sha" in text
    assert text.index("github.rest.git.createRef") < text.index("github.rest.git.deleteRef")


def test_twelfth_wave_excludes_incomplete_protection_picker_and_production_state() -> None:
    text = _workflow()
    manifest = text.split("const reviewedRetirements = new Map([", 1)[1].split("]);", 1)[0]
    for held in (
        "main",
        "aaa-cinema-pre-pregateway-bind",
        "audit/protection-picker-migration",
        "feat/cinema-rolling-supervised-worker-20261014",
        "fix/ticket-panel-legacy-interaction-recovery-20260924",
        "feat/cinema-library-intelligence",
        "backup/stoneyverify-stable-7fdc331-20261008",
    ):
        assert f"['{held}', {{ kind:" not in manifest


def test_reviewed_zero_ahead_batch_requires_main_ancestry_and_archive() -> None:
    text = (Path(__file__).resolve().parents[1] / '.github/workflows/guarded-merged-branch-cleanup.yml').read_text(encoding='utf-8')
    for name in (
        'cleanup/schema-authority-consolidation',
        'fix/live-captions-stable-interim-recovery-20260927',
        'chore/fix-auto-archive-pr-pagination-20261016',
    ):
        assert "'" + name + "'" in text
    assert 'reviewedAncestorBatch.has(name)' in text
    assert 'prs.some(pr => !pr.merged_at || pr.base?.ref !== defaultBranch)' in text
    assert 'if (!await ancestorOfMain(branch.commit.sha)) continue;' in text
    assert 'archive.object.sha !== candidate.sha' in text
    assert "ref: `heads/${candidate.name}`" in text
    assert 'aaa-cinema-pre-pregateway-bind' in text


def test_member_evidence_retirement_requires_exact_head_single_file_and_main_parity() -> None:
    text = _workflow()
    assert "phase3/recent-evidence-filter" in text
    assert "e2286cc610b6a11e4da9d7f3657de7ccb217d376" in text
    assert "evidenceReview.sha !== branch.commit.sha || prs.length" in text
    assert "original.parents?.length !== 1" in text
    assert "original.files?.length !== 1" in text
    assert "original.files[0].status !== 'added'" in text
    assert "async def _load_recent_discord_evidence(" in text
    assert "activity_signals.setdefault(int(uid), []).extend(signals)" in text
    assert "Users Found — Page" in text
    assert "branch-retirement-20261016/" in text


def test_phase2_member_scan_retirement_is_exact_head_and_ui_parity_gated() -> None:
    text = _workflow()
    assert "phase2/show-member-scan-users-found" in text
    assert "939035b5ed2c94b0c0991966c4287ced4a60ecb3" in text
    assert "scanUiReview.sha !== branch.commit.sha" in text
    assert "else if (prs.length) continue;" in text
    assert "original.parents?.length !== 1" in text
    assert "original.files?.length !== 1" in text
    assert "original.files[0].status !== 'modified'" in text
    assert 'Users Found — Page {page + 1}/{pages}' in text
    assert "value=_build_page_users_text(report, page=page)" in text


def test_phase3_compact_scan_retirement_requires_closed_unmerged_pr_and_parity() -> None:
    text = _workflow()
    assert "phase3/compact-member-scan-ui" in text
    assert "ebee209590d27e7c60d9d7927ee21bf77274a8f6" in text
    assert "mergedPr: 30" in text
    assert "mergedPrHead: 'ca1034432791172783d0b0255d1c7fd49be57df5'" in text
    assert "prs.length !== 1" in text
    assert "prs[0].state !== 'closed'" in text
    assert "!prs[0].merged_at" in text
    assert "prs[0].head.sha !== taskLockReview.mergedPrHead" in text
    assert "comparison.data.ahead_by !== 1" in text
    assert "comparison.data.behind_by !== 0" in text
    assert "!uiText.includes('Page IDs')" in text
    assert "!uiText.includes('Data Notes')" in text


def test_stale_task_lock_retirement_requires_exact_head_and_no_pr() -> None:
    text = _workflow()
    assert "audit/protection-picker-migration" in text
    assert "66b3d2c1e175dd1a92ce71b58f0ce48f00b62b3b" in text
    assert "taskLockReview.sha !== branch.commit.sha" in text
    assert "else if (prs.length) continue;" in text
    assert "original.parents?.length !== 1" in text
    assert "original.files?.length !== 1" in text
    assert "original.files[0].status !== 'modified'" in text
    assert "currentText.includes('DS-AUD-PROTECTION-PICKERS')" in text


def test_historical_access_repair_task_record_retirement_is_pr_gated() -> None:
    text = _workflow()
    assert "fix/access-repair-fail-closed-20260924" in text
    assert "9b2116dfbf68876cb3df02ac357ac7af5435fec6" in text
    assert "historicalPr: 318" in text
    assert "prs[0].state !== 'closed'" in text
    assert "prs[0].merged_at" in text
    assert "prs[0].head.sha !== branch.commit.sha" in text


def test_reviewed_modlog_hotfix_retirement_requires_main_parity() -> None:
    text = _workflow()
    assert "hotfix/modlog-noise-coalesce" in text
    assert "1c9aa21dbd39c924632a544f3dff67fa798d1e0b" in text
    assert "modlogReview.sha !== branch.commit.sha" in text
    assert "DANK_ENABLE_PUBLIC_MEMBER_UPDATE_FALLBACK_MODLOG" in text
    assert "original.files[0].status !== 'modified'" in text


def test_reviewed_non_runtime_heads_are_strictly_bounded() -> None:
    """Two specifically audited historical refs cannot widen cleanup scope."""
    text = _workflow()
    manifest = text.split("const reviewedNonRuntime = new Map([", 1)[1].split("]);", 1)[0]
    assert manifest.count("kind:") == 2
    for name, sha in (
        ("chore/close-antinuke-authorized-bot-trust", "15b8b1ac26761339e80cd6f42e83ca44d12a63a5"),
        ("feature/shared-picker-entity-selects", "7ee337f12df9f02e3c75a3bf5a9b569dac02ada8"),
    ):
        assert manifest.count(f"['{name}', {{") == 1
        assert manifest.count(f"sha: '{sha}'") == 1

    assert "kind: 'closed-task-record', pr: 237, implementedPr: 236" in manifest
    assert "onlyFile: 'ACTIVE_TASK.md', ahead: 2" in manifest
    assert "kind: 'post-merge-audit', pr: 83" in manifest
    assert "mergedHead: '98ad6a9cff82e6deb88875b8c44c8443fb03417e'" in manifest
    assert "onlyFile: 'tools/audit_picker_adoption.py', ahead: 3" in manifest

    assert "nonRuntime.sha !== branch.commit.sha || prs.length !== 1" in text
    assert "pr.state !== 'closed'" in text
    assert "pr.base?.ref !== defaultBranch" in text
    assert "pr.merged_at || pr.head.sha !== branch.commit.sha" in text
    assert "!implemented || !await ancestorOfMain(implemented.merge_commit_sha)" in text
    assert "!pr.merged_at || pr.head.sha !== nonRuntime.mergedHead" in text
    assert "comparison.data.behind_by !== 0" in text
    assert "comparison.data.ahead_by !== nonRuntime.ahead" in text
    assert "comparison.data.files?.length !== 1" in text
    assert "comparison.data.files[0].filename !== nonRuntime.onlyFile" in text
    assert "comparison.data.files[0].status !== fileStatus" in text
    assert "current.protected || current.commit.sha !== candidate.sha" in text
    assert "archive.object.sha !== candidate.sha" in text
    assert "if (inUseByOpenPR.has(name)) continue;" in text


def test_reviewed_document_only_historical_audit_exact_manifest() -> None:
    text = _workflow()
    manifest = text.split(
        "const reviewedDocumentationOnly = new Map([", 1
    )[1].split("]);", 1)[0]
    assert manifest.count("['audit/") == 1
    assert "audit/p0-int-global-patcher-closeout" in manifest
    assert "12000de5475d99b7e62a319a3f14d3eb2536417b" in manifest
    assert "ahead: 3" in manifest
    for path in (
        "ACTIVE_TASK.md",
        "docs/PRODUCTION_READINESS_COMMAND_CENTER.md",
        "docs/STARTUP_GUARD_RUNTIME_OWNERSHIP_AUDIT.md",
    ):
        assert f"'{path}'" in manifest
    assert "branch.commit.sha !== documentary.sha || prs.length" in text
    assert "comparison.data.ahead_by !== documentary.ahead" in text
    assert "comparison.data.files?.length !== documentary.files.length" in text
    assert "f.status === 'modified' && documentary.files.includes(f.filename)" in text
    assert "inUseByOpenPR.has(name)" in text
    assert "current.protected || current.commit.sha !== candidate.sha" in text
    assert "archive.object.sha !== candidate.sha" in text
