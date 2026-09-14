# ACTIVE TASK

## Verification integrity audit repair

**Status:** IMPLEMENTED ON BRANCH — FOCUSED VALIDATION PENDING
**Branch:** `fix/verification-integrity-mode-authorization`
**Base:** `9112528e42e77ec348abe69d9207e37a64294380`
**PR:** not opened yet

## Outcome

Ensure every Basic Verify entry point uses one canonical per-guild authorization policy so stale Discord buttons, persistent views, fallback listeners, staff tools, or legacy configuration cannot grant access when Simple Verify is not actually enabled.

## Scope

- Canonical Basic / Voice / ID / disabled verification-mode resolution.
- Authorization at the Basic role-mutation boundary.
- Basic verification panel posting authorization.
- Backward compatibility for legitimate legacy Basic Verify configurations.
- Behavioral regression coverage for specialized and stale verification states.

AntiNuke, release governance, tickets unrelated to verification, setup redesign outside verification mode truth, and unrelated cleanup are out of scope.

## Findings / root cause

- `app.py` installs the persistent Basic Verify runtime globally before Discord login so old posted buttons remain dispatchable after restart.
- `apply_basic_verification()` previously refreshed guild configuration only to resolve roles; it did not verify that Simple Verify remained authorized before adding access roles and removing Unverified.
- A stale Basic Verify message could therefore reach the role-grant path after a guild changed to Voice Verify, protected ID/Web verification, or disabled Simple Verify.
- `setup_engine.verification_modes.effective_verification_mode()` previously represented only `id_verify` versus `basic_button`, so Voice-only and disabled verification state collapsed to Basic.
- Canonical `SetupServiceState` already distinguishes `simple_verify`, `voice_verify`, and `id_verify`, including intentional custom Simple + Voice configurations.
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
- Protected allowlisted ID/Web verification takes precedence over Basic Verify.
- Canonical setup-service state now determines whether Simple Verify is enabled.
- Voice-only state resolves to `voice_verify`; no authorized Basic/ID/Voice service resolves to `disabled`.
- Intentional custom Simple + Voice configurations still resolve to `basic_button` because Simple Verify is explicitly enabled.
- Legitimate legacy Basic configs remain supported through historical aggregate `verification_enabled=true` and Basic mode aliases when they do not conflict with newer specialized/explicit state.
- Explicit Basic disable beats stale legacy Basic mode strings.
- Non-allowlisted ID requests no longer silently downgrade to one-click Basic Verify.
- `apply_basic_verification()` now blocks unauthorized Basic Verify before snapshot/role resolution or any Discord role mutation.
- `post_basic_verify_panel()` now refuses to post/refresh a Basic panel when Simple Verify is unauthorized.
- `/verify panel` explains the canonical disabled reason instead of presenting a non-working or unsafe Basic panel.
- Added focused behavioral regressions for Basic, Voice-only, disabled, Simple + Voice, legacy Basic, explicit-disable precedence, ID precedence, non-allowlisted ID, stale Basic buttons, and disabled panel posting.
- Updated the older non-allowlisted ID regression to require fail-closed `disabled` state rather than silent Basic downgrade.

## Validation / results

Local container execution is unavailable because the execution environment cannot resolve GitHub to clone the repository. Validation must therefore proceed through the repository's protected PR CI after final pre-CI review.

## Validation required

- Focused verification authorization and ID ticket tests pass.
- Existing Basic Verify restart/persistent-view tests pass.
- Existing setup service-state, Voice Verify, member-browser, and ID verification regressions pass.
- Python compile/static checks pass.
- Full exact-head Dank Shield CI passes.
- Relevant companion workflows pass.
- Final diff contains only verification-integrity work plus this task record.
- No new startup guard, monkey patch, root runtime patch, or second verification-role mutation owner is introduced.

## Cleanup / conflicts

The role mutation itself is now the fail-closed authority. Existing upstream staff-browser preflight still uses the canonical effective mode for UX and does not bypass the mutation boundary; review will determine whether removing that redundant preflight is worth widening this repair.

## Blockers / risks

No known product blocker. The remaining risk is regression compatibility with older verification-mode tests and historical setup shapes; exact-head CI is required before merge readiness.

## Completed prior task

### DS-AUD-009 — Release governance and production promotion safety

Completed and merged as PR #212. Canonical merge SHA `9112528e42e77ec348abe69d9207e37a64294380` passed post-merge Dank Shield CI before the gated Supabase production promotion ran successfully. `main` is protected with required pull-request checks.

## Suspended task

### DS-SEC-044 — Hostile bot re-entry race and integration persistence

Suspended by explicit FORCE SWITCH after PR #211 merged and exact-head CI passed. Remaining acceptance evidence: after deployment, repeat the hostile/GANG-Nuker re-entry test and confirm no destructive action lands before the hostile identity/integration is removed.

## Next step

Open a draft PR to obtain focused/full CI evidence, inspect any failure at the exact failing step, correct only root-cause regressions, then perform final exact-head diff and workflow validation before merge readiness.
