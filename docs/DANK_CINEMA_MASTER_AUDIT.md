# Dank Cinema Master Audit

Issue: #425
Baseline audited: `main@3c8989e6bfa70fd463e1914bfa96d60e9f3a6233`
Audit branch: `audit/425-cinema-master-audit`

## Audit goal

Keep the complete Dank Cinema feature set while making the normal path obvious, setup lighter, error recovery predictable, and playback/session ownership safer.

The primary product path remains:

`Cinema Home -> Find Movie -> Choose Movie -> Choose Release -> Watch`

Low-frequency controls remain behind **More** and staff-only configuration remains behind **Cinema Settings**.

No second provider stack, torrent runtime, media server, room model, or browser-login system is introduced by this audit.

## Runtime ownership map

### Discord UI

`commands_ext/public_movie_night.py` owns:
- `/movie`;
- Watch Party / Private Session entry;
- Find Movie;
- Movie Picks;
- release selection;
- Queue;
- contextual voting;
- Rejoin;
- Pass Host;
- More;
- Cinema Settings;
- Movie Sources;
- Setup & Diagnostics;
- direct magnet / .torrent attachment.

### Room authority

`movie_night.py` owns:
- room identity;
- host identity and handoff;
- active viewer heartbeat;
- votes;
- candidate/release state;
- queue;
- server-authoritative playback position;
- group buffering;
- private-room access.

### Watch player

`movie_night_web.py` owns:
- signed per-user Watch page;
- state polling;
- 3-second viewer heartbeat;
- host playback actions;
- reconnect/sync behavior;
- private-room web access enforcement.

### Torrent runtime

`torrent_streaming.py` plus `api_new/torrent_stream_routes.py` own:
- magnet / .torrent session creation;
- one canonical libtorrent runtime;
- identity reuse;
- per-room leases;
- byte-range streaming;
- adaptive buffer planning;
- resource admission;
- idle cleanup;
- invalid-handle containment.

### Catalog/providers

`movie_catalog.py`, `media_source_registry.py`, and `media_source_resolver.py` own:
- TMDB catalog identity;
- JustWatch availability via TMDB;
- Internet Archive Feature Films;
- direct magnet / .torrent media;
- generic structured JSON APIs;
- RSS / Atom / Torznab / Newznab-style feeds;
- SSRF/private-network rejection;
- response/result/concurrency caps;
- provider dedupe/ranking.

## Existing strengths verified

- One compact public command doorway.
- Progressive disclosure already keeps most admin/runtime detail off the viewer home screen.
- Private Sessions are invite-only for up to 20 total authorized viewers; the host keeps programming/playback/end authority in Discord and the Watch player.
- Pass Host changes authority without replacing the room or resetting playback.
- First-ranked release is not preselected, avoiding the Discord mobile first-option trap.
- Search/provider work is bounded by timeout, result, response-size, concurrency, and SSRF guards.
- Arbitrary provider HTML is rejected instead of scraped.
- Torrent v1/v2 identities, shared sessions, leases, disk/memory admission, and invalid-handle containment have direct regression coverage.
- Watch refresh keeps the same client session and protects viewer telemetry/sync state.
- Stale Discord viewers can explicitly Rejoin without losing room/queue/media state.
- Adult-content preference is guild-scoped, defaults off, and is enforced across TMDB plus explicitly labeled provider results.
- Action pickers use no selected default for first-use actions.
- Current Movie Night component custom IDs are unique and current button rows stay within Discord limits.
- The invalid pseudo-emoji regression from #420 is covered.

## Findings and remediation

### Critical: abandoned room could lease torrent media indefinitely

**Finding**

The live-viewer heartbeat is intentionally short, but the room itself previously had no inactivity expiry. After #423, a leased Movie Night torrent is correctly protected from torrent idle cleanup. Together those two correct behaviors created an unintended leak: a room with no viewers could retain its tracked torrent lease until someone explicitly ended the room or the process restarted.

**Remediation**

The audit adds a separate room-level empty timeout:
- default: 1800 seconds / 30 minutes;
- configurable with `DANK_MOVIE_NIGHT_EMPTY_ROOM_TTL_SECONDS`;
- based on last real room presence, not merely the 35-second live-viewer flag;
- active viewers prevent expiry;
- final eligibility is rechecked before ending;
- room is marked ended before external lease cleanup begins;
- canonical termination releases the torrent lease and retires room-owned state.

This preserves fast live-viewer expiry for sync/quorum while preventing abandoned rooms from owning resources indefinitely.

### High: notification role was incorrectly a launch prerequisite

**Finding**

