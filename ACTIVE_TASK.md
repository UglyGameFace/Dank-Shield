# ACTIVE TASK

## DS-ANTINUKE-001 — Native AntiNuke destructive-action containment

**Status:** IN PROGRESS — LIVE WIRING / UI / VALIDATION PENDING
**Branch:** `feature/native-antinuke-core`
**Base:** current `main` after merged PR #105

## Single Active Task Lock

Do not switch to unrelated implementation work until this task reaches Definition of Done or the owner explicitly force-switches tasks.

## Hard Architecture Rules

- No monkey patches.
- No startup guards as the implementation path.
- Do not reactivate the dormant startup-guard loader.
- No new `*_new` parallel tree.
- New AntiNuke behavior belongs in its native engine and existing Protection Center/event runtime.
- Per-guild DB config is authoritative; no guild-specific `.env` IDs.
- Never punish an unattributed actor. Audit-log attribution must succeed first.
- Never contain the guild owner.
- Behavioral tests only; no new source-shape/static tests.

## Scope

Build the first production AntiNuke layer for high-confidence destructive actions:

- mass channel deletion;
- mass role deletion;
- mass bans;
- mass kicks;
- webhook creation floods;
- dangerous role-permission escalation;
- dangerous role grants to members;
- trusted-user and trusted-role exemptions;
- alert-only and contain modes;
- containment by removing only manageable dangerous roles from the attributed actor;
- rollback of manageable dangerous permission escalation/grants;
- modlog incident reporting;
- Protection Center status/config surface;
- live runtime registration without guards or monkey patches.

## Root Cause / Gap

Dank Shield has SpamGuard, RaidGuard, Automod, moderation logging, tickets, and verification, but no native unified AntiNuke engine. Existing modlog listeners observe channel/role/server changes but do not stop an attributed actor from continuing a destructive burst.

AntiNuke also requires reliable `View Audit Log` access for actor attribution. The engine must fail safe when attribution is unavailable instead of guessing.

## Implementation So Far

- Added `stoney_verify/anti_nuke.py` as the native AntiNuke owner.
- Added per-guild settings using the existing flexible `guild_configs.settings` storage; no schema migration or duplicate table.
- Product defaults currently use:
  - enabled;
  - `contain` mode;
  - 15-second detection window;
  - 3 channel deletes;
  - 3 role deletes;
  - 5 bans;
  - 5 kicks;
  - 3 webhook creates;
  - immediate dangerous permission-escalation protection.
- Added audit-log actor attribution with freshness checks, bounded retries, and audit-entry dedupe.
- Added trusted actor rules for guild owner, Dank Shield itself, explicit trusted users, and explicit trusted roles.
- Added per-actor/action sliding windows and trigger cooldowns.
- Added containment that removes only dangerous roles Dank Shield can actually manage.
- Added role-permission rollback and dangerous member-role-grant rollback when containment mode is active.
- Added AntiNuke incidents to the existing per-guild modlog.
- Added behavioral tests for defaults, bounds, trust policy, permission escalation, window thresholds, containment, unattributed fail-safe behavior, and listener registration.

## Validation

Pending:

- Wire the native engine into a guaranteed live non-guard runtime path.
- Add Protection Center AntiNuke status and controls.
- Add/verify `View Audit Log` permission guidance without requiring Administrator.
- Targeted AntiNuke tests.
- Python compile validation.
- Full `pytest tests/` regression suite.
- Standalone tools/audits required by CI.
- Final diff/conflict inspection.
- GitHub Actions on the final PR head.

## Cleanup / Conflict Inspection

- No startup guard added.
- No monkey patch added.
- No new runtime patch file added.
- No Supabase migration required; AntiNuke settings use existing JSON config storage.
- Existing SpamGuard/RaidGuard engines are not modified or duplicated.
- Existing modlog coverage remains observation/logging; AntiNuke owns containment decisions.

## Blockers

None known. Live wiring, UI, permission guidance, and validation remain.

## Backlog After This Task

1. Configuration backup + version history.
2. Reusable configuration templates.
3. Multi-server configuration sync.
4. Cross-server analytics.
5. Global moderation / shared security profiles.

## Definition of Done

- [x] Native AntiNuke owner exists outside startup guards.
- [x] Per-guild settings and safe defaults exist without a schema migration.
- [x] Unattributed actions never trigger containment.
- [x] Guild owner and configured trusted actors are exempt.
- [x] Mass channel/role deletes and mass ban/kick bursts are tracked per actor.
- [x] Webhook creation floods are tracked per actor.
- [x] Dangerous permission escalation/grants can be rolled back when manageable.
- [x] Containment removes only manageable dangerous actor roles.
- [x] Behavioral tests cover core policy and fail-safe behavior.
- [ ] Engine is loaded by the guaranteed live runtime without a guard/patch.
- [ ] Protection Center exposes clear AntiNuke status/configuration.
- [ ] Missing View Audit Log is clearly reported as a feature blocker.
- [ ] Targeted tests pass.
- [ ] Full regression/compile/audits pass.
- [ ] Final diff contains only task-related permanent code/tests/task record.
- [ ] Final-head GitHub Actions pass.
- [ ] Merge/deploy requires explicit user approval.
