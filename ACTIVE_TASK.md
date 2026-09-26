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
7. PR #335 merged the Live Captions backend, but the Community Hub home exposed no discoverability path; host controls were only reachable through a session's Manage flow and participant consent only from the public session card.
8. The merged caption callbacks referenced the caption manager / voice receive capability / exception names without importing them into `public_community_hub.py`, so pressing the controls could raise `NameError` even though Python compilation passed.
9. PR #336's first discovery implementation read `membership["user_id"]`, but `list_user_sessions()` intentionally does not select that column. Consent status therefore needed to use the actual interaction user ID instead of widening persistence reads.

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

PR #335 merged into `main` as `93f41dc4845f78b7858c4a89a633c31be3830e0e` from final head `47fc75c52f692065a18ccd5cbd8ad469ff61d085`. Its exact-head required workflows were green, post-merge Community Hub CI and Dank Shield CI were green, Supabase migration deployment was green, and Discloud reported `discloud/commit: success`.

Current branch: `fix/community-hub-live-captions-discovery-20260926`
Current draft PR: **#336 — Community Hub: make Live Captions visible and reachable**
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
- Live Captions are default-off behind `DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED`; code may deploy without exposing un-soaked DAVE receive to users;
- caption startup fails closed if the required privacy notice cannot be posted;
- consent/start copy explicitly states that opted-in audio is sent to OpenAI's transcription API and that Dank Shield itself does not save the audio;
- caption shutdown cancels in-flight transcription tasks and discards queued/buffered audio so a stopped session cannot publish late captions;
- a missing/zero transcription confidence is treated as uncertain, never as implicitly trustworthy.
- process-wide Live Captions scale is bounded by configurable active-guild, speakers-per-session, and global transcription concurrency limits so one public deployment cannot fan out unbounded API/RAM load;
- `.env.example` documents the default-off feature gate, provider key/model/language, and scale limits without containing any real secret.
- PR #336 imports the caption runtime/receive names used by the UI callbacks, adds a discoverable Live Captions entry/overview from Community Hub home, exposes self-consent from private session details as well as the public card, and keeps the transcription gate disabled until the real DAVE soak test passes.
- PR #336 consent-status rendering uses the actual interaction user ID rather than assuming `list_user_sessions()` embeds a user ID that it does not return.

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

PR #335 final-head validation completed green for Community Hub CI, Dank Shield CI, Profile Runtime Diagnostics, Dank Design Regression CI, Ticket Owner Emergency Override, and Application Command Size Diagnostics. The merge commit also completed Community Hub CI, Dank Shield CI, Ticket Owner Emergency Override, Supabase migration deployment, and Discloud deployment successfully.

PR #336 head `0af6f6d66b7809a78f307b463c53cc3c406e3d3d` completed Community Hub CI, Dank Shield CI, Ticket Owner Emergency Override, and Application Command Size Diagnostics successfully before final review found the consent-status identity bug. The corrected exact head must rerun all workflows triggered by the final diff before merge.

Required before merging PR #336:
- every workflow triggered on the corrected exact head green;
- final diff/mergeability review against current `main`;
- no re-enabling of `DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED` before the real Discord DAVE soak test.

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

Validate the corrected exact head of PR #336, review the final diff against current `main`, and merge only if every triggered gate is green and the head has not moved unexpectedly. After merge, verify post-merge CI and Discloud deployment. Live receive still requires a real Discord DAVE soak test covering one speaker, overlapping speakers, reconnect/SSRC changes, epoch/key transitions, packet loss/out-of-order delivery, stop/restart/opt-out/session-end cleanup, and long-running operation. Keep `DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED` off until that soak test passes.
