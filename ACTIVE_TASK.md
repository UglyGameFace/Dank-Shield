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

The investigation found two reconciliation gaps that can leave a visibly
clickable Basic Verify panel with no route to the current process.

### 1. Persisted component identity was not proven

Basic Verify restart reconciliation persisted and trusted only:

- `basic_verify_panel_message_id`;
- `basic_verify_panel_application_id`.

That is not enough for long-lived public panels. The exact component contract
must also be proven before a zero-REST message-bound View registration.

The current component contract is:

`dank:basic_verify:v1`

### 2. Legacy discovery ignored foreign application panels and stale channel IDs

When no usable persisted panel identity existed, the posting/discovery path
looked only at Basic Verify messages authored by the **current** bot application.
A panel authored by a previous Dank Shield application could therefore remain
visible while its clicks were routed somewhere other than the current process.

The reconciler also trusted only the persisted verify-channel ID. If that slot
was missing/stale, it returned `no_channel` even when a cached
`#verification` / `#verify` text channel clearly existed.

This matches the live boundary: fresh `/dank home` controls work, while the
old public Verify panel produces Discord's red failure and no canonical
`basic_verify click` evidence.

## Repair

Basic Verify panel identity now persists a third proof:

- `basic_verify_panel_component_id = dank:basic_verify:v1`.

The zero-REST exact-bind path is allowed only when all three are current:

1. exact persisted message ID;
2. exact current bot/application identity;
3. exact current Basic Verify component ID.

Existing rows without component proof, or rows with a retired component ID, use
one bounded reconciliation fetch.

The reconciler resolves the verify channel from persisted guild config first and
then falls back to the guild's cached `verification` / `verify` text channel
name. This fallback performs no Discord REST scan across the guild.

Basic Verify history discovery is still bounded to one verification channel and
80 messages. It now recognizes both current-application and foreign-application
Basic Verify-marked messages.

For a current-bot Basic Verify message:

- if the actual message already contains `dank:basic_verify:v1`, persist the
  component proof and bind it;
- if the embed/message is Basic Verify but the actual component ID is old or
  missing, edit that exact message **in place** with the current embed +
  `BasicVerifyView`, persist the new component proof, and bind it;
- if the edit fails, report `component_repair_failed` and do not falsely record
  the panel as healthy.

For a foreign-application Basic Verify message:

- first confirm a current-application replacement exists or was posted;
- then remove the stale foreign panel when Discord permissions allow;
- never delete the stale panel before the replacement is confirmed;
- keep the scan bounded to the verification channel.

## Safety invariants

- no duplicate Verify panel is created merely because the component version is old;
- the existing message is repaired in place when owned by the current bot;
- no role mutation occurs during startup reconciliation;
- Verify clicks still acknowledge before DB/role work;
- the persistent view and delayed fallback still delegate to one canonical
  `maybe_handle_basic_verify_interaction` business path;
- startup REST remains bounded for legacy/unproven rows;
- no all-channel or all-message reconciliation is introduced;
- a missing saved verify-channel ID can recover from the guild's already-cached
  channel list without REST.

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
