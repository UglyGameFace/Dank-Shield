# Active Task

## Active task / desired outcome

**COMMUNITY-HUB-COMPLETION-RELIABILITY-014 — finish the Dank Shield Community Hub as a reliable, general-use product instead of a one-pass scaffold**

Desired outcome: Community Hub interactions must acknowledge reliably, stale private Hub panels must recover without red Discord failures, the user-facing flow must match the intended plain-English experience, and missing promised Hub capabilities must be completed without weakening public-session persistence, permissions, cleanup, rate-limit safety, or privacy boundaries.

## Scope / single active task lock

Only Community Hub completion and the interaction/runtime behavior required for Community Hub correctness are active.

Included:
- Community Hub private-menu and public-session interaction lifecycle;
- stale/restarted Hub recovery;
- Hub interaction acknowledgement diagnostics and regression coverage;
- member UX: Find Players, Open to Play, Quick Match, groups/sessions, events, notifications, safety;
- staff Hub settings/health/session/event/partner management;
- approved partner activity/discovery behavior;
- Hub runtime, service, SQL, and cleanup/reconciliation only where required by the Hub.

Do not broaden into Invite Shield, AntiNuke policy redesign, verification, tickets, Dank Design, or unrelated cleanup.

## Prior task closure

PR #330, **Prevent AntiNuke self-ejection on local bulk message cleanup**, merged into `main` as `d55ace775f1ec1f2f401dbeca9c5e72875b9537d`.

Its exact head `6ee520397771e4c71d0d7c84f8df6f06d5896fe4` completed every observed workflow successfully:
- Profile Runtime Diagnostics
- Dank Shield CI
- Dank Design Regression CI
- Ticket Owner Emergency Override
- Application Command Size Diagnostics

The AntiNuke task therefore satisfies its repository completion gates and the active-task lock can move to Community Hub.

## Findings / root cause

PR #326 merged the first broad Community Hub implementation, but the feature was treated as production-ready before live UX/reliability follow-through was complete.

Confirmed current-state gaps:

1. Private Community Hub views use the shared 15-minute in-memory private-menu lifetime. After timeout or a bot restart/redeploy, the old ephemeral message can remain visible while its ViewStore owner is gone.
2. The shared component runtime can recover definitely unowned private panels, but it applies a generic recovery grace delay before claiming the interaction. For a known Community Hub custom-id namespace that is already proven unowned, that delay only consumes Discord's initial-response window.
3. Community Hub's durable public session card is correctly persistent (`timeout=None`) and must remain separate from private-menu recovery.
4. The existing Community Hub contract test proves helper presence and static structure but does not exercise the stale Hub recovery path end-to-end.
5. Current partner activity is aggregate-only (groups, voice count, Open to Play count, optional online/gaming counts). It does not yet provide the chosen-partner live member experience originally requested.
6. The merged Hub contains substantial backend/state-machine work, but the user-facing product still needs a completion pass rather than another disconnected feature layer.

## Execution path

Fresh private Hub click:

`/dank home -> Community Hub -> discord.py ViewStore-owned private view -> callback acknowledges -> Hub action`

Stale private Hub click after timeout/restart:

`ephemeral Hub message remains visible -> ViewStore has no owner -> shared component runtime proves owner_state=False -> Community Hub-specific safe refresh claims interaction immediately -> stale action is NOT replayed -> fresh Community Hub replaces stale panel`

Durable public session click:

`public session card -> globally registered CommunitySessionPublicView(timeout=None) -> normal callback -> acknowledge first -> durable session mutation`

Public session controls must never be routed through private stale recovery.

## Changes

PR #332 merged the stale Community Hub interaction recovery slice.
PR #333 merged the Open to Play → Find Players → Quick Match formation slice as `5600b50110bc5a27e88a088a50291d2990e1b53d`.

Current branch: `feat/community-hub-hublink-20260926`

Current implementation slice: **HubLink server integration without server-ID entry**.

Planned/active changes:
- replace the staff-facing “Partner server ID” request flow with short-lived one-time HubLink codes;
- derive the source and target guild IDs only from trusted Discord interactions;
- store only a cryptographic hash of the human-readable code;
- make redemption atomic and idempotent in PostgreSQL;
- treat code creation as source-server consent and redemption/confirmation as target-server consent;
- activate public partner-session discovery on successful HubLink redemption while keeping aggregate activity sharing off by default;
- generate a normal Discord bot-install URL using the repository’s approved non-Administrator permission set when Dank Shield is not yet installed in the target server;
- add a Community Hub readiness report that identifies missing guild/channel permissions and gives exact repair steps plus a pinned reauthorization link when possible;
- keep the existing legacy pending-link records readable/manageable but stop asking new users for raw server IDs;
- expire/revoke HubLink codes safely and add CI coverage for replay, expiry, self-link prevention, permission boundaries, and migration idempotency.

Still deferred after HubLink:
- session privacy / incomplete `invite_only` behavior;
- staff event edit/cancel/delete;
- partner live-activity redesign and scale-safe caching beyond the link/connect foundation.

## Validation / results

PR #333 completed its exact-head Community Hub, full Dank Shield, schema, profile, design, ticket-owner, and command-size gates before merge.

HubLink validation requirements for this branch:
- follow-up migration applies twice on a fresh PostgreSQL database;
- plaintext HubLink codes never persist in the database;
- expired/redeemed/revoked codes cannot be reused;
- source and target guild IDs come from interaction context, never user-entered ID fields;
- self-linking and unauthorized redemption are rejected;
- partner link creation is atomic under concurrent redemption;
- public session discovery is enabled only after successful mutual HubLink consent;
- aggregate activity sharing remains disabled by default;
- existing partner-link reads/revocation remain compatible;
- install/repair URLs request the approved non-Administrator permission set;
- focused Community Hub + interaction tests and full Dank Shield CI pass on the exact PR head.

## Cleanup / conflicts

The fix must not add a second Community Hub business handler or replay stale actions. The shared component runtime remains the only stale-private-panel recovery owner. CommunitySessionPublicView remains the only durable public session-button owner.

## Blockers / risks

No production log excerpt for the latest intermittent Community Hub failure is available yet, so current code inspection can prove the stale-panel timing gap but cannot claim every fresh-panel failure has the same cause. Existing component-runtime diagnostics must remain intact so any remaining fresh-panel failure produces actionable evidence instead of guesswork.

## Backlog inside this same active task

After interaction reliability is validated:
- finish the intended partner live-activity experience using explicit per-partner authorization/privacy controls;
- finish member-facing Hub UX/polish and remove awkward/dead-end flows;
- verify event/session/notification discoverability from mobile;
- run a full promised-vs-implemented Community Hub feature audit and close each real gap without creating duplicate ownership.

## Next step

Implement the HubLink persistence/RPC layer first, then wire the Discord staff UI and readiness/repair flow. Validate the exact head in Community Hub CI and full Dank Shield CI before merge. Do not start the event/privacy/live-activity slices until HubLink is complete.
