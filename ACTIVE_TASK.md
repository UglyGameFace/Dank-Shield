# ACTIVE TASK

## Active task / desired outcome

**P0-ACCESS-REPAIR-RUNTIME-004 — consolidate Dank Shield access repair and eliminate false startup-health failures**

Production screenshots after reinvite + role repositioning show that Dank Shield's
repair surfaces disagree with each other:

- Diagnostics → **Fix Channel Access** can deny the guild owner with
  `Manage Server, Manage Channels, or Administrator is required`;
- selected-target Fix Access can attempt a write and then return Discord
  `Forbidden`;
- Setup Check can say configuration passed while reporting several configured
  verification/ticket targets still missing bot access;
- diagnostics reports retired/lazy verification compatibility modules as
  missing startup owners even though current production boot intentionally does
  not load them as startup owners.

PR #313 is already merged and all exact-head workflows passed. Basic Verify
legacy-panel repair remains separate and is not being reopened here.

## Single active task lock

Only access-repair authority/capability ownership and startup-health truth are
active in this task.

Do not broaden into unrelated setup, ticket, verification, AntiNuke, or design
changes.

## Root causes

### 1. Repair authorization had a second local authority implementation

`permission_repair_core._actor_can_manage()` independently required a cached
`discord.Member` and read only cached guild permissions/owner ID.

That bypassed the canonical owner/interaction-permission authority introduced
for the rest of the public command surface. A valid owner/manager could pass the
Diagnostics doorway and then be rejected by the Fix Access subsystem.

### 2. Setup repair still used the wrong Discord permission prerequisite

The setup repair compatibility path treated **Manage Channels** as the
permission required to mutate channel permission overwrites.

discord.py `GuildChannel.set_permissions()` requires **Manage Roles**
(Discord's channel UI calls the equivalent channel capability
**Manage Permissions**).

This caused repair previews/results to disagree with the selected-target repair
and to give the wrong remediation instruction.

### 3. Self-lockout was presented as auto-repairable

If Dank Shield has Manage Roles at the server level but a category/channel
resolves **Manage Permissions** as denied for the bot, Discord will not permit
the bot to edit the overwrite that is blocking itself.

The UI still exposed green repair actions and retry/reauthorize guidance even
though that exact target required a manual Discord permission change first.

### 4. Startup diagnostics still expected retired/lazy compatibility guards

`startup_diagnostics.EXPECTED_STARTUP_OWNER_MODULES` still listed:

- `basic_verification_mode_guard`;
- `id_verify_allowlist_guard`;
- `unverified_ticket_panel_flow`.

Current `main.py`, `app.py`, and `commands.py` do not own those as
mandatory startup modules. The health report therefore emitted false warnings.

## Merged foundation

PR #314 — canonical access-repair authority, overwrite capability truth, Setup
Check readiness truth, undo truth, and startup-diagnostics ownership — is merged
to `main` and Discloud reports the merge deployed successfully. All exact-head
workflow groups passed before merge.

## Merged contextual consolidation

PR #316 — contextual repair handoff to the canonical access hub — is merged to
`main`, all exact-head workflows passed, and Discloud reports the merge
deployed successfully.

Production then reported the repair still failed.

The new live failure exposed two remaining defects **inside the canonical hub
itself**, not another feature-specific repair owner:

1. the preview always rendered an enabled green **Apply Safe Fixes** button even
   when its own result contained zero safe changes and only manual blockers;
2. the canonical hub and Specific Channel repair used acknowledgement helpers
   that swallowed Discord defer failures and continued execution, violating the
   claim-first boundary used elsewhere in Dank Shield.

This remains the same P0. No unrelated feature work is active.

## Repair in progress

### Canonical actor authority

A shared
`interaction_has_channel_management_authority()` now recognizes:

- actual guild owner;
- Administrator;
- Manage Server;
- Manage Channels;

