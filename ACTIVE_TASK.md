# Active Task

## Active task / desired outcome

**P0-MEMBER-LIFECYCLE-LOGGING-005 — restore detailed Modlog member exits and reliable operational join/leave logging**

Desired outcome: keep one canonical operational join/leave router, restore useful
member lifecycle detail, keep staff Modlog detail separate, make the real
operational route repairable/diagnosable, and preserve the access-repair work
already merged through PRs #318 and #317.

## Scope / single active task lock

Only member lifecycle logging is active:

- canonical operational join/leave delivery;
- staff Modlog detail for ordinary member departures;
- Member Logs route configuration, contextual access repair, and runtime health;
- the static/runtime contracts needed to validate those paths.

Do not broaden into verification, tickets, AntiNuke, general setup redesign, or
other Modlog event families unless tracing proves they are directly required.

## Status

**IMPLEMENTATION COMPLETE — #315 rebuilt on current main after merged #318/#317; final exact-head validation pending**

Branch: `fix/member-lifecycle-log-detail-runtime-20260924`

Current base: `main` at `0a19334af1ae26abcb79f4de66ce78f6c529cf2f` after merged PR #317.

## Findings / root cause

1. The retired historical lifecycle sender contained richer cards, but reviving
   it would create duplicate join/remove listeners. The surviving canonical
   router had not inherited enough of that useful detail.
2. The ordinary voluntary-leave staff Modlog path had regressed to a minimal
   user-only record even though the canonical member-context builder still
   existed.
3. Member Logs contextual repair omitted the actual operational join/leave
   channel, so the route that records lifecycle events could remain broken while
   the repair UI looked elsewhere.
4. Operational lifecycle events were suppressed when Welcome/Exit Card Studio
   posted to the same channel. The card route and audit-style event route are
   separate products and must not replace one another.
5. Choosing an operational join/leave route silently rewrote
   `exit_card_channel_id`, crossing ownership into Exit Card Studio.
6. Startup diagnostics resolved the route but did not prove View Channel,
   Send Messages, and Embed Links readiness.
7. The previous #315 head passed compile and the full pytest suite but failed one
   standalone static contract because the test looked for hardcoded
   `member join event delivered` / `member leave event delivered` strings
   while the runtime intentionally logs both through the generic
   `member {event_name} event delivered` helper.

## Execution path

Join:
`Discord on_member_join`
→ canonical lifecycle router
→ Welcome Card Studio delivery
→ independently resolve configured operational join/leave log
→ public-safe lifecycle embed
→ operational event send.

Leave:
`Discord on_member_remove`
→ canonical lifecycle router sends Exit Card Studio + independent operational
  lifecycle event
→ existing `events.on_member_remove` separately checks kick/ban attribution
→ only ordinary leave builds the detailed staff Modlog record.

Member Logs repair:
`/dank member-logs`
→ exact saved routes
→ shared contextual permission-repair core
→ post-repair lifecycle runtime-health screen.

## Changes

- Keep one canonical operational join/leave sender; retired legacy sender stays
  unreachable from command profiles.
- Operational join/leave embeds include identity, account creation/age, profile
  state, member count, avatar, and membership duration on leave.
- Add canonical detailed voluntary-leave Modlog embed with account/membership
  history, roles at exit, and existing staff member context.
- Keep kick/ban attribution paths unchanged and only use the ordinary-leave
  builder after those checks fail to identify a moderation exit.
- Include the exact operational join/leave route in Member Logs contextual
  permission repair.
- Stop operational route selection from rewriting Exit Card Studio's route.
- Always emit the operational event even when the member-facing card uses the
  same channel.
- Expose route writability, requested Server Members intent, and canonical
  join/leave listener registration in Member Lifecycle Routing.
- Add the same operational route writability to startup route diagnostics.
- Correct the centralization static test to validate the generic success logger
  that actually owns both join and leave delivery.
- Rebuild #315 directly on current `main`; none of its nine lifecycle code/test
  files overlapped the #318/#317 code changes. Only this task record conflicted.

## Validation required / results

Previous head evidence:

- Python compile: passed;
- full `pytest tests/`: passed;
- claim-first ticket security: passed;
- managed-category SQL smoke: passed;
- Application Command Size Diagnostics: passed;
- Dank Design Regression CI: passed;
- Profile Runtime Diagnostics: passed;
- Ticket Owner Emergency Override: passed;
- standalone tools: one failure only, now root-caused and corrected in
  `tools/test_join_leave_log_centralized.py`.

Final exact-head validation still required:

- full unit suite;
- standalone tool checks;
- Python compile and diff whitespace;
- repository audits;
- all workflow groups;
- currentness/mergeability/review state;
- final diff hygiene and no conflict artifacts.

## Cleanup / conflicts

- Rebased/squashed the lifecycle work onto current `main` instead of merging
  22 stale commits through the newer access-repair history.
- Preserved all current #318/#317 access-repair code.
- No second operational lifecycle sender or listener owner is introduced.
- Welcome Card Studio, Exit Card Studio, operational lifecycle logging, and
  staff Modlog retain separate ownership.
- No temporary/debug code is intentionally retained.

## Blockers / risks

No implementation blocker is known. Final readiness depends on the new exact-head
CI and GitHub currentness checks.

## Backlog

No additional open PR is being worked while #315 is active.

## Next step

Run exact-head CI on the rebuilt branch. If every gate passes and the PR remains
current/mergeable with no unresolved review state, mark ready and merge with
expected-head protection.

## Production acceptance after deploy

1. A normal join produces the configured member-facing join card and an
   independent operational join event.
2. A normal leave produces the configured member-facing exit card, an
   independent operational leave event, and a detailed staff Modlog record.
3. Kick/ban exits retain their existing attributed moderation records rather
   than being mislabeled as ordinary voluntary leaves.
4. Operational logging still occurs when the card and operational routes point
   to the same channel.
5. `/dank member-logs` can repair the exact operational join/leave route and
   does not retarget Exit Card Studio.
6. Member Lifecycle Routing reports route resolution/writability, Server
   Members intent request state, and canonical listener registration.
7. Startup logs include the operational route's writable/not-writable health.
