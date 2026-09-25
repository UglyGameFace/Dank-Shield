# ACTIVE TASK

## Active task / desired outcome

**P0-DIAGNOSTICS-ACTIVITY-REPAIR-005 — make Repair Bot Access actually repair the activity-access failures shown by Diagnostics**

The live Diagnostics screen is now structurally healthy:

- startup ownership: OK;
- expected boot owners: 10;
- loaded: 10;
- missing: 0;
- startup blockers: none;
- startup warnings: none;
- recent native interaction failures: none.

The remaining live problem is authoritative activity coverage:
only 13/60 message channels/active-thread scopes are inspectable and the report
shows missing View Channel, Read Message History, and Manage Threads access.

## Single active task lock

Only the Diagnostics -> Repair Bot Access activity-scope handoff and its
non-destructive repair behavior are active.

Do not reopen Basic Verify, startup ownership, ticket runtime, AntiNuke, or
general setup work unless new evidence points back to them.

## Root cause

Diagnostics correctly audits the whole activity scope, but its
**Repair Bot Access** callback called:

`open_permission_repair(interaction, parent="security")`

without `include_activity_coverage=True`.

The shared repair service deliberately defaults that flag to false so ordinary
Setup repair does not unexpectedly touch dozens of channels.

Result: Diagnostics warned about 47 activity-access gaps, then opened a repair
preview that intentionally excluded those exact gaps.

## Repair

Diagnostics now hands off to the canonical repair service with:

- `parent="logs"`;
- `include_activity_coverage=True`.

The canonical activity repair will therefore preview every repairable
bot-only View Channel / Read Message History / Manage Threads gap in one run.

The preview's primary action is labeled **Fix All Safe Access** when activity
coverage is included.

## Non-destructive overwrite preservation

The activity-only path previously created a fresh bot overwrite containing only
View Channel / Read Message History / Manage Threads for channels that were not
already setup targets.

That could replace unrelated explicit bot permissions on the same channel.

The repair now starts from:

`channel.overwrites_for(bot_member)`

and only turns on the required activity permissions, preserving every unrelated
explicit allow/deny already configured for Dank Shield.

## Manual-fix boundary

The bot should automatically repair all targets where Discord still allows it
to edit permission overwrites.

Manual Discord work is required only when the bot is denied the permission
needed to edit that target's overwrite itself. The canonical repair runtime
already detects that self-lockout and reports **Manual Discord Fix Required**
instead of pretending a retry can succeed.

## Safety invariants

- Diagnostics remains read-only until the user explicitly opens repair.
- Opening Repair Bot Access still previews first; no mutation happens on open.
- One **Fix All Safe Access** action applies all safe activity/setup bot-access
  changes in the selected scope.
- Only Dank Shield's own activity-access overwrite is expanded.
- Existing unrelated Dank Shield overwrite entries are preserved.
- Member/staff visibility is not changed by activity coverage repair.
- Self-locked targets remain manual-only rather than generating doomed writes.

## Validation required

- diagnostics handoff test proves activity scope is enabled;
- activity preview exposes one clear Fix All Safe Access action;
- overwrite-preservation contract test;
- Python compile;
- full test suite;
- all GitHub workflows;
- currentness / mergeability / review / diff hygiene.

## Status

**IN PROGRESS — implementation complete; exact-head validation pending**

Branch: `fix/diagnostics-activity-access-repair-20260924`

Base: current `main` after merged PR #316.

## Production acceptance after deploy

1. Open `/dank diagnostics`.
2. Press **Repair Bot Access**.
3. Preview must include the activity-access gaps from Diagnostics.
4. Press **Fix All Safe Access** once.
5. All auto-repairable channel gaps are repaired in that run.
6. Any remaining items must be explicit Discord self-lockouts/manual-only
   blockers, not omitted repair scope.
7. Re-run Diagnostics and confirm activity coverage increases accordingly.
