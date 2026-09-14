# ACTIVE TASK

## DS-SEC-045 — Legitimate self-action audit classification

**Status:** IMPLEMENTED — FINAL EXACT-HEAD VALIDATION REQUIRED BEFORE PROTECTED MERGE  
**Branch:** `fix/basic-verify-self-action-proof`  
**Base:** `5f51da0e338208538133f0b610c3e14a9c6f0bbc`  
**PR:** #214 — `Prevent legitimate member updates from triggering self-ejection`

## Outcome

Prevent legitimate Dank Shield member mutations from being mistaken for unverified bot-identity activity and triggering durable compromise quarantine / self-ejection, while preserving the fail-closed response for genuinely unmatched Dank Shield-attributed audit actions.

## Scope

- Discord `Modify Guild Member` PATCH classification used by AntiNuke self-action proof.
- Basic Verify and verification role swaps performed with `Member.edit(roles=...)`.
- Voice moves/disconnects performed through `Member.move_to()` / `Member.edit(voice_channel=...)`.
- Correct proof target scoping for role-update versus voice audit entries.
- Regression coverage against the composed production classifier.

Out of scope: changing verification authorization policy, AntiNuke thresholds, durable quarantine duration, credential policy, persistence schema, setup UX, unrelated security redesign, and unrelated cleanup.

## Findings / root cause

- Production pins `discord.py==2.7.1`.
- `discord.py` routes several member mutations through `PATCH /guilds/{guild_id}/members/{user_id}`.
- Basic Verify's successful role path calls `fresh.edit(roles=final_roles, reason="Dank Shield basic button verification")`.
- The base self-action request classifier treated that generic PATCH as `member_update`.
- Discord records a `roles` payload as `member_role_update`, so the one-time local authorization was stamped for the wrong action and could not be consumed by the resulting audit entry.
- `anti_nuke_zero_damage_runtime` deliberately treats an unmatched protected audit event attributed to Dank Shield as possible bot-identity compromise in Contain mode, persists quarantine, warns the owner, and self-ejects. That is why an ordinary Basic Verify click produced the owner DM and bot removal.
- The same generic PATCH route is used by `Member.move_to()` / voice-channel edits, while Discord records `member_move` or `member_disconnect` instead of `member_update`.
- Member move/disconnect audit entries do not expose a reliable affected-member target, so applying the normal member target-key requirement would create another false mismatch.
- `anti_nuke_audit_compat_runtime` is already the installed compatibility owner for Discord audit behavior that differs from the pinned discord.py surface. Extending that owner is smaller and cleaner than adding another startup guard or duplicate classifier.
- `anti_nuke_zero_damage_runtime` already owns protected-action registration for `member_move` and `member_disconnect`; the final repair does not duplicate that registration in the compatibility layer.

## Execution path before repair

1. A user presses the Basic Verify button.
2. `apply_basic_verification()` calls `Member.edit(roles=...)`.
3. discord.py sends `PATCH /guilds/{guild}/members/{member}` with a `roles` JSON field.
4. The self-action proof classified the request as `member_update` and stamped a one-time nonce into the Discord audit reason.
5. Discord emitted a `member_role_update` audit entry for the same legitimate request.
6. Action mismatch prevented the nonce authorization from being consumed.
7. The zero-damage unmatched-self-action handler treated the bot-attributed event as possible credential compromise, persisted quarantine, DMed the owner, and attempted self-ejection.

## Implemented

- Extended the existing audit-compat route classifier to inspect generic member PATCH payloads before the legacy generic fallback.
- `roles` payloads now authorize `member_role_update` and retain affected-member target scoping.
- `channel_id` payloads now authorize `member_move` or `member_disconnect`.
- Voice move/disconnect proof is scoped by action + guild + one-time nonce rather than a member target that Discord does not reliably provide for those audit entries.
- All other generic member PATCH requests continue through the existing `member_update` classifier unchanged.
- The existing zero-damage runtime remains the sole owner that adds move/disconnect to the protected self-action set.
- No new startup module, guard, monkey patch layer, verification mutation owner, persistence field, or fallback was introduced.
- Added focused regressions that compose the real self-action + zero-damage + audit-compat classifier and verify:
  - Basic Verify-style `roles` PATCH -> `member_role_update`;
  - the stamped Basic Verify authorization is consumed by the corresponding role-update audit event;
  - voice move -> `member_move` without an invalid target requirement;
  - voice disconnect -> `member_disconnect` without an invalid target requirement;
  - a targetless move audit consumes its authorization;
  - ordinary member PATCH fields still map to `member_update`.

