# ACTIVE TASK

## DS-TICKET-036 — Repair ticket creation after category selection

**Status:** IMPLEMENTED / EXACT-HEAD VALIDATION PENDING
**Branch:** `fix/ticket-confirm-create-runtime-196`
**Base:** `b356440c5a1b8b345580a079ab41d947bab6db01` (`main`, squash merge of PR #195)
**Started:** 2026-09-10

## User-visible failure

The public **Create Ticket** button works and the private **Choose a ticket type** menu loads the configured choices (for example Appeal, Report a Member, and Support), but the flow can fail after category selection / Confirm so no ticket channel is created and the member receives no useful completion or failure response.

## Authoritative execution path

`PublicCreateTicketPanelView.create_ticket()` → `_handle_panel_button()` → `_handle_panel_button_core()` → `TicketSelect.callback()` → `TicketConfirmView.confirm()` → optional `DashboardTicketFormModal` → `_create_ticket()` → persistent ticket-number allocator → `_create_synced_ticket_channel()` → ticket DB row/opening message.

`stoney_verify/commands_ext/public_ticket_panel_clean.py` remains the single native owner for the public ticket panel flow. Do not restore retired runtime callback-rewrite guards.

## Root causes proven from code

### 1. Confirm could miss Discord's interaction acknowledgement window

`TicketConfirmView.confirm()` performed `_ticket_setup_preflight()` before acknowledging the component interaction. That preflight can refresh guild config and perform several awaited lookups with timeouts of up to 6s/6s/4s. A slow persistence/config response can therefore outlive Discord's component response window and make Confirm appear dead.

The slow Confirm preflight was also redundant for creation safety: the initial Create Ticket path already preflights before showing the chooser, and `_create_ticket()` performs the authoritative active-category, staff-role, permissions, duplicate-open-ticket, numbering, and channel-create checks again at creation time.

For form-enabled categories, the slow preflight was especially harmful because Discord requires the modal to be sent as the immediate interaction response; the modal submission later calls the same `_create_ticket()` safety path on a fresh interaction.

### 2. Persistent ticket-number failure could escape without a member-facing response

`_create_ticket()` called `_next_number()` outside its exception handling. The allocator deliberately fails closed if Supabase/counter reservation cannot guarantee a unique never-reused ticket number. That invariant is correct, but an allocator exception could bubble out before channel creation and leave the member with no useful error.

## Implemented repair

- Removed the duplicate slow `_ticket_setup_preflight()` call from `TicketConfirmView.confirm()`.
- Direct Confirm now consumes the current menu session, disables the view, and acknowledges the component immediately with **Opening your ticket…** before entering `_create_ticket()`.
- Form-enabled Confirm opens the modal immediately without slow setup I/O first; modal submission still enters `_create_ticket()` and all authoritative safety checks.
- `_create_ticket()` still revalidates active category, staff role, category permissions/privacy shape, and an existing open ticket before allocation/channel creation.
- Persistent numbering remains mandatory and fail-closed; no Discord-channel-derived fallback number was introduced.
- Ticket-number allocation exceptions are now caught and returned to the member as **Could not reserve a safe ticket number ... Nothing was created**.
- Channel creation still cannot run unless a persistent number was successfully reserved.
- Added compact production telemetry for ticket-type selection, Confirm mode, successful number reservation, and number-allocation failures.

## Safety invariants

- One native public ticket-panel owner remains.
- Newest-menu/session ownership and per-member Confirm locking remain intact.
- Duplicate ticket detection remains in `_create_ticket()`.
- Ticket numbers remain persistent per guild and are never intentionally recycled from Discord channel state.
- Active Tickets category privacy/staff/bot permission validation remains authoritative at creation time.
- Requester permissions are applied only after the channel is created; failed requester permission setup still removes the partial channel.
- Optional intake forms remain supported and submit into the same creation path.
- Existing channel-create `discord.Forbidden` and generic exception reporting remain unchanged.
- Invite reconciliation/hardening files from PRs #194/#195 are untouched by this task.

## Regression coverage

`tests/test_ticket_confirm_create_runtime_196.py` covers:

- direct Confirm acknowledges Discord before ticket creation and does not run slow setup preflight first;
- form Confirm opens the modal before any slow setup preflight and does not create until modal submission;
- persistent counter allocation failure creates no channel and produces a member-visible error.

Existing ownership/restart regressions are also required:

- `tests/test_public_ticket_panel_single_owner.py`
- `tests/test_ticket_panel_native_restart_runtime.py`

## Validation required before merge

- focused DS-TICKET-036 tests;
- existing public ticket-panel single-owner tests;
- ticket native restart/persistence tests;
- persistent ticket-counter regressions;
- ticket category/menu/doctor audits;
- Python compile check and committed diff whitespace check;
- full repository pytest suite;
- every relevant PR workflow green on one exact head;
- final base-drift, changed-file scope, diff, conflict-marker, and review-thread check.

## Production acceptance after deploy

Require a clean end-to-end run from the existing public panel:

1. **Create Ticket** opens the chooser.
2. Choosing Appeal / Report a Member / Support immediately transitions to the Confirm screen and emits `ticket type selected` telemetry.
3. Direct **Confirm** immediately acknowledges with **Opening your ticket…** and emits `ticket confirm acknowledged ... mode=direct`.
4. A successful allocation emits `ticket number reserved ... number=...` and creates exactly one `ticket-####` channel under the configured Active Tickets category.
5. The requester can see/use the new channel and the opening message/actions appear.
6. Form-enabled categories open their modal immediately; submitting the modal creates through the same safe path.
7. Repeated/duplicate Confirm clicks do not create duplicate channels.
8. If persistent numbering is unavailable, the member receives the explicit safe-number failure and no channel is created.
9. No Discord `Unknown interaction` / expired-interaction failure occurs during the normal Confirm path.

## Suspended / backlog

- **DS-INVITE-035 final hardening production acceptance:** PR #195 is merged and post-merge CI is green; corrected RSS/invite hardening telemetry still needs observation after the Discloud build picks up that merge.
- **DS-TICKET-034 production acceptance:** historical Ticket Choices restore/cross-guild live acceptance remains separate from this Confirm runtime repair.
- **Join-context Supabase schema mismatch:** production previously reported missing `entry_confidence` in `guild_members` and `member_joins`.
- **Generic memory optimization:** judge actual retained memory using corrected `rss_current`, not the old peak-only reading.
- **Server Design setup regression/full audit:** remains backlog.

## Next step

Open PR #196, freeze one exact code-and-record head, run the focused ticket gates plus full repository CI, review the final scoped diff, and squash-merge only after that exact head is green. Production acceptance follows on Discloud.
