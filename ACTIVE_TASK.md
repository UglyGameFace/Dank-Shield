# ACTIVE TASK

## DS-SEC-041 — Persistent hostile actor / AntiNuke re-entry containment

**Status:** IMPLEMENTED ON DRAFT PR #206 / VALIDATION IN PROGRESS
**Branch:** `fix/persistent-hostile-actor-reentry`
**Base:** `91bce1e3a72b16e079f99febb6519b3144c36955` (`main`, merge of PR #203)
**PR:** #206

## Outcome

A Discord identity that crosses confirmed AntiNuke containment must not return as a fresh actor after a kick, reinvite, bot re-add, process restart, or ordinary SpamGuard bot exclusion. Exact Discord IDs are authoritative. Human alt propagation is allowed only from hard identity evidence, never heuristic name/profile similarity.

## Root cause / findings

- Canonical AntiNuke containment treated a successful kick as definitive containment.
- No guild-scoped hostile identity disposition survived leave/rejoin/reinvite.
- SpamGuard intentionally rejects bot-authored messages before its counters and settings path.
- RaidGuard/member-risk intentionally excludes Discord bots from human alt scoring.
- Member history exists, but AntiNuke did not consume it as a durable hostile-identity enforcement decision.
- The tested GANG Nuker flow can therefore keep its Discord bot process alive, be reinvited, reacquire guild access, and resume destructive actions unless the Discord identity itself remains contained.

## Execution path

1. AntiNuke attributes a destructive actor and calls `_contain_actor`.
2. The hostile-actor runtime records the guild/user ID before containment.
3. Containment attempts a ban first; the canonical kick/role-strip path remains the fallback if Discord denies the ban.
4. Rejoin checks exact hostile ID before ordinary join heuristics.
5. Previously confirmed hostile bots bypass the normal SpamGuard bot exemption through a reputation prefilter.
6. Bot-add audit handling checks the target bot ID before the normal untrusted-bot path, including owner re-adds.
7. Human linked accounts inherit containment only when verified identity proof or a staff-confirmed duplicate link points to an active hostile ID.
8. Ready-time reconciliation checks already-present members after a restart.

## Changes

- `stoney_verify/anti_nuke_hostile_actor_runtime.py`
  - durable Supabase + local outage-continuity reputation store;
  - exact-ID re-entry enforcement;
  - ban-first confirmed AntiNuke containment with canonical fallback;
  - Ban Members readiness requirement bridge;
  - known-hostile bot-add interception;
  - SpamGuard / Invite Shield reputation prefilter before generic bot exclusion;
  - hard-proof linked-alt inheritance;
  - ready-time reconciliation;
  - explicit `clear_hostile_reputation()` API for audited owner/admin integration.
- `supabase/migrations/20260913163000_hostile_actor_reputation.sql`
  - guild-scoped durable reputation table, active disposition, incident count, hard-link parent, explicit clear metadata, service-role-only access.
- `main.py`
  - installs hostile reputation after AntiNuke gateway/finalizer/incident policy is finalized.
- `tests/test_antinuke_hostile_actor_runtime.py`
  - persistence, ban-first containment, fallback, permission readiness, exact rejoin, hard-proof alt inheritance, SpamGuard bot prefilter, and owner re-add regressions.

## Validation / results

- Source implementation and focused tests committed to PR #206.
- Exact-head GitHub Actions validation: pending.
- Local clone/test execution is unavailable in this session because the container cannot resolve `github.com`; GitHub Actions is the authoritative validation path for this PR.

## Cleanup / conflicts

- Accidental temporary branch files created while opening the draft PR were deleted before implementation continued.
- No other open AntiNuke/SpamGuard/security PR overlaps this task; PR #206 is the only matching open PR found.
- The GANG Nuker screenshot is treated only as defensive evidence of modular/persistent attack behavior. No tool-specific fingerprinting or offensive recreation is being added.

## Blockers / risks

- The Supabase migration must be applied in production for cross-host durable reputation. The local mirror is an outage fallback, not a substitute for the database migration.
- Discord hierarchy/permissions can still block ban/kick containment; those paths must remain visible in incidents and readiness checks.
- Discord guild owners cannot themselves be banned/contained by a bot.
- Structural server restore remains a separate recovery-system concern and is not part of this active task.

## Backlog

- Structural channel/role/server-state backup and restoration after destructive deletion remains separate from actor containment.

## Next step

Run exact-head CI, inspect every failure against this task's execution path, fix only task-related regressions, then perform final diff/cleanup/conflict review before changing PR readiness.

## Definition of done

- confirmed destructive actors persist by guild + Discord ID across restart/rejoin/reinvite;
- confirmed actors are ban-first contained with safe fallback;
- a previously hostile bot cannot regain an operational foothold merely because bot messages are normally excluded from SpamGuard;
- owner re-add of a known hostile bot is detected and the target remains contained;
- only hard identity proof can propagate hostile status to another human account;
- targeted tests, full Dank Shield CI, compile/static checks, and exact-head workflow checks are green;
- migration and startup wiring are reviewed;
- final diff contains no placeholders, temp files, unrelated changes, secrets, or conflict artifacts;
- PR metadata reflects actual validated state before readiness/merge.
