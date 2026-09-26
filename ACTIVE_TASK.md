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

PR #332 merged stale Community Hub interaction recovery.
PR #333 merged the Open to Play → Find Players → Quick Match loop as `5600b50110bc5a27e88a088a50291d2990e1b53d`.

PR #334 merged HubLink into `main` as `addd2580ddff320b8e64c6d8df50dc848f4e5b65`. Post-merge Dank Shield CI and the Supabase migration deployment both completed successfully.

Current branch: `feat/community-hub-cross-server-parties-20260926`
Current slice: **Community Hub hardened per-speaker DAVE receive + opt-in Live Captions**

Implemented in this slice:
- removed the staff-facing raw **Partner server ID** modal from the new Partner Network path;
- added short-lived human HubLink codes formatted as `DANK-XXXX-XXXX`, using an ambiguity-resistant alphabet;
- plaintext codes exist only in the source admin's in-memory response; PostgreSQL stores only SHA-256 `code_hash` plus a short non-secret hint;
- one pending code per source server, 15-minute expiry, explicit Cancel HubLink, replacement revocation, bounded expiry cleanup, and a 10/hour source creation limit;
- source and target guild IDs come only from trusted Discord interaction context;
- target modal submit re-authorizes owner/admin/manage-guild authority before any lookup or mutation;
- target admin reviews the source server by name and must explicitly confirm **Connect Servers**;
- redemption is atomic/idempotent with advisory + row locks, rejects self-linking, rejects wrong-server replay, and safely replays only for the same target;
- successful HubLink connection activates the canonical partner pair with public session discovery on and aggregate/live activity sharing off by default;
- existing active link sharing choices are preserved on safe replay/reconnection;
- all user-facing partner fallbacks hide raw guild IDs;
- source creator receives a best-effort confirmation DM after successful redemption;
- **Add Dank Shield** uses a Community Hub-only non-Administrator OAuth permission set rather than the bot's broader moderation permission bundle;
- Community Hub install permissions cover View Channels, Send Messages, Send Messages in Threads, Embed Links, Read Message History, Manage Threads, Manage Channels, and View Audit Log; they explicitly exclude Administrator, Kick, Ban, Moderate Members, Manage Messages, and Manage Roles;
- guild-pinned **Reauthorize Dank Shield** opens the current server directly when server-level Hub permissions are missing;
- readiness checks cover server-level Hub permissions, effective configured Hub-channel permissions, deleted/missing Hub channel, configured/fallback temporary voice category, and category-level Manage Channels;
- readiness instructions include mobile navigation plus **Open Access Repair** and **Check Again**;
- runtime retention expires stale pending HubLinks without affecting ordinary Community Hub paths during schema rollout;
- legacy partner link review/revoke remains available for pre-HubLink records;
- Community Hub CI applies the base, matchmaking, and HubLink migrations twice and exercises create/replacement/redeem/replay/wrong-target/self-link/expiry/privacy/privilege invariants.

Implemented in the current voice-caption slice:
- changed the pinned Discord dependency to `discord.py[voice]==2.7.1`, installing the PyNaCl + davey voice dependencies that production logs previously reported missing;
- pinned `discord-ext-voice-recv` to reviewed inbound-DAVE PR #62 head `bec048127f4148fd147afa3182c3771b6955dc08` instead of tracking a moving branch;
- added a Dank Shield receive boundary that requires the voice receiver's SSRC→user mapping to agree with the source user before PCM may enter captions;
- added memory-only per-speaker consent so non-consenting users are dropped before PCM enters the caption queue;
- added counters for unknown speakers, identity mismatches, malformed PCM, queue overflow, and callback failures;
- added speech-preserving segmentation that splits isolated speakers by packet gaps/max duration without a destructive noise gate;
- the first transcription pass uses the untouched isolated PCM; low-confidence speech may receive a second amplitude-normalized pass that preserves every sample and timing;
- conflicting low-confidence transcriptions resolve to `[unclear audio]` rather than fabricated speech;
- added an optional OpenAI transcription provider using the current `/v1/audio/transcriptions` API and transcription logprobs;
- added host/co-host/staff **Live Captions** control plus participant **Caption My Voice** self-consent;
- only one caption receiver may own a guild voice connection at a time;
- ending/cleaning a Community Hub session shuts the receiver down and clears speaker consent;
- no Chat Link API or message behavior is guessed or duplicated; cross-server text remains an external integration boundary.

Still deferred after HubLink:
- session privacy / incomplete `invite_only` behavior;
- staff event edit/cancel/delete;
- partner live-activity redesign and scale-safe caching beyond the link/connect foundation.

## Validation / results

Evidence obtained during PR #334 development:
- HubLink migration already passed Community Hub PostgreSQL smoke on an earlier implementation head, including two-pass migration replay and all one-time-code invariants;
- Schema Authority SQL passed on an earlier HubLink head;
- Python compilation passed before the first focused-test run;
- the first focused-test failure identified two removed privacy-copy guarantees, and the implementation restored the guarantees rather than weakening tests;
- no raw `Partner server ID` modal or `Server {other_id}` fallback remains in the user-facing Community Hub partner code;
- install/reauthorization has been narrowed to Community Hub-only non-Administrator permissions;
- readiness was traced against the actual thread/voice provisioning path and now checks contextual channel/category permissions.

Required before merge on the final exact head:
- Community Hub Python + interaction contract green;
- Community Hub PostgreSQL smoke green;
- Schema Authority SQL green;
- full Dank Shield CI green;
- Profile Runtime Diagnostics green;
- Dank Design Regression CI green;
- Ticket Owner Emergency Override green;
- Application Command Size Diagnostics green;
- final diff/mergeability review against current `main`.

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

Validate the exact voice-caption head in CI: dependency installation, Python compile, DAVE-capability contract, per-user isolation/mismatch tests, speech-preserving segmentation, low-confidence dual-pass behavior, and the full Dank Shield suite. Unit tests cannot manufacture Discord's ephemeral MLS/DAVE keys, so do not call live voice receive production-proven until a real Discord soak test covers simultaneous speakers, epoch/key changes, packet loss, disconnect/reconnect, and long-running sessions.
