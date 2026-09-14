# ACTIVE TASK

## Persistent interaction compatibility audit

**Status:** VALIDATED — FINAL BOOKKEEPING HEAD REVALIDATION REQUIRED BEFORE PROTECTED MERGE
**Branch:** `audit/persistent-interaction-compatibility`
**Current base:** `0e75103d0a4da161241ed9500217db2d33402e8d`
**Functional validated head:** `df1a9ab954cd8debc3d6bdba9872fe2c6635d63d`
**PR:** #216 — `Harden persistent interaction restart compatibility`

## Outcome

Make every live persistent Discord interaction owner restart-safe, retryable after partial registration failure, and semantically bound to the exact persisted message being clicked so users cannot become stuck or accidentally act on newer state.

## Scope

- Live persistent views and stable `custom_id` ownership reachable from `stoney_verify/app.py`.
- Transcript/ticket persistent view registration.
- `tickets_new.panel` restart registration.
- Spam Guard persistent panel/restore registration.
- Member Activity Notice persistent DM actions and worker/view registration.
- Focused restart, stale-message, duplicate-owner, and retry regression coverage.

Out of scope: AntiNuke behavior, schema changes, billing, broad setup redesign, dormant startup-guard activation, unrelated cleanup, and old modules without a live importer.

## Findings / root cause

- `stoney_verify/transcripts.py` set `_TRANSCRIPT_VIEWS_REGISTERED = True` before four `add_view()` calls succeeded. A single startup failure therefore permanently suppressed retries even though the central interaction router intentionally steps aside for those stable ticket/verification component IDs.
- `stoney_verify/tickets_new/panel.py` had the same premature aggregate-success bug across four persistent ticket views.
- Spam Guard only marked aggregate success after the whole registry completed, but a retry re-added views that had already succeeded. It also registered both active and disabled renderings of `SpamIncidentRestoreView`; both share the same restore custom ID, creating duplicate callback ownership.
- Member Activity Notice DM buttons resolved `_latest_pending_notice_for_user()` rather than the exact Discord message clicked. An old DM could therefore mutate a newer pending notice.
- Member Activity Notice runtime used one `_NOTICE_WORKER_STARTED` flag set before persistent view or listener registration succeeded and silently swallowed `add_view()` failure, preventing safe retry after startup trouble.
- Existing `dm_message_id` persistence already provided the correct compatibility key, so no per-notice dynamic custom IDs or new registry layer were required.

## Execution path before repair

1. Bot starts/reconnects and attempts persistent `add_view()` registration.
2. A partial registration failure occurs.
3. Transcript/ticket aggregate flags can still claim completion, or Spam Guard retries all prior successes.
4. Stable components remain posted in Discord across process restarts.
5. The central router intentionally excludes IDs owned by registered persistent views, so a missing owner can leave an otherwise valid old button dead.
6. Member Activity Notice buttons separately look up the newest pending row rather than the row whose `dm_message_id` matches the clicked DM.

## Implemented

- `transcripts.py`
  - Added per-view success keys.
  - Added `register_transcript_persistent_views()`.
  - Retry only missing views.
  - Aggregate success becomes true only when all required owners are registered.
- `tickets_new/panel.py`
  - Added per-view success keys.
  - Added `register_ticket_persistent_views()`.
  - Retry only missing ticket views and mark aggregate success only when complete.
- `spam_guard.py`
  - Added per-view/page success keys.
  - Retry only missing Spam Guard panels.
  - Register only active `SpamIncidentRestoreView(restored=False)` as the persistent callback owner; `restored=True` remains a disabled rendering state only.
- `commands_ext/public_members_group.py`
  - Added exact `dm_message_id` notice resolution.
  - `I’m still active` and `I’m okay leaving` now fail closed for unmatched, resolved, or expired old messages instead of mutating a newer notice.
  - `What is this?` resolves the exact historical notice represented by the clicked DM where available.
  - Split persistent DM-view registration state from worker-listener registration state.
  - Failed view registration remains retryable on ready.
  - Failed listener attachment no longer silently marks the runtime installed.
- Added `tests/test_persistent_interaction_compatibility.py` covering all confirmed defects.

## Validation history

Initial canonical-environment validation used Python 3.11.16 and the repository dependency set:

- Supabase 2.x import smoke: passed.
- `tests/test_persistent_interaction_compatibility.py`: 6 passed.
- Related existing restart/persistence regressions: 23 passed.
- `python -m compileall -q stoney_verify main.py tools tests`: passed.
- `git diff --check`: passed.

GitHub-hosted one-shot repair validation run `34842302455` repeated the Python 3.11 dependency install, strict patch application, focused regressions, compile, and diff check successfully before committing the implementation. The temporary repair workflow removed itself and is absent from the PR tree/diff.

### Superseded exact-head failure and correction

