# ACTIVE TASK

## DS-FIX-ANTINUKE-AUTHORIZED-BOT-TRUST — Separate inviter trust from bot-target authorization

**Outcome:** AntiNuke now uses one canonical bot-add authorization policy, treats delegated inviter trust separately from pre-approved bot target IDs, keeps hostile reputation authoritative, exposes both concepts clearly in Protection Center, and has regressions preventing duplicate lockdown ownership from returning.

**Status:** MERGED + REPOSITORY VALIDATED. PR #236 is merged into canonical `main`. The only remaining unverified boundary is the external live Discloud deployment/runtime revision.

**Implementation branch:** `fix/antinuke-authorized-bot-trust`
**Validated final PR head:** `7e52aabc75e08fdcf3df2e1801209a620b2340e7`
**Merge SHA / canonical main:** `eb94e6e46d0c0cfdef2657eb302d0538a6e804fa`
**PR:** #236 — `Fix AntiNuke authorized bot trust ownership`

## Scope

- `stoney_verify/anti_nuke.py`
- `stoney_verify/anti_nuke_guardian_runtime.py`
- `stoney_verify/anti_nuke_lockdown_runtime.py`
- `stoney_verify/commands_ext/public_protection_center.py`
- focused AntiNuke bot-authorization, Protection Center, gateway/event, and ownership regression coverage
- this task record

Unrelated AntiNuke incidents, moderation behavior, setup, tickets, verification, and PR #235 were not mixed into this task.

## Findings / root cause

1. `antinuke_trusted_user_ids` represented trusted human/delegated actors but was also being used as a hidden bot-target allowlist in duplicate lockdown bot-add logic.
2. That overloaded meaning made owner-added trusted bots impossible to configure truthfully from Protection Center and left multiple runtimes claiming bot-add authorization ownership.
3. The correct split is `antinuke_trusted_user_ids` / `antinuke_trusted_role_ids` for delegated inviters and `antinuke_trusted_bot_ids` for bot targets explicitly pre-approved by the guild owner.
4. The physical guild owner cannot be contained by a Discord bot, so owner-added bots must be target-preapproved rather than treating owner status itself as authorization.
5. Active hostile reputation must outrank normal trust signals so a previously confirmed hostile bot cannot bypass enforcement through a normal trust list.
6. Lockdown tests still referenced the deleted `_patch_bot_add_guardian` / `_BOT_ADD_PATCH_FLAG` implementation after ownership moved into native AntiNuke + guardian.
7. The first exact-head CI run exposed two stale regression assertions expecting the retired incident title `🚨 AntiNuke Untrusted Bot Added`; runtime behavior was correct. Those assertions were updated to the canonical `🚨 AntiNuke Unauthorized Bot Added` wording and the full exact-head suite then passed.

## Execution path / authority

- Native `anti_nuke.bot_add_authorization()` is the canonical first-seen bot-add authorization policy.
- `anti_nuke_guardian_runtime._handle_bot_add()` delegates authorization decisions to that native policy and owns guardian rollback/incident handling.
- Owner-added targets require `antinuke_trusted_bot_ids` pre-approval unless Dank Shield itself initiated the addition.
- Explicitly trusted delegated users/roles may authorize an otherwise normal bot addition.
- Active hostile reputation overrides target or inviter trust.
- `anti_nuke_hostile_actor_runtime` remains the durable known-hostile re-entry layer and intentionally wraps guardian; it is not a competing allowlist owner.
- `anti_nuke_lockdown_runtime` no longer owns bot-add authorization.

## Changes

- added and normalized `antinuke_trusted_bot_ids`
- added native `bot_add_authorization()` with explicit owner/delegated/target/reputation semantics
- moved guardian bot-add decisions onto the native policy
- removed the duplicate lockdown bot-add policy patch
- Protection Center now shows trusted inviter counts separately from pre-approved bot counts
- owner-only Trust Lists modal now edits trusted user IDs, trusted role IDs, and pre-approved bot IDs as separate settings
- added canonical policy, guardian, and Protection Center regressions
- replaced obsolete lockdown bot-add behavior tests with an ownership regression preventing the retired patch from returning
- aligned gateway/event regressions with the canonical unauthorized-bot incident wording

## Validation / results

On exact final PR head `7e52aabc75e08fdcf3df2e1801209a620b2340e7`:

- Dank Shield CI #2159 — SUCCESS
  - committed diff whitespace — SUCCESS
  - Python compile — SUCCESS
  - full unit test suite — SUCCESS
  - standalone tool checks — SUCCESS
  - public setup/canonical command/startup-friction/invite/setup-safety/Dank Design/role-ownership/event-boundary audits — SUCCESS
  - Managed category SQL smoke test — SUCCESS
  - Claim-first ticket security — SUCCESS
- Dank Design Regression CI #410 — SUCCESS
- Application Command Size Diagnostics #1163 — SUCCESS
- Profile Runtime Diagnostics #917 — SUCCESS
- Ticket Owner Emergency Override #730 — SUCCESS
- final PR head remained mergeable and matched the expected SHA at merge
- no blocking reviews or review threads; only the expected Supabase no-directory-change informational comment
- final diff inspection found no conflict markers or unrelated implementation work

PR #236 was marked ready only after the exact-head checks passed and was merged with an expected-head SHA lock. Canonical `main` now points to merge SHA `eb94e6e46d0c0cfdef2657eb302d0538a6e804fa`.

## Cleanup / conflicts

- duplicate lockdown bot-add authorization implementation is retired instead of preserved as a compatibility shim
- stale lockdown tests depending on the retired implementation were removed/replaced
- no new config bucket, startup workaround, retry, fallback, or duplicate policy owner was added
- Protection Center changes are confined to AntiNuke trust-list wording, display, persistence, and its existing owner-only control surface
- PR #235 remains separate and untouched

## Remaining blocker / risk

GitHub proves the repository state and exact-head validation, but cannot prove which revision the live Discloud process is currently running. Live Discord behavior changes only after the validated canonical `main` revision is deployed/restarted on the host.

## Backlog

- PR #235 and its separate authorized-bot false-positive incident remain independent work.
- All unrelated setup/startup-guard, operation-queue, and other repository cleanup remains outside this task.

## Next step

Repository work for this task is closed after this bookkeeping record is merged. The only remaining boundary is live deployment/runtime verification of canonical `main` merge SHA `eb94e6e46d0c0cfdef2657eb302d0538a6e804fa`.
