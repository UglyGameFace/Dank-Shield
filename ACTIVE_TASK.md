# Active task: Action & Reminder Center (FORCE SWITCH, 2026-10-22)

## Current single-task lock
The owner explicitly FORCE SWITCHED to **Issue #367, Action & Reminder Center**, after PR #691 merged. The previous 4:20 task and guarded branch-retirement task are now both paused. Do not mix further 4:20 work, branch cleanup, Cinema, or unrelated verification framework implementation into this slice.

## Slice 4D.5A: Non-sending deployment and database preflight (active, 2026-10-23)
- PR #698 (4D.4) is VERIFIED MERGED at `1d12e05d408bd5e7198d607422aad44a80c71927` and all seven workflows passed. No open PRs existed at the start of this slice. This does NOT prove Discloud deployed that SHA.
- Active branch `feat/action-center-4d5-live-readiness-20261023` starts from that exact merged main; single active task remains **Action & Reminder Center** only.
- Add app-owner `Readiness Report` inside existing Owner Canary Console, accessible even when exact-target flags are disabled. Read-only source fingerprint/declared SHA observation, private claims and audit schema SELECT readiness, dedicated PG DSN presence boolean, target flags matched-to-guild boolean; no secrets/user IDs exposed.
- Expand runtime source fingerprint to include action canary, audit, claim, consent and permission-critical files; print equivalent CI fingerprint so owner can compare with Discloud. A runtime SHA/fingerprint remains **unverified against current GitHub main** until actual deployed release evidence is supplied.
- Fix owner console button authority: Discord application ownership is canonical; no unrelated guild staff-role prerequisite inside the owner-only view. Entry navigation remains staff-scoped.
- New CI tests cover default-off/DB-unconfigured behavior, wrong-target rejection, failures without private data exposure, read-only proof and owner permission. No server scan, new worker, migration, auto-DM, Discloud deploy or env variable changes.
- **Production gates still pending:** verify deployed SHA/fingerprint from Discloud; separately approve the private 4C claim and 4D.4 audit migrations; verify a genuinely opted-in, non-exempt member with pending published Member Setup revision; separately approve exact recipient and live one-shot canary. No production actions from this PR.
- Paused: 4E/4F, 4:20, Cinema, Verification Framework, member lifecycle and branch retirement.

## Slice 4D.4: app-owner manual canary console + durable audit (active, 2026-10-23)
- Verified PR #697 (4D.3) merged as `cc98488482da22e505b089e931ee8e8c1618d1ba` with 6/6 checks green. Current branch: `feat/action-center-4d4-owner-canary-console-20261023` on that exact main.
- Expose **Owner Canary Console** only from Action Builder → Safety Schedule; actual Dank Shield application owner verified via `interaction.client.is_owner(user)`, not a configured staff role or server Administrator. Typed confirmation `SEND guild/user/revision` is one-shot, and all 4 target-locked process-local canary gates still have to match. None of those gates are set by this PR.
- Require durable `requested` PostgreSQL audit record *before* calling the existing strict single-user claim dispatcher, and final audit status after; fail closed if either 4C claims ledger or separately staged 4D.4 audit schema is missing. No message content in audit.
- The additive `postgres/migrations/202610230002_action_center_canary_audit.sql` plus `tools/migrate_action_canary_audit_postgres.py` are staged but **never applied automatically to production**. Original 4C claim migration remains unapplied. No auto-DMs, schedulers, root slash commands or broad member fetches.
- Extend focused and isolated PostgreSQL CI, owner permission and confirmation replay regression, lifecycle/retention ownership; verify full CI at PR head before merge, then Android/Discloud/real DB rollout via separate explicit owner authorization. Keep owner 4D.5 canary rollout and general 4E/4F feature work paused.
- Single active task lock remains Action & Reminder Center 4D.4. All unrelated tasks paused.

## Slice 4D.3: disconnected manual single-member canary (active, 2026-10-23)
- Verified PR #696 (4D.2) merged as `688e0c600a16bab5579b10c7f5d3e73a098b312b`, all six checks green. Branch `feat/action-center-4d3-canary-dispatch-20261023` starts from that exact main.
- Add **unwired** `action_canary_delivery.py` for one Member Setup user/revision only, requiring exact 4-way process-local canary gates before any claim, cached membership proof, 4D.2 durable readiness and atomic private PostgreSQL reservation. After reservation, recheck user opt-in, canonical Member Setup completion, guild schedule/quiet hours, same recipient/revision and environment gate; cancel any uncertain outcome without retry.
- Canary limits strictly ONE attempt per member/revision and ONE per guild-local day. DM text is fixed, minimal and mention-free. Existing verification reminder worker is unchanged.
- No public slash/button, guild scan, startup hook, scheduled worker, deployment configuration, secrets, live DMs or private PostgreSQL migrations in this slice. The 4C claim tables remain **not applied** to production.
- Focused tests and CI cover opt-out/completion after claim, exact gate revoke, day rollover, failed Discord send, no callers, denied claims, isolated real PG17 concurrency.
- **Single active task remains Action & Reminder Center 4D.3.** Paused: 4:20, Cinema, wider Verification Framework and branch-retirement work. Post-merge approval for any true canary rollout requires a separate, audited human-authorized invocation and live DB migration review.
- See `docs/ACTION_REMINDER_CENTER.md`.

