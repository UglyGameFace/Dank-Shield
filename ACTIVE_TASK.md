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

Reliability slice PR #332 merged as `ace943f74234d4a8078f9b21c26d608668c64f66` after all exact-head workflows passed.

Current branch: `feat/community-hub-matchmaking-loop-20260926`

Current implementation slice:
- connect Open to Play to the actual Find Players / Quick Match flow;
- keep existing availability discoverable without silently auto-enrolling members;
- add an explicit per-availability Quick Match opt-in that defaults existing rows to off;
- make the authoritative PostgreSQL Quick Match path join an existing group first, then safely form a new group only from an explicitly opted-in compatible availability row;
- preserve match-safety exclusions, session quotas, creation cooldowns, restart-safe idempotency, and one authoritative session state machine;
- expose Open-to-Play activity inside Find Players so the member flow is no longer split across disconnected menus.

## Validation / results

Required before completion:
- compile all changed Python sources/tests;
- focused interaction-guard/component-lifecycle/Community-Hub tests;
- Community Hub CI;
- full Dank Shield CI and relevant regression workflows;
- exact-head workflow review;
- final diff cleanup and duplicate-owner inspection;
- live production acceptance for stale/restarted Hub panel recovery and fresh Hub interactions.

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

Implement and validate the Open to Play → Find Players → Quick Match/group-formation loop, including the follow-up SQL migration, focused PostgreSQL smoke coverage, UI/service wiring, and exact-head CI. Do not start session privacy, event-management, or partner-activity redesign until this slice is complete.