using Discord-resolved interaction permissions before cached Member state.

Fix Access delegates to that owner instead of maintaining a second permission
gate.

### Canonical overwrite capability

`permission_overwrite_edit_blocker()` is now the single capability check for
channel/category overwrite mutation.

It distinguishes:

1. missing server-level Manage Roles;
2. target-level Manage Permissions self-lockout;
3. a genuinely repairable target.

Selected-target and setup repair consume the same capability truth.

When a target is self-locked, the normal green Fix Missing Access action is
disabled and labeled **Manual Discord Fix Required** instead of repeatedly
issuing doomed writes.

### Setup repair compatibility

The still-referenced setup compatibility helper keeps its historical function
name for callers, but its implementation now checks Manage Roles / Manage
Permissions rather than Manage Channels.

Setup preview/apply uses the canonical overwrite capability and reports Discord
Forbidden results with the same explanation as selected-target repair.

### Setup Check truth

Setup Check no longer treats saved configuration completeness as sufficient to
show a green pass.

When the guided configuration is structurally complete but the configured
channel/category access audit is unhealthy:

- the title becomes **Configuration Saved — Access Needs Attention**;
- the description states that feature testing is blocked on access;
- the misleading guided-test / continue action is removed;
- the contextual access-repair/manual-fix control becomes the forward action.

This prevents the previous contradictory card that said Configuration Check
Passed while simultaneously listing unrepaired access targets.

### One generic repair doorway

Diagnostics now exposes **Repair Bot Access** and hands off to the same
`setup_permission_repair_services.open_permission_repair()` hub used by Setup.

The canonical generic flow is:

1. preview configured access;
2. apply safe fixes;
3. use **Specific Channel** only to inspect one blocked target.

Existing ticket/setup recovery guidance points to that same route.


### Contextual repair handoff

Setup, Verification, Welcome, Tickets, Profile/Self Roles, Modlog, and Member
Logs now use one shared contextual decision:

- healthy → disabled **Access Healthy**;
- safe repairable gaps → **Fix Issues** repairs in place through the canonical
  overwrite mutation owner;
- manual-only state → **Repair Bot Access** opens the canonical repair hub
  instead of rerunning a repair path that already knows it cannot succeed.

Ticket health remains readable to configured ticket staff, but permission
mutation now requires the canonical setup/server-management authority instead
of treating ordinary ticket staff as channel-permission administrators.

Member Logs authority now uses the canonical interaction-resolved
channel-management helper, so guild owners are not rejected because cached
`Member` state is incomplete.

### Undo truth

A repair that changes zero overwrites no longer creates or displays an undo
token. Failed/no-op attempts are still audit-recorded, but no restore snapshot
is manufactured. **Undo Repair** remains disabled until a real change produced
a valid token.

### Canonical hub action truth

The canonical Repair Bot Access preview now derives its primary action from the
actual preview result:

- one or more safe changes available → enabled **Apply Safe Fixes**;
- no safe changes and no blockers → disabled **Access Healthy**;
- no safe changes but manual/error blockers remain → disabled
  **Manual Discord Fix Required**.

The preview card mirrors the same truth. It no longer tells the user to press
Apply Safe Fixes when there is nothing safe to apply.

The Reauthorize link is shown only when the preview detected missing
server-level bot permissions. A channel/category self-lockout no longer points
the user back to a reauthorization step that cannot remove a target-specific
deny.

### Reauthorization guidance is authoritative

The canonical preview, post-repair result, and Specific Channel repair now use
the same server-level prerequisite truth before showing **Reauthorize Dank
Shield**. A target-specific channel/category self-lockout no longer exposes an
OAuth reinvite that cannot remove the deny. Reauthorization is offered only
when known server-level prerequisites are actually missing.

### Claim-first repair boundary

The canonical access hub now claims the interaction through
`interaction_guard.safe_defer_interaction()` before preview/apply work. A
failed acknowledgement stops the path before queue, REST, database, or Discord
mutation.

