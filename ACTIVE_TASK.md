# ACTIVE TASK

## Active task / desired outcome

**P0-INTERACTION-RUNTIME-001 — restore production-wide Discord button reliability across deploy/restart**

Fix the live public **Basic Verify** and **Create Ticket** panels so already-posted
components cannot silently lose their interaction route after bot restarts,
application identity changes, or command-registration refactors. Keep one
canonical business/mutation owner per feature.

## Production symptom

### Latest escalation

Production now reports that **all buttons appear nonfunctional**, not only Basic
Verify and Create Ticket. PR #311 is still unmerged, so the new #311 branch
changes are not the source of the live outage.

This broadens the same active interaction-reliability task to the shared Discord
component runtime. Feature-specific redesign remains out of scope.

Production has two confirmed durable-panel failures:

- the green **Verify** button returns Discord's red `This interaction failed`;
- **Create Ticket** returns the same red interaction failure.

The matching symptom is significant because neither canonical handler can ack a
component interaction that Discord routes to a different application identity.

## Status

**IN PROGRESS — production-wide button outage reported; #311 intentionally blocked from merge pending shared-runtime proof and exact-head validation**

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

### Shared native interaction acknowledgement defect

`interaction_guard.run_guarded_interaction(..., defer=True)` called the shared
defer helper but ignored its boolean result. If Discord rejected an unanswered
interaction acknowledgement, the guarded action still executed DB/config/role or
channel work. This violates the repository's claim-first contract and became more
important as many public surfaces were migrated onto the native guard.

The repair now treats an already-completed response as a valid prior canonical
acknowledgement, but a real defer failure on an unanswered interaction stops the
action before mutation and returns the exact defer-failure record for that same
interaction.

### Production-wide dispatch findings

Repository inspection has ruled out several global-dispatch theories:

- current `main` does not clear discord.py's ViewStore;
- there are no `remove_view(...)` calls;
- the retired private `View._scheduled_task`/global interaction monkey patch is
  not live;
- Spam Guard's interaction-flood runtime does not intercept component clicks;
- the startup recovery REST limiter uses `asyncio.sleep`, not blocking sleep;
- the activity tracker is an additive `on_interaction` listener and performs its
  persistence through `asyncio.to_thread`;
- PR #304 removed only the duplicate Basic Verify compatibility dispatcher, not
  a generic all-button dispatcher.

A fresh `/dank home` **Close** callback is a clean runtime canary: it performs a
single `interaction.response.edit_message(...)` with no DB, role, ticket,
verification, or native-guard dependency. If that fresh button is unacknowledged,
the fault is below feature business logic.

Many `/dank` menu views have a 900-second in-memory lifetime and are intentionally
not persistent. A Discloud redeploy invalidates panels opened before that restart;
that expected stale-menu behavior must not be confused with fresh-panel failure.

### Passive component ingress/ACK observability

The shared interaction service now installs one passive observer before Discord
login. It never acknowledges, dispatches, retries, or mutates a feature. For a
component that reaches the current process but remains unanswered after 2 seconds,
it records bounded diagnostics containing custom ID, interaction/message IDs,
message author ID, current bot ID, interaction application ID, interaction age,
and the current persistent-view count/types. Startup also logs the current bot
identity and persistent-view snapshot once.

This discriminates the two remaining system classes without adding another
fallback owner:

1. no observer event for a reproduced click => Discord did not deliver that
   component to this Gateway process (foreign/stale application ownership or
   external interaction-delivery configuration must be checked);
2. observer records `component_unacknowledged` => the click reached Dank Shield
   but native ViewStore/callback/ack handling did not claim it.

The observer is globally bounded to avoid log storms on a large public bot.

### Shared lifecycle defect: persisted message ID was treated as ownership proof

PR #308 correctly added exact message-ID persistence/binding for Basic Verify, but
its fast path stored only `basic_verify_panel_message_id`. Once that ID existed,
startup called `bot.add_view(..., message_id=...)` without proving the saved
message was authored by the currently running Discord application.

