# ACTIVE TASK

## DS-ANTINUKE-001 — Native AntiNuke destructive-action containment

**Status:** IN PROGRESS — PRODUCTION PERMISSION ALIGNMENT + FINAL VALIDATION PENDING
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
- Public install must not require Discord Administrator permission.

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
- live runtime registration without guards or monkey patches;
- least-privilege public invite guidance including View Audit Log.

## Root Cause / Gap

Dank Shield has SpamGuard, RaidGuard, Automod, moderation logging, tickets, and verification, but no native unified AntiNuke engine. Existing modlog listeners observe channel/role/server changes but do not stop an attributed actor from continuing a destructive burst.

AntiNuke also requires reliable `View Audit Log` access for actor attribution. The engine fails safe when attribution is unavailable instead of guessing.

A separate production-readiness issue was found during this task: the dashboard server-install route currently falls back to Discord permission integer `8` (Administrator). That conflicts with Dank Shield's public least-privilege policy and must be replaced with an explicit non-Administrator permission set before this task is production-ready.

## Implementation So Far

- Added `stoney_verify/anti_nuke.py` as the native AntiNuke owner.
- Added per-guild settings using the existing flexible `guild_configs.settings` storage; no schema migration or duplicate table.
- Product defaults are intentionally **opt-in**:
  - disabled until the guild owner explicitly enables AntiNuke;
  - `contain` is the selected response mode once enabled;
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
- Wired the native engine through `public_protection_center.py`, which is already a guaranteed public runtime module; no `main.py`, `app.py`, startup guard, or monkey patch change was needed.
- Added Protection Center AntiNuke status, permission health, ON/OFF toggle, Alert/Contain mode control, trusted user/role IDs, and threshold editor.
- AntiNuke refuses to enable when required permissions are missing and clearly reports `View Audit Log`; Contain mode also requires `Manage Roles`.
- Added `View Audit Log` to Dank Shield public permission guidance and its permission audit.
- Added behavioral tests for defaults, bounds, trust policy, permission escalation, window thresholds, containment, unattributed fail-safe behavior, and listener registration.
- Fixed the behavior tests after the opt-in default change so active-engine tests explicitly enable AntiNuke instead of depending on the product default.

## Validation

Completed:

- Core AntiNuke branch passed Dank Shield CI run 469 before UI wiring.
- Live-wiring/UI head initially failed CI run 470 because two behavioral tests still assumed the old enabled-by-default policy.
- Tests were corrected to explicitly enable AntiNuke for active-engine scenarios; the safer opt-in product default was preserved.
- Exact corrected head `dddf3fe1fbfe037a1540510204331805ef1dda54` passed Dank Shield CI run 471.
- Python compile, full `pytest tests/`, standalone tools, public setup/isolation audit, canonical command-surface audit, startup-friction audit, public invite-permission audit, setup-safety audit, Dank Design audit, role-truth audit, and event-boundary audit all passed in run 471.
- Termux local targeted tests/import smoke could not run because that local Python environment imports a broken/incompatible `supabase` package (`cannot import name 'Client' from 'supabase'`). This is not reproduced in clean GitHub CI and is not being worked around in production code.

Pending:

- Replace the dashboard's Administrator (`permissions=8`) install fallback with an explicit least-privilege Dank Shield permission bitfield that includes `View Audit Log` and preserves supported moderation/ticket/verification behavior.
- Final PR diff/conflict inspection after permission alignment.
- Final review-thread inspection.
- Final exact-head GitHub Actions after any remaining Dank Shield repo changes.

## Cleanup / Conflict Inspection

- No startup guard added.
- No monkey patch added.
- No new runtime patch file added.
- No Supabase migration required; AntiNuke settings use existing JSON config storage.
- Existing SpamGuard/RaidGuard engines are not modified or duplicated.
- Existing modlog coverage remains observation/logging; AntiNuke owns containment decisions.
- `main.py`, `app.py`, `globals.py`, `guild_config.py`, `sitecustomize.py`, and `usercustomize.py` were not changed.

## Blockers

- Dashboard public-install fallback still uses Administrator permission integer `8`; this must be removed before production-ready signoff.

## Backlog After This Task

1. Configuration backup + version history.
2. Reusable configuration templates.
3. Multi-server configuration sync.
4. Cross-server analytics.
5. Global moderation / shared security profiles.

## Definition of Done

- [x] Native AntiNuke owner exists outside startup guards.
- [x] Per-guild settings and safe opt-in defaults exist without a schema migration.
- [x] Unattributed actions never trigger containment.
- [x] Guild owner and configured trusted actors are exempt.
- [x] Mass channel/role deletes and mass ban/kick bursts are tracked per actor.
- [x] Webhook creation floods are tracked per actor.
- [x] Dangerous permission escalation/grants can be rolled back when manageable.
- [x] Containment removes only manageable dangerous actor roles.
- [x] Behavioral tests cover core policy and fail-safe behavior.
- [x] Engine is loaded by the guaranteed live runtime without a guard/patch.
- [x] Protection Center exposes clear AntiNuke status/configuration.
- [x] Missing View Audit Log is clearly reported as a feature blocker.
- [x] Targeted/core behavior is green in clean CI.
- [x] Full regression/compile/audits pass on corrected AntiNuke head.
- [ ] Dashboard install flow no longer defaults to Administrator.
- [ ] Final diff contains only task-related permanent code/tests/task record.
- [ ] Final review-thread inspection is clean.
- [ ] Final exact-head GitHub Actions pass after the last Dank Shield repo change, if any.
- [ ] Merge/deploy requires explicit user approval.
