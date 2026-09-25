# Active Task

## Active task / desired outcome

**P0-DIAGNOSTICS-ACTIVITY-REPAIR-005 — make Diagnostics repair the exact authoritative activity-access gaps it reports**

Desired outcome: **Repair Bot Access** from `/dank diagnostics` opens one
preview-first, activity-only repair scope that can safely restore every
repairable View Channel / Read Message History / Manage Threads gap represented
by the authoritative activity audit, including active-thread gaps via their
repairable parent channel.

## Scope / single active task lock

Only Diagnostics activity-access handoff, authoritative activity target
construction, bot-overwrite preservation, activity repair UI truth, and the
integration conflict with merged PR #318 are in scope.

Do not broaden into verification, tickets, AntiNuke, general setup redesign, or
member lifecycle logging (#315) until this task is complete.

## Status

**IMPLEMENTATION COMPLETE — #317 integrated on top of merged #318; final exact-head validation pending**

Branch: `fix/diagnostics-activity-access-repair-20260924`

Current base: `main` after merged PR #318
(`aa07b908e509a9377f59e723865497fb3497f712`).

## Findings / root cause

1. Diagnostics correctly uses `audit_activity_scope()`, but its
   **Repair Bot Access** callback previously opened the canonical repair hub
   without activity scope enabled.
2. The existing activity repair builder independently swept `guild.channels`
   instead of deriving targets from the authoritative audit. The audit also
   includes active threads, so the audit and repair scopes could disagree.
3. The independent sweep required Manage Threads on every text/forum channel,
   even when Diagnostics did not report that permission as missing.
4. Activity-scoped repair started from the normal setup-repair target list.
   That could include role/member visibility overwrites even though the
   activity UI promises bot-only access changes.
5. Activity mode also inherited unrelated setup-wide blocker notes such as
   Manage Channels and View Audit Log, and its post-apply path ran Setup Check
   instead of staying inside activity-repair ownership.
6. PR #317 was 11 commits behind after #318 merged and conflicted in the
   canonical repair service. Taking either side wholesale would either lose the
   Diagnostics fix or regress #318's fail-closed/action-truth/reauthorization
   fixes.

## Execution path

`/dank diagnostics`
→ `DiagnosticsActionView.fix_access()`
→ `open_permission_repair(..., parent="logs", include_activity_coverage=True)`
→ claim-first interaction boundary from #318
→ `preview_or_apply()`
→ activity-only `_build_expanded_targets()`
→ `audit_activity_scope(guild)`
→ map audited channel/thread problems to repairable channel overwrites
→ preview
→ **Fix All Safe Access**
→ queue/serialized apply
→ post-repair result.

## Changes

- Diagnostics now explicitly opens the canonical hub with activity scope.
- Activity mode is now activity-only instead of starting from normal setup
  targets.
- Repair targets are built from `audit_activity_scope()` problems, not an
  independent all-channel sweep.
- Active-thread problems map to the thread parent for overwrite repair.
- Only permissions actually reported missing by the audit are enabled.
- Each target starts from `channel.overwrites_for(bot_member)`, preserving
  unrelated explicit bot allows/denies.
- Activity targets contain only Dank Shield's own overwrite; member/staff
  overwrites are not added or rewritten.
- Activity mode requires only the server-level Manage Roles prerequisite needed
  to edit channel overwrites; unrelated Setup-wide capability notes are not
  injected into this repair screen.
- Activity apply no longer runs an unrelated Setup Check afterward. It tells the
  admin to re-run Diagnostics after Discord propagates the overwrite updates.
- Activity preview uses **Fix All Safe Access** only when a safe action exists;
  #318's **Manual Discord Fix Required** and **Access Healthy** fail-closed
  states remain authoritative.
- #318's claim-first interaction handling and conditional Reauthorize guidance
  are preserved.

## Validation required / results

Pending exact-head validation:

- Diagnostics handoff regression;
- authoritative active-thread → parent mapping regression;
- existing bot overwrite preservation regression;
- activity-only target ownership regression;
- activity-only blocker-scope regression;
- primary-action truth regression;
- Python compile and diff whitespace;
- full test suite;
- standalone tool checks and repository audits;
- all GitHub workflow groups;
- currentness, mergeability, reviews/threads, and final diff hygiene.

## Cleanup / conflicts

- Resolved the #317/#318 overlap semantically on top of #318 rather than
  restoring stale pre-#318 repair code.
- Removed the obsolete independent activity all-channel scan helpers.
- No duplicate activity mutation owner is introduced.
- The authoritative activity audit remains read-only; mutation stays in the
  canonical permission-repair service.

## Blockers / risks

No implementation blocker is known. Final status depends on exact-head CI and
GitHub currentness checks.

## Backlog

- PR #315: restore detailed member lifecycle and Modlog logging. Not
  investigated while #317 is active.

## Next step

Run exact-head CI. If all gates pass and the PR is current/mergeable with no
unresolved review state, mark ready and merge with expected-head protection.

## Production acceptance after deploy

1. Open `/dank diagnostics` on a server with incomplete activity coverage.
2. Press **Repair Bot Access**.
3. Preview shows the same authoritative activity gaps Diagnostics reported,
   collapsed to the channel overwrites that can actually repair them.
4. **Fix All Safe Access** appears only when safe changes exist.
5. One apply repairs every auto-repairable activity gap without changing
   member/staff overwrites or unrelated bot overwrite bits.
6. Self-locked targets remain manual-only.
7. Re-run Diagnostics and confirm coverage increases; any remainder must be a
   true manual blocker or a newly changed Discord state.
