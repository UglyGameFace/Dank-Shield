# ACTIVE TASK

## DS-AUD-PROFILE-SELFROLES-CONTEXTUAL-REPAIR — Move Profile and Self Roles onto shared same-screen repair

**Outcome target:** Extend the merged contextual permission-repair contract into the normal public Profile / Self Roles workflows. Any Profile or role-panel screen that can prove Dank Shield itself is missing safe access to an exact selected/configured channel must expose the same `Fix Issues` / `Access Healthy` / `Manual Fix Needed` behavior and delegate overwrite mutation to the shared repair owner.

**Status:** IMPLEMENTATION / VALIDATION

**Branch:** `audit/profile-selfroles-contextual-repair`
**Base main:** `b8c798422dfd8d56c8dd7a6159c20cdcf1d6804a`
**Previous integrated task:** PR #247 merged at `b8c798422dfd8d56c8dd7a6159c20cdcf1d6804a`

## Scope

- `/dank profile builder` and its profile/self-role panel posting path in `public_self_roles_group`
- Compact Profile Signatures setup in `profile_card_setup_ui`
- Roles Center self-role panel posting path in `roles_center_services`
- reuse of `contextual_permission_repair` / `permission_repair_core`; bypass feature-local channel overwrite repair ownership
- exact selected/configured channels only; no guessed replacement targets
- same-screen repair state and immediate post-repair re-audit where the workflow owns the exact target
- focused regressions and task/PR bookkeeping

Out of scope:
- changing profile role taxonomy, cosmetic-role safety policy, member privacy, profile-card rendering, or role creation semantics
- moving Dank Shield's bot role or other role hierarchy
- granting Administrator or server-level permissions through channel repair
- widening member/@everyone visibility
- clearing explicit denies without the existing explicit confirmation path
- Protection / VC / Embed / Status contextual repair adoption
- broad startup-guard retirement

## Findings

1. PR #247 is merged and `main` points at `b8c798422dfd8d56c8dd7a6159c20cdcf1d6804a`.
2. `public_self_roles_group` already had a local Profile Builder health model separating channel-effective failures from manual role/server prerequisites.
3. The old `builder:fix` path called `channel.set_permissions(...)` directly for Dank Shield. That duplicated permission ownership and bypassed the shared repair audit/undo/event path.
4. Profile Builder channel health historically checked View Channel, Send Messages, and Embed Links. The shared `general` profile covers those plus Attach Files and Read Message History, which are safe bot-only panel capabilities.
5. Manage Roles, role hierarchy, managed roles, and explicit denies are not safe channel-overwrite repairs and remain manual.
6. Compact Profile Signatures validates every exact saved/selected channel and previously refused save/re-enable when access was missing, but provided no repair action. Its configured/selected IDs are deterministic repair targets.
7. Compact setup runtime builds `profile_card_setup_ui.ProfileCardSetupView` dynamically after the late setup presentation patch, so the contextual subclass can be composed without creating a second setup command owner.
8. Roles Center owns the exact text channel selected immediately before it posts a pronoun/identity self-role panel, so it can preflight and safely repair only that selected channel.

## Implemented execution path

- added `public_profile_contextual_permission_repair` and activate it from the existing late public setup gate
- Profile Builder status now evaluates the exact current panel channel through the shared contextual audit while preserving the existing role/hierarchy manual blockers
- Profile Builder button state is now `Fix Issues`, disabled `Access Healthy`, or `Manual Fix Needed`
- `builder:fix` is intercepted before the historical local mutation path and delegates to `contextual_permission_repair.repair_context()`
- after Profile Builder repair, the same interaction response is refreshed with a fresh access audit and last-repair result
- Compact Profile Signatures gains a shared contextual access button on the same setup view
- saved Compact Signature channels are repaired/re-audited through the shared owner
- when an administrator selects new Compact Signature channels that are otherwise valid but inaccessible, the selected exact channels are repaired through the shared owner before the canonical save path continues; unresolved/manual access prevents the selection from being saved
- Roles Center preflights the exact selected self-role panel channel through the shared owner before canonical role creation/posting continues
- no replacement channel is guessed anywhere in these paths
- no new feature-local `set_permissions`, explicit-deny clearing, role mutation, Administrator grant, or member-visibility widening path was added

## Safety contract

- only exact current/saved/selected channels are targeted
- no replacement channel is guessed
- no @everyone/member/staff visibility is widened
- no role hierarchy is moved
- no Administrator permission is granted
- explicit denies stay preserved by the shared repair core
- Manage Roles, role hierarchy, missing mappings, unsupported channel types, and server-level prerequisites remain manual

## Validation added

`tests/test_profile_contextual_permission_repair.py` covers:
- no permission mutation ownership in the integration module
- Profile Builder `fix` interception and same-screen refresh
- shared three-state button contract
- role prerequisites / explicit denies remaining manual
- Compact Profile Signatures same-screen repair control
- exact selected-channel repair before canonical save
- Roles Center exact selected-channel preflight repair
- general minimum permission profile / no name guessing
- late setup-gate activation
- runtime rebinding of all three Profile/Self Roles surfaces

## Validation gate

- normal public Profile / Self Roles execution paths proven by source tracing
- focused contextual-repair regressions pass
- exact final branch 0 behind `main`
- final changed-file scope contains only task-owned implementation/tests/bookkeeping
- full required GitHub Actions pass on exact final head
- no review/thread issue ignored
- no merge until exact-head validation and scope review are clean

## Backlog after this task

- Protection contextual repair adoption
- remaining VC-specific repair cleanup
- Embed / Status contextual repair adoption
- admin-only `/dank tickettool-check` contextual repair adoption
- `/dank protection` remaining non-invite picker/guard cleanup
- `/dank design` picker migration
- admin-only legacy setup picker cleanup

## Next step

Normalize this task to one final commit, open the draft PR, run exact-head focused/full CI, inspect any concrete failure, perform final scope/drift/review checks, merge only when the exact final head is clean, verify `main`, then release the lock and move to Protection contextual repair.