## Slice 4D.2: actual private ledger readiness (active, 2026-10-23)
- PR #695 (4D.1) merged into `86d44bb9e135d6862c61c3becc10f0cdb58f7f00`, with all six checks green. Member consent remains explicitly opt-in per guild; zero automatic Action Center reminder DMs enabled.
- Branch `feat/action-center-4d2-ledger-readiness-20261023` starts from that exact merged main; this is the **only active development task**.
- A safe per-user/revision per-guild/day PostgreSQL claim **read-only** adapter returns zero counters only for a successful empty query, never a failed/missing DB/migration. Existing 4C claim schema is **not** installed to production through this PR.
- `action_delivery_readiness.py` performs the 4D.1 canonical preflight before and after actual ledger reads, comparing guild schedule/draft and applying true retry cooldown, attempts and guild daily budget. **delivery_authorized always False**; no atomic reservation, new worker, notifications or broad member sweep.
- Existing Action Builder → Safety Schedule gains staff-only `Check Delivery Readiness` for the requesting staff member, via existing authorization checks. Staff exemptions and unprovisioned ledger produce honest blocked results.
- Focused CI, real disposable PG17 tests, full bot tests, mobile UX and Discloud commit verification remain gates. Other tasks (4:20, Cinema, verification framework, branch retirement) remain paused.

## Slice 4D.1: private member reminder consent and no-send preflight (active, 2026-10-23)
- PR #694 (4C) merged as `311d4890e920b279348c71125e2c868f6e20c08b`, with all seven workflows passing. The manual private PostgreSQL DDL has **not** been applied, and no Action Center automatic delivery has been activated.
- Active branch: `feat/action-reminder-center-4d-member-consent-20261023`, based on the exact merged main.
- Member-owned per-server, default-OFF Member Setup DM preference uses existing private `profile_guild_settings` and verified write/reread. The Action Inbox gains a private My Reminder Preferences page with enable/stop controls and click-time owner identity checks.
- `stoney_verify/action_delivery_preflight.py` rechecks canonical Member Setup exemption/completion/revision date, per-member consent, draft inclusion and schedule preview. Its `delivery_authorized` is always false: no sender, no runtime claim, no new worker, no schema or secrets change.
- Focused regressions check per-guild privacy, failed writes/reads, unknown schema, stale/complete requirements, no double verification reminder ownership, and never-authorized sends.
- **Single active task remains Action & Reminder Center 4D.1.** Previous 4:20, Cinema, full Verification Framework, member lifecycle and guarded branch cleanup remain paused. Do not enable a sender in this PR; independent 4D.2 delivery rollout needs explicit migration and canary.
- Validate exact-head focused/full CI and Discord Android member UX before merge. Discloud SHA still needs real-world acceptance.

## Slice 4C: Scheduling and anti-spam safety policy (active, 2026-10-23)
- PR #693 (4B) merged into `c3a5ba5852a44a460e8120681da3b4b20591ff0e`. Active branch `feat/action-reminder-center-4c-safety-20261023` starts at that exact SHA.
- 4C writes **only preview-only scheduling limits**, not enabled reminder automation: first wait, repeat cooldown, max attempts, quiet hours in a validated IANA timezone, per-guild daily cap.
- New `stoney_verify/action_schedule_policy.py` is the separate CAS-saved guild schedule namespace, strict `enabled: false`, source-backed dry-run decision; `public_action_schedule.py` offers staff-only edit and personal preview through existing Action Builder.
- Staged **manual-only** private PostgreSQL DDL and dormant transactional claim adapter for future Member Setup revision reminders in `postgres/migrations/202610230001_action_center_delivery_reservations.sql` / `stoney_verify/action_center_postgres.py`. The existing verify reminder worker/ledger remain canonical; there is **no new worker, production migration, or live notification** in 4C.
- Validate exact PR head, fast and full regressions, staged SQL smoke, Android mobile click-time authority, Discloud deployed SHA. Do not claim 4C is live or start 4D delivery before explicit post-merge acceptance.
- All unrelated tasks remain paused by single active task lock.

