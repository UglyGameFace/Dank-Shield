# Active Task

## Active task / desired outcome

**P0-OWNER-AUTHORITY-VERIFY-INTERACTION-011 — restore guild-owner access to staff management surfaces and make every stale Basic Verify panel eventually self-heal**

Desired outcome: a real Discord guild owner must never be rejected as
`❌ Staff only.` because an interaction user object lacks cached member/guild
shape, and every durable Basic Verify panel must have a live current-application
component owner after deploy/restart instead of remaining permanently deferred.

## Scope / single active task lock

Only the production interaction failures reported on 2026-09-25 are active:

- preserve the privacy boundary for ordinary non-staff users;
- resolve guild owner / Administrator authority from the full interaction before
  falling back to member-shaped staff-role checks;
- use one canonical interaction-aware ticket/staff authority helper across the
  affected management gates;
- keep each feature's native second-stage permission requirement unchanged;
- preserve Basic Verify's one canonical acknowledgement + role-mutation path;
- preserve the existing `dank:basic_verify:v1` public component contract;
- keep migrated current-application panels on the zero-REST exact-bind path;
- repair every legacy/unproven Verify panel eventually, using the existing shared
  recovery REST budget and discord.py's route-aware limiter;
- never turn a startup migration batch size into a permanent skipped-guild cap;
- cover owner-with-partial-user-shape and multi-wave legacy recovery regressions.

Do not broaden into AntiNuke, tickets redesign, Server Stats UX, moderator trust,
or unrelated cleanup.

## Prior task closure

PR #324, **Prevent AntiNuke self-ejection on delayed bot-authored PATCH**, was
merged into `main` as `e642323db703d9045c3f60f9f523e8eae3eacf68` on
2026-09-25.

Its exact PR head `4226c4824f7727cd8927a44169030b38247fc9e8` passed:

- full repository suite: **2026 passed, 13 warnings**;
- Dank Shield CI;
- Profile Runtime Diagnostics;
- Dank Design Regression CI;
- Ticket Owner Emergency Override;
- Application Command Size Diagnostics;
- managed-category SQL and claim-first ticket security;
- repository audits.

This task branches from that merged main and does not modify AntiNuke.

## Discord documentation findings

Checked current official Discord Developer Documentation on 2026-09-25.

- Interaction payloads carry guild/member context and resolved permissions.
  Owner/admin decisions should therefore use the interaction's guild and
  permission evidence instead of requiring a fully cached Member-shaped user.
- Guilds expose `owner_id`; Discord's permission computation grants the guild
  owner all permissions before normal role/channel permission evaluation.
- Message buttons use a developer-defined `custom_id`, which is returned in the
  component interaction payload and identifies the callback contract.
- An interaction must receive its initial response within **3 seconds** or the
  interaction token is invalidated.
- Discord rate limits are dynamic and route-specific; applications should honor
  Discord/discord.py pacing rather than hardcode guessed route limits.

## Findings / root cause

### Owner incorrectly receives `Staff only`

PR #323 added a staff-privacy boundary to multiple management surfaces, but
several interaction handlers called:

`scoped_is_ticket_staff(interaction.user)`

before using the already-existing interaction-aware owner/permission helpers.

That member-only helper can fail closed when the interaction user does not expose
the guild/member shape expected by the helper, even though
`interaction.guild.owner_id == interaction.user.id`. The repository already
has the correct authoritative owner path in
`public_owner_authority.interaction_is_actual_guild_owner()` and resolved
Administrator handling.

### Old Basic Verify button can remain dead indefinitely

The canonical Basic Verify handler already acknowledges before DB/role work, so a
red Discord `This interaction failed` on an old visible panel points to the
click never reaching a live callback/acknowledgement owner.

The startup reconciler correctly knows how to:

- exact-bind a proven current-app/current-component message with zero REST;
- fetch and migrate an unproven saved message;
- repair an old custom ID in place;
- replace a confirmed foreign-application panel;
- repair disabled legacy panels so they answer with policy instead of timing out.

However, startup legacy recovery was capped to the first 50 guilds by default.
After that cap, rows received `allow_legacy_rest=False` and returned
`legacy_deferred`. Reconciliation was one-shot for the process, so those guilds
were never retried. A visually valid old panel could therefore remain permanently
unowned.

## Execution paths

Owner denial before fix:

`management interaction -> member-only scoped_is_ticket_staff(user) ->
partial/non-Member user shape -> False -> Staff only`

Correct owner path:

`management interaction -> scoped_interaction_is_ticket_staff(interaction) ->
interaction guild owner / resolved Administrator -> allow -> feature-native
permission gate`

Verify failure before fix:

`on_ready -> one-shot panel reconcile -> first 50 legacy rows consume migration
slots -> later row legacy_deferred -> no retry -> old message remains visible ->
button click has no live current-app callback -> no initial response -> Discord
red interaction failure`

Correct Verify recovery:

`on_ready background reconciler -> zero-REST proven panels first -> every legacy
row processed in bounded waves -> shared process-wide recovery REST reservation
+ discord.py route limiter -> migrate/replace/bind -> normal Verify click ->
canonical _ack() -> role mutation -> follow-up`

## Changes

Branch: `fix/owner-authority-verify-recovery-20260925`

Implemented so far:

- added `scoped_interaction_is_ticket_staff(interaction)`;
- owner identity and resolved Administrator authority are checked before
  member-shaped staff-role fallback;
- updated Server Design, Diagnostics, Embed Builder, Setup, Setup Overview, and
  server-control second-stage staff checks to use interaction-aware authority;
- exported the new canonical helper;
- changed Basic Verify legacy migration from a permanent per-start cap into
  background waves that continue until every discovered legacy row is processed;
- current-app/current-component rows are processed first and remain zero-REST;
- legacy Discord work continues through the existing shared recovery REST budget
  and discord.py route-aware rate limiting;
- preserved the existing single Verify callback, custom ID, acknowledgement
  boundary, role mapping, and verification policy;
- added owner-without-member-shape regression coverage across the affected gates;
- added regression coverage proving a legacy set larger than one wave is fully
  reconciled with no `allow_legacy_rest=False` skips.

## Validation / results

Pending exact-head validation.

Required before completion:

- inspect final branch diff for accidental scope changes;
- compile all changed modules;
- focused owner-authority tests;
- focused Basic Verify restart/runtime tests;
- interaction lifecycle policy tests;
- relevant setup/public-command regression tests;
- full `pytest tests/`;
- standalone repository audits;
- all GitHub workflow groups green;
- verify non-staff still receive only `❌ Staff only.` where intended;
- verify owner/admin still reaches second-stage feature permission logic;
- verify current Verify panels remain zero-REST;
- verify all legacy panels are eventually migrated without bypassing shared REST
  pacing;
- verify the canonical Verify handler still acknowledges before any DB/role
  mutation and duplicate mutation remains impossible.

## Cleanup / conflicts

No second Verify dispatcher, fallback business implementation, permission shim,
or hardcoded Discord route limit has been added.

The shared recovery REST budget remains authoritative for bulk migration pacing.

## Blockers / risks

Repository CI is the executable validation environment available through the
GitHub connector. Live Discord acceptance remains necessary after deployment for
the exact old production panel shown in the screenshot.

## Backlog

Preserve unrelated existing follow-ups, including trusted-role UX, moderator
re-entry behavior, and Server Stats modal/button redesign.

## Next step

Finish diff/test inspection, open a draft PR, run exact-head CI, fix only
same-root regressions, then mark ready only after full validation is green.
