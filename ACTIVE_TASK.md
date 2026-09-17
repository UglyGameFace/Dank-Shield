# ACTIVE TASK

## DS-MEMBER-JOIN-LOG-REGRESSION — Restore reliable member join/leave lifecycle logging

**Status:** FINAL VALIDATION / MERGE GATE

**Branch:** `fix/member-join-leave-log-regression`
**Base main:** `98290563f7244af198c4384a595cb66cdc76e0e3`

## Previous task lock closed

`DS-AUD-CONTEXTUAL-REPAIR-LIVE-REGRESSION` is no longer active:

- PR #250 final head: `e28a6a65bb678d4b0f147c0c4cea0e9e987d8a62`
- all five triggered exact-head workflows completed successfully
- PR #250 merged as `98290563f7244af198c4384a595cb66cdc76e0e3`
- that merge commit became `main`
- Discloud commit status on that merge commit was successful

The Single Active Task Lock is now this member-lifecycle regression only. Do not switch to another audit item until this exact task is validated, merged, and verified on `main`.

## User-reported production regression

Real members were joining the guild, including members arriving through a bot that presents Discord invites as hyperlinks, but Dank Shield was not posting those joins in the configured join/leave channel.

## Root cause

The August 6 canonical Welcome Card change (`ae556fa34d4f9e2dd8cb7562d81472c592baa22e`) removed the operational join sender from `JOIN_LEAVE_KEYS` and made Welcome Card Studio the only public join output. That made the configured join/leave route effectively exit-only.

Closure review found the matching leave-side coupling in the same lifecycle boundary: `_leave_listener()` delegated only to `send_live_exit_card()`, and that runtime correctly honors the Exit Card Studio enable gate. As a result, explicitly disabling or failing Exit Card Studio could also suppress the configured operational leave entry.

Invite-source attribution is independent of Discord's member gateway event. A normal invite wrapped in a hyperlink still produces `on_member_join`; redirect/OAuth-style sources may be unattributable to a specific invite code, but attribution uncertainty must never suppress the base lifecycle event.

Discord member events are enabled in code (`intents.members = True`). The defect was lifecycle route ownership, not the hyperlink itself.

## Canonical ownership after the fix

- `public_member_lifecycle_runtime` is the public-core bootstrap and installs `member_lifecycle_router_guard`.
- `member_lifecycle_router_guard` owns the operational join/leave event route through `JOIN_LEAVE_KEYS`.
- Welcome Card Studio owns the optional member-facing welcome card.
- Exit Card Studio owns the optional member-facing leave card.
- Staff invite-source/modlog auditing stays separate from public lifecycle output.
- `events.py` keeps its member lifecycle output staff/modlog-only.
- Profile-card `on_member_remove` handlers remain cleanup-only.
- AntiNuke `on_member_remove` remains security-only.
- `public_setup_logs` explicitly does not register lifecycle listeners.
- historical `welcome_member_events_guard` is not startup-loaded.
- historical `public_member_lifecycle_logs` remains unregistered from command profiles.
- retired v3/v4 public lifecycle marker senders remain disabled.

## Implementation

- preserve Welcome Card Studio as the canonical member-facing welcome card
- preserve Exit Card Studio as the canonical member-facing leave card
- independently log every configured member join and leave through `JOIN_LEAVE_KEYS`
- operational join logging does not depend on Welcome Card Studio being enabled, succeeding, or using the same channel
- operational leave logging does not depend on Exit Card Studio being enabled, succeeding, or using the same channel
- if a Studio successfully posts to the exact same configured lifecycle channel, suppress only that true duplicate
- if a Studio posts to a different channel, keep the operational lifecycle log
- if a Studio raises an exception, keep the operational lifecycle log
- keep join logging independent of invite-source attribution success
- preserve staff audit/modlog ownership separately
- `/dank member-logs` no longer forcibly enables Exit Card Studio merely because the lifecycle log channel changed
- `/dank member-logs` still updates the Exit Card target for compatibility without overriding the user's explicit Studio enable/disable choice
- lifecycle status/help text now describes the operational route and Studio routes separately

## Regression coverage

`tests/test_modlog_join_dedupe_behavior.py` covers:

- canonical Welcome Card runtime still receives joins
- disabled Welcome Card Studio does not suppress the operational join log
- same-channel Welcome Card delivery suppresses only the duplicate operational join
- different-channel Welcome Card delivery does not suppress the operational join
- Welcome Card runtime exceptions do not suppress the operational join
- disabled Exit Card Studio does not suppress the operational leave log
- same-channel Exit Card delivery suppresses only the duplicate operational leave
- different-channel Exit Card delivery does not suppress the operational leave
- Exit Card runtime exceptions do not suppress the operational leave
- existing modlog semantic dedupe remains intact

`tools/test_join_leave_log_centralized.py` guards:

- independent join and leave operational senders
- `JOIN_LEAVE_KEYS` resolution for both directions
- same-channel duplicate suppression markers
- no configured-route diagnostics
- independence from both Studio enable gates
- `/dank member-logs` must not force `exit_card_enabled = True`
- retired public lifecycle sender markers must stay absent

Existing lifecycle static tests additionally guard the public-core bootstrap, old listener retirement, broad alias compatibility, staff/public separation, and canonical Studio ownership.

## Scope / integration review

Current branch scope is intentionally limited to:

- `ACTIVE_TASK.md`
- `stoney_verify/startup_guards/member_lifecycle_router_guard.py`
- `tests/test_modlog_join_dedupe_behavior.py`
- `tools/test_join_leave_log_centralized.py`

No invite-policy changes, moderation-policy changes, database/schema changes, role changes, ticket changes, or unrelated audit redesign are included.

## Validation gate

Before this task lock can be released:

- targeted lifecycle behavior tests pass
- lifecycle centralization/runtime static audits pass
- Python compile checks pass
- full applicable unit suite passes
- every triggered workflow succeeds on the exact final head
- branch is 0 behind current `main`
- final diff contains only the intended four files
- no unresolved review threads or review blockers exist
- no debug code, conflict artifacts, secrets, generated junk, or duplicate lifecycle sender is present
- PR is merged only with the exact validated head
- resulting merge commit is verified as current `main`
- merge/deployment status is checked

A real Discord member join/leave is the final production exercise and cannot be simulated by GitHub CI; repository validation must not be mislabeled as a live Discord event test.

## Backlog after this task closes

Do not widen this task. If a specific third-party invite bot uses a non-invite OAuth flow that Discord cannot attribute to an invite code, richer source attribution can be evaluated separately after the lifecycle log regression is merged and verified. Dormant historical lifecycle compatibility files can likewise be considered during later cleanup only if they are proven safe to remove without reactivating or breaking ownership contracts.

## Next step

Validate the exact final branch head, fix only failures tied to this lifecycle task, compare against current `main`, inspect the final PR state, merge the exact validated head, verify `main` and deployment/status evidence, then release the task lock.