A Watch Party could be prevented from starting because the optional Movie Night notification role was missing, unmentionable, or Dank Shield lacked Manage Roles. Notification enrollment is useful, but it is not required to search, queue, stream, synchronize, or vote.

**Remediation**

Notification readiness is now a warning, not a launch blocker.
- Watch Parties can start without a notify role.
- The channel still receives a normal Dank Cinema announcement.
- If a valid notify role exists, it is pinged.
- If it does not, the announcement is sent without a role mention.
- Setup calls the action **Repair Notifications** rather than exposing role plumbing as mandatory product setup.

### High: playback launch could be considered ready while media server was down

**Finding**

Configured URL/signing values with a non-running public media server were previously only a warning. That could allow a room to launch even though the Watch surface could not actually serve playback.

**Remediation**

The live public media server is now a launch blocker.

### Medium: optional metadata probing was treated as playback-critical

**Finding**

PyAV is used for verified media metadata probing. Probe failures are already caught and do not own byte-range playback, yet missing PyAV was a launch blocker.

**Remediation**

Missing PyAV is now an explicit optional warning. Playback remains launchable; verified codec/audio metadata may be reduced.

### Medium: solo Watch Party exposed meaningless democracy

**Finding**

A one-person public Watch Party displayed movie/release voting controls even though there was nobody else to vote with and the action resolves immediately.

**Remediation**

For a solo room:
- movie vote button is hidden;
- release vote button is hidden;
- queue is labeled **Add to Queue**;
- current host sees **Play This Release**.

When more than one active viewer exists, collaborative voting controls return. A non-host sees **Request This Release** rather than the ambiguous combined Play/Request label.

### Medium: direct-media failures could drop active room context

**Finding**

Some magnet/.torrent failure paths replaced the response with a bare error and an empty-home view even when an active room already existed.

**Remediation**

Those paths now keep the current room embed and controls attached. Setup guidance points to the real path:
`More -> Cinema Settings -> Setup & Diagnostics`.

### Medium: announcement failure could disguise a successful room as failed

**Finding**

Room creation and the public channel announcement were inside the same exception boundary. If Discord rejected or transiently failed the announcement after `create_room()` succeeded, the user was told the room could not start even though a live room already existed.

**Remediation**

Room creation now owns its own failure boundary. Announcement delivery is best-effort after the canonical room exists. A notification/announcement failure reports a warning while keeping the real active room visible.

### Medium: slow provider work could outlive the room that requested it

**Finding**

Structured provider/TMDB work crosses network await boundaries. A room could be ended while a search was still in flight, leaving the old room object available to later result-materialization code.

**Remediation**

After provider/catalog work returns, Dank Cinema re-resolves the canonical room and verifies it is still active and accessible before renewing presence or materializing candidates. Stale results are discarded with a usable Cinema Home instead of being attached to dead state.

### Medium: process memory fallback exceeded checked-in host allocation

**Finding**

The checked-in Discloud allocation and `.env.example` use 1495 MB. The code fallback for `DANK_PROCESS_MEMORY_LIMIT_MB` was 1536 MB. If the production variable were absent, admission safety could believe it had more process memory than the checked-in host allocation.

**Remediation**

The code fallback is now 1495 MB.

### Medium: Setup repeated controls already available one level above

**Finding**

Setup & Diagnostics repeated Provider Deck and Community & Pings navigation that already exists in Cinema Settings, increasing choice count without adding capability.

**Remediation**

Setup is now focused on repair/diagnostics:
- Repair Notifications;
- Test Media Endpoint;
- Refresh;
- Back to Settings;
- Close.

Movie Sources and Notifications remain first-class Cinema Settings destinations.

### Medium: lifecycle copy no longer matched the actual torrent lease model

**Finding**

The UI still said attached media would be reclaimed by the torrent idle timeout while the room stayed alive. After lease hardening, that statement was false for tracked Movie Night media.

**Remediation**

Lifecycle text now explains four separate clocks:
- live-viewer presence;
- 30-minute empty-room cleanup;
- 6-hour signed Watch links;
- process-restart behavior.

It also states that attached media stays leased while its room is alive.

## Setup model after audit

A guild owner should not need to understand torrent internals to use Cinema.

### Viewer path

No setup controls are shown on the main viewer surface.

### Staff path

`More -> Cinema Settings`

- **Viewer Experience**
  - Adult Content
- **Movie Sources**
  - Search Providers
  - RSS / Atom feeds
  - admin Reference Links
- **Notifications**
  - optional subscription configuration
- **Setup & Diagnostics**
  - core playback readiness and repair
- **Session & Lifecycle**
  - timing, current room, viewers, media, queue, votes

