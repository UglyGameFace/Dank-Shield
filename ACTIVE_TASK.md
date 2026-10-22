# Active task: It's 4:20 Somewhere (FORCE SWITCH, 2026-10-22)

## Active outcome
Build version 2 of issue #687 after the owner merged PR #688: richer world clock, quick and join-active canonical /toke integration, opt-in server daily celebrations with quiet hours and notification-role controls, and personal city/timezone/DM preferences. This is a continuation of the SAME active 4:20 task; branch-retirement remains suspended.

## Authority and execution path
- Current main at branch creation: `830e961eab0e2e348d4b606402aea4bfa455f691` (merged PR #686).
- `commands_ext/public_command_surface_v2.py` owns the public 11-item application-command tree and the canonical /dank home router. `navigation_registry.py` owns discoverability.
- `commands_ext/public_toke.py:open_toke_command` owns existing per-guild starter/ping authorization, permission validation, durable cooldowns, media composer, and session posting. Never replicate its outbound session path.
- `guild_config.get_guild_config` and `compare_and_swap_guild_config_key` own safe per-guild feature state writes.
- **Requirement interpretation:** separate world-time activity integrated via existing /toke, not a new slash command. On-demand board with manager-controlled non-pinging public share. Auto announcements are deliberately not enabled; no scalable scheduler exists for this feature and the owner did not choose a cadence or consent model.

## Changes in feature branch
- Pure `stoney_verify/somewhere_420_clock.py`: DST-aware 4:20 AM/PM in IANA cities, grouped next waves.
- `stoney_verify/commands_ext/public_somewhere_420.py`: on-demand world-board, private refresh/toggle, canonical /toke handoff and staff-only public sharing guarded by atomic per-guild one-hour claim.
- Add feature to canonical registry and compact nav router, without adding command roots or startup workers.
- Regression tests `tests/test_somewhere_420.py`; product/acceptance documentation `docs/ITS_420_SOMEWHERE.md`.
- No production database migration or Discloud changes.

## Validation status
- Pending CI, focused tests, production-path integration canary, clean diff check. Do not claim deployed or complete before evidence.
- Test permission errors, concurrency and config-write failures, 4:20 DST edges, and /toke integration.
- Release through one reviewed PR; evaluate CI after each commit.

## After first-release merge: version 2 work (2026-10-22)
- PR #688 merged into main at `85896fdeafbe725acc076816c1402b064df90d12`; original 4:20 launch implementation is on main.
- Current feature branch `feat/somewhere-420-celebrations-20261022` begins exactly at that merged SHA.
- New `somewhere_420_schedules.py`, `somewhere_420_runtime.py`, and SQL migrations introduce service-role-only schedule storage and atomic at-most-once daily delivery claims. Worker starts only from the existing guarded background-workers path; claims due records without scanning guilds.
- `commands_ext/public_somewhere_420_settings.py` provides Search-Safe server channel selection, explicit opt-in timezone/quiet/role settings, and personal favorite/DM options. `public_somewhere_420.py` adds next-wave focus, quick-to-text-only Toke, existing-sesh lookup, and panel links.
- Public /toke parameters must stay unchanged; a private wrapper calls the same existing /toke handler's guard path.
- **Migration not applied by PR:** only enable daily schedules after correct Supabase deployment, RLS/claims validation, focused CI, full regression checks and real Android Discord canary. Do not claim 10k concurrent punctual sends without load test.
- Dedicated `.github/workflows/somewhere-420-ci.yml` validates SQL and focused Python; full CI still applies.
- Status: work in feature branch, not yet merged/deployed. Confirm CI and schema safety before merge.

## Suspended task (preserve for resumption; NOT active)
**Guarded retirement of remaining Dank Shield branches**, previously active in `ACTIVE_TASK.md`.
- Last reviewed main `830e961eab0e2e348d4b606402aea4bfa455f691`; existing PR #686 merged and GitHub Action runs #39847982721 (preview) and #39848036329 (delete) succeeded. 33 branches remained after completed deletion.
- Previous active branch was `chore/retire-last-proven-ancestor-20261022`, merged in #686; reviewed original 22-ref batch had already retired. No unfinished proposed branch-retirement PR remains.
- Next work: independently evaluate 24 diverged branches carrying unique code and 8 zero-ahead known-good/backup/recovery refs before archive-first retirement; protect `main` and live Discloud rollback refs. Example held unique branch: `feat/cinema-rolling-supervised-worker-20261014` adds a file not on main. Do not delete by age/name or run broad deletion modes until proof.
- Branch-cleanup tests and exact-SHA archive safeguards remain in `.github/workflows/guarded-merged-branch-cleanup.yml`; suspended validation/cleanup status: reviewed 22+1 batch succeeded; remaining 32 non-main historical heads unreviewed for safe retirement.
- Discloud bot, source/candidate DB, and all recovery credentials remain untouched. Dedicated recovery drill #39721416434 passed, but no database production cutover authorized.
- Backlog: resume this retirement only after current 4:20 feature reaches its full Definition of Done, or another explicit FORCE SWITCH.

## Additional backlog
Discord developer compatibility tracker is scheduled weekly, read-only; do not start improvements mid-feature.

## Risk/blockers/next step
Current code is on a temporary feature branch, no merge/deploy. Need confirm all CI suites, verify user-facing navigation route from /dank home, and run a real Discord test where reasonably possible. Automatic scheduled announcements remain intentionally out of scope.
