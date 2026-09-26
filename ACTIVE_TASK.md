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
Current draft PR: **#333 — Community Hub: connect Open to Play with Quick Match formation**

Implemented in this slice:
- added follow-up migration `20260926044000_community_hub_matchmaking_loop.sql`;
- Open to Play remains expiring/discoverable, while automatic pairing is a separate `auto_match` opt-in that defaults existing and new rows to off;
- Find Players now shows availability totals and lets members browse active Open-to-Play listings by game;
- Quick Match still prefers an existing public group and only forms a new group from an explicitly opted-in same-game member when no open group is available;
- PostgreSQL owns candidate selection, row locking, Match Safety checks, idempotency replay, session-creation quotas/cooldowns, and final candidate revalidation;
- one member cannot be automatically paired into a second active session for the same game;
- Discord provisioning for a newly formed match stays inside the same operation-queue action and does not create a second matcher or state machine;
- pre-formed sessions normalize to `forming` only after publication;
- successful formation consumes same-game availability, while provisioning failure restores the claimed candidate's Quick Match eligibility best-effort;
- previous-process `creating` sessions are detected from the runtime start timestamp, moved into bounded cleanup, and Quick Match eligibility is restored when appropriate;
- member departure clears active Open-to-Play rows;
- availability embeds are bounded to eight rows with truncated notes so user data cannot exceed Discord embed limits and turn a deferred interaction into a failed edit;
- Community Hub CI applies the base + follow-up migration twice and exercises consent, formation, replay, existing-group preference, Match Safety, duplicate same-game exclusion, and RPC privilege boundaries.

Deferred until this slice is merged:
- session privacy / incomplete `invite_only` behavior;
- staff event edit/cancel/delete;
- partner activity/control redesign and scale-safe caching.

## Validation / results

Evidence already obtained during PR #333 development:
- Community Hub Python + contract passed after the matchmaking implementation;
- Community Hub PostgreSQL smoke passed after correcting the test harness delimiter;
- the SQL smoke has proven default-off consent, opted-in formation, idempotent replay, existing-group preference, Match Safety exclusion, duplicate same-game exclusion, and service-role-only execution;
- Schema Authority SQL has passed on matchmaking heads;
- diff review is confined to Community Hub runtime/service/UI, its follow-up migration, focused tests/workflow, and this task record;
- no conflict markers, debug code, generated artifacts, or obvious secret-bearing changes were found;
- the branch remains based directly on merged PR #332 and has not absorbed unrelated main changes.

Final exact-head validation is running after the latest UI safety/consent and task-record updates. Required before merge:
- Community Hub CI green;
- full Dank Shield CI green;
- Schema Authority SQL green;
- Profile Runtime Diagnostics green;
- Dank Design Regression CI green;
- Application Command Size Diagnostics green;
- Ticket Owner Emergency Override green;
- final PR diff/mergeability review against unchanged `main`.

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

Finish exact-head validation for PR #333. If every gate is green and the final diff remains focused, mark the PR ready and merge it with the head SHA pinned. Only then advance the same Community Hub task to the next incomplete product slice.
