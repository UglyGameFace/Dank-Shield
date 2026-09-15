# ACTIVE TASK

## DS-FIX-ANTINUKE-AUTHORIZED-BOT-TRUST — Separate inviter trust from bot-target authorization

**Outcome:** AntiNuke uses one canonical bot-add authorization policy, treats delegated inviter trust separately from pre-approved bot target IDs, keeps hostile reputation authoritative, exposes both concepts clearly in Protection Center, and has regressions that prevent duplicate lockdown ownership from returning.

**Status:** IMPLEMENTATION COMPLETE; EXACT-HEAD VALIDATION PENDING. Do not call this merge-ready until the final branch head passes required CI and the final diff/PR review is clean.

**Implementation branch:** `fix/antinuke-authorized-bot-trust`
**PR:** #236 — `Fix AntiNuke authorized bot trust ownership` (draft during validation)

## Scope

- `stoney_verify/anti_nuke.py`
- `stoney_verify/anti_nuke_guardian_runtime.py`
- `stoney_verify/anti_nuke_lockdown_runtime.py`
- `stoney_verify/commands_ext/public_protection_center.py`
- focused AntiNuke bot-authorization, Protection Center, and ownership regression coverage
- this task record

Unrelated AntiNuke incidents, moderation behavior, setup, tickets, verification, and other backlog work are excluded.

## Findings / root cause

1. `antinuke_trusted_user_ids` represented trusted human/delegated actors but was also being used as a hidden bot-target allowlist in duplicate lockdown bot-add logic.
2. That overloaded meaning made owner-added trusted bots impossible to configure truthfully from Protection Center and let multiple runtimes claim bot-add authorization ownership.
3. The correct split is `antinuke_trusted_user_ids` / `antinuke_trusted_role_ids` for delegated inviters and `antinuke_trusted_bot_ids` for bot targets explicitly pre-approved by the guild owner.
4. The physical guild owner cannot be contained by a Discord bot, so owner-added bots must be target-preapproved instead of treating owner status itself as authorization.
5. Active hostile reputation must outrank all normal trust signals so a previously confirmed hostile bot cannot bypass enforcement by appearing on a normal trust list.
6. Lockdown tests still referenced the deleted `_patch_bot_add_guardian` / `_BOT_ADD_PATCH_FLAG` implementation after ownership moved into native AntiNuke + guardian.

## Execution path / authority

- Native `anti_nuke.bot_add_authorization()` is the canonical first-seen bot-add authorization policy.
- `anti_nuke_guardian_runtime._handle_bot_add()` delegates authorization decisions to that native policy and owns guardian rollback/incident handling.
- Owner-added targets require `antinuke_trusted_bot_ids` pre-approval unless Dank Shield itself initiated the addition.
- Explicitly trusted delegated users/roles may authorize an otherwise normal bot addition.
- Active hostile reputation overrides target or inviter trust.
- `anti_nuke_hostile_actor_runtime` remains the durable known-hostile re-entry layer and wraps guardian intentionally; it is not a replacement allowlist owner.
- `anti_nuke_lockdown_runtime` no longer owns bot-add authorization.

## Changes

- added and normalized `antinuke_trusted_bot_ids`
- added native `bot_add_authorization()` with explicit owner/delegated/target/reputation semantics
- moved guardian bot-add decisions onto the native policy
- removed the duplicate lockdown bot-add policy patch
- Protection Center now shows trusted inviter counts separately from pre-approved bot counts
- owner-only Trust Lists modal now edits trusted user IDs, trusted role IDs, and pre-approved bot IDs as separate settings
- added focused canonical-policy and guardian regressions
- added Protection Center regressions proving the three trust lists persist separately and the bot-target field is exposed
- replaced obsolete lockdown bot-add behavior tests with an ownership regression that prevents the retired patch from returning

## Validation / results

Repository implementation inspection is complete. Local checkout/test execution is unavailable in this tool environment because direct GitHub network access from the execution container cannot resolve `github.com`; validation must therefore come from repository CI.

Pending on the final exact branch head:

- required GitHub workflow runs
- targeted AntiNuke regression results
- full repository CI / compile / static lanes required by the repo
- final diff and changed-file inspection
- PR review/thread status
- merge-base / branch freshness check against canonical `main`

No completion or merge-readiness claim is valid until those checks pass.

## Cleanup / conflicts

- duplicate lockdown bot-add authorization implementation is retired instead of preserved as a compatibility shim
- stale lockdown tests that depended on the retired implementation are removed/replaced
- no new config bucket, startup workaround, retry, fallback, or monkey patch was added for this behavior
- unrelated open PR #235 (`fix/authorized-bot-ban-false-positive`) is intentionally not mixed into this task
- Protection Center change is confined to AntiNuke trust-list wording, display, persistence, and its existing owner-only control surface

## Blockers / risks

- Exact-head CI has not run on the final task head yet.
- The branch must be compared against current `main` before merge-readiness because canonical main may have advanced since this branch was created.
- Live Discord behavior cannot be proven until a validated revision is deployed; that is an external runtime boundary after repository validation.

## Backlog

- PR #235 and its separate authorized-bot false-positive incident remain independent work.
- All unrelated setup/startup-guard, operation-queue, and other repository cleanup remains outside this task.

## Next step

Freeze PR #236's exact head, run/inspect all required CI on that SHA, resolve only failures belonging to this task, then perform final diff/review cleanup before marking it merge-ready.
