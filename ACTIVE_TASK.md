# ACTIVE TASK

## DS-TICKET-034 — Restore public Create Ticket interaction after restart

**Status:** PRODUCTION RUNTIME REGISTERED — LIVE CLICK DISPATCH TELEMETRY IN VALIDATION
**Branch:** `investigate/ticket-panel-live-dispatch-telemetry`
**Base:** `0a9dc169d222c89634267a701dd0de4022f621ae` (`main`, merged PR #190)
**Started:** 2026-09-10

## Outcome required

An already-posted Dank Shield **Create Ticket** panel must remain usable after bot restart regardless of slash-command profile selection or an unrelated command-registration failure. Clicking the public button must reach the existing canonical ticket handler quickly enough to acknowledge Discord, without introducing a second ticket-creation implementation or weakening ticket safety checks.

## User-reported failure

- Existing public support panel displays **The application didn't respond in time** when **Create Ticket** is pressed.
- The affected panel was posted on 2026-06-11 and uses the same clean persistent custom ID still used by current code, so a stale button-ID mismatch is not the cause.

## Confirmed findings

- `commands_ext.public_ticket_panel_clean` remains the authoritative ticket creation owner.
- Its button handler calls `_defer(..., thinking=True)` before database/setup work once the handler is actually entered.
- PR #190 moved the clean persistent view/runtime binding outside selectable command registration and added an independent delayed fallback listener.
- PR #190 was merged to `main` as `0a9dc169d222c89634267a701dd0de4022f621ae` and deployed to Discloud app `1777867264417`.
- Production startup logs proved the deployed runtime registered successfully with `ticket_panel_runtime ready persistent_view=True fallback_listener=True` before the Discord gateway became ready.
- Production also registered the legacy ticket action views, connected to the Discord gateway, completed ticket startup sync/backfill, and showed no ticket-panel traceback in the captured startup logs.
- The captured production logs did not contain click-level telemetry, so they cannot distinguish among: Discord/event-loop delivery delay, persistent-view callback success, fallback takeover, or a handler return without acknowledgement.

## Current execution path under investigation

`Discord clean Create Ticket button (sv:ticket:panel:create:clean:v1)`
→ discord.py component event
→ persistent `PublicCreateTicketPanelView`
→ existing `_handle_panel_button`
→ immediate defer in `_handle_panel_button_core`
→ existing ticket setup/category flow

Independent recovery route:
`on_interaction` for the same clean custom ID
→ 150 ms grace period for persistent-view dispatch
→ if already acknowledged, stop
→ otherwise delegate to canonical `handle_public_ticket_panel_click`

The canonical handler's existing interaction-ID lock remains the duplicate-suppression authority.

## Changes in merged PR #190

- Added `stoney_verify/ticket_panel_runtime.py` as a runtime-binding module only.
- Installed the ticket runtime strictly after mandatory ticket security bootstrap and before selectable/general command registration.
- Registered both the canonical persistent view and a delayed clean-ID-only fallback listener.
- Added restart/idempotency/degraded/fail-closed regression coverage.
- Extended the dedicated Ticket Panel Single Owner workflow to own this runtime/startup path.

## Current telemetry changes

- Added narrow `ticket_panel_trace` logging only for the canonical clean Create Ticket custom ID.
- The runtime now records `listener_received` with interaction age, acknowledgement state, interaction ID, guild ID, and user ID.
- After the 150 ms persistent-view grace period it records either `persistent_ack_observed` or `fallback_dispatch`.
- After canonical fallback handling returns it records `fallback_return` with the final acknowledgement state and listener elapsed time.
- Fallback exceptions record `fallback_exception` before the existing warning.
- No ticket creation, setup, permission, category, numbering, persistence, or menu business logic was changed.
- Added focused tests proving the trace distinguishes persistent acknowledgement from fallback recovery and remains silent for unrelated component IDs.

## Validation / results

Merged/deployed implementation evidence:

- PR #190 exact-head workflows were green before merge.
- Full Dank Shield CI reported **1137 passed, 9 warnings**.
- Production Discloud update returned HTTP 200 and restarted the app successfully.
- Production startup proof: `ticket_panel_runtime ready persistent_view=True fallback_listener=True`.
- Production gateway connection succeeded and health heartbeat remained healthy in the captured log window.

Current telemetry branch validation is pending exact-head GitHub Actions.

## How live telemetry will identify the next root cause

- `listener_received age_ms < 3000` + `persistent_ack_observed` means Discord delivered promptly and the persistent callback acknowledged; a client-visible timeout would then point outside the registered ticket route and needs exact interaction timing/client evidence.
- `listener_received age_ms < 3000` + `fallback_dispatch` + `fallback_return response_done=True` means persistent dispatch missed the click but the independent fallback recovered it within the interaction window.
- `listener_received age_ms >= 3000` means the interaction reached the bot too late for a normal acknowledgement, strongly implicating process-wide event-loop/gateway delay rather than ticket database/setup work.
- `fallback_return response_done=False` means the canonical path returned without consuming the interaction and provides a concrete handler/lock path to inspect next.
- No `listener_received` line for a tested click means the bot process did not receive/dispatch that component event to the registered listener, which narrows the investigation to Discord routing/session/runtime behavior rather than ticket business logic.

## Cleanup / conflicts

- No second ticket creation implementation is being added.
- Legacy `ticket_create` compatibility remains separate from the clean custom ID.
- The runtime fallback still delegates to the clean owner and relies on the existing interaction-ID lock.
- Telemetry is scoped to one custom ID and does not log unrelated Discord component traffic.
- No unrelated Dank Design, verification, moderation, or ticket business behavior is in scope.

## Blockers / risks

- There is no direct Discloud connector in this conversation, so live deployment/log collection still runs through the user's Termux session.
- The current telemetry branch must pass focused and full repository validation before another production deployment.
- Production acceptance must use the existing June 11 panel. Reposting it would not test the original persistent-message path.

## Backlog

- None added. Event-loop/gateway remediation is not authorized until live telemetry proves that class of failure.

## Next step

Validate the telemetry branch on GitHub Actions, inspect the exact diff for accidental behavior changes, merge only if green, deploy that exact main head to Discloud app `1777867264417`, press the existing June 11 **Create Ticket** button once, and immediately capture the `ticket_panel_trace` lines. Use those trace stages and `age_ms` values to identify the failing layer before making any further functional change.

---

Previous implementation PR: DS-TICKET-034 restart-safe runtime was merged in PR #190 as `0a9dc169d222c89634267a701dd0de4022f621ae`; this branch continues the same active task with production-only diagnostic telemetry.
