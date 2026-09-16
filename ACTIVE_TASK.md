# ACTIVE TASK

## DS-AUD-SETUP-VERIFY-CONTEXTUAL-REPAIR — Adopt same-screen repair across Setup and Verification

**Outcome target:** Extend the merged contextual self-repair contract from PR #244 into the canonical Setup and Verification surfaces that already diagnose permission/access failures. A user who is shown a repairable Setup or Verification access problem should be able to repair every safe issue from that same workflow, re-check immediately, and receive precise manual instructions only for conditions Discord or safety policy prevents Dank Shield from fixing automatically.

**Status:** IMPLEMENTATION / VALIDATION

**Branch:** `audit/setup-verification-contextual-repair`
**Base main:** `3f99bfa4710f3d2cbc67ffe6e11217760084256d`
**Previous integrated task:** PR #244 merged at `3f99bfa4710f3d2cbc67ffe6e11217760084256d`

## Scope

- canonical `/dank setup` Setup Check review surface
- canonical Verification Channels picker surface
- reuse of merged `contextual_permission_repair` and `permission_repair_core` ownership rather than new mutation paths
- same-screen `Fix Issues` / `Access Healthy` / `Manual Fix Needed` behavior when current guild/config context is available
- one-action repair of all safe configured targets owned by the current menu, followed by exact re-audit
- preservation of member visibility, role hierarchy, explicit-deny, and Administrator safety boundaries
- focused regression coverage and task/PR bookkeeping

Out of scope unless tracing proves a direct dependency:
- Tickets, Modlog/Member Logs, Profile/Self Roles, Protection, Embed/Status feature-specific adoption beyond targets already surfaced by Setup Check
- changing verification policy semantics or role assignment logic
- making private/staff categories public automatically
- granting Administrator or moving roles automatically
- broad cleanup of unrelated startup guards

## Findings

1. PR #244 merged the shared contextual repair contract and verified its first acceptance surface on `/dank welcome join-leave`.
2. The normal public Setup Check is `public_setup_recommend._open_health_check()`, which renders `SetupReviewView`. Before this task it could diagnose configuration/access trouble but offered only Continue Setup / Test / navigation, not a same-screen repair action.
3. The normal public Verification Channels surface is `public_setup_solid.VerificationChannelsPickerView`. It already saves the exact configured verification channel IDs, so it has enough target context for safe bot-only access repair.
4. Existing `setup_permission_repair_services` is broader and can mutate member/role overwrite plans. It is deliberately not used by the new contextual button because the merged contract from #244 is narrower: repair only Dank Shield's own missing target permissions.
5. Existing VC/setup compatibility guards remain separate legacy/local paths. This task does not delete them without proof they are unreachable or redundant; the normal public Setup Check and Verification Channels screens now prefer the shared owner.
6. Missing mappings, server-level bot permissions, explicit denies, unsafe visibility/category placement, and role hierarchy cannot be silently repaired by this feature. Those stay manual.

## Implemented execution path

- extended `public_contextual_permission_repair` instead of introducing another command or persistence owner
- added exact configured-target discovery for enabled Setup services:
  - Verification start channel
  - Voice Verify room
  - Voice Verify staff request channel
  - ticket panel / active category / archive category / transcripts
  - moderation / security logs
- added verification-only target scoping so the Verification Channels screen never drags ticket/log targets into its repair action
- added server-level permission and verification-mapping manual blockers
- added contextual Setup Check button state from the shared audit contract
- Setup Check repair runs `contextual_permission_repair.repair_context()`, then reopens the canonical Setup Check with the result and fresh config
- Verification Channels gets a same-screen Check / Fix Access control; after execution it refreshes the same workflow with fresh state
- no `set_permissions` logic was added to the feature integration; all mutation remains in `permission_repair_core`

## Safety contract

- only already configured Discord targets are considered
- no replacement channel is guessed
- no member/staff visibility is widened
- no role hierarchy is moved
- no Administrator permission is granted
- explicit denies remain preserved by the canonical repair core
- category child repair remains disabled for this contextual path
- disabled services do not pull unrelated targets into Setup Check repair

## Validation added

`tests/test_setup_verification_contextual_permission_repair.py` covers:
- enabled-service target scoping
- verification-only scope isolation
- verification mapping gaps staying manual
- `Fix Issues` / `Access Healthy` / `Manual Fix Needed` state contract
- repair followed by same-screen Setup Check re-open
- runtime binding of Setup Check + Verification Channels to the shared integration
- no feature-local `set_permissions` mutation

Existing Welcome contextual-repair regressions remain intact.

## Validation gate

- exact final branch 0 behind `main`
- focused contextual repair regressions pass
- full required GitHub Actions pass on the exact final head
- no review/thread issue ignored
- final changed-file scope contains only task-owned implementation/tests/bookkeeping
- no merge until exact-head validation and scope review are clean

## Backlog after this task

- Tickets contextual repair adoption
- Modlog / Member Logs contextual repair adoption
- Profile / Self Roles contextual repair adoption
- Protection contextual repair adoption
- remaining VC-specific repair cleanup after the shared normal-public path is proven in production
- Embed / Status contextual repair adoption
- `/dank protection` remaining non-invite picker/guard cleanup
- `/dank design` picker migration
- admin-only legacy setup picker cleanup

## Next step

Open the draft PR, run exact-head CI, inspect any focused/full-suite failures, perform final drift/review/scope checks, and merge only when the exact final head is green.
