# ACTIVE TASK

## DS-TICKET-034 — Restore public Create Ticket interaction after restart

**Status:** IMPLEMENTATION + CODE VALIDATION GREEN — FINAL RECORD COMMIT CI PENDING; PRODUCTION ACCEPTANCE PENDING
**Branch:** `fix/ticket-panel-persistent-runtime-timeout`
**Base:** `b4550f255fe8e7d46944b141f33324a7ad01d255` (`main`, merged PR #189)
**Started:** 2026-09-10

## Outcome required

An already-posted Dank Shield **Create Ticket** panel must remain usable after bot restart regardless of slash-command profile selection or an unrelated command-registration failure. Clicking the public button must reach the existing canonical ticket handler quickly enough to acknowledge Discord, without introducing a second ticket-creation implementation or weakening ticket safety checks.

## User-reported failure

- Existing public support panel displays **The application didn't respond in time** when **Create Ticket** is pressed.
- The affected panel was posted on 2026-06-11 and uses the same clean persistent custom ID still used by current code, so a stale button-ID mismatch is not the cause.

## Findings / root cause

- `commands_ext.public_ticket_panel_clean` remains the authoritative ticket creation owner.
- Its button callback enters `_handle_panel_button_core`, which immediately defers the Discord interaction before database/setup work. If that callback is dispatched, the normal path acknowledges promptly.
- The clean panel's persistent `bot.add_view(PublicCreateTicketPanelView())` registration was coupled to `register_public_ticket_panel_clean`, which itself only runs through the selectable/tolerant slash-command module loader.
- Therefore an already-posted clean panel could be left with no runtime handler after restart when that command module was not selected or command registration failed before reaching it.
- The old `tickets_new.panel.TicketPanelView` only covers legacy `ticket_create` messages and cannot dispatch the clean `sv:ticket:panel:create:clean:v1` button.
- Basic Verify already uses the correct structural pattern: install the interaction runtime independently from slash-command registration before login.

## Execution path

`Discord clean Create Ticket button (sv:ticket:panel:create:clean:v1)`
→ persistent `PublicCreateTicketPanelView`
→ `handle_public_ticket_panel_click`
→ `_handle_panel_button`
→ immediate defer in `_handle_panel_button_core`
→ existing open-ticket/setup/category checks
→ category picker / confirm
→ existing canonical ticket creation path

Independent delayed fallback:
`on_interaction` for the same clean custom ID
→ wait 150 ms for persistent-view dispatch
→ stop if already acknowledged
→ canonical `handle_public_ticket_panel_click`

The canonical handler's existing interaction-ID lock remains the duplicate-suppression authority.

## Changes

- Added `stoney_verify/ticket_panel_runtime.py` as a runtime-binding module only. It does not create channels, allocate tickets, or own business logic.
- It installs both the canonical persistent clean panel view and a delayed clean-ID-only fallback listener.
- Runtime installation is idempotent and reports ready/degraded/unavailable state.
- `stoney_verify/commands.py` installs the public ticket panel runtime strictly after mandatory ticket security bootstrap and before the selectable general command registrar.
- The existing `public_ticket_panel_clean` registrar remains compatible and sees the runtime registration flags, so it does not register a second persistent view.
- Added `tests/test_ticket_panel_native_restart_runtime.py` covering primary registration, independent fallback registration, idempotency, clean-ID filtering, canonical delegation, acknowledged-interaction suppression, degraded fallback operation, fail-closed behavior, and startup ordering.
- Test runtime globals are reset automatically so the new tests do not leak registration state into unrelated tests.
- Extended `.github/workflows/ticket-panel-owner.yml` so runtime and startup-path changes trigger the focused ticket panel gate and compile the affected files.

## Validation / results

Implementation-bearing head `ba656ff4e5ce24ff38f9e13860859ced2f816772` passed every PR workflow before this record-only update:

- **Ticket Panel Single Owner #20:** success; focused compile succeeded and **19 passed, 1 warning**.
- **Dank Shield CI #1771:** success; `git diff --check`, compileall, **1137 passed, 9 warnings**, standalone tool checks, public setup/command/invite/safety audits, Smart Auto-Detect audit, role-truth audit, event-boundary audit, managed-category SQL smoke test, and claim-first ticket security all passed.
- **Application Command Size Diagnostics #821:** success.
- **Ticket Owner Emergency Override #342:** success.
- **Profile Runtime Diagnostics #626:** success.
- **Dank Design Regression CI #99:** success.
- Full-CI startup smoke emitted `ticket_panel_runtime ready persistent_view=True fallback_listener=True`, proving the new runtime registers before the public command surface is built.
- Branch comparison after the green run was **7 commits ahead, 0 behind `main`**, with the diff limited to ticket runtime/startup wiring, focused tests/workflow, and this active-task record.
- PR review-thread check returned no review threads.

This ACTIVE_TASK update is intentionally a record-only final branch mutation. Its exact resulting SHA must pass the same PR checks before repository validation is treated as final. The exact final SHA and final CI result should be recorded in PR #190 rather than creating another bookkeeping commit and restarting the validation loop again.

## Cleanup / conflicts

- No second ticket creation implementation was added.
- Legacy `ticket_create` compatibility remains intact and separate from the clean custom ID.
- The new fallback delegates to the clean owner and uses the owner's existing interaction-ID lock rather than introducing another duplicate/rate-limit mechanism.
- No unrelated Dank Design behavior is being changed.
- Focused tests now restore module-level runtime flags after each case, preventing cross-test pollution.
- The dedicated workflow now owns regression coverage for the new runtime files instead of relying only on broad CI.

## Blockers / risks

- Repository access is available, but there is no Discloud runtime/deployment connector in this conversation. Production logs and live deployment must therefore be verified externally after repository validation.
- Production acceptance still requires pressing the **existing June 11 Create Ticket panel** after the validated code is deployed; reposting the panel would not prove restart persistence for the old message.
- If production still times out after startup logs prove `persistent_view=True fallback_listener=True`, the remaining likely class is process-wide event-loop/gateway starvation. That requires live runtime timing/log evidence and must not be guessed into this patch.

## Backlog

- None added. Process-wide gateway/event-loop starvation is not included unless production acceptance proves the registered handler is still missing Discord's acknowledgement window.

## Next step

Wait for the exact final record-only head to pass the same PR workflows, re-check **0 behind `main`**, review the final PR diff for accidental/unrelated changes and conflict artifacts, update PR #190 with final evidence, and mark it ready. Production acceptance remains the final Definition-of-Done gate: deploy the validated change and click the existing June 11 panel without reposting it; the task is complete only when that click is acknowledged and opens the category flow.

---

Previous completed task record: DS-DESIGN-033 was completed and merged in PR #189 before this task began; its full record remains available in git history at base `b4550f255fe8e7d46944b141f33324a7ad01d255`.
