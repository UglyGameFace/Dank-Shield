# ACTIVE TASK

## DS-AUD-INTERACTION-OWNERSHIP — Retire private View scheduler patch into native interaction ownership

**Status:** CLOSED — implementation merged and exact-SHA production acceptance passed
**Implementation PR:** #229
**Frozen implementation head:** `61f9f8a1c5040d9aadbd513b019f3a3af0b9364b`
**Implementation merge SHA:** `e7ac11cb7d457d53f61f0cf709c330219e6ff202`

## Outcome

The process-wide patch of private `discord.ui.View._scheduled_task` is retired. Dank Shield now relies on its explicit native interaction owner, `stoney_verify.interaction_guard`, plus feature-owned ticket mutation locks. Production boot no longer imports or expects the retired scheduler guard.

User-visible behavior preserved:

- duplicate in-flight native actions are rejected with a clear ephemeral busy response;
- safe defer/send/follow-up and structured interaction failure diagnostics remain intact;
- ticket close/reopen/delete mutation locks remain intact;
- menu-first feature callbacks and their existing destinations remain unchanged.

Removed architecture debt:

- production import of `startup_guards.interaction_action_lock_guard`;
- startup-diagnostics ownership requirement for that guard;
- historical startup inventory entry for that guard;
- the superseded guard module and its process-wide private discord.py mutation;
- the static source-shape test that required `_scheduled_task` patching.

Behavior-level coverage now proves the native owner rejects a duplicate and does not mutate `discord.ui.View._scheduled_task`.

## Root cause closed

The old startup guard globally replaced a private discord.py scheduler for every component callback even though its default mode was observe-only, repository configuration did not configure blocking targets, and its counters/patch markers had no live consumers outside its own source-shape test. Real duplicate protection already belonged to `stoney_verify.interaction_guard` and feature-owned locks.

## Exact-head validation

Frozen implementation head `61f9f8a1c5040d9aadbd513b019f3a3af0b9364b` passed all applicable workflows:

- Dank Shield CI #2134 / `34914271470` — SUCCESS, including the full unit suite and every standard audit lane.
- Ticket Owner Emergency Override #705 / `34914271443` — SUCCESS.
- Application Command Size Diagnostics #1142 / `34914271440` — SUCCESS.
- Dank Design Regression CI #392 / `34914271457` — SUCCESS.
- Ticket Category Menu Sanity #530 / `34914271445` — SUCCESS.
- Schema Authority SQL #56 / `34914271464` — SUCCESS.
- Profile Runtime Diagnostics #898 / `34914271444` — SUCCESS.

Before merge, PR #229 was mergeable, changed exactly the intended nine files, had no reviews or inline review comments, and canonical `main` had not drifted from its base.

## Post-merge production acceptance

Implementation PR #229 merged as `e7ac11cb7d457d53f61f0cf709c330219e6ff202`.

Acceptance on that exact canonical SHA passed:

- Dank Shield CI #2136 / `34915202887` — SUCCESS, including compile, full pytest, standalone tools, public setup/isolation, canonical command surface, startup friction, invite permissions, setup safety, Dank Design, role truth, event boundary, Claim-first security, and Managed SQL. Canonical CI completed `2026-09-15T01:07:35Z`.
- Ticket Category Menu Sanity #531 / `34915202861` — SUCCESS on the same SHA.
- Ticket Owner Emergency Override #707 / `34915202863` — SUCCESS on the same SHA.
- Schema Authority SQL #57 / `34915202864` — SUCCESS on the same SHA.
- Deploy Supabase migrations #32 / `34916007882` — SUCCESS on the same SHA; started `2026-09-15T01:07:37Z`, two seconds after canonical CI completed.
- Supabase validated-release checkout, immutable current-main verification, required secrets, CLI install, project link, migration status, preview, and apply all passed.
- Canonical `main` remained exactly `e7ac11cb7d457d53f61f0cf709c330219e6ff202` after promotion.

This finding must not be reopened without new regression evidence.

## Separate follow-up retained

`stoney_verify.interaction_guard` currently retains completed `asyncio.Lock` objects in `_ACTION_LOCKS`. That is real bounded-by-keyspace retention debt, but it is a separate concurrency/lifecycle finding because cleanup semantics differ when `reject_duplicate=False`. Do not smuggle that change into unrelated ownership work.

## Next master-audit candidate

### DS-AUD-GUILD-CONFIG-OWNERSHIP — Move public server-ID isolation and saved-ID validation into canonical config owners

Read-only evidence already bounds the next candidate:

- `startup_guards.public_server_env_id_guard` imports `stoney_verify.globals` and mutates deployment-level guild/channel/role/category IDs to zero after import in public mode.
- `startup_guards.guild_config_runtime_validator` replaces canonical `guild_config.discover_runtime_guild_config` at import time.
- canonical `guild_config.py` already owns public config isolation and runtime discovery, so validation/purge belongs there rather than in a startup monkey patch.
- `verification_member_role_fallback_guard` currently patches the validator guard's private `_apply_runtime_discovery`; Verified-as-member semantics must be preserved explicitly before the validator wrapper is retired.
- direct server-ID env consumers such as transcript/modlog helpers must be checked against the same public isolation policy so moving the globals guard does not create a fake sense of safety.
- `discord_api_safety` is deliberately excluded. Its audit-log/send/edit retry and AntiNuke behavior is broader security-sensitive ownership and must be handled as its own finding.

## Next step

Merge this bookkeeping-only closeout after exact-head CI proves `ACTIVE_TASK.md` is the only change. Then start `DS-AUD-GUILD-CONFIG-OWNERSHIP` from the resulting accepted canonical `main` SHA and preserve current public/beta behavior with behavioral tests before retiring either config startup guard.
