# ACTIVE TASK

## DS-SEC-041 — Persistent hostile actor / AntiNuke re-entry containment

**Status:** IMPLEMENTATION COMPLETE / FINAL PR-HEAD RECHECK AFTER BOOKKEEPING UPDATE
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
- Member history existed, but AntiNuke did not consume it as a durable hostile-identity enforcement decision.
- The tested GANG Nuker flow could therefore keep its Discord bot process alive, be reinvited, reacquire guild access, and resume destructive actions unless the Discord identity itself remained contained.

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
  - SpamGuard-independent known-hostile message backstop;
  - hard-proof linked-alt inheritance;
  - ready-time reconciliation;
  - explicit `clear_hostile_reputation()` API for audited owner/admin integration.
- `supabase/migrations/20260913163000_hostile_actor_reputation.sql`
  - guild-scoped durable reputation table, active disposition, incident count, hard-link parent, explicit clear metadata, service-role-only access.
- `.github/workflows/hostile-actor-reputation-sql.yml`
  - PostgreSQL 16 migration idempotency, RLS/index, lifecycle, and service-role-only access smoke coverage.
- `main.py`
  - installs hostile reputation after AntiNuke gateway/finalizer/incident policy is finalized.
- `tests/test_antinuke_hostile_actor_runtime.py`
  - persistence, ban-first containment, fallback, permission readiness, exact rejoin, explicit-clear precedence, hard-proof alt inheritance, known-hostile bot message backstop, alert-only behavior, and owner re-add regressions.
- `tests/test_antinuke_finalizer_runtime.py`
  - asserts the hostile runtime is installed in the required startup order before app import.

## Validation / results

Runtime code head `1b946b6b622f2b1b45d1d967aa4376ed3dbe7369` passed the complete validation gate before this bookkeeping-only update:

- `Dank Shield CI` run #2014: **SUCCESS**.
  - committed diff whitespace: success;
  - Python compile: success;
  - full unit-test suite: success;
  - standalone tool checks: success;
  - setup/public-command/invite/setup-safety/design/role-truth/event-boundary audits: success;
  - managed-category SQL smoke: success;
  - claim-first ticket security: success.
- `Hostile Actor Reputation SQL` run #3: **SUCCESS**.
- `Dank Design Regression CI` run #297: **SUCCESS**.
- `Application Command Size Diagnostics` run #1044: **SUCCESS**.
- `Profile Runtime Diagnostics` run #803: **SUCCESS**.
- `Ticket Owner Emergency Override` run #585: **SUCCESS**.
- Local clone execution was unavailable in this session; GitHub Actions is the authoritative validation path used here.

This task-record update contains no runtime, migration, or test behavior change. The resulting PR head must still finish its own exact-head workflow rerun before PR #206 is marked ready.

## Cleanup / conflicts

- Compared base `91bce1e3a72b16e079f99febb6519b3144c36955` through validated code head `1b946b6b622f2b1b45d1d967aa4376ed3dbe7369`: branch was 16 commits ahead and 0 behind before this bookkeeping update.
- Runtime diff was reviewed after CI: seven task-owned files only; no unrelated product changes were introduced.
- No conflict markers, secrets, generated artifacts, debug bypasses, or placeholder implementation remain in the task diff.
- Accidental temporary branch files created while opening the draft PR were deleted before implementation continued.
- No other open AntiNuke/SpamGuard/security PR overlaps this task; PR #206 is the matching active implementation PR.
- The GANG Nuker screenshots are treated only as defensive evidence of persistent/modular attack behavior. No tool-specific fingerprinting or offensive recreation was added.

## Compatibility review

- Existing AntiNuke alert mode remains alert-only; durable reputation does not silently turn alert mode into containment.
- AntiNuke disabled state remains disabled; known-hostile presence is surfaced but not automatically removed.
- Canonical kick/role-strip containment remains the fallback when Discord rejects a ban.
- Guild owners and Dank Shield itself remain protected from self-containment paths.
- Human alt inheritance requires hard identity evidence and does not promote username/profile heuristics into punishment proof.
- The existing member lifecycle router does not remove the hostile-reputation join listener.
- Production already has a main-push Supabase migration workflow; after merge, the new migration is eligible for the normal dry-run + `db push` production path.

## Blockers / risks

- The Supabase migration must actually succeed in production after merge for cross-host durable reputation. The local mirror is outage continuity, not a substitute for the shared database.
- Discord hierarchy/permissions can still block ban/kick containment; readiness now requires Ban Members for contain mode and incident output retains fallback/blocker visibility.
- Discord guild owners cannot themselves be banned/contained by a bot.
- No repository test can substitute for the final live Discord adversarial retest against the real GANG Nuker account after deployment.
- Structural server restore remains a separate recovery-system concern and is not part of this active task.

## Backlog

- Structural channel/role/server-state backup and restoration after destructive deletion remains separate from actor containment.

## Next step

Verify the workflow rerun on the bookkeeping-only PR head. If every required workflow remains green, update PR #206 metadata and mark it ready for review. Do not merge until the owner explicitly approves the merge.

## Definition of done

- confirmed destructive actors persist by guild + Discord ID across restart/rejoin/reinvite;
- confirmed actors are ban-first contained with safe fallback;
- a previously hostile bot cannot regain an operational foothold merely because bot messages are normally excluded from SpamGuard;
- owner re-add of a known hostile bot is detected and the target remains contained;
- only hard identity proof can propagate hostile status to another human account;
- targeted tests, full Dank Shield CI, compile/static checks, and exact-head workflow checks are green;
- migration and startup wiring are reviewed;
- final diff contains no placeholders, temp files, unrelated changes, secrets, or conflict artifacts;
- PR metadata reflects actual validated state before readiness/merge;
- production migration/deploy and live adversarial retest remain explicit post-merge acceptance checks rather than being falsely claimed from CI alone.
