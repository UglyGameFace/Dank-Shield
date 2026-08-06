# ACTIVE TASK

## DS-SETUP-020 — Entitled ID-verification setup selection and VC permissions regression

**Status:** ACTIVE — root-cause inspection in progress
**Branch:** `fix/ds-setup-020-id-verification-selection`
**Started:** 2026-08-06
**Forced switch:** User supplied the exact required `FORCE SWITCH` instruction because this blocks setup for the explicitly entitled partner server.

## Scope

Reported from guild `1357215261001912320`, which has explicit access to the ID Verification feature.

1. The setup module picker must let the server owner select exactly the modules they want.
2. `Voice Verify` must not hard-force `Simple Verify` when entitled `ID Verification` is selected and can satisfy the verification dependency.
3. Toggle actions must be independent, deterministic, repeat-safe, and protected against stale interaction state.
4. Continue Setup must request, create, and reconcile only the roles, channels, and permissions needed by the final selected modules.
5. No Simple Verify channel may be required or created when Simple Verify is OFF.
6. The VC verification channel must give the Unverified role the intended view/connect/speak/video-stream permissions and remove stale or contradictory managed overwrites without granting unrelated permissions.
7. Entitled and ordinary guild behavior must remain correctly separated.

## Required inspection before editing

- [ ] Canonical setup state model and saved-draft schema
- [ ] Preset selection and every custom toggle callback
- [ ] Dependency normalization and setup-plan rendering
- [ ] Entitlement source of truth and compatibility fallbacks
- [ ] Resume, Back, Setup Home, and repeated/stale interaction paths
- [ ] Continue Setup validation and question planner
- [ ] Channel creation/update and permission-reconciliation paths
- [ ] Voice verification runtime permission expectations
- [ ] Existing tests, audits, callers, guards, and related configuration
- [ ] Prior implementation from DS-SETUP-018 / PR #165 for regressions or conflicting compatibility code

## Findings

- The live UI explicitly reports `Voice Verify needs Simple Verify. Kept Simple Verify ON.`, proving the current dependency normalization forces Simple Verify before considering this guild's entitled ID-verification path.
- The live report also shows custom toggles can affect several modules at once or fail to preserve the owner's intended selection.
- Prior setup-safety evidence shows the voice-verification channel has previously allowed Unverified users to connect without the intended staff-controlled setup, so exact overwrite reconciliation must be tested rather than inferred.

## Changes

- [ ] No runtime code edited yet; inspection must identify the real execution path and root cause first.

## Validation plan

- [ ] Focused state/dependency unit tests
- [ ] Every toggle independently and repeated clicks
- [ ] Entitled ID-only, ID+Voice, Simple-only, Simple+Voice, no-verification, and ordinary-guild cases
- [ ] Resume/back and stale-interaction regressions
- [ ] No-Simple-Verify setup completion
- [ ] Exact VC overwrite reconciliation and stale-overwrite repair
- [ ] Targeted setup/audit suites
- [ ] Full Python compilation/static validation
- [ ] Full regression suite
- [ ] Conflict and duplicate implementation inspection

## Cleanup status

- [ ] Remove or integrate redundant dependency code, compatibility shims, temporary diagnostics, and duplicate permission writers only after references are verified.

## Blockers

- None currently known.

## Backlog preserved by the Single Active Task Lock

### DS-STATS-019 — Durable Dank Stats invite-block counting

Merged and code-revalidated, but live Discloud/Discord verification remains pending. It was explicitly paused by the user's force switch; no additional DS-STATS-019 implementation may start during DS-SETUP-020.

### DS-SETUP-019 — Cleanup confirmation modal crashes

Backlog only. Do not begin without another exact force switch after DS-SETUP-020 reaches its Definition of Done.

### DS-TICKET-020 — Guild owner final override for ticket actions

GitHub issue `#168`. Backlog only.

### DS-MEDIA-001 — Klipy direct GIF unfurl listener

Backlog only.

## Definition of Done

This task is not complete until root cause, implementation, targeted tests, regression checks, compilation/static validation, cleanup, conflict inspection, and branch/PR validation all pass. Live behavior must then show that guild `1357215261001912320` can select its entitled ID-verification setup without Simple Verify being forced and that the Unverified-role VC permissions match the intended verification flow.

## Single Active Task Lock

Do not begin another bug, feature, redesign, audit, or cleanup request until DS-SETUP-020 satisfies its Definition of Done, unless the user writes the exact required `FORCE SWITCH` instruction.
