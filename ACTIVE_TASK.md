# ACTIVE TASK

## Active task / desired outcome

**P0-INTERACTION-RUNTIME-001 — restore production-wide Discord button reliability across deploy/restart**

Restore **production-wide Discord component reliability** for Dank Shield as a
public, soon-to-be-paid bot.

Durable public panels must survive restart/application-identity drift. Private
ephemeral control sessions may expire for safety, but stale controls must never
fall through silently to Discord's red `This interaction failed` banner. Keep
one canonical business/mutation owner per feature and one shared lifecycle
runtime for stale-session recovery/diagnostics.

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

**VALIDATING — product-wide lifecycle repair implemented; #311 remains blocked from merge until one stable exact head passes the full Definition of Done**

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

The screenshot of the app-style `/dank home` panel proved a second root cause:
the panel was created at **2:58 AM** and observed failing at **3:17 AM**, while
`CompactDankHomeView` used a 900-second (15-minute) in-memory timeout. That is a
19-minute-old private session. Discord kept rendering the enabled-looking buttons
after discord.py had removed their ViewStore owner.

This failure predates PR #309's later merge/redeploy, so #309 did not cause that
specific dashboard outage.

A Discloud redeploy creates the same symptom for any non-persistent private view:
the message remains visible in Discord while the new process has no message-bound
ViewStore owner. The correct product behavior is not to make destructive/session
controls immortal; it is to recover a definitely orphaned private session safely
without replaying its stale action.

### Shared component lifecycle runtime

The pre-login shared interaction service is now the canonical
`install_component_interaction_runtime()`.

For each component interaction it mirrors discord.py 2.7.1's real ViewStore
ownership lookup:

1. exact message-ID owner;
2. global persistent owner registered under `None`;
3. dynamic-item pattern owner.

If any owner exists, the runtime does **not** handle the click. Native discord.py
dispatch remains authoritative.

If **no** ViewStore owner exists and the source message is private/ephemeral, the
runtime gives existing additive listeners a short grace window. If the click is
still unanswered and still has no ViewStore owner, it does **not replay the stale
button action**. It atomically replaces that same stale ephemeral message with a
fresh canonical Dank Shield Control Center and tells the user the old action was
not executed. discord.py stores the replacement View under the same message ID
as part of the edit response, so the repaired controls become live immediately
without spawning duplicate private panels.

That makes expired/restarted private menus self-healing without creating a second
business-logic implementation.

Non-ephemeral/public components are not generically replayed. Durable public
panels remain feature-owned by their persistent views/reconciliation logic.

The runtime also keeps bounded ingress/ACK observability. Components that have a
real owner but remain unanswered near Discord's acknowledgement deadline are
logged with custom ID, message/application identity, interaction age, and
persistent-view inventory.

Because the ownership check intentionally mirrors discord.py internals, recovery
is pinned to `discord.py==2.7.1`. Unknown library/ViewStore layouts fail closed:
recovery disables itself instead of stealing a potentially valid callback.

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

### Duplicate historical Basic Verify owner retired

The historical `member_lifecycle_verify_runtime_hardening.py` module still
contained a self-installing Basic Verify `on_interaction` fallback. The bulk
startup-guard registry is inert today, and no production direct import was found,
but importing that module in the future would have resurrected a second Basic
Verify interaction owner with different timing.

That duplicate fallback implementation and its install call are now removed.
The module retains its unrelated setup/schema/modlog compatibility work. The
standalone runtime ownership audit now asserts the duplicate listener remains
absent and points to the canonical `install_basic_verify_runtime` owner.

### Product-wide private-session lifecycle policy

`panel_lifecycle.py` now owns the normal private navigation session lifetime
(`PRIVATE_MENU_TTL_SECONDS = 15 minutes`) and stale recovery grace.

The main public command centers use that shared value instead of each defining
their own copy. The compact `/dank home` panel now also uses stable semantic
component IDs such as `dank:home:setup:v1`,
`dank:home:verification:v1`, and `dank:home:close:v1` so production logs and
future migrations can identify exact controls instead of random generated IDs.

The Control Center explains its private-session lifetime to users. Expired or
pre-redeploy private controls recover to a fresh Control Center automatically
when discord.py proves they have no owner.

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
- ViewStore ownership detection covers message-specific, global persistent, and
  dynamic-item routes;
- unowned ephemeral components recover to a fresh canonical Control Center;
- owned components are never taken over by stale recovery;
- existing additive interaction listeners receive a grace window before recovery;
- unowned public components are not generically replayed;
- unknown discord.py/ViewStore versions fail closed;
- component runtime registration is idempotent;
- still-unacknowledged owned components remain observable;

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
- no per-guild background private-menu watcher;
- stale private recovery runs only when a user actually clicks an orphaned
  component and only after ViewStore proves no native owner exists;
- recovery replaces the stale private message in place and immediately installs
  a fresh ViewStore owner instead of creating another panel;
- recovery never replays the stale feature mutation;
- component probes/logging are globally bounded;
- already-migrated panel identities require zero Discord REST at restart;
- legacy reconciliation is bounded and recovery-budget paced;
- replacement occurs only for a configured message that is clearly a Dank Shield
  ticket panel;
- transient fetch failure does not create duplicate panels.

### Validation-infrastructure defect exposed by this task

Touching Community Tools correctly activated its existing SQL/RLS workflow. That
gate exposed three malformed PostgreSQL anonymous blocks already present on
`main`: `do $ ... $;` instead of `do $ ... $;`. PostgreSQL failed before
testing the feature logic.

Because all workflow gates are mandatory for this P0, the branch repairs only
those dollar-quote delimiters in
`.github/workflows/smart-stickies-029.yml`. No Community Tools SQL/business
logic was changed.

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

After the production-wide interaction runtime closes:

- reduce startup activity-history recovery cost/noise and its multi-minute REST
  reconciliation footprint;
- live-acceptance check a fresh `/dank home`, an intentionally stale private
  menu, Basic Verify, Create Ticket, Verification Center, Ticket Center,
  Moderation Center, Protection Center, Server Stats, and Server Design after
  Discloud deploy.

## Next step

Run exact-head CI for the full component-lifecycle branch. Inspect shared
ViewStore recovery, acknowledgement boundaries, durable panel reconciliation,
root component IDs, and all changed public centers. Keep PR #311 unmerged until
compile, full pytest, standalone audits, all workflow gates, final diff/review
inspection, currentness, and mergeability are proven. Then merge the exact head
and use Discloud's redeploy plus the new component-runtime telemetry for live
acceptance. Do not start the startup-performance backlog until this P0 is closed.
