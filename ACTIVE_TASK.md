# ACTIVE TASK

## Persistent interaction compatibility audit

**Status:** IMPLEMENTED — EXACT-HEAD VALIDATION IN PROGRESS
**Branch:** `audit/persistent-interaction-compatibility`
**Current base:** `2d90a4be61cdd32ee8a4b049c3098e48064c66f2`
**PR:** #216 — `Harden persistent interaction restart compatibility`

## Outcome

Make every live persistent Discord interaction owner restart-safe, retryable after partial registration failure, and semantically bound to the exact stale/persisted message being clicked so users cannot become stuck or accidentally act on newer state.

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
  - Register only the active `SpamIncidentRestoreView(restored=False)` as the persistent callback owner; `restored=True` remains a disabled rendering state only.
- `commands_ext/public_members_group.py`
  - Added exact `dm_message_id` notice resolution.
  - `I’m still active` and `I’m okay leaving` now fail closed for unmatched, resolved, or expired old messages instead of mutating a newer notice.
  - `What is this?` resolves the exact historical notice represented by the clicked DM where available.
  - Split persistent DM-view registration state from worker-listener registration state.
  - Failed view registration remains retryable on ready.
  - Failed listener attachment no longer silently marks the runtime installed.
- Added `tests/test_persistent_interaction_compatibility.py` covering all confirmed defects.

## Validation / results so far

Local canonical-environment validation used Python 3.11.16 and the repository dependency set:

- Supabase 2.x import smoke: passed.
- `tests/test_persistent_interaction_compatibility.py`: **6 passed**.
- Related existing restart/persistence regressions: **23 passed**.
- `python -m compileall -q stoney_verify main.py tools tests`: passed.
- `git diff --check`: passed.

GitHub-hosted one-shot repair validation run `34842302455` repeated the same Python 3.11 dependency install, strict patch application, focused regressions, compile, and diff check successfully before committing the implementation.

The temporary repair workflow removed itself in the implementation commit and is absent from the final PR tree/diff.

Exact-head validation on `fcd9a84a3160b7ac4027b7ce35d89cef69b69f18` reached green companion workflows and green Claim-first/Managed-category jobs while the full Python unit lane was still running. That head was superseded before merge because `main` advanced through PR #215; its incomplete run is retained only as intermediate evidence, not merge evidence.

## Current-main integration

`main` first advanced during the audit to `1b31acc3a29a1057d5f188ad409fc7e1619ed54e` via PR #214. That production change touched `anti_nuke_audit_compat_runtime.py`, its focused tests, and `ACTIVE_TASK.md`; it did not overlap the four persistent-interaction runtime modules.

The audit branch integrated that main state using merge commit `e5fd8418ac27c947d6cf3cb8aa437a3f461c5b2e`, preserving the already-validated interaction implementation blobs byte-for-byte.

`main` then advanced again through bookkeeping-only PR #215 to `2d90a4be61cdd32ee8a4b049c3098e48064c66f2`. PR #215 changed only `ACTIVE_TASK.md` and recorded DS-SEC-045 post-merge acceptance; no runtime or test overlap exists with this interaction repair. The current branch integrates that closure record while continuing to overlay only the same five validated interaction implementation/test blobs on current main.

## Final validation required

- Exact-head Dank Shield CI passes all required jobs.
- Full unit suite and standalone `tools/test_*.py` checks pass.
- Public setup, public command-surface, startup-friction, invite, setup-safety, Dank Design, role-truth, and event-boundary audits pass.
- Claim-first ticket security passes.
- Managed category SQL smoke test passes.
- Companion workflows triggered for the exact head pass.
- PR #216 remains mergeable with no unresolved review threads.
- Final compare against current `main` contains only:
  - `ACTIVE_TASK.md`
  - `stoney_verify/transcripts.py`
  - `stoney_verify/tickets_new/panel.py`
  - `stoney_verify/spam_guard.py`
  - `stoney_verify/commands_ext/public_members_group.py`
  - `tests/test_persistent_interaction_compatibility.py`
- No temporary workflow, startup guard, root runtime patch, schema migration, monkey patch, generated file, or unrelated change remains.
- Merge only the exact validated head through protected `main`.
- Validate canonical post-merge `main` CI and gated production-promotion ordering before calling the task complete.

## Cleanup / conflicts

- Basic Verify native restart runtime, clean public ticket panel runtime, Profile, and Community Tools were inspected and left unchanged because they already have correct retry/ownership behavior.
- `commands_ext/public_tickettool_parity_polish.py` has no live runtime registration caller; it remains a helper/legacy surface and is not a second live owner.
- Old submissions modules expose view builders but have no live importer; the live central interaction handler remains authoritative.
- Dormant `startup_guards/*` were not activated or modified.
- The temporary GitHub repair workflow is deleted from the branch final tree.
- PR #214 AntiNuke code and PR #215 DS-SEC-045 closeout evidence are preserved from current `main`.

## Blockers / risks

No known implementation blocker. Exact-head CI and protected merge remain hard gates. Runtime acceptance after deployment should include clicking representative old persistent ticket/member-notice components after a bot restart/reconnect to confirm Discord-side persistence behavior matches the tested registry semantics.

## Completed prior task

### DS-SEC-045 — Legitimate self-action audit classification

Complete, merged, and post-merge validated. Implementation PR #214 final head `8c6617a00258d9a5c4d1be878e6ec885f0f56eb6` merged as canonical main `1b31acc3a29a1057d5f188ad409fc7e1619ed54e`. Its full PR validation passed, post-merge Dank Shield CI run #2089 passed all required jobs, Ticket Owner Emergency Override passed, Deploy Supabase migrations succeeded, and `main` remained protected. Bookkeeping closeout PR #215 merged as `2d90a4be61cdd32ee8a4b049c3098e48064c66f2`. A live Basic Verify click remains useful operational smoke testing but is not a code blocker.

### Verification integrity audit repair

Completed and merged as PR #213. Canonical merge SHA: `5f51da0e338208538133f0b610c3e14a9c6f0bbc`.

### DS-AUD-009 — Release governance and production promotion safety

Completed and merged as PR #212. Canonical merge SHA: `9112528e42e77ec348abe69d9207e37a64294380`.

## Suspended task

### DS-SEC-044 — Hostile bot re-entry race and integration persistence

Suspended previously after PR #211 merged and CI passed. Remaining acceptance is the hostile/GANG-Nuker re-entry production test. Do not resume during this active task without explicit `FORCE SWITCH`.

## Next step

Validate the exact PR #216 head after integrating current `main` `2d90a4be61cdd32ee8a4b049c3098e48064c66f2`. If all required and companion workflows are green and the six-file compare remains clean, update this record with immutable validation evidence, revalidate that final bookkeeping head, mark PR #216 ready, merge the exact validated head through protected `main`, and verify canonical post-merge CI plus gated production promotion.