Exact head `b5295bac128c0e0cf5e09cce45277bc2b19a6554` produced one full-suite failure with 1464 tests passing. The runtime implementation itself was correct. The new regression asserted the concrete class name `TicketPanelView`, but another test can import dormant `startup_guards/legacy_public_ticket_panel_disable.py`, which replaces that module symbol with `DisabledLegacyTicketPanelView` inside the shared test process. Both classes occupy the same single legacy-public registration slot.

The correction changed only the regression assertion so it verifies the single registration slot and retry behavior regardless of which legitimate compatibility class currently owns the symbol. No runtime module changed in that correction.

## Final functional validation evidence

Functional exact head `df1a9ab954cd8debc3d6bdba9872fe2c6635d63d` is fully green.

### Dank Shield CI

Run #2100 / `34851504419` — **success**.

Required jobs:

- `Python compile check` — success.
  - committed diff whitespace — success.
  - Python compile — success.
  - full unit suite — **1465 passed, 9 warnings**.
  - standalone `tools/test_*.py` checks — success.
  - public setup text/isolation audit — success.
  - canonical public command-surface audit — success.
  - public command/startup-friction audit — success.
  - public invite permissions audit — success.
  - setup safety audit — success.
  - Dank Design Smart Auto-Detect audit — success.
  - role-truth ownership audit — success.
  - event-boundary ownership audit — success.
- `Claim-first ticket security` — success.
- `Managed category SQL smoke test` — success.

### Companion workflows

All companion workflows on the same exact head succeeded:

- Application Command Size Diagnostics #1118 / `34851504449` — success.
- Dank Design Regression CI #368 / `34851504421` — success.
- Ticket Owner Emergency Override #671 / `34851504460` — success.
- Profile Runtime Diagnostics #874 / `34851504466` — success.

### PR integrity

- PR #216 is mergeable.
- No unresolved review threads.
- Final compare against current `main` contains exactly six files:
  - `ACTIVE_TASK.md`
  - `stoney_verify/transcripts.py`
  - `stoney_verify/tickets_new/panel.py`
  - `stoney_verify/spam_guard.py`
  - `stoney_verify/commands_ext/public_members_group.py`
  - `tests/test_persistent_interaction_compatibility.py`
- No temporary workflow, startup guard, root runtime patch, schema migration, monkey patch, generated file, or unrelated runtime change remains.
- Current `main` is `0e75103d0a4da161241ed9500217db2d33402e8d` and remains protected with required GitHub Actions checks for `Python compile check`, `Claim-first ticket security`, and `Managed category SQL smoke test`.

## Current-main integration

`main` advanced during this task through PR #214, bookkeeping PR #215, and bookkeeping PR #218. Their runtime changes did not overlap the four persistent-interaction implementation modules. PR #218 changed only `ACTIVE_TASK.md`. The audit branch integrated each current-main advance while preserving the persistent-interaction runtime blobs and final six-file scope.

## Cleanup / conflicts

- Basic Verify native restart runtime, clean public ticket panel runtime, Profile, and Community Tools were inspected and left unchanged because they already have correct retry/ownership behavior.
- `commands_ext/public_tickettool_parity_polish.py` has no live runtime registration caller; it remains a helper/legacy surface and is not a second live owner.
- Old submissions modules expose view builders but have no live importer; the live central interaction handler remains authoritative.
- Dormant `startup_guards/*` were not activated or modified.
- The temporary GitHub repair workflow is deleted from the branch final tree.
- No production runtime file was changed by the final regression-assertion correction.

## Blockers / risks

No known implementation blocker remains. The only repository gate left is revalidation of this final bookkeeping-only task-record head before protected merge. Runtime acceptance after deployment should include representative old persistent ticket/member-notice components after restart/reconnect to confirm Discord-side persistence behavior matches the tested registry semantics.

## Completed prior task

### DS-SEC-045 — Legitimate self-action audit classification

Complete, merged, and post-merge validated. Implementation PR #214 final head `8c6617a00258d9a5c4d1be878e6ec885f0f56eb6` merged as canonical main `1b31acc3a29a1057d5f188ad409fc7e1619ed54e`. Bookkeeping closeout PR #215 merged as `2d90a4be61cdd32ee8a4b049c3098e48064c66f2`.

### Verification integrity audit repair

Completed and merged as PR #213. Canonical merge SHA: `5f51da0e338208538133f0b610c3e14a9c6f0bbc`.

### DS-AUD-009 — Release governance and production promotion safety

Completed and merged as PR #212. Canonical merge SHA: `9112528e42e77ec348abe69d9207e37a64294380`.

## Suspended task

### DS-SEC-044 — Hostile bot re-entry race and integration persistence

Suspended previously after PR #211 merged and CI passed. Remaining acceptance is the hostile/GANG-Nuker re-entry production test. Do not resume during this active task without explicit `FORCE SWITCH`.

## Next step

Revalidate the exact head created by this bookkeeping-only task-record update. If all required and companion workflows remain green, mark PR #216 ready, merge that exact validated head through protected `main`, then verify canonical post-merge Dank Shield CI and gated Supabase production-promotion ordering. Do not modify the branch again after the final green validation unless a new failure requires it.