### Actual launch blockers

Only things needed for the requested function should block launch:
- View / Send / Embed permission in the channel;
- public media base URL;
- stream-signing secret;
- externally reachable media bind for external playback;
- libtorrent runtime;
- running media server.

Optional enhancements are warnings:
- notification role;
- Manage Roles for creating that optional role;
- Attach Files;
- PyAV verified metadata probing;
- optional Movie Sources;
- temporary capacity pressure is explained at media-admission time.

## UX reachability contract

Every normal viewer state must preserve a route to a useful next action.

- No room -> Start Watch Party / Start Private Session.
- Active, no movie -> Find Movie.
- Search results -> choose movie or return Home.
- Candidate -> choose release, queue, or return Results.
- Release -> play/request, queue, or return Candidate.
- Media attached -> Watch Movie.
- Stale viewer -> Rejoin Movie Night.
- Open collaborative vote -> contextual Yes / No.
- Host with another active viewer -> Pass Host.
- Error during an existing room -> keep current room navigation attached.
- Explicit Close is the only normal action intended to remove the panel.

## Known limits not disguised as fixed bugs

### Process restart

Room/session authority is in memory. A bot process restart ends the live room model and existing signed room pages no longer have a canonical room to query. Persisting a safe room snapshot is a separate architecture project because libtorrent handles themselves cannot simply be serialized and restored.

### Viewer scale

The torrent download session is shared, but each viewer receives a separate HTTP byte stream. Outbound bandwidth therefore scales with viewer count. The code has resource controls, but a promise such as “20 viewers with zero issues” requires production load testing on the actual host/network.

### Signed Watch links are bearer credentials

Dank Cinema signs Watch URLs for a specific Discord user identity and expiry, which avoids a separate browser login flow. The URL itself is still a bearer credential: manually forwarding a valid private-room owner's URL can grant that identity until expiry. Adding mandatory Discord OAuth would reduce that sharing risk but would also add login/token/callback complexity to every Movie Night. The audit keeps the current signed-link model and documents this boundary rather than pretending the browser can identify the human holding a forwarded URL.


### Direct media adult classification

A bare magnet or .torrent file may not provide trustworthy content classification metadata. The Adult Content setting does not pretend to infer content from arbitrary bytes.

### External provider quality

Structured providers are bounded, normalized, and isolated, but an external API/feed can still be unavailable or return poor metadata. Dank Cinema reports provider failures and retains the canonical built-in/direct paths.

## Maintainability risk

`commands_ext/public_movie_night.py` is a large product surface combining Discord presentation, setup, provider administration, catalog orchestration, and session actions.

A large mechanical split during this audit would create more regression risk than value. The audit therefore changes behavior only where evidence supports it. A later refactor should extract pure presentation/setup/provider-admin modules behind the current public API while keeping regression tests at the command boundary.

## Production canary checklist

After exact-head CI is green:

1. Open `/movie` as a normal member and verify the compact Home surface.
2. Start Watch Party with no notify role configured. Confirm room starts and announcement posts without a role ping.
3. Configure the notify role and confirm a later Watch Party can ping it.
4. Leave a search/catalog picker open longer than 35 seconds, then continue. Confirm presence renews and no inactive-viewer error occurs.
5. Solo host: confirm no useless movie/release vote buttons.
6. Add a second viewer: confirm collaborative voting controls return.
7. Find -> choose -> release -> Watch on Android.
8. Refresh Watch during loading and after playback begins. Confirm same-session progress/sync survives.
9. Pass Host to the second viewer and verify playback position does not reset.
10. Close host Watch page without passing host. Confirm authoritative position keeps advancing and host-away fallback remains usable.
11. Leave the room entirely empty past the configured empty-room TTL. Confirm the room auto-ends and the torrent lease is released.
12. Start another room in that channel afterward.
13. Exercise a bad magnet/provider/network failure and confirm current Cinema navigation remains attached.
14. Confirm no new invalid libtorrent-handle traceback from an active leased room.
15. Review process RSS/capacity telemetry against the 1495 MB deployment allocation.

## Definition of done

The audit is complete when:
- audit findings are represented by tests or explicit known-limit documentation;
- targeted Movie Night/provider/torrent tests pass;
- complete repository CI passes at the exact final head;
- PR diff is clean and scoped;
- mobile/production canary items that require a live Discord/Discloud environment are not falsely reported as automated successes.

No software audit can prove that future bugs are impossible. The standard here is narrower and useful: remove every evidence-backed defect found in this audit, cover known regression classes, keep failure modes recoverable, and document the remaining environment-dependent limits instead of hiding them.
