# ACTIVE TASK

## Verification integrity audit repair

**Status:** IMPLEMENTED — FINAL EXACT-HEAD VALIDATION REQUIRED BEFORE PROTECTED MERGE
**Branch:** `fix/verification-integrity-mode-authorization`
**Base:** `9112528e42e77ec348abe69d9207e37a64294380`
**PR:** #213 — `Make verification mode authorization fail closed`

## Outcome

Ensure every Basic Verify entry point uses one canonical per-guild authorization policy so stale Discord buttons, persistent views, fallback listeners, staff tools, or legacy configuration cannot grant access when Simple Verify is not actually enabled.

## Scope

- Canonical Basic / Voice / ID / disabled verification-mode resolution.
- Authorization at the Basic role-mutation boundary.
- Basic verification panel posting authorization.
- Backward compatibility for legitimate legacy Basic and Voice verification configurations.
- Behavioral regression coverage for specialized, conflicting, and stale verification states.

AntiNuke, release governance, tickets unrelated to verification, setup redesign outside verification mode truth, and unrelated cleanup are out of scope.

## Findings / root cause

- `app.py` installs the persistent Basic Verify runtime globally before Discord login so old posted buttons remain dispatchable after restart.
- `apply_basic_verification()` previously refreshed guild configuration only to resolve roles; it did not verify that Simple Verify remained authorized before adding access roles and removing Unverified.
- A stale Basic Verify message could therefore reach the role-grant path after a guild changed to Voice Verify, protected ID/Web verification, or disabled Simple Verify.
- `setup_engine.verification_modes.effective_verification_mode()` previously represented only `id_verify` versus `basic_button`, so Voice-only and disabled verification state collapsed to Basic.
- Canonical `SetupServiceState` already distinguishes `simple_verify`, `voice_verify`, and `id_verify`, including intentional custom Simple + Voice configurations.
- Historical verification routing also recognized legacy Voice mode aliases such as `voice_check`, `voice`, `vc`, `vc_verify`, `voice_verify`, and `id_voice_check`. Without carrying those aliases into the canonical resolver, a legacy Voice row with aggregate `verification_enabled=true` could still be mistaken for Basic.
- The richer DS-SETUP-020 compatibility layer is loaded through the dormant startup-guard catalog, so it cannot be treated as production authorization ownership. `CLAUDE.md` explicitly documents that the startup-guard loader is not called at boot.
- Existing setup tests already assert that specialized verification must not fake Simple Verify.

## Execution path before repair

1. `app.py` globally registers `BasicVerifyView` and the fallback Basic interaction listener.
2. An old or current Basic Verify component with the stable custom ID dispatches after restart.
3. `apply_basic_verification()` loads guild config and resolves verification roles.
4. Without checking whether Simple Verify is still enabled, it adds Verified/member access and removes Unverified.
5. Upstream callers could add their own mode checks, but the actual role mutation boundary itself was not fail closed.

## Implemented

- Added `basic_verify_allowed_for_guild()` as the canonical Basic authorization policy.
- Any persisted ID/Web request blocks Basic Verify so an unavailable protected ID flow cannot silently downgrade into one-click access.
- Protected allowlisted ID/Web verification resolves to `id_verify`.
- Added canonical current + historical Voice recognition through service flags and legacy mode aliases.
- Canonical setup-service state determines whether Simple Verify is enabled.
- Voice-only state resolves to `voice_verify`; no authorized Basic/ID/Voice service resolves to `disabled`.
- Non-allowlisted ID + Voice preserves the valid Voice service while still blocking Basic and ID panel access.
- Intentional custom Simple + Voice configurations still resolve to `basic_button` when Simple Verify is explicitly enabled.
- Legitimate legacy Basic configs remain supported through historical aggregate `verification_enabled=true` and Basic mode aliases when they do not conflict with specialized state.
- Legacy Voice mode-only rows cannot fall through an aggregate `verification_enabled=true` value into Basic Verify.
- Explicit Basic disable beats stale legacy Basic mode strings.
- `apply_basic_verification()` blocks unauthorized Basic Verify before snapshot/role resolution or any Discord role mutation.
- `post_basic_verify_panel()` refuses to post/refresh a Basic panel when Simple Verify is unauthorized.
- `/verify panel` explains the canonical disabled reason instead of presenting a non-working or unsafe Basic panel.
- Added focused behavioral regressions for Basic, Voice-only, disabled, Simple + Voice, legacy Basic, legacy Voice, explicit-disable precedence, ID precedence, non-allowlisted ID, conflicting stale flags, stale Basic buttons, and disabled panel posting.
- Updated the existing non-allowlisted ID + Voice regression to require Basic denial while preserving `voice_verify`.

## Validation / results

Functional exact head `82dcf85c69af7704593815a1e242a3d84a79c9ba` passed the complete PR validation set before this bookkeeping-only update:

- Dank Shield CI — success.
  - `Python compile check` — success.
  - committed diff whitespace — success.
  - Python compile — success.
  - full unit test suite — success.
  - standalone tool checks — success.
  - public setup text/isolation audit — success.
  - canonical public command-surface audit — success.
  - command-surface/startup-friction audit — success.
  - public invite audit — success.
  - setup safety audit — success.
  - Dank Design audit — success.
  - role-truth ownership audit — success.
  - event-boundary ownership audit — success.
  - `Claim-first ticket security` — success.
  - `Managed category SQL smoke test` — success.
- Application Command Size Diagnostics — success.
- Dank Design Regression CI — success.
- Ticket Owner Emergency Override — success.
- Profile Runtime Diagnostics — success.

That evidence validates the functional code but is intentionally superseded for merge evidence by this task-record-only commit. The final exact head must pass the same protected PR checks before merge.

## Final validation required

- Final exact-head Dank Shield CI passes all three required jobs.
- Python lane again passes the unit suite, standalone tools, and every public/static audit.
- All relevant companion workflows pass.
- Final diff remains exactly the six verification-integrity/task-record files already reviewed.
- No new startup guard, monkey patch, root runtime patch, schema change, AntiNuke change, or second verification-role mutation owner is introduced.
- `main` remains protected before merge.

## Cleanup / conflicts

The role mutation itself is now the fail-closed authority. Existing upstream staff-browser preflight still uses the canonical effective mode for UX and does not bypass the mutation boundary. Rewriting that large member-browser module would widen this security repair without improving the actual authorization boundary, so it remains unchanged.

## Blockers / risks

No known product blocker. Functional code validation is green. The only remaining gate is final exact-head CI after this documentation-only bookkeeping update, followed by protected merge and post-merge `main` validation.

## Completed prior task

### DS-AUD-009 — Release governance and production promotion safety

Completed and merged as PR #212. Canonical merge SHA `9112528e42e77ec348abe69d9207e37a64294380` passed post-merge Dank Shield CI before the gated Supabase production promotion ran successfully. `main` is protected with required pull-request checks.

## Suspended task

### DS-SEC-044 — Hostile bot re-entry race and integration persistence

Suspended by explicit FORCE SWITCH after PR #211 merged and exact-head CI passed. Remaining acceptance evidence: after deployment, repeat the hostile/GANG-Nuker re-entry test and confirm no destructive action lands before the hostile identity/integration is removed.

## Next step

Validate the new exact head created by this bookkeeping-only update. If all required and companion workflows are green and the six-file diff remains clean, mark PR #213 ready and merge it through protected `main` using the exact validated head SHA. Then validate the resulting canonical `main` CI and gated production-promotion run.
