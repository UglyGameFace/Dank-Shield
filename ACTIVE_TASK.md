# ACTIVE TASK

## Persistent interaction compatibility audit

**Status:** COMPLETE — MERGED AND POST-MERGE VALIDATED
**Implementation branch:** `audit/persistent-interaction-compatibility`
**Implementation PR:** #216 — `Harden persistent interaction restart compatibility`
**Final validated PR head:** `39c9e74c086b2e7039ba9052d473867adfbaebf8`
**Canonical merge SHA:** `4c7d21eaf893f2ba6ad4079f4fea2a79cc96c4c9`
**Closeout branch:** `chore/close-persistent-interaction-audit`

## Outcome

Make every live persistent Discord interaction owner restart-safe, retryable after partial registration failure, and semantically bound to the exact persisted message being clicked so users cannot become stuck or accidentally act on newer state.

## Scope completed

- Live persistent views and stable `custom_id` ownership reachable from `stoney_verify/app.py`.
- Transcript/ticket persistent view registration.
- `tickets_new.panel` restart registration.
- Spam Guard persistent panel/restore registration.
- Member Activity Notice persistent DM actions and worker/view registration.
- Focused restart, stale-message, duplicate-owner, and retry regression coverage.

Out of scope remained unchanged: AntiNuke behavior, schema redesign, billing, broad setup redesign, dormant startup-guard activation, unrelated cleanup, and old modules without a live importer.

## Root causes fixed

- `stoney_verify/transcripts.py` set `_TRANSCRIPT_VIEWS_REGISTERED = True` before all persistent `add_view()` calls succeeded, suppressing retries after a partial startup failure.
- `stoney_verify/tickets_new/panel.py` had the same premature aggregate-success problem across persistent ticket views.
- Spam Guard retried already-successful persistent registrations after a partial failure and registered both active and disabled renderings of the same restore custom ID.
- Member Activity Notice DM buttons resolved the newest pending notice instead of the exact Discord DM message clicked, allowing stale UI to target newer state.
- Member Activity Notice runtime marked worker/view setup as started before persistent view or listener registration actually succeeded.

## Implemented

- `stoney_verify/transcripts.py`
  - Added per-view success keys.
  - Added `register_transcript_persistent_views()`.
  - Retry only missing views.
  - Aggregate success becomes true only when all required owners are registered.
- `stoney_verify/tickets_new/panel.py`
  - Added per-view success keys.
  - Added `register_ticket_persistent_views()`.
  - Retry only missing ticket views and mark aggregate success only when complete.
- `stoney_verify/spam_guard.py`
  - Added per-view/page success keys.
  - Retry only missing Spam Guard panels.
  - Register only active `SpamIncidentRestoreView(restored=False)` as the persistent callback owner; the restored/disabled view remains rendering-only.
- `stoney_verify/commands_ext/public_members_group.py`
  - Added exact `dm_message_id` notice resolution.
  - Stale/resolved/expired notice DMs fail closed instead of mutating a newer notice.
  - `What is this?` resolves the exact historical notice represented by the clicked DM where available.
  - Split persistent DM-view registration state from worker-listener registration state.
  - Failed view registration remains retryable on ready.
  - Failed listener attachment no longer silently marks runtime setup successful.
- Added `tests/test_persistent_interaction_compatibility.py` covering the confirmed defects.

## Validation history

Initial canonical-environment validation used Python 3.11.16 and the repository dependency set:

- Supabase 2.x import smoke — passed.
- `tests/test_persistent_interaction_compatibility.py` — 6 passed.
- Related existing restart/persistence regressions — 23 passed.
- `python -m compileall -q stoney_verify main.py tools tests` — passed.
- `git diff --check` — passed.

GitHub-hosted one-shot repair validation run `34842302455` repeated the Python 3.11 dependency install, strict patch application, focused regressions, compile, and diff checks successfully. The temporary repair workflow removed itself and was absent from the final PR tree.

### Superseded test-only failure

Exact head `b5295bac128c0e0cf5e09cce45277bc2b19a6554` produced one full-suite failure with 1464 tests passing. The runtime implementation was not the failure. The new regression asserted the concrete class name `TicketPanelView`, while another test can import dormant `startup_guards/legacy_public_ticket_panel_disable.py` and replace that module symbol with `DisabledLegacyTicketPanelView` in the shared test process.

The correction changed only the regression assertion to verify the single legacy-public registration slot and retry semantics regardless of which valid compatibility class owns the symbol. No runtime module changed in that correction.

## Final PR validation

Functional exact head `df1a9ab954cd8debc3d6bdba9872fe2c6635d63d` passed the full implementation validation.

Final bookkeeping head `39c9e74c086b2e7039ba9052d473867adfbaebf8` was then revalidated from scratch before merge.

On that exact final PR head:

