# ACTIVE TASK

## DS-PROFILE-STUDIO-LIVE-009 — Complete live signature activation and delivery

**Status:** CLEAN IMPLEMENTATION / EXACT-HEAD CI REQUIRED
**Branch:** `fix/profile-live-channel-autosave-runtime`
**PR:** #138
**Base:** current `main`

## Single Active Task Lock

Do not switch to unrelated implementation work until a deployed normal member message posts a compact signature in an enabled channel and both server/member ON/OFF controls are obvious.

## Confirmed findings

- The canonical setup picker staged channel IDs and required a second **Save Selected Channels** click.
- The supplied interaction log showed the channel-select event but no save-button interaction, so the intended channel list was never persisted or enabled.
- PR #139 already merged the runtime-delivery repair into `main`, including stale-card verification, refreshed guild configuration, and delivery diagnostics.
- The old PR #138 branch diverged from `main`, duplicated runtime work, and still contained a temporary self-modifying integration workflow.
- `profile_card_setup_ui_core.py` still contained a complete obsolete staged setup panel with the old Save button and Welcome shortcut even though the canonical UI had moved elsewhere.
- Re-enabling previously saved channels did not revalidate current channel permissions.

## Scope

- Save and enable selected signature channels immediately from the channel picker.
- Remove the hidden second Save step from the canonical setup UI.
- Add an obvious server **Enable Live Signatures / Disable Live Signatures** control while preserving configured channels.
- Revalidate saved channel existence and permissions before server-wide re-enable.
- Keep the member-facing **Turn On/Off Live Signature** control already on `main`.
- Retain privacy precedence, one bot-owned card per channel, and safe cleanup.
- Keep the runtime-delivery repair from PR #139 unchanged rather than duplicating it.
- Remove the obsolete staged setup implementation from the shared core so there is only one real setup path.

## Validation

- [x] PR #138 branch rebuilt directly from current `main`.
- [x] Duplicate runtime edits and the temporary integration workflow removed.
- [x] Obsolete staged setup/save/welcome implementation removed from the shared core.
- [x] Static setup contracts require immediate saving, clear server controls, permission-safe re-enable, and one canonical setup path.
- [x] A callback regression test exercises the actual save path, persistence payload, cleanup, reconciliation, permission failure, and response acknowledgement.
- [x] Changed Python modules and focused test files pass local syntax compilation.
- [ ] Full unit suite and repository audits pass on the exact head.
- [ ] Branch remains conflict-free and zero commits behind current `main` after final validation.
- [ ] Deployed Discord smoke produces `✅ live_profile_card posted` and a visible signature after one channel selection.

## Cleanup

- [x] No alternate runtime, duplicate setup panel, compatibility fork, or temporary workflow remains in the PR.
- [x] PR diff is limited to setup activation, shared-helper cleanup, focused tests, and this task record.

## Backlog

- Fix departed-member reconciliation async-generator handling.
- Review contradictory worker startup wording.
- Enable automatic sharding before 100+ public guilds.