## Validation / results

Functional implementation head: `1e449bfa6ab15043f03d564444c8e523881e4a6b`.

At the time this task record was written:

- Application Command Size Diagnostics — success on the functional head.
- Ticket Owner Emergency Override — success on the functional head.
- Dank Shield CI — running on the functional head.
  - `Claim-first ticket security` — success.
  - `Managed category SQL smoke test` — success.
  - `Python compile check` — compile and committed-diff whitespace steps passed; full unit/static lane still running.

This task record intentionally does **not** claim the repair complete. The new exact head created by this bookkeeping update must pass the complete protected validation set before the PR can leave draft state or merge.

## Final validation required

- Exact-head Dank Shield CI passes all jobs.
- Python lane passes committed diff whitespace, Python compilation, full unit suite, standalone tool checks, public setup/isolation audit, canonical public command-surface audit, startup-friction audit, public invite audit, setup safety audit, Dank Design audit, role-truth ownership audit, and event-boundary ownership audit.
- All companion workflows triggered for the exact head pass.
- PR remains mergeable with no unresolved review threads.
- Final diff contains only the existing audit-compat owner, the focused regression file, and this task record.
- No redundant move/disconnect protected-action registration remains in audit compat.
- No temporary/debug code, generated files, secrets, merge-conflict artifacts, or unrelated changes are present.
- Merge must use the exact validated head through the repository's normal protected-main workflow.
- Post-merge `main` CI must be checked before calling the repository work complete.
- Live Discord Basic Verify behavior remains a post-deploy runtime acceptance check because GitHub CI cannot click the production Discord component.

## Cleanup / conflicts

- An intermediate implementation briefly duplicated `member_move` / `member_disconnect` protected registration inside audit compat. Inspection showed `anti_nuke_zero_damage_runtime` already owns those actions, so the duplicate was removed before the final functional diff.
- The route-to-audit translation stays in the already-existing `anti_nuke_audit_compat_runtime`; no second compatibility module or new startup shim was created.
- Ordinary `member_update`, kick, role-specific PUT/DELETE, and existing voice-status compatibility behavior are preserved.
- No unrelated repository files are changed by the functional repair.

## Blockers / risks

- No known implementation blocker.
- Exact-head CI is still a hard merge gate.
- GitHub's connected integration cannot read the branch-protection endpoint (`403 Resource not accessible by integration`), so protection must not be inferred from that API; mergeability/check evidence and GitHub's protected merge behavior remain authoritative.
- Production Discord interaction has not yet been re-run after deployment; that is the remaining live acceptance check after code merge/deploy.

## Completed prior task

### Verification integrity audit repair

PR #213 merged as canonical `main` SHA `5f51da0e338208538133f0b610c3e14a9c6f0bbc`. That repair made Basic Verify authorization fail closed when Simple Verify is not actually enabled. DS-SEC-045 is a separate regression in the AntiNuke self-action proof revealed by a legitimate Basic Verify role mutation.

## Suspended task

### DS-SEC-044 — Hostile bot re-entry race and integration persistence

Suspended previously by explicit task switch. Remaining acceptance evidence is the hostile/GANG-Nuker re-entry runtime test after deployment; do not resume it during DS-SEC-045 without an explicit `FORCE SWITCH`.

## Next step

Validate the new exact head produced by this task-record commit. If every required and companion workflow is green, the diff/review checks remain clean, and PR #214 is still mergeable, mark it ready and merge the exact validated head through `main`. Then validate canonical `main` CI and leave the live Basic Verify click as the explicit post-deploy acceptance check.