- Dank Shield CI #2101 / `34852996633` — success.
  - Python compile check — success.
  - Full unit suite — success.
  - Standalone `tools/test_*.py` checks — success.
  - Public setup/isolation audit — success.
  - Canonical public command-surface audit — success.
  - Public command/startup-friction audit — success.
  - Public invite permissions audit — success.
  - Setup safety audit — success.
  - Dank Design Smart Auto-Detect audit — success.
  - Role-truth ownership audit — success.
  - Event-boundary ownership audit — success.
  - Claim-first ticket security — success.
  - Managed category SQL smoke test — success.
- Application Command Size Diagnostics #1119 / `34852996581` — success.
- Dank Design Regression CI #369 / `34852996644` — success.
- Ticket Owner Emergency Override #672 / `34852996589` — success.
- Profile Runtime Diagnostics #875 / `34852996665` — success.
- PR #216 remained mergeable with no unresolved review threads.
- Final PR file scope remained exactly six files: this task record, four runtime modules, and the focused regression file.

## Merge and production acceptance

PR #216 was marked ready only after final exact-head validation and merged through protected `main` using expected-head guard `39c9e74c086b2e7039ba9052d473867adfbaebf8`.

Canonical merge:

- `main`: `4c7d21eaf893f2ba6ad4079f4fea2a79cc96c4c9`
- Merge commit is verified and has parents `0e75103d0a4da161241ed9500217db2d33402e8d` and final PR head `39c9e74c086b2e7039ba9052d473867adfbaebf8`.
- `main` remains protected with required checks for `Python compile check`, `Claim-first ticket security`, and `Managed category SQL smoke test`.

Post-merge acceptance on that exact canonical merge SHA:

- Dank Shield CI #2102 / `34857032006` — success.
  - Python compile check — success.
  - Full unit suite — success.
  - Standalone tool checks — success.
  - All public/static audits — success.
  - Claim-first ticket security — success.
  - Managed category SQL smoke test — success.
- Ticket Owner Emergency Override #673 / `34857032034` — success.

## Production promotion ordering

Release governance behaved correctly after the merge:

1. Canonical Dank Shield CI #2102 completed successfully on `4c7d21eaf893f2ba6ad4079f4fea2a79cc96c4c9` at 2026-09-14T14:47:57Z.
2. Only after that success, Deploy Supabase migrations #22 / `34857969808` started at 2026-09-14T14:47:58Z.
3. The deployment targeted the same canonical merge SHA and completed successfully.
4. Deployment job `Push pending migrations` passed:
   - checkout validated release commit;
   - immutable current-main target verification;
   - required-secret verification;
   - Supabase CLI installation;
   - production project link;
   - migration status;
   - pending-migration preview;
   - pending-migration apply.

No production migration workflow ran ahead of canonical CI.

## Cleanup / conflicts

- Basic Verify native restart runtime, clean public ticket panel runtime, Profile, and Community Tools were inspected and left unchanged because their retry/ownership behavior was already correct.
- `commands_ext/public_tickettool_parity_polish.py` has no live runtime registration caller and remains a helper/legacy surface, not a second live owner.
- Old submissions modules expose view builders but have no live importer; the central live interaction handler remains authoritative.
- Dormant `startup_guards/*` were not activated or modified.
- No new startup guard, root runtime patch, schema migration, monkey patch, generated file, or unrelated production change was introduced by this task.
- No implementation blocker remains.

## Runtime acceptance note

Repository acceptance is complete. A live operational smoke test after bot restart/reconnect can still click representative old persistent ticket and member-notice components to confirm Discord-side persistence behavior in production, but that is not a remaining code or merge blocker.

## Completed prior tasks

### DS-SEC-045 — Legitimate self-action audit classification

Complete, merged, and post-merge validated. Implementation PR #214 final head `8c6617a00258d9a5c4d1be878e6ec885f0f56eb6` merged as canonical main `1b31acc3a29a1057d5f188ad409fc7e1619ed54e`. Bookkeeping closeout PR #215 merged as `2d90a4be61cdd32ee8a4b049c3098e48064c66f2`.

### Verification integrity audit repair

Completed and merged as PR #213. Canonical merge SHA: `5f51da0e338208538133f0b610c3e14a9c6f0bbc`.

### DS-AUD-009 — Release governance and production promotion safety

Completed and merged as PR #212. Canonical merge SHA: `9112528e42e77ec348abe69d9207e37a64294380`.

## Suspended task

### DS-SEC-044 — Hostile bot re-entry race and integration persistence

Still suspended. PR #211 merged and CI passed previously. Remaining acceptance is the hostile/GANG-Nuker re-entry production test after deployment. Do not resume it from this closeout record without the explicit task-switch instruction already required by the project workflow.

## Next step

Persistent interaction compatibility audit is complete. Merge this one-file closeout record only after its own exact-head required CI is green. After that closeout merge, verify canonical `main` CI once more and leave this task closed unless new runtime evidence shows a regression.
