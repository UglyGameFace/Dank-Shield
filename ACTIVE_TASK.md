# ACTIVE TASK

## DS-MEMBER-JOIN-LOG-REGRESSION — Restore reliable member join/leave lifecycle logging

**Status:** FINAL EXACT-HEAD VALIDATION AFTER MAIN SYNC

**Branch:** `fix/member-join-leave-log-regression`
**Current base main:** `4f40659375cf15811dba17a4da484532eb7f1c1e`
**PR:** #251

## Outcome target

Real Discord member joins and leaves must always produce the configured operational lifecycle log independently of optional Welcome/Exit Card Studio delivery and independently of invite-source attribution. A successful Studio delivery may suppress only a true same-channel duplicate.

## Root cause

The canonical Welcome Card migration removed the operational join sender from the configured `JOIN_LEAVE_KEYS` route and made the optional Studio path the only public join output. Closure review found the same coupling on leave: disabling, failing, or separately routing Exit Card Studio could suppress the configured leave log as well. Invite attribution is a separate concern and must never gate the base `on_member_join` lifecycle event.

## Execution / ownership

- `public_member_lifecycle_runtime` installs the authoritative `member_lifecycle_router_guard`.
- `member_lifecycle_router_guard` owns the configured operational join/leave route.
- Welcome Card Studio remains the optional member-facing welcome card owner.
- Exit Card Studio remains the optional member-facing leave card owner.
- staff invite-source/modlog output stays separate.
- retired legacy public lifecycle senders remain inactive.

## Implemented changes

- independently send configured operational join and leave events through `JOIN_LEAVE_KEYS`
- preserve operational logging when either Studio is disabled, unavailable, fails, or targets another channel
- suppress only a successful Studio delivery to the exact same lifecycle channel
- keep join logging independent of invite attribution success
- stop `/dank member-logs` from forcibly enabling Exit Card Studio when only the lifecycle log channel is changed
- preserve compatibility target mapping without overriding the user's explicit Studio enable/disable choice
- update lifecycle status/help and centralization guards

## Regression coverage

`tests/test_modlog_join_dedupe_behavior.py` covers Studio-disabled, Studio-failed, same-channel duplicate, different-channel, join, leave, and existing semantic-dedupe behavior.

`tools/test_join_leave_log_centralized.py` guards canonical ownership, both operational senders, `JOIN_LEAVE_KEYS`, duplicate suppression, Studio-gate independence, member-logs behavior, and retirement of legacy senders.

## Validation history

The pre-sync final head `cb9dd9fd157f7fe622f92e153ae0e321309c78dc` passed all triggered workflows, including Dank Shield CI, Ticket Owner Emergency Override, Dank Design Regression CI, Schema Authority SQL, Application Command Size Diagnostics, and Profile Runtime Diagnostics.

That head could not be merged because `main` advanced by five commits after the PR branched. GitHub correctly reported the PR as diverged and non-mergeable even though its own exact-head CI was green. Current `main` changes are AntiNuke-only plus `ACTIVE_TASK.md`; the lifecycle production/test files do not overlap.

This branch now incorporates current `main` while preserving the four-file lifecycle task scope. The resulting exact synchronized head must pass the full validation wave again before merge.

## Scope / cleanup

Intended task files only:

- `ACTIVE_TASK.md`
- `stoney_verify/startup_guards/member_lifecycle_router_guard.py`
- `tests/test_modlog_join_dedupe_behavior.py`
- `tools/test_join_leave_log_centralized.py`

The current-main AntiNuke changes are inherited from `main`, not part of this PR's lifecycle implementation. No invite-policy, moderation-policy, schema, role, ticket, or unrelated redesign belongs in this task.

## Merge gate

- exact synchronized head is 0 behind current `main`
- all required and companion workflows pass on that exact head
- final PR diff is limited to the intended lifecycle task
- no unresolved review/thread blocker exists
- PR is mergeable and marked ready
- merge only the exact validated head
- verify resulting merge commit is current `main`
- verify post-merge deployment/status before releasing this task lock

A live Discord join/leave remains the final production exercise and cannot be simulated by GitHub CI.

## Next step

Run the full exact-head validation wave on the synchronized branch. If green, mark PR #251 ready, merge the exact validated SHA, verify `main` and deployment/status, then return to PR #253 and resynchronize/revalidate it against the new `main` before merging.