## Slice 4B: Action Builder and Conditions (active, 2026-10-23)
- PR #692 (4A) merged in `8ed1455321ef52c80b15ada83db4f7bb53c38fbb` with all eight CI workflows passing. Discord/Discloud acceptance remains a deployment gate.
- New active branch: `feat/action-reminder-center-4b-policy-20261023` from that exact merged main.
- Per-guild **draft-only** policy in `action_builder_policy.py`: allowed source conditions `pending_verification` and `member_setup_revision`; strict pending-member scope; preview-only/manual schedule; authoritative severity never downgraded.
- Config stored in the existing `guild_configs` feature namespace `action_reminder_builder_v1`, validated and saved via atomic `compare_and_swap_guild_config_key`; revision conflicts and unknown schema fail closed. No new table, migration, worker or secret.
- Staff-only mobile `commands_ext/public_action_builder.py` linked from the canonical private Action Inbox, with edit, preview, refresh and back; recheck staff authority at click-time; preview only the requesting staff member's live canonical truth. New stored flags always `delivery_enabled: false`.
- **Single active task lock** remains Action & Reminder Center 4B. Verification Framework, 4:20 follow-ups, Cinema, member lifecycle and branch-retirement work stay suspended. Do not start 4C scheduled delivery until this PR passes full CI and mobile acceptance.
- See `docs/ACTION_REMINDER_CENTER.md` and `tests/test_action_builder_policy.py`.

## Slice 4A: one authoritative private Action Inbox
- Baseline `main` SHA: `7f2c981b8952341c3556f71136cc752ca0968fac`.
- Feature branch: `feat/action-reminder-center-foundation-20261022`.
- `action_center_service.py` reads pending verification from canonical role truth and review-needed Member Setup from `public_member_setup._member_payload` so access-gate grandfathering, exemption, and completion truth are retained. Recompute at each open; unknown sources fail closed.
- `commands_ext/public_action_center.py` renders the owner's private mobile inbox with only source-derived actions. Staff shortcuts delegate to existing Verification Reminders and Member Setup managers after click-time staff checks.
- Register one canonical `action_reminder_center` route in `navigation_registry.py` and `public_command_surface_v2.py`. No new slash root, independent worker, database table, scheduler, unsolicited ping, persisted completion, or access mutation.
- Action contract: Target → Condition → Schedule → Delivery → Action → Completion. Slice 4A restricts to member pending conditions, on-open read-only private delivery, and completion exclusively in original owners.
- Rollout gates: fast focused tests, exact-head full CI, registry parity, Android Discord canary with 2 pending/cleared cases and role changes, Discloud live SHA evidence. No production data/secrets changes.
- Future slices after explicit completion: validated shared action-adapter policy, configured schedule & throttle, durable claims/recheck at delivery, staff tasks, events, notifications. Do not call this full Action & Reminder Center complete just because the inbox ships.

## Paused preceding 4:20 work, retained verbatim for audit


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

## Version 3: role-only gentle heads-up (October 22)
- The owner confirmed merged PR #689 is running in Discord but Daily Server Settings reports missing database migration. Connected read-only queries confirmed neither known Supabase project has `dank_somewhere_420_schedules`; deployment migrations are blocked by two remote-only historic schema-migration versions, which MUST NOT be removed blindly. Those two already-applied historical SQL statements have now been mirrored exactly to their original filenames on this PR branch, without changing migration history or executing DDL. Confirm production migration preview before deployment.
- Active feature branch `feat/420-gentle-headsup-20261022` starts at merged main `252ccc08b7d7c24e8586b4c373ce8d5e74ca663e`.
- **Owner update:** eligible people are automatically included while holding the exact server-selected @Stoners role, with a prominent DM Stop reminders button and member menu Stop/Resume. Staff still explicitly enable this role-based server program; other guilds are not automatically opted in.
- One at-most-once 4:15 PM server-local reminder wave, no 4:20 repeat, no public pings, at most one DM per user per local day even across multiple servers. All durable opt-outs/claims are private tables; role resolution uses shared Search-Safe picker, not native Discord pickers, and actual role membership checked at send time. No mass guild fetch.
- A second migration `20261022201500_somewhere_420_role_heads_up.sql` depends on v2 schedule table and must be applied on the actual active production DB before staff can enable the feature. Merging code does not apply it.
- Conservative wave limit 250 cached role members per guild per day, at most two waves concurrently with paced DMs and a hard 4:20 cutoff, for Discloud REST safety. Large-guild delivery and cache-completeness requirements remain acceptance gates, not solved by optimistic claims.
- CI and mobile canary required. No migrations or production data writes are authorized through this documentation; do not silently repair unrelated migration history.
- **Single task lock remains 4:20 improvement**, guarded branch retirement stays paused.

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
