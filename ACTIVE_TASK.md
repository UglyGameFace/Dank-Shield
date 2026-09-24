# ACTIVE TASK

## Active task / desired outcome

**P0-BASIC-VERIFY-COMPONENT-003 — repair legacy Basic Verify buttons that remain visibly clickable but cannot dispatch**

Fresh `/dank home` controls now work on the deployed #312 runtime, while the
existing public **Verify** button still returns Discord's red
`Interaction failed` banner.

This isolates the remaining live failure to Basic Verify panel compatibility.

## Single active task lock

Only this Basic Verify component-identity repair is active.

Do not reopen the completed global component-runtime or authority work unless
new evidence points back to it.

## Production proof

PR #312 is merged and Discloud reports the merge deployed successfully.

Live startup proves:

- `profile_interaction runtime ready persistent_view=True listener=True`;
- `ticket_panel_runtime ready persistent_view=True fallback_listener=True panel_reconciler=True`;
- `basic_verify runtime ready owner=persistent_view delayed_fallback=True panel_reconciler=True`;
- `interaction_handlers: registered component interaction handler`;
- `component_runtime ready release=src:7e2f3278f466512a`;
- persistent ViewStore inventory includes `BasicVerifyView`;
- a fresh `/dank home` button works.

Therefore this is no longer a generic ViewStore/startup-registration failure.

## Root cause

Basic Verify restart reconciliation persisted and trusted:

- `basic_verify_panel_message_id`;
- `basic_verify_panel_application_id`.

That was insufficient.

An older Basic Verify message can still belong to the current Discord application
while carrying a retired button `custom_id`. The startup fast path saw the
current application identity and called:

`bot.add_view(BasicVerifyView(), message_id=<old message>)`

without proving that the visible button on that message used today's component
contract:

`dank:basic_verify:v1`

discord.py dispatches views by component type + `custom_id` (and message/global
scope). Binding today's View to an old message ID cannot make a retired custom ID
magically match. The panel therefore looked healthy at startup but clicks still
fell through to Discord's red failure banner.

## Repair

Basic Verify panel identity now persists a third proof:

- `basic_verify_panel_component_id = dank:basic_verify:v1`.

The zero-REST exact-bind path is allowed only when all three are current:

1. exact persisted message ID;
2. exact current bot/application identity;
3. exact current Basic Verify component ID.

Existing rows without component proof, or rows with a retired component ID, use
one bounded reconciliation fetch.

For a current-bot Basic Verify message:

- if the actual message already contains `dank:basic_verify:v1`, persist the
  component proof and bind it;
- if the embed/message is Basic Verify but the actual component ID is old or
  missing, edit that exact message **in place** with the current embed +
  `BasicVerifyView`, persist the new component proof, and bind it;
- if the edit fails, report `component_repair_failed` and do not falsely record
  the panel as healthy.

Foreign-application replacement behavior remains unchanged.

## Safety invariants

- no duplicate Verify panel is created merely because the component version is old;
- the existing message is repaired in place when owned by the current bot;
- no role mutation occurs during startup reconciliation;
- Verify clicks still acknowledge before DB/role work;
- the persistent view and delayed fallback still delegate to one canonical
  `maybe_handle_basic_verify_interaction` business path;
- startup REST remains bounded for legacy/unproven rows.

## Validation required

- focused Basic Verify restart/runtime tests;
- component lifecycle architecture tests;
- Python compile;
- full `pytest tests/`;
- all GitHub workflow gates;
- currentness / mergeability / review / diff hygiene;
- after deployment, confirm startup reports
  `repaired_component` for the affected legacy panel or otherwise shows the
  exact reconciliation result;
- live click on the repaired public Verify button must emit
  `✅ basic_verify click ...` and acknowledge before role work.

## Status

**IN PROGRESS — component identity repair implemented; exact-head validation pending**

Branch: `fix/basic-verify-component-identity-20260924`

Base: current `main` after PR #312.

## Next step

Open the focused PR, run exact-head CI, repair only failures causally related to
this Basic Verify compatibility defect, and merge only when all required gates
are green.