Specific Channel repair now uses the same claim-first helper contract for
normal repair and explicit-deny confirmation.

Render failures are no longer silently discarded. If both the original-response
edit and follow-up fail, the interaction failure ring records a structured
`access_repair_render_failed` event.

### Repair callback diagnostics

The selected-target repair View's generic error handler previously emitted:

`Fix Access could not finish that interaction. Nothing was changed.`

That claim was unsafe because a callback exception can occur after Discord has
already accepted a mutation.

The handler now records a structured interaction failure with an error ID and
traceback and tells the user to re-preview before assuming the target state.

### Startup diagnostics

The startup-health contract now tracks actual native boot owners:

- process health;
- Discord API safety;
- command runtime;
- public env-ID isolation;
- shared interaction runtime;
- Basic Verify runtime;
- public ticket panel runtime;
- centralized interaction handlers;
- Profile/Role interaction runtime;
- authoritative activity tracker.

Retired/lazy verification compatibility modules are no longer false mandatory
startup owners.

## Safety invariants

- actual guild owners must never be denied because Member cache shape is partial;
- non-owner users without resolved management authority remain denied;
- no repair path claims Manage Channels is the permission required for
  `set_permissions()`;
- no target is advertised as auto-fixable when the bot cannot edit permission
  overwrites there;
- reauthorization remains non-Administrator;
- no unrelated member/staff visibility is modified;
- no dormant startup-guard bulk loader is restored;
- diagnostics remains read-only.

## Validation required

- canonical owner/Manage Channels interaction authority tests;
- server-level Manage Roles prerequisite tests;
- channel-level Manage Permissions self-lockout tests;
- setup repair regression tests;
- startup diagnostics owner-contract tests;
- Python compile;
- full test suite;
- all GitHub workflow gates;
- currentness / mergeability / review / diff hygiene;
- regression coverage that reauthorization is hidden after healthy repair and
  for target-specific self-lockout, but remains available for missing
  server-level prerequisites.

### Exact-head validation result

At `123f5bf58611ab5b9f0ca4614e909a8698f67aae`, all six PR workflow groups completed successfully:

- Dank Shield CI;
- Ticket Owner Emergency Override;
- Application Command Size Diagnostics;
- Dank Design Regression CI;
- DS Backlog 027 Validation;
- Profile Runtime Diagnostics.

The full unit suite, Python compile, focused claim-first security checks, SQL smoke
test, profile regressions, design regressions, command-size diagnostics, and
backlog static validation all passed. The branch was 0 commits behind `main`
before this validation-record-only commit and remained mergeable with no review
threads.

## Status

**VALIDATED — canonical hub fail-closed/action-truth repair and unified reauthorization guidance implemented; all exact-head workflow groups green at 123f5bf58611ab5b9f0ca4614e909a8698f67aae**

Branch: `fix/access-repair-fail-closed-20260924`

Base: current `main` after merged/deployed PR #316.

## Production acceptance after deploy

1. guild owner can open Diagnostics → Fix Channel Access;
2. a repairable channel performs the write successfully;
3. a self-locked channel clearly says exactly which Discord permission must be
   manually restored and does not expose a fake green repair action;
4. Setup Check and Specific Channel show the same prerequisite truth;
5. diagnostics no longer reports retired/lazy verification guards as missing
   startup owners;
6. manual-only contextual screens show **Repair Bot Access** and enter the one
   canonical hub instead of rerunning doomed safe-repair callbacks;
7. ticket permission repair is not available to ordinary ticket staff unless
   they also hold canonical server-management authority;
8. a manual-only hub preview has **no enabled Apply Safe Fixes** action;
9. a failed component acknowledgement stops the repair before mutation and
   emits structured interaction diagnostics;
10. a callback/render exception exposes an error ID and never claims the target
    definitely remained unchanged without re-auditing it.
