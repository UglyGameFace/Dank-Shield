# ACTIVE TASK

## Active task / desired outcome

**P0-BASIC-VERIFY-PANEL-002 — make old Basic Verify panels self-heal after deploy/restart**

Fix the production case where an already-posted Basic Verify panel still renders
but its green Verify button can return Discord's red **This interaction failed**
banner after runtime changes/restarts.

## Status

**ROOT CAUSE GAP IDENTIFIED — canonical reconciliation implemented; validation pending**

Branch: `fix/basic-verify-panel-reconciliation-20260924`

Base: `main@1321af896acff5bd33d8823c5b5ccac8239836a6`

## Evidence

The live panel shown by the owner was posted on 2026-08-20 and still carries
the expected Basic Verify footer.

PR #306 restored the delayed `on_interaction` safety route and is deployed.
PR #307 separately fixed false **Staff only** denial for the actual guild owner.

The supplied runtime log excerpt shows the bot process is healthy while startup
activity reconciliation is still running and being paced by the recovery REST
budget. It contains no Basic Verify click/ack/fallback lines, so it does not
show a handler-side exception for the green button.

The recovery budget is not wrapping `InteractionResponse.defer()`; it only
paces callers that explicitly reserve startup-recovery REST capacity. Therefore
those activity pacing warnings are not themselves proof of the Verify failure.

## Concrete ownership gap

Dank Shield already had
`startup_guards/basic_verify_panel_auto_refresh_guard.py`, whose stated purpose
is to refresh stale Basic Verify panels on startup.

That guard is now historical/dormant. `startup_guards/__init__.py` explicitly
states that its registry is inert and nothing iterates it in production.
The auto-refresh guard is not explicitly imported by the live app.

Result: the canonical Basic Verify runtime registers a global persistent view and
fallback listener, but it does **not** reconcile the actual message identity of
an old production panel after restart.

That leaves two cases unowned:

1. a current-bot legacy panel has never had its exact Discord message ID persisted
   and rebound to the current process;
2. a visually identical legacy panel was authored by an older Discord application
   identity. The current application cannot receive that component interaction,
   even though the message still says Dank Shield.

## Repair

### Canonical runtime owns reconciliation

`stoney_verify/verification_new/basic_verify.py` now owns restart reconciliation.
No dormant startup guard is reactivated and no second verification policy owner
is introduced.

### Persist exact panel identity

Whenever `post_basic_verify_panel()` updates or posts a panel it now persists:

`basic_verify_panel_message_id`

through canonical `guild_config.upsert_guild_config()`.

The same message is registered with:

`bot.add_view(BasicVerifyView(), message_id=<exact message id>)`

in addition to the global persistent view.

### Restart behavior

The native Basic Verify runtime now registers one `on_ready` reconciliation
listener.

After Discord is ready it:

- reads current-process guild panel metadata from Supabase in bounded 100-guild
  batches;
- binds persisted panel IDs without Discord history REST;
- performs a one-time bounded legacy backfill only for guilds that do not yet
  have a stored panel ID;
- uses the existing shared recovery REST budget before a legacy history scan;
- updates a current-bot legacy panel and stores/binds its exact message ID;
- if the only matching panel belongs to another application identity, posts a
  fresh current-bot panel instead of preserving a permanently dead button;
- if Basic Verify is disabled, it may bind a current-bot legacy panel so clicks
  can return the canonical disabled reason rather than time out;
- never posts a replacement when the startup history scan itself failed, avoiding
  duplicate panels caused by a transient Discord error.

Default legacy migration is capped at 50 guilds per process start and is
configurable with:

`DANK_BASIC_VERIFY_LEGACY_PANEL_BACKFILL_PER_START`

New and subsequently repaired panels no longer need history scanning.

### Interaction ownership remains single-path

Still canonical:

`persistent BasicVerifyView`
→ `maybe_handle_basic_verify_interaction`
→ `_ack`
→ `apply_basic_verification`

Safety path:

`on_interaction`
→ 150 ms grace
→ same canonical handler only if still unanswered

Not restored:

- old compatibility verification wrapper;
- duplicate role/config mutation callback;
- dormant bulk startup-guard loading.

## Tests added/updated

`tests/test_basic_verify_native_restart_runtime.py` now covers:

- persistent view + delayed safety listener + ready reconciler registration;
- idempotent registration;
- partial route recovery;
- exact persisted message-ID binding with no channel history scan;
- legacy current-bot panel update/persist/bind;
- foreign-application legacy panel replacement with a fresh current-bot panel;
- persistent callback winning the delayed fallback race;
- fallback takeover on missed view dispatch;
- acknowledgement before database/role mutation;
- duplicate role mutation prevention.

## Scale / compatibility

- no per-guild schema migration;
- panel message ID lives in existing guild settings;
- no all-channel scan;
- no all-message scan;
- only explicitly configured verification channels are inspected;
- history migration is bounded and recovery-budget paced;
- existing custom ID `dank:basic_verify:v1` remains unchanged;
- already-posted current-bot panels are edited in place when found.

## Validation required

- Python compile;
- focused Basic Verify restart/reconciliation tests;
- public Verify Panel tests;
- verification-mode policy tests;
- persistent interaction compatibility;
- full `pytest tests/`;
- standalone repository audits;
- GitHub workflow gates;
- final diff/currentness/review-thread inspection.

## Backlog

**Next P0 after this task:** live ticket creation/panel failure reported in
production. Do not mix ticket changes into this Basic Verify PR. Reproduce the
exact public ticket entry path against current main, trace persistent-panel
ownership, acknowledgement timing, setup/config lookup, channel/category
creation, permission overwrites, and post-create handoff before changing code.

The startup activity reconciliation log is extremely noisy and can take minutes
across channel/thread history. Its requests are being paced correctly, but its
scope/cost deserves a separate performance task after verification reliability
is closed.

## Next step

Open the isolated draft PR, run exact-head CI, repair only failures caused by
this reconciliation work, and merge only after the complete repository gates
pass.
