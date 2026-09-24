# ACTIVE TASK

## Active task / desired outcome

**P0-TICKET-PANEL-RECOVERY-001 — stop existing Create Ticket panels from timing out after deploy/restart**

Restore every known public Create Ticket button generation so an already-posted
panel can reach the one canonical ticket creation handler instead of Discord
showing **This interaction failed**.

## Status

**ROOT CAUSE COMPATIBILITY GAP CONFIRMED — repair implemented; exact-head validation pending**

Branch: `fix/ticket-panel-legacy-interaction-recovery-20260924`

Base: `main@353a43ddbf45e15fc93c9eb3e2cd19a3935dfd1f`

PR #309 owner-authority consolidation was validated and merged before this task
started.

## Production symptom

The owner reported that pressing **Create Ticket** on the live public ticket
panel returns Discord's red **This interaction failed** banner.

This is an interaction-routing failure shape: the current clean handler defers
immediately before database/config/category work, so an ordinary setup blocker
should produce a bot response rather than an unacknowledged Discord component.

## Execution path inspected

Current public owner:

`commands_ext/public_ticket_panel_clean.py`
→ `PublicCreateTicketPanelView`
→ `handle_public_ticket_panel_click()`
→ `_handle_panel_button()`
→ `_handle_panel_button_core()`

Restart safety:

`commands.py`
→ `ticket_panel_runtime.install_public_ticket_panel_runtime(..., strict=True)`
→ current persistent view + delayed `on_interaction` fallback
→ same canonical clean handler

Legacy `ticket_create` panels also have a compatibility view in
`tickets_new/panel.py` that delegates to the same canonical clean handler.

## Root cause / compatibility gap

Dank Shield has emitted **three** durable public Create Ticket custom IDs over
the lifetime of the current codebase:

- current clean owner: `sv:ticket:panel:create:clean:v1`;
- earlier TicketTool parity panel: `sv:ticket:panel:create:v6`;
- older legacy panel: `ticket_create`.

The restart-safe fallback runtime only recognized the current
`PANEL_BUTTON_CUSTOM_IDS` set, and that set contained **only**
`sv:ticket:panel:create:clean:v1`.

The parity `v6` panel was actively shipped from April through the later
TicketTool parity work, but its module is no longer a public runtime registrar.
An already-posted `v6` message can therefore remain visually valid while no
current persistent view owns its custom ID. The global ticket fallback ignored
that ID too, which produces exactly the observed no-acknowledgement failure.

The exact live panel custom ID has not been observed in logs yet, so the report
does not prove the affected message is specifically `v6`. The code defect is
nevertheless real and covers every known historical public ticket panel
generation.

## Secondary registration defect

`public_ticket_panel_clean.register_public_ticket_panel_clean()` previously did:

1. successfully register the persistent view;
2. **not** call `add_listener()`;
3. set `_PANEL_FALLBACK_LISTENER_REGISTERED = True` anyway.

Normal production startup currently installs `ticket_panel_runtime` first, so
that false bookkeeping is not the only explanation for the live failure.
However, it makes alternate/reordered registration paths lie about whether a
fallback route actually exists and can cause runtime reconciliation to make the
wrong ownership decision.

## Repair

### Canonical historical-ID support

`public_ticket_panel_clean.py` now defines the current ID plus a fixed known
legacy set:

- `sv:ticket:panel:create:clean:v1`;
- `sv:ticket:panel:create:v6`;
- `ticket_create`.

All are accepted only as **entry aliases**. Every click still delegates to the
same canonical clean handler and ticket/category implementation.

No old ticket creation business logic is revived.

### Truthful fallback registration

The clean registrar now registers its delayed fallback whenever that fallback
has not actually been registered, regardless of whether the persistent view
succeeded.

It no longer sets the fallback flag merely because a view exists.

### Runtime reconciliation

`ticket_panel_runtime.py` now treats view and fallback registration flags
independently. If an alternate entrypoint registered both real routes first, the
runtime adopts both instead of attaching a duplicate listener.

Ticket trace output now includes the observed `custom_id`, making any future
historical/stale component immediately attributable from Discloud logs.

## Interaction safety

The delayed listener still:

- waits 150 ms for discord.py's native view dispatch;
- exits when the interaction is already acknowledged;
- delegates only still-unanswered known ticket panel IDs;
- uses the existing per-interaction lock/TTL so two routes cannot create two
  ticket menus/tickets from one Discord interaction.

Temporary category-select recovery remains unchanged.

## Tests / audits updated

Behavioral coverage now proves:

- the clean registrar records a fallback only after actually calling
  `add_listener()`;
- registrar-first then runtime installation does not duplicate views/listeners;
- every known historical public Create Ticket custom ID reaches the canonical
  handler through restart fallback;
- the current clean ID remains supported;
- interaction duplicate suppression and category-select recovery remain owned by
  the existing canonical code.

The DS Backlog 027 static audit now requires the truthful fallback condition and
historical-ID compatibility marker instead of the obsolete fallback-only-on-view
failure contract.

## Scope / compatibility

Preserved:

- current panel custom ID;
- old panel messages;
- canonical category picker;
- confirmation flow;
- ticket numbering;
- setup preflight;
- claim-first security;
- category privacy/permissions;
- verification-ticket special handling;
- one canonical ticket mutation path.

Not changed:

- ticket channel creation semantics;
- ticket database schema;
- staff permissions;
- Basic Verify;
- owner authority;
- startup activity recovery.

## Validation required

- exact diff/currentness inspection;
- conflict-marker/whitespace/debug inspection;
- Python compile;
- ticket native restart tests;
- public ticket single-owner tests;
- persistent interaction compatibility tests;
- claim-first ticket security;
- DS Backlog 027 audit;
- full `pytest tests/`;
- all repository workflow gates;
- final review-thread/mergeability inspection.

## Backlog

After ticket interaction reliability closes:

- reduce startup activity-history reconciliation cost/noise without weakening
  continuity guarantees.

## Next step

Open an isolated draft PR and run the complete exact-head validation suite. Fix
only failures caused by this ticket interaction repair, then make the PR
merge-ready once every gate and final inspection is green.
