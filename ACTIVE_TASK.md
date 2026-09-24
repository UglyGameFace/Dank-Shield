# ACTIVE TASK

## Active task / desired outcome

**P0-TICKET-PANEL-001 — restore Create Ticket interaction reliability across deploy/restart**

Fix the live public **Create Ticket** panel so an already-posted panel cannot
silently lose its interaction route after bot restarts, application identity
changes, or command-registration refactors.

## Production symptom

Members press **Create Ticket** and Discord returns:

`This interaction failed`

The failure appears alongside the recent Basic Verify/persistent-panel incidents,
but ticket creation has its own runtime and must be repaired independently.

## Status

**IMPLEMENTED — PR #310 compatibility folded into #311; exact-head validation rerun pending**

Branch: `fix/ticket-panel-restart-reconciliation-20260924`

Base: `main@353a43ddbf45e15fc93c9eb3e2cd19a3935dfd1f`

## Previous task closed

PR #309 — canonical guild-owner authority consolidation — merged to `main` as
`353a43ddbf45e15fc93c9eb3e2cd19a3935dfd1f`.

Its final exact head passed all repository CI/workflow gates before merge.

## Execution path

Production command bootstrap:

`commands.py`
→ `install_public_ticket_panel_runtime(bot, strict=True)`
→ persistent `PublicCreateTicketPanelView`
→ delayed `on_interaction` fallback
→ `public_ticket_panel_clean.handle_public_ticket_panel_click()`
→ one canonical panel-button handler
→ category picker
→ ticket creation path

The native runtime is installed before the general command registrar, so its
existing delayed fallback is normally real in production.

## Root-cause findings

Two concrete lifecycle defects remained.

### 1. Saved panel identity was never used by the runtime

`public_ticket_panel_clean._post_panel()` already persisted:

- `ticket_panel_channel_id`
- `ticket_panel_message_id`

But `ticket_panel_runtime.py` never read those values after restart and never
bound the persistent view to the exact saved Discord message.

The runtime therefore knew the custom ID globally but did not reconcile the
actual production panel message.

### 2. Application identity drift was not detectable

A Discord component is routed to the application that authored it. A visually
identical old Dank Shield panel can therefore remain clickable while no current
runtime handler can ever receive that click if the panel belongs to an older
application identity.

The saved config did not record which bot/application authored the panel, and
startup never fetched/reconciled legacy panel identity.

### 3. The clean registrar had false fallback bookkeeping

`register_public_ticket_panel_clean()` contained an independent registration
path. When its persistent view registered successfully it could set
`_PANEL_FALLBACK_LISTENER_REGISTERED = True` without calling
`bot.add_listener()`.

Normal production bootstrap installs `ticket_panel_runtime` first, so this was
not sufficient by itself to explain every live failure. It was still an invalid
secondary ownership path and could lie in alternate/test registration orders.

### 4. Historical public panel IDs were outside the current alias set

