# Active Task

## Active task / outcome

**DANK-SHIELD-VERIFICATION-FRAMEWORK-022 — issue #367 slice 3: Configurable Verification Framework**

Build a generic, versioned, per-guild verification-flow policy that composes existing verification runtimes instead of replacing their authority.

Production baseline: `main` = `fd1b95a6bc2128388e9cfb0d2bc9b09ad21bc4ab` (PR #372 merged and deployed).

Active branch: `feat/367-verification-framework-foundation`.

Active PR: not opened yet.

Issue #367 remains the umbrella epic. Slice 1 and Slice 2 are complete. This record covers Slice 3 only.

## Completed prior slice

Slice 2 — Generic Community & Pings Builder — is complete:
- PR #371 shipped the generic v2 Community & Pings model/runtime;
- PR #372 fixed the production-canary `/role` staff/member navigation ambiguity;
- final PR #372 head `20eba3e20b553bb0a01dd1fef9ac7cd4c76db6d7` passed all exact-head workflows;
- production merge `fd1b95a6bc2128388e9cfb0d2bc9b09ad21bc4ab` passed canonical Dank Shield CI #3307;
- chained Supabase deploy #167 passed on the exact merge SHA;
- Discloud deployment is green on the exact merge SHA;
- Android `/role` canary passed after deployment.

## Slice 3 architecture contract

Verification is not allowed to become a second role/access authority.

Canonical ownership:
- **Verification Flow Policy** owns the ordered requirements, preset, contexts, failure behavior, revision, and activation readiness.
- **role_truth** owns per-guild verified/pending/member role truth.
- **Basic Verify** owns its button/runtime action.
- **Voice Verify** owns voice verification execution.
- **ID/Web verification** remains protected/allowlisted and owns its ticket/evidence path.
- **Member Setup** owns Member Setup completion/revision truth.
- **Access Gate** owns effective protected-category/member access.
- **Ticket system** owns verification-ticket lifecycle.
- **Staff approval** continues through guarded existing staff verification actions.
- No opaque AI routing or AI verification decision.

The framework may coordinate those owners, but must not duplicate their authorization or completion engines.

## Root cause / current state

Verification is currently distributed across:
- `setup_service_state.py` feature switches;
- `setup_engine/verification_modes.py` legacy Basic/Voice/ID precedence;
- `verification_new/basic_verify.py`;
- Voice/ID verification services;
- `members_new/join_verification_service.py`;
- `role_truth.py`;
- verification tickets;
- startup guards;
- `public_verify_command_center.py`;
- Member Setup / Access Gate.

The existing mode model answers roughly “which verification service is enabled?” It does not model an ordered multi-step flow such as rules → account-age requirement → Member Setup → verify, nor separate new/returning/trusted/manual-review contexts.

Rules acknowledgement/version does not currently have a canonical durable owner. It must be added deliberately rather than inferred from “the rules channel exists.”

## Current implementation slice

**Slice 3 foundation — verification policy model only.**

Included now:
- `verification_flow_v2` authority key;
- bounded versioned flow model;
- ordered step definitions;
- product presets:
  - Simple;
  - Standard;
  - Guarded;
  - Approval;
  - Application;
  - Custom;
- supported context vocabulary:
  - new member;
  - returning member;
  - trusted/invited member;
  - manual review;
- configurable failure-action vocabulary:
  - wait;
  - limited access;
  - staff review;
  - ticket;
  - deny;
- legacy Simple/Voice/ID configuration adapter for inspection/migration;
- fail-closed malformed-v2 behavior;
- activation blockers so a preset cannot be considered runnable before every required step has a real runtime integration;
- focused regression tests.

This foundation does **not** change live verification behavior and exposes no incomplete member UI.

## Planned Slice 3 integration order

1. Policy foundation and presets.
2. Canonical Verification Framework manager with CAS-protected draft writes and Preview/Activation readiness.
3. Existing Simple Verify integration through the policy without changing its role-truth owner.
4. Member Setup, account-age, membership-delay, verification-ticket, Voice, and staff-approval step adapters.
5. Durable rules acknowledgement/version owner.
6. Standard / Guarded / Approval / Application activation paths.
7. Context-specific flow selection and configurable failure behavior through canonical Access Gate/ticket owners.
8. Migration/legacy compatibility, diagnostics, exact-head CI, deployment, and Android owner/member canaries.

One integration at a time. Do not stack Action & Reminder Center or Activity/Reverification work into this slice.

## Explicitly out of scope

- Action & Reminder Center;
- Member Activity & Reverification Lifecycle;
- quiet-server / active-VC behavior;
- Cheers timeout;
- Live Captions;
- AntiNuke;
- Minecraft;
- Unity;
- Idle Grow;
- unrelated backlog.

## Safety requirements

- no guild-specific hardcoding;
- no global guild sweeps for routine verification;
- no implicit downgrade from protected ID/Voice verification to Simple Verify;
- malformed v2 policy fails closed;
- presets are drafts by default;
- a preset cannot become active while required step integrations/settings are unavailable;
- no invented account-age or membership-delay threshold;
- all Discord role/access mutations retain live permission and hierarchy checks;
- stale admin writes must eventually use CAS at the policy key;
- migration must preserve existing working Simple/Voice/ID setups until an owner deliberately activates v2.

## Definition of Done

Slice 3 is complete only when:
- owners can configure and preview the supported presets/custom ordered flow;
- live activation is refused when a required step is unsupported or incomplete;
- Simple Verify remains a valid preset;
- Standard, Guarded, Approval, and Application flows use canonical step owners;
- rules acknowledgement has durable versioned truth;
- account-age and server-membership-delay checks are deterministic and owner-configured;
- Member Setup completion is consumed from Member Setup truth;
- staff approval/ticket/Voice/ID flows reuse existing guarded owners;
- context-specific policies behave deterministically;
- failure behavior routes through canonical Access Gate/ticket owners;
- legacy working setups do not silently change on deployment;
- exact-head CI is green;
- merge/deploy evidence is green;
- Android owner + member live canaries pass.

## Next step

Validate the policy foundation on its exact branch head. If green, open/prepare the focused foundation PR and perform diff hygiene. Only after that foundation is merged should the next Slice 3 integration add the canonical manager and CAS-protected draft persistence.