That means a foreign-application Basic Verify message could be persisted and then
faithfully rebound forever even though its click can never arrive at the current
bot. The existing persistent view and delayed fallback are powerless in that
case because there is no gateway interaction to acknowledge.

The #308 regression test explicitly codified the zero-REST message-ID-only fast
path, so this was a real architectural gap rather than missing registration.

Ticket #311 independently identified the same ownership problem for Create
Ticket. The active task therefore covers both durable public panels under one
root-cause class instead of shipping separate bandages.

The ticket path also had these concrete lifecycle defects:

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

### 5. Ticket acknowledgement failure was not a hard stop

The canonical Create Ticket path called `_defer()` before expensive lookup and
creation work, but `_defer()` swallowed exceptions and returned no claim state.
An otherwise-unanswered interaction could therefore fail its acknowledgement and
still continue into DB/category/channel work.

The direct Confirm path is intentionally different: it edits the menu first, so
its interaction is already acknowledged before `_create_ticket()` runs. The
repair therefore treats an already-completed response as a valid claim, while a
real defer failure on an unanswered interaction returns false and stops before
mutation.

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

Basic Verify now persists both:

- `basic_verify_panel_message_id`;
- `basic_verify_panel_application_id`.

Its restart reconciler only takes the zero-REST exact-bind path when the saved
application identity matches the current bot. Legacy persisted IDs without an
application ID are bounded by the existing startup recovery cap and fetch only
the exact saved message. Current-app messages are migrated by persisting the
application identity; foreign-app messages are replaced by a current-app panel,
and the stale foreign panel is deleted only when it is clearly a Basic Verify
panel and Manage Messages is available.

Create Ticket panel posting now persists through canonical guild-config ownership:

- `ticket_panel_channel_id`;
- `ticket_panel_message_id`;
- `ticket_panel_application_id`.

The write uses `explicit_override` with source `ticket_panel.identity` and no
longer routes panel identity updates through the setup-builder writer, so posting
or reconciling a panel does not invalidate completed setup state.

A newly posted panel is also immediately bound to its exact message ID through
the canonical ticket runtime.

### Ticket acknowledgement claim boundary

`public_ticket_panel_clean._defer()` now returns a real acknowledgement result.
Both the initial Create Ticket button handler and ticket-creation path stop before
expensive lookup or mutation when an unanswered interaction cannot be deferred.
An interaction already acknowledged by the canonical Confirm edit remains valid,
so the existing direct-confirm flow is preserved.

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

- shared native guard stops before action when an unanswered defer fails;
- already-acknowledged canonical flows remain valid through the shared guard;
- passive component observer records only still-unacknowledged component clicks;
- component observer registration is idempotent;

- failed ticket acknowledgement stops before lookup/mutation;
- already-acknowledged direct Confirm remains valid;
- foreign Basic Verify replacement posts successfully before stale-panel deletion;
- failed foreign Basic Verify replacement preserves the stale panel;

- Basic Verify saved current-application panels bind with zero REST;
- Basic Verify legacy saved IDs fetch the exact message and persist application identity;
- Basic Verify foreign-application saved panels are replaced;
- Basic Verify unknown-identity recovery respects the bounded startup REST cap;
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

After persistent-panel interaction reliability closes:

- reduce startup activity-history recovery cost/noise and its multi-minute REST
  reconciliation footprint;
- re-check live Basic Verify and Create Ticket acceptance after Discloud deploy.

## Next step

Run exact-head CI for the shared-runtime branch, inspect all changed callers for
pre-ack/modal compatibility, and keep PR #311 unmerged until the final diff and
review gates pass. After deploy, the new component-runtime lines provide the
production proof needed to distinguish Gateway/application ownership from a
current-process acknowledgement miss. Do not start the startup-performance
backlog until this P0 is closed.