A separate open PR (#310) confirmed that Dank Shield has shipped three durable
public Create Ticket IDs:

- current: `sv:ticket:panel:create:clean:v1`;
- historical TicketTool-parity panel: `sv:ticket:panel:create:v6`;
- older legacy panel: `ticket_create`.

PR #311 originally reconciled exact message/application identity but recognized
only the current clean ID in its generic delayed fallback. The unique
compatibility work from #310 is now folded into this branch so old panels and
the newer reconciliation logic are one coherent runtime instead of two
diverging PRs.

## Repair

### Canonical runtime ownership only

`public_ticket_panel_clean.register_public_ticket_panel_clean()` now delegates
runtime registration to `ticket_panel_runtime.install_public_ticket_panel_runtime()`.

It no longer:

- independently calls `bot.add_view()`;
- independently installs a component fallback;
- sets the fallback-registered flag merely because the persistent view exists.

The duplicate clean-module fallback implementation was removed.

Historical Create Ticket IDs from PR #310 are now aliases into the same
canonical handler. No historical ticket-creation implementation is revived.
Runtime trace telemetry includes the observed component custom ID so future live
failures identify the exact panel generation immediately.

### Persist exact panel + application identity

Panel posting now persists through canonical guild-config ownership:

- `ticket_panel_channel_id`;
- `ticket_panel_message_id`;
- `ticket_panel_application_id`.

The write uses `explicit_override` with source `ticket_panel.identity` and no
longer routes panel identity updates through the setup-builder writer, so posting
or reconciling a panel does not invalidate completed setup state.

A newly posted panel is also immediately bound to its exact message ID through
the canonical ticket runtime.

### Restart reconciliation

The ticket runtime now registers one background `on_ready` reconciler.

It:

- discovers configured ticket-panel rows in Supabase batches of 100;
- binds panels with a saved current application ID without Discord REST;
- performs bounded legacy identity checks only when application identity is
  missing or mismatched;
- fetches the exact saved panel message when a message ID exists;
- can scan only the configured panel channel for a legacy row without a saved
  message ID;
- persists the current application identity once a current-bot legacy panel is
  confirmed;
- upgrades a current-bot legacy panel to the canonical Create Ticket view when
  its custom ID is stale;
- detects a configured ticket panel authored by another Discord application,
  posts a fresh current-application panel, rebinds it, and deletes the stale
  foreign panel only when the message is clearly a Dank Shield ticket panel and
  the current bot has Manage Messages;
- does not blindly recreate a panel when a saved message was deleted or a fetch
  fails.

Legacy Discord REST work is capped per process start by
`DANK_TICKET_PANEL_LEGACY_RECONCILE_PER_START` (default 50) and uses the shared
startup recovery REST budget.

## Single mutation owner preserved

The reconciliation/runtime layer does not create tickets.

Ticket business logic remains:

`handle_public_ticket_panel_click`
→ `_handle_panel_button`
→ `_handle_panel_button_core`
→ existing canonical category/confirm/create flow

The existing interaction-ID duplicate suppression, menu session ownership,
persistent ticket number allocation, permission preflight, and claim-first
security are unchanged.

## Tests updated/added

Focused coverage now verifies:

- persistent view + real delayed fallback + ready reconciler registration;
- idempotent registration;
- fallback-only degraded operation;
- strict failure when no interaction route can register;
- current-application saved panels bind exact message IDs without REST;
- legacy current-bot panels persist application identity and exact binding;
- foreign-application saved panels are replaced and rebound;
- category-select fallback behavior remains intact;
- current and historical public Create Ticket custom IDs all reach the same
  canonical handler through the delayed restart fallback;
- clean registrar delegates to the canonical runtime instead of maintaining a
  second fallback owner;
- panel-lifetime/static audits track the canonical view after fallback cleanup;
- DS-BACKLOG-027 static acceptance follows the new single-owner runtime contract.

## Scale / safety

- no schema migration;
- no all-channel scan;
- no all-message scan;
- already-migrated panel identities require zero Discord REST at restart;
- legacy reconciliation is bounded and recovery-budget paced;
- replacement occurs only for a configured message that is clearly a Dank Shield
  ticket panel;
- transient fetch failure does not create duplicate panels.

## Validation required

- exact diff/currentness inspection;
- Python compile;
- focused ticket-panel restart tests;
- single-owner/static audits;
- ticket category/select behavior tests;
- claim-first ticket security workflow;
- full `pytest tests/`;
- standalone repository audits;
- all GitHub workflow gates;
- final review-thread/mergeability inspection.

## Superseded PR cleanup

PR #310 is superseded by this branch. Its unique historical-ID compatibility and
traceability changes have been incorporated into PR #311 while #311 retains the
more complete exact-message/application-identity reconciliation and single
runtime owner.

## Backlog

After ticket interaction reliability closes:

- reduce startup activity-history recovery cost/noise and its multi-minute REST
  reconciliation footprint;
- re-check live Basic Verify and Create Ticket acceptance after Discloud deploy.

## Next step

Run exact-head CI after folding PR #310 compatibility into #311. If all
workflows remain green, perform final currentness/diff/review inspection, close
the superseded PR #310, and make PR #311 ready for merge.
