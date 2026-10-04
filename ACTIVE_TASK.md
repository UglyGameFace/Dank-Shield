# Dank Shield Active Task

## Active task / outcome

**DANK-SHIELD-430 — Dank Cinema premium 420 Lobby theater website**

Baseline:
`main@2397a03626bf49d48ee87dbd7b83480e99baa305` (PR #429 merged).

Active branch:
`fix/430-cinema-real-controls`

Issue:
**#430 — Dank Cinema: match premium 420 Lobby theater website mockup**

Pull request:
**#432 — Dank Cinema: replace mockup controls with real web actions**

Outcome:
Rebuild the signed Dank Cinema Watch page to match the owner-approved premium dark-green 420 Lobby theater mockup while preserving the canonical synchronized playback, signed access, torrent runtime, and Discord-owned movie programming paths.

## Scope

- signed `/movie/{room_id}/watch` theater page only;
- approved Dank Cinema / The 420 Lobby visual system;
- readable graffiti-style wordmark plus inline cinema mark;
- custom mobile-first video chrome;
- supported-browser casting through native Remote Playback / AirPlay picker APIs;
- remove the inline browser download control from the visible player;
- movie metadata/poster and queue projection from the existing canonical room state;
- stream-health-first UX with torrent detail kept under advanced diagnostics;
- mode-correct Private Session / Movie Night wording;
- focused regression tests and repository CI.

## Status

**Production canary failed the UX Definition of Done after PR #431 merged as `5783dcfdc0a1d4d1e817e6ccf8881e0ccf8bde5e`. The page looked like a mockup rather than a real product because several visible controls only wrote instructional text instead of performing actions, placeholder poster/avatar/profile elements rendered as if they were real data, and the Cast button relied on generic Remote Playback feature detection rather than a real Google Cast sender session. Issue #430 is reopened and remediation is active on `fix/430-cinema-real-controls`.**

## Findings / root cause

1. **The current Watch page is functionally mature but visually still a diagnostics panel.**
   - native video controls, external Play/Pause/End buttons, room ID text, and torrent statistics dominate the page;
   - the approved product direction makes the movie/player primary and moves technical diagnostics behind an advanced disclosure.

2. **Native inline video controls expose browser-owned actions such as download.**
   - Dank Cinema cannot reliably remove one browser-native menu item across every browser;
   - using custom player chrome removes the visible inline download control while the signed stream endpoint and authorization remain unchanged.

3. **Casting is browser/platform capability dependent.**
   - no second casting backend is required for the first implementation;
   - use the standards-based Remote Playback API where available and WebKit's playback target picker for supported Apple browsers;
   - unsupported browsers show casting as unavailable rather than pretending it worked.

4. **Existing canonical Cinema data already contains enough metadata for the approved theater composition.**
   - current candidate metadata can provide title/year/overview/TMDB poster;
   - room queue candidate IDs can be projected into a safe queue view;
   - no duplicate movie/session store is needed.

5. **Production exposed fake affordances.**
   - Home/Browse/My Stuff, header search/notifications, Viewers/Chat, Pass Host, and Manage Queue were visible buttons whose handlers only displayed notices;
   - blank poster cards and synthetic viewer/avatar/profile visuals made missing data look fabricated;
   - these controls are being removed or replaced with actions that actually navigate, open Discord, reveal live session state, or control playback.

6. **The first Cast implementation was not Chromecast integration.**
   - `HTMLMediaElement.remote.prompt()` / AirPlay feature detection does not create a Google Cast sender session;
   - the remediation uses Google's Web Sender SDK with the Default Media Receiver and a separately signed Cast media URL;
   - native Remote Playback/AirPlay are only used as fallbacks after the browser reports a real available target;
   - Cast is hidden unless at least one real casting transport is available and the selected media container is directly castable.

## Execution path

`signed Watch URL -> _room_and_user -> _state_payload -> custom Dank Cinema theater UI -> heartbeat/state/action -> canonical MovieNightManager + TorrentMediaManager`.

Artwork:
`candidate.metadata.poster_url -> strict image.tmdb.org allowlist -> CSP-authorized poster rendering`.

Casting:
`Google Cast Web Sender SDK -> Default Media Receiver -> signed torrent media URL -> room state continues as playback authority`.

## Changes

- Rebuilt the Watch page into the approved charcoal/deep-green premium theater layout.
- Added readable graffiti-style Dank Cinema branding and an inline graffiti/cinema reel mark without adding a second asset pipeline.
- Added custom player controls for play/pause, host seek, ±10 seconds, volume/mute, fullscreen, timeline, and cast.
- Removed native `controls` from the video and retained `controlsList="nodownload"` as an extra browser hint; context-menu download affordance is suppressed on the player.
- Added native Remote Playback / AirPlay target picking when supported.
- Added mode-aware room/host wording and `End Private Session` / `End Movie Night` labels.
- Added movie title/year/overview/TMDB poster projection to Watch state.
- Added queue projection from the existing room candidates.
- Added stream-health presentation while keeping progress, seeds/leechers, rate, and buffer target under Advanced Stream Details.
- Preserved signed access, sync/heartbeat, host action API, progressive torrent playback, and existing room authority.
- Pass Host and queue programming remain Discord-owned; the web mockup buttons route users back to the canonical Cinema panel rather than creating duplicate authority.
- Production follow-up removes the fake header/profile/search/notification affordances and fake Home/Browse/My Stuff actions.
- Navigation now exposes only real Theater, Queue, Details, and Discord actions.
- Session tab renders real room/viewer/sync state; Chat opens the actual Discord channel.
- Missing TMDB poster/overview data is hidden instead of replaced with fabricated placeholder content.
- Host sheet keeps only working Discord Controls, Fullscreen, Pause for Everyone, and End Session actions.
- Chromecast support now uses Google's Web Sender SDK and Default Media Receiver; availability-gated native Remote Playback/AirPlay remain truthful fallbacks where supported.
- Signed torrent stream responses now expose narrowly scoped gstatic CORS for Cast receiver fetches; the HMAC URL remains required.

## Validation / results so far

Regression tests updated/added for:
- existing sync/heartbeat/host control contracts;
- new Dank Cinema branding/player structure;
- casting feature detection hooks;
- no visible native video download controls;
- TMDB-only remote artwork sanitization;
- CSP allowing only the trusted TMDB image host;
- mode-correct private wording.

PR #431 exact-head CI ultimately passed all five workflow families and the PR merged. The production/mobile canary then failed usability because visible controls were not real actions. New regression coverage now asserts the fake navigation/actions/placeholders are absent, Google Cast sender integration is present, and Cast CORS only permits Google receiver origins. Exact-head CI for this remediation branch is pending.

## Cleanup / conflicts

- No second room model, torrent runtime, queue store, provider stack, or playback API was introduced.
- Private Session host-transfer semantics remain unchanged; the redesign does not silently overturn #429's authority rules.
- Existing viewer sync/drift correction and stream retry behavior remain authoritative.
- The user-facing page hides technical torrent detail by default but does not remove diagnostics.

## Blockers / risks

- Google Cast Web Sender and native remote-playback support are browser/device dependent. Unsupported browsers get no Cast button rather than a fake one.
- Chromecast receiver codec support can still reject a selected release even when local browser playback works.
- Custom controls need real Android/Discloud validation for fullscreen, audio gesture, sync, Google Cast discovery/load, and fallback behavior.
- A determined authorized viewer can still inspect network requests for the signed media URL; hiding the browser Download control is UI hardening, not DRM.

## Backlog

- full standalone Browse/Home/My Stuff website surfaces backed by persistent web identity/session data are outside this focused Watch-page redesign;
- first-class web Pass Host and queue editing would require explicit authority/API design instead of dead duplicate controls;
- **RSS Feed surfacing follow-up:** add a clean first-class RSS area in both Discord Cinema and the website using the existing Movie Sources/feed model. Feeds must be organized by source/category/status rather than dumped into one flat RSS list, with per-feed management and clear distinction from Search Providers and Reference Links.

## Next step

Run exact-head repository workflows for PR #432, repair only evidence-backed failures, inspect the final diff, then repeat the Android/Discloud canary. The canary must verify that every visible control performs a real action, unsupported Cast is hidden, supported Google Cast actually opens device discovery and loads the signed stream, missing metadata does not render fake content, and core host/viewer sync remains intact.

---

## Previous completed task / outcome

**DANK-SHIELD-405-FOLLOWUP — eliminate slow-swarm play/stop glitching and invalid partial stream bodies**

Production:
PR #427 merged as `fe5e7b70616eefe401126b19597086cf6621f70e`.

Validation:
Exact-head `ae13ae720b950908565ff61d2b7a146c6eb2099f` passed all six required workflow families. The user then confirmed the production host/viewer canary continues playing correctly. Issue #405 was closed completed.



**DANK-SHIELD-425 — Dank Cinema master audit: setup, UX, lifecycle, playback, providers, and capacity**

Production baseline:
`main@3c8989e6bfa70fd463e1914bfa96d60e9f3a6233` (PR #424 merged; Discord menu presence/Rejoin follow-up exact-head CI green).

Active branch:
`audit/425-cinema-master-audit`

Issue:
**#425 — Dank Cinema master audit: simplify setup, navigation, lifecycle, and playback**

Status:
**Full end-to-end audit is active. The audit covers the real Discord, room, Watch, provider, torrent, persistence, capacity, and test paths. Evidence-backed defects are being repaired on this branch while known environment/architecture limits are documented instead of being mislabeled as fixed. Exact-head CI and live mobile/Discloud canary remain pending.**

## Audit contract

Primary viewer path stays:
`Cinema Home -> Find Movie -> Choose Movie -> Choose Release -> Watch`.

Rules:
- preserve feature depth;
- show contextual controls only when actionable;
- keep rare/admin/technical controls under More / Cinema Settings;
- auto-detect or safely repair setup where possible;
- optional features must not block core playback;
- every room-scoped error must preserve useful Cinema navigation;
- no second provider stack, torrent runtime, media server, or room model;
- regression-test every production bug class discovered from #408 forward.

Detailed audit record:
`docs/DANK_CINEMA_MASTER_AUDIT.md`.

## Architecture verified

Discord:
`/movie -> MovieNightHubView -> search/catalog -> candidate -> release -> Watch`.

Authority:
`MovieNightManager -> MovieNightRoom -> host/viewers/votes/queue/playback clock`.

Watch:
`signed per-user URL -> state/heartbeat/action -> canonical room + torrent runtime`.

Torrent:
`TorrentMediaManager -> shared identity/session -> room leases -> signed byte-range route`.

Providers:
`TMDB/JustWatch + Internet Archive + direct magnet/.torrent + generic JSON/RSS/Atom/Torznab resolver`.

Persistence:
- guild provider/preferences/config persist;
- live Movie Night room authority is currently in memory.

## Findings and current repairs

### Critical — abandoned rooms could retain leased media indefinitely

A room previously had no inactivity expiry, while #423 correctly prevents leased torrents from ordinary torrent idle cleanup. With no viewers, that combination could hold a tracked torrent lease until explicit End Movie Night or process restart.

Repair in progress/implemented:
- `DANK_MOVIE_NIGHT_EMPTY_ROOM_TTL_SECONDS` defaults to **1800 / 30 minutes**;
- short **35-second** live-viewer TTL remains unchanged for sync/quorum;
- room expiry uses last real room presence;
- active viewers prevent expiry;
- cleanup rechecks eligibility;
- room is synchronously ended before external lease cleanup;
- canonical termination releases media and retires room state;
- cleanup worker starts with Movie Night public routes.

### High — optional notifications blocked Watch Party launch

Notification role, pingability, and Manage Roles were product-enhancement concerns but were treated as core launch blockers.

Repair:
- missing/unpingable notification role is a warning;
- missing Manage Roles only warns that the optional role cannot be created;
- public Watch Party still posts a channel announcement without a role;
- configured valid role is still pinged;
- Setup action is **Repair Notifications**.

### High — media-server process readiness was only a warning

A configured URL/secret could report launch-ready even when the actual Watch media server was not running.

Repair:
- live media-server readiness is a launch blocker.

### Medium — PyAV metadata probing blocked playback

PyAV is used for verified codec/audio metadata, and probe failures already fail soft.

Repair:
- PyAV is now an optional warning;
- libtorrent remains required;
- playback can launch without verified metadata probing.

### Medium — one-person Watch Party exposed pointless voting UI

Repair:
- solo candidate/release vote buttons hidden;
- solo queue action says **Add to Queue**;
- host says **Play This Release**;
- collaborative controls return when another active viewer joins;
- non-host action says **Request This Release**.

### Medium — direct-media failures could lose room context

Repair:
- magnet/.torrent startup and signed-stream errors keep the active room embed/controls;
- setup errors point to the real **More -> Cinema Settings -> Setup & Diagnostics** path.

### Medium — torrent process-memory fallback exceeded checked-in host RAM

Repair:
- code fallback changed from 1536 MB to the checked-in Discloud allocation of **1495 MB**.

### Medium — Setup repeated Settings navigation

Repair:
- Setup & Diagnostics now focuses on **Repair Notifications**, **Test Media Endpoint**, Refresh, Back, Close;
- Provider Deck and Notifications remain first-class Cinema Settings destinations instead of being duplicated inside Setup.

### Medium — lifecycle copy contradicted lease hardening

Repair:
- lifecycle text now separately explains live-viewer expiry, 30-minute empty-room expiry, 6-hour signed Watch links, media lease ownership, and process-restart behavior.

## Preserved behavior

- Watch Party announcements and collaborative voting;
- Private Viewing owner-only isolation;
- Pass Host and host-away playback fallback;
- queue;
- progressive-disclosure Home/More/Settings UI;
- Rejoin after short Discord/Watch presence expiry;
- TMDB/JustWatch;
- generic structured providers;
- direct magnet/.torrent;
- adult-content guild setting;
- first-release mobile selection fix;
- refresh/reconnect/session-aware sync;
- adaptive group buffering;
- shared torrent identities and per-room leases;
- dynamic memory/disk admission;
- invalid libtorrent-handle containment;
- canonical End Movie Night cleanup.

## Validation added/updated

Manager:
- empty-room timeout starts from last presence;
- active viewer prevents room expiry.

Session lifecycle:
- inactive room cleanup releases media and retires only the stale room;
- cleanup worker registration is part of Movie Night public-route startup.

UI:
- notification role is optional for launch;
- public no-role announcement still posts;
- media server required but PyAV optional;
- solo/public/private action labels and vote visibility;
- simplified Setup buttons;
- accurate viewer/empty-room/link/restart lifecycle text.

Capacity:
- `.env.example` contains empty-room TTL;
- torrent memory fallback must match 1495 MB deployment allocation.

## Known limits / not falsely claimed fixed

- live room authority is in memory and does not survive process restart;
- each Watch viewer receives a separate outbound HTTP stream, so high viewer counts require real load testing;
- arbitrary direct magnet/.torrent media cannot be reliably adult-classified without trustworthy metadata;
- external provider availability/metadata quality remains outside Dank Shield's control;
- `public_movie_night.py` is a maintainability hotspot, but a large mechanical module split is deferred because it would add regression risk during a correctness audit.

## Blockers / remaining work

1. finish static/diff inspection for the final audit branch;
2. update all expectations affected by simplified Setup/notification/lifecycle semantics;
3. run targeted Movie Night/provider/torrent tests through repository CI;
4. patch only evidence-backed failures;
5. verify Discord component IDs/rows/emoji remain valid;
6. exact-head full workflow set must be green;
7. mobile/production canary from the audit document remains operator validation, not an automated success claim.

## Backlog after #425

- optional persistent room snapshot/recovery across process restart;
- practical 20-viewer load/capacity validation on the actual hosting/network path;
- optional majority-approved Claim Host fallback when the host disappears without passing control;
- separate Movie Ready notification refinement if a second notification beyond room-start announcement is still desired.

## Next step

Complete test expectation cleanup, open the focused #425 audit PR, run exact-head CI, repair any evidence-backed failures, then merge only after the final head is clean and green.


---

## Previous completed Share Router direct-memes follow-up

**DANK-SHIELD-397 — Share Router parity for direct memes-channel video posts**

PR #399 merged to production `main` as:
`e647876844f9766c119b62534bd20e43af0dd8aa`.

Final exact-head `02e886fd84d0a5d04471a3c6e35e0945ef3e1390` passed:
- Dank Shield CI #3538;
- Profile Runtime Diagnostics #2084;
- Dank Design Regression CI #1616;
- Application Command Size Diagnostics #2395;
- Ticket Owner Emergency Override #2109.

That baseline keeps one Share Router listener/media stack, resolves the configured memes destination from the canonical `share-memes` route, reuses the native-video upload owner, preserves proxy behavior, and leaves direct memes posts non-destructive.

---

## Previous completed Dank Cinema provider follow-up

**DANK-SHIELD-MOVIE-NIGHT-EASY-SOURCES / dual provider modes**

PR #398 merged to production `main` as:
`d0b7b9cc34ab4725bccf7c12526409bb75539d8e`.

That merged baseline includes:
- zero-setup TMDB catalog matching and JustWatch availability;
- built-in Internet Archive Feature Films playable search;
- direct magnet/.torrent playback;
- JSON Provider and external Search Link modes;
- acknowledgement-before-persistence on Search Link setup;
- Discord link-button URL-budget validation;
- bounded Provider Deck field rendering and dual-mode guidance.

At the #397 task transition, post-merge main CI #3533 was still running. Same-SHA production/mobile Dank Cinema canary remains an operator acceptance item and is not represented as completed evidence here.

---

## Previous completed Movie Night follow-up

**DANK-SHIELD-MOVIE-NIGHT-FOLLOWUP — simple custom-source UX + complete session termination**

PR #395 merged to production main as:
`d8a3f2ddd0ece13dfe2164176076ecc5c3988cc1`.

That baseline includes:
- host-confirmed and viewer-voted complete Movie Night termination;
- canonical shared torrent lease cleanup;
- web-player termination convergence;
- internal source-ID hiding/generation;
- first-class custom-source edit / enable / disable / remove.

---


## Previous completed baseline

**DANK-SHIELD-MOVIE-NIGHT-393 — dynamic media capacity + shared torrent reuse**

PR #392 / issue #391 are merged to production main as:
`151076e83ec49a8a09a1856fbedd5dd5a61a62df`.

Issue #393 was completed and merged as PR #394 into:
`7d09c89c15ed48067cb97ee32bc900931e1833dd`.

Previous implementation branch:
`feat/movie-night-dynamic-capacity`

Current host facts supplied by the owner:
- allocated RAM: **1.46 GB / ~1495 MiB**;
- ordinary Dank Shield RSS observed before Movie Night load: **~340–390 MB**;
- current free disk reported by Movie Night setup: **~1.17 TiB**;
- Discloud plan supports Site deployment, so the same `main.py` process can keep Discord ownership while exposing the media-only HTTP surface.

## Current task contract

1. Keep one canonical torrent runtime; do not create a second media stack.
2. Protect the core Discord bot with dynamic memory admission before starting a new **unique** torrent.
3. Start conservatively at 2 unique torrents on this host; keep a configurable hard ceiling of 4 while burst admission remains disabled by default.
4. Reuse an existing torrent session/file cache when multiple Movie Night rooms choose the same canonical torrent identity.
5. Track per-room leases so one guild switching/ending cannot delete a torrent still used by another room.
6. Preserve independent Movie Night clocks/viewers even when rooms share one torrent session.
7. Raise the practical disk-backed movie ceiling to 25 GiB, 50 GiB declared torrent budget, and a 64 GiB free-disk safety reserve.
8. Expose current RSS, protected memory reserve, unique torrent count, leases/shared sessions, admission slots, disk reserve, committed media, and free disk in Movie Night Setup.
9. Convert the checked-in Discloud profile to the Site-capable 1.46 GB deployment target.
10. Document the exact production `.env` values and protect secrets.
11. Add regression coverage for memory rejection, disk rejection, identity reuse, shared lease release, replacement, and capacity telemetry.
12. The 300,000+ guild scale target is based on concurrent unique media sessions, not installed guild count; the public `/movie` UX must remain compatible with a future external media-worker pool.

## Scope

Build the real torrent media runtime, not a decorative command:

- accept user-supplied magnet links;
- accept user-supplied `.torrent` attachments;
- resolve metadata;
- select a playable video file;
- progressively prioritize pieces for startup and seeks;
- expose the selected file through a signed HTTP byte-range stream;
- integrate ingestion into the existing Share Router route owner;
- enforce strict session, disk, bandwidth, metadata, peer, timeout, and cleanup limits;
- keep the structured bot/admin API private;
- do not add torrent indexing/search/discovery.

Use is limited to lawful, public-domain, or otherwise user-authorized media.

## Movie Night release selection + custom sources

Expanded requirements now implemented in the active branch:

- release variants carry live `seeds`, `leechers`, total peers, seed/leech ratio, and a human swarm-health label;
- default release ordering is availability-first when votes are tied: live seeds → seed/leech balance → leech count → verified quality/codec efficiency → file size;
- viewer votes remain authoritative once the room starts choosing between variants;
- zero-seed/dead variants sort behind live alternatives by default;
- one movie candidate can hold multiple quality/release variants instead of one opaque source;
- variants retain source ID/display label provenance;
- verified media metadata and release-name inference remain separate truth levels;
- guilds have a revisioned custom-media-source registry persisted through canonical guild-config CAS;
- custom sources can be added, updated, enabled/disabled, and removed;
- custom source URLs require HTTPS and reject embedded credentials plus obvious local/private/reserved literal addresses;
- the future resolver must revalidate DNS/network destinations at request time before fetching;
- custom-source results will merge into the same Movie Night variant list and voting/queue model;
- no temporary duplicate source-config command is being added; the first canonical Movie Night manager owns Sources → Add / Enable / Disable / Remove.

New regression coverage:
- seed/leech/peer status exposure;
- seed-first default variant ordering;
- source provenance retention;
- custom source parse/add/update/disable/remove round-trip;
- unsafe custom source URL rejection;
- atomic guild-config CAS ownership.

## Community & Pings Movie Night role integration

Movie Night role ownership now uses the existing generic Community & Pings system:

- canonical capability: `movie_night_notify`;
- runtime role lookup is capability-based, not name-based or hard-coded-ID-based;
- direct Community & Pings manager control: **Movie Night Notify**;
- direct mapping is limited to enabled safe notification options;
- Movie Night role registration helper creates/normalizes a notification option and assigns the capability uniquely;
- existing prerequisite/exclusivity/removability/group/presentation/unrelated capability state is preserved when mapping an existing role;
- /toke start/notify capabilities are preserved;
- role rename/styling after setup is safe;
- setup can call `movie_night_registration_blocker()` before creating a Discord role so the 25-option cap cannot leave an avoidable orphan role;
- member opt-in remains owned by the existing Community & Pings picker and per-member lock;
- no second Movie Night role table/config key is introduced.

Regression coverage now checks capability payload round-trip, unique reassignment, preservation of existing rules and /toke capabilities, direct manager mapping, notification-only filtering, and option-capacity preflight.

## Public Movie Night command + complete setup

The feature now has a real public doorway and setup surface instead of backend-only state.

Public entry:
- `/movie` opens the canonical Movie Night hub;
- `/movie magnet:<link>` attaches an authorized magnet to the active/current-channel room;
- `/movie torrent:<file>` accepts an uploaded .torrent metadata file;
- Dank Home → Community & Engagement → **Movie Night** routes to the same owner;
- no duplicate `/movienight`, `/movie-search`, or setup command tree is introduced.

Hub controls:
- Start / Join;
- Search / Vote;
- Queue;
- Vote Yes / Vote No;
- Sources;
- Setup;
- Community & Pings;
- Refresh;
- Close.

Complete setup checks:
1. Movie Night notification role exists and maps through Community & Pings capability `movie_night_notify`;
2. role notification ping is actually usable;
3. current channel has View Channel / Send Messages / Embed Links and reports Attach Files status;
4. libtorrent runtime is installed;
5. PyAV/FFmpeg metadata runtime is installed;
6. `DANK_MEDIA_PUBLIC_BASE_URL` exists;
7. dedicated `DANK_TORRENT_STREAM_SECRET` exists;
8. externally-addressed media uses an externally reachable bind host (Discloud: `0.0.0.0`);
9. the dedicated media server reports started;
10. custom source counts/enabled state are visible;
11. **Test Media Endpoint** performs a real health request after deferring the Discord interaction.

Role setup behavior:
- **Create / Repair Role** preflights Community & Pings capacity before creating Discord state;
- new role is created as a ping-ready Movie Night notification role;
- role is registered atomically into the existing Community & Pings config;
- if persistence loses a CAS race/fails, the newly-created Discord role is deleted as rollback;
- repairing an existing mapped role preserves the capability model and can make it mentionable when the bot otherwise cannot ping it safely;
- no second Movie Night role config authority exists.

Source setup behavior:
- **Sources** lists guild-owned custom sources with provenance and revision;
- **Add / Update Source** persists through the canonical CAS registry;
- **Manage Source** supports Enable / Disable / Remove;
- source setup remains staff-only;
- normal members can still open Movie Night and their own Community & Pings choices.

Command-surface contract:
- intentional final public surface is now 10 items including `/movie`;
- attachment doorways are intentionally limited to `/dank upload:file`, `/toke upload:`, and `/movie torrent:`;
- navigation registry and compact command audits are updated to fail closed on drift.

Regression coverage includes:
- `/movie` schema and optional attachment type;
- hub/setup/source component labels and Discord component limits;
- navigation aliases such as `movie night`, `watch party`, `group streaming`, and `torrent streaming`;
- external-media bind readiness;
- complete ready-state evaluation;
- media health test acknowledgement before network I/O.

## Historical #391 hosting constraint (superseded by active #393)

At the time #391 was implemented, production `discloud.config` was `TYPE=bot`.
Issue #393 intentionally changes the checked-in deployment target to `TYPE=site`
with `RAM=1495` now that the owner confirmed a Site-capable Diamond plan.

Discloud Bot deployments do not expose an external HTTP port. Externally reachable
web/API/bot-with-web-interface deployments use `TYPE=site` and Discloud proxies
traffic to `0.0.0.0:8080`.

Therefore:

- the torrent engine can run under the current bot process;
- actual external playback needs a public media endpoint;
- the implementation uses a **separate media-only server** on the media port;
- the structured admin API stays on `127.0.0.1:8081`;
- production `discloud.config` is intentionally not changed automatically because
  Site hosting depends on the account plan/subdomain.

## Architecture

### `stoney_verify/torrent_streaming.py`

Canonical torrent runtime owner:

- pinned `libtorrent==2.1.1`;
- live-session cap with one-session default for the current 512 MB host;
- magnet and `.torrent` ingestion;
- normalized BTIH identity across hex/base32 magnets;
- metadata timeout and input limits;
- torrent total-size and selected-file limits;
- largest supported playable video selection;
- non-selected files priority 0;
- startup piece priority 7;
- bounded readahead priority 6;
- tail priority for MP4-style end metadata;
- seek reprioritization from HTTP byte ranges;
- piece-availability wait before sparse-file reads;
- signed temporary stream URLs;
- periodic idle cleanup and file deletion.

### `stoney_verify/api_new/torrent_stream_routes.py`

- public byte-range stream handler;
- correct `Accept-Ranges` / `206 Partial Content` behavior;
- signed URL validation;
- buffering `503` when the requested first chunk is not ready;
- internal-auth status/cancel routes.

### `stoney_verify/torrent_media_server.py`

Dedicated public media-only server:

- no ticket/member/admin endpoints;
- public stream URL must be HTTPS outside localhost development;
- own bind host/port;
- requires dedicated `DANK_TORRENT_STREAM_SECRET`;
- starts through native `app.py` lifecycle.

### Share Router

- detects a magnet in the source message;
- detects `.torrent` attachments;
- refuses to join a swarm before public media/signing configuration exists;
- dedupes recent torrent sources before starting duplicate magnet sessions;
- creates one canonical torrent session;
- posts the temporary playback URL and media details;
- deletes the proxy source only after successful stream creation;
- leaves failed/unconfigured sources intact and logs the blocked route.

## Historical #391 resource defaults (superseded by active #393)

The original #391 conservative defaults were:

- live sessions: 1;
- metadata: 4 MiB;
- selected video: 2 GiB;
- total torrent declared size: 4 GiB;
- startup window: 8 MiB;
- tail probe: 4 MiB;
- readahead: 16 MiB;
- metadata wait: 30 s;
- buffering wait: 20 s;
- idle TTL: 30 min;
- peers/connections: 80;
- download cap: 8 MiB/s;
- upload cap: 512 KiB/s.

All are generic environment settings, not guild hardcoding.

## Regression coverage

New `tests/test_torrent_streaming.py` covers:

- magnet and `.torrent` detection;
- base32/hex BTIH normalization;
- supported video formats/content types;
- normal/open/suffix HTTP range parsing;
- invalid/multi-range rejection;
- largest playable file selection;
- selected-file-only priorities;
- startup/tail priority behavior;
- seek + readahead piece math;
- requested-piece availability waits;
- live-session capacity;
- signed stream URL validation/tamper rejection;
- HTTPS-only public media policy;
- isolated public media server ownership;
- Share Router magnet / `.torrent` integration markers.

## Validation required

Before merge:

- compile/import;
- pinned libtorrent installs successfully on CI Python 3.11;
- focused torrent streaming tests;
- focused Share Router tests;
- structured API security tests;
- full Dank Shield pytest/CI;
- application command diagnostics unchanged;
- final diff/reference audit;
- branch remains 0 behind production main;
- no public admin API exposure;
- no arbitrary torrent index/search feature.

After merge/deploy:

1. first deploy under existing `TYPE=bot` should keep the media server disabled unless configured;
2. confirm the Discord bot and private structured API remain healthy;
3. when a Discloud Site/subdomain is intentionally configured, expose only the media server on `0.0.0.0:8080`;
4. test a known legal/public-domain magnet;
5. confirm playback starts before the entire selected file completes;
6. seek forward and confirm piece reprioritization/buffering recovers;
7. test a legal `.torrent` attachment;
8. verify duplicate magnet share does not start a second session;
9. verify idle cleanup removes the handle and files;
10. verify tampered/expired stream URLs fail.

## Suspended work

Issue #388 / PR #389 — **Share Router native video + X/Twitter dedupe** — merged as
`585f06121ca3c20759aac0bd43f506b82c9cb7f0`; exact-head workflows passed.
Its production Android canary is suspended by this FORCE SWITCH.

Issue #390 — **Universal Share Router media resolver and stream playback** remains queued.
Torrent support is being built first by explicit FORCE SWITCH and should later plug into that
broader resolver instead of being reimplemented.

Issue #386 / PR #387 — **Toke media** — merged previously; post-merge Android canary remains suspended.

Issue #380 — **True master runtime ownership + production-path audit** remains queued.

Issue #384 — **Audit repeated Discord 429s during/after activity recovery** remains backlogged.

## Suspended Protection task record

## Production evidence

After PR #376 deployed successfully on Discloud:
- the original red Discord timeout changed behavior;
- pressing **Home → Safety & Moderation → Protection** can now appear to do nothing;
- PR #376 already defers before persisted reads and edits the original panel after loading.

The failed canary proves acknowledgement alone was insufficient.

## Root cause found after PR #376

The merged refresh owner acknowledges the interaction and then waits on:

- fresh guild configuration load;
- Spam Guard settings load.

Those reads were joined without any bounded completion deadline or degraded fallback.
If either read stalls after Discord has already acknowledged the click, mobile no longer shows a timeout error; it simply leaves the user staring at an unchanged/deferred interaction.

Additionally, Spam Guard can return an `unavailable:...` source without throwing, so exception-only degradation would be incomplete.

A second canary/CI pass exposed one more pre-ack hole: public Protection entry routes still called `_require_setup_permission()` before the shared refresh owner. For delegated staff, that permission path can consult configured control-role state and may synchronously fetch configuration on a cold cache. The protection refresh itself could therefore be perfectly deferred while the button still stalled before it ever reached that refresh.

## Current fix

The shared Protection owner now:

1. uses one idempotent Protection-entry acknowledgement helper **before authorization and backend I/O** across the slash command, categorized navigation, legacy home button, setup button, advanced setup route, service entry, and Invite Shield return path;
2. immediately edits the original interaction to a visible **Opening Protection Center** loading state;
3. loads guild config and Spam Guard behind bounded, independent read-only waits;
4. uses the existing cached guild config path when a fresh config refresh is slow/unavailable;
5. falls back to explicit unknown/default-safe Spam Guard state when that source cannot load;
6. treats returned `unavailable:...` Spam Guard state as degraded even without an exception;
7. still renders the Protection Center in degraded mode instead of leaving the button visually dead;
8. disables all mutating controls while live state is incomplete;
9. leaves only **Retry Live State** and **Close** enabled in degraded mode;
10. shows an explicit warning that no protection setting was changed by the fallback;
11. keeps live security-stat repair after the panel is already visible;
12. uses one generic runtime-configurable load budget:
    `DANK_PROTECTION_PANEL_LOAD_TIMEOUT_SECONDS`.

## No hardcoding contract

This fix contains:
- no guild IDs;
- no role IDs;
- no channel IDs;
- no server names;
- no owner-specific exceptions;
- no per-server timeout branch.

The load budget is process configuration and applies generically to every guild.

## Safety

- no guessed protection state may authorize mutations;
- incomplete live state fails closed;
- cached state may be displayed with an explicit degraded warning, but mutation remains locked;
- existing AntiNuke owner-only controls remain owner-only;
- no changes to AntiNuke hostile-reputation semantics from PR #376;
- no global guild sweep;
- no database migration;
- no new persistence authority.

## Validation required

Before merge:
- regression coverage that every public Protection entry acknowledges before its permission check;
- focused runtime test that acknowledgement is idempotent;
- focused runtime test for visible loading before slow reads;
- focused runtime tests for a hung config read;
- focused runtime tests for a hung Spam Guard read;
- cached-config fallback test;
- returned `unavailable:...` Spam Guard degradation test;
- degraded view enables only Retry + Close;
- static check that loading UI appears before backend reads;
- exact-head full CI;
- exact-head Protection/AntiNuke focused checks;
- branch 0 behind main;
- diff hygiene.

After merge:
- exact merge SHA Discloud success;
- canonical post-merge CI;
- Supabase workflow if triggered;
- Android canary:
  1. open Safety & Moderation;
  2. press Protection;
  3. confirm immediate loading state;
  4. confirm final Protection Center opens;
  5. press Refresh;
  6. confirm it remains responsive;
  7. if backend is degraded, confirm Retry/Close only and no settings mutation.

## CI failure already resolved

Exact head `3fea2335e2309f3318072ff6e591ea8b66dbec53` failed Dank Shield CI only because the old acknowledgement regression expected the first panel edit to occur after config/spam reads. The new visible-loading contract intentionally performs a loading edit before those reads. The test now verifies the correct order: defer → loading edit → config/spam → final edit → stats.

That review also exposed and fixed the remaining pre-ack permission-check hole described above.

Exact head `7bf5c3edca7960e708df4825f581d6f84453d47f` then failed only on two stale test contracts:
- a static test still required the literal `interaction.response.defer` inside `_refresh_panel()` even though acknowledgement is now deliberately centralized in `_ack_protection_entry()`;
- an advanced-setup test used a minimal fake response that never modeled acknowledgement because the old route performed permission checking first.

Those tests now validate the new architecture directly: the shared ack helper owns Discord defer, and the setup route order is **ack → permission → Protection refresh**.


## Protection runtime-ownership correction

The latest CI failure exposed a deeper architecture defect in the setup compatibility layer:

- `stoney_verify/commands_ext/public_setup_compact.py` was still monkey-patching `setup._open_protection_options` at import/runtime;
- that wrapper performed setup authorization before the canonical Protection acknowledgement owner;
- the permanent setup audit and runtime-integrity test incorrectly required that monkey patch to exist, so the audit could pass while violating the stated native-ownership goal.

Correction on this branch:

- retired the Protection-specific runtime reassignment and saved-original-function attribute;
- Protection setup navigation now calls the native `public_setup_recommend._open_protection_options` directly;
- changed runtime-integrity coverage to require the native function and absence of `_DANK_SETUP_ORIGINAL_OPEN_PROTECTION_OPTIONS`;
- changed `tools/audit_setup_safety.py` to fail if the retired Protection wrapper, saved-original marker, or reassignment returns.

This correction is part of the same Protection incident because the runtime replacement directly caused the pre-ack permission regression. Other unrelated setup compatibility wrappers remain outside this active task unless evidence shows they affect Protection.

## Next step

Validate PR #377 on the new exact head. Patch only evidence-backed failures. If CI is green, complete final diff/branch hygiene, mark ready, merge with the exact expected head, verify post-merge CI/Supabase/Discloud, then rerun the Android Protection canary. Do not close issue #375 until that live canary passes.


## Post-merge Android canary failure — permanent loading card

Production merge SHA: `1fac08e87c534328ee94263156bcbd1b3d33c3e7`.

Android canary evidence after PR #377:
- Home → Protection acknowledges successfully;
- the original private response is replaced with **Loading Protection Center…**;
- the loading card can remain indefinitely and the final controls never appear.

Execution-path finding:
- `_refresh_panel()` bounded only guild-config and Spam Guard reads;
- after those reads, the real AntiNuke readiness/embed construction, Protection view construction, and final Discord edit were outside the terminal-state guarantee;
- Home/navigation callers invoke the canonical refresh directly, so an exception after the loading edit can leave the loading card as the permanent UI;
- existing tests stubbed `_protection_embed` and `ProtectionCenterView`, so they did not exercise the production render/send boundary;
- the AntiNuke Permission health field could exceed Discord's 1,024-character embed field-value limit because multiple verbose hierarchy/channel blockers were joined without truncation. A Discord 400 on the final edit then retried the same invalid payload and could leave the loading card unchanged.

Current hotfix branch: `fix/protection-loading-terminal-state`.

Hotfix behavior:
- cap the dynamic AntiNuke field to Discord's field-value limit;
- make the canonical refresh owner catch unexpected state-load and panel-render failures;
- bound the final Discord edit;
- if the final panel cannot be rendered/sent, replace the loading card with a plain terminal error containing an Error ID;
- never resend the same rejected full panel as the fallback;
- add regression tests for overlong AntiNuke health content, render failure after loading, and final-edit rejection after loading.

Issue #375 remains open. Do not claim the Protection incident resolved until exact-head CI, merge/deploy, and Android canary all pass.


## PR #378 Android canary — actual render crash and stale setup navigation

The post-merge Android canary on production `0ce2b74856262be1b74ef3066426d85b89136d9f` produced terminal Protection errors instead of hanging, which exposed the exact remaining runtime conflict.

Production diagnostics/logs recorded both navigation routes failing at the same renderer boundary:

- Error IDs `DANK-653ABC6A` and `DANK-50BEDB33`;
- stage `protection_panel_render_failed`;
- `TypeError: _patch_ui.<locals>.embed() got an unexpected keyword argument 'channel'`;
- callers included `dank:navigation:feature:protection:v1` and `dank_setup_security:protection`.

Root cause:
- `anti_nuke_product_policy_runtime._patch_ui()` still replaced the canonical Protection embed, view class, and AntiNuke UI callbacks after import;
- its replacement embed retained the old four-argument signature while the canonical Protection renderer now supplies `channel=` and `load_warning=`;
- this was a live runtime monkey patch that the prior ownership audit did not prohibit.

Related navigation evidence from the same canary:
- `/dank home` already uses `navigation_registry.CATEGORIES`;
- `/dank setup` still used a separate hard-coded nine-item `FEATURE_AREAS` list;
- tests explicitly required those stale labels, so CI preserved the divergence.

Current correction:
- Strict Lockdown UI/state/toggles now live natively in `commands_ext/public_protection_center.py`;
- canonical Protection accesses AntiNuke through the module service owner so post-import policy/readiness behavior is not bypassed by stale captured aliases;
- `anti_nuke_product_policy_runtime.py` retains engine/policy behavior but no longer replaces Protection UI functions/classes;
- obsolete self-applying Protection presentation guards are deleted after reference verification;
- setup's feature picker derives from `navigation_registry.CATEGORIES` and routes through the canonical category owner;
- unit/static tests and `tools/audit_setup_safety.py` now reject Protection UI rebindings, retired patch files, and a standalone setup taxonomy.

Validation is still required. Do not claim issue #375 resolved until exact-head CI passes, the branch is merged/deployed, and Android canary confirms both Protection entry routes and the setup category picker.


## PR #379 CI failure correction

First exact-head CI on `f431928dc1de0efed617c2b5958d3fabef77a968` completed with **2390 passed / 9 failed** in Dank Shield CI. All other workflows passed.

The 9 failures were stale regression contracts, not a new production runtime failure:

- 2 AntiNuke trust tests still monkey-patched removed `public_protection_center.get_antinuke_settings` / `save_antinuke_settings` aliases even though Protection now intentionally calls `anti_nuke_service` as the authoritative runtime owner.
- 7 setup picker tests still required the retired `core/tickets/verification/security/logs/design/history` route map. The new picker routes canonical `navigation_registry.CATEGORIES` keys through `public_command_surface_v2._open_category`; the old FakeResponse then failed on the real Discord `response.is_done()` contract.

Correction:
- AntiNuke trust tests now patch `protection.anti_nuke_service`, matching the actual execution path.
- the setup picker regression test now asserts every canonical category routes through `public_command_surface_v2._open_category`; it no longer preserves the retired route map.

Current validation head: `71bb1c5375233a7595171d4153561062da20dfd3`. Dank Shield CI run #3346 and the companion workflows are in progress. Do not merge until this exact head passes.


## PR #382 CI correction

Exact-head Dank Shield CI on `17e4e74a259502582559431b08429377c1d39a86` finished with **2394 passed / 1 failed**. All companion workflows passed.

The single failure was a stale navigation regression contract in `tests/test_navigation_registry_runtime.py`. It still required `public_toke.py` to own `if replace_message:`, `_defer_update`, and `_defer_private` even though this task intentionally retired the duplicate Toke setup owner and delegates setup to `public_community_pings.py`.

Correction:
- the test now requires the compatibility entrypoints to delegate `replace_message` into the canonical Community & Pings owner;
- it explicitly rejects the retired local defer helpers from returning to `public_toke.py`;
- the canonical `public_community_pings.py` replace-message acknowledgement contract remains required.

Current validation head: `ca3299e7216474a7c961648c3c603d4f723a749c`. Do not merge until exact-head CI passes.


## PR #382 second CI correction

Exact-head Dank Shield CI on `f87431cb52060ceab58a9a47414c392869cb0704` again finished with **2394 passed / 1 failed**. Companion workflows all passed.

The single failure came from an overbroad negative assertion added in the previous correction. It banned `await _defer_private(interaction)` from the entire `public_toke.py` module even though the real member-facing `open_toke_command` legitimately uses that defer before config reads and message send work.

Correction:
- the negative assertion is now scoped only to the retired setup compatibility block between `open_toke_preset_setup` and `TokeCheersView`;
- the test explicitly confirms the live `open_toke_command` still owns its valid private defer.

PR #382 subsequently passed exact-head CI and merged. The active follow-up is PR #383; validate its exact head before merge.


## Android canary follow-up — role mappings hidden

The large-server channel picker now reaches and saves the expected `#general` channel. Android canary then exposed the next blocker in the same /toke setup flow:

- manager shows `Starter: Not configured`;
- manager shows `Notify: Not configured`;
- preferred channel is correctly configured;
- `/toke` rejects execution because both required role capabilities are absent;
- the only configured option in the reported server is `Stoner`.

Root cause:
- `toke_start` / `toke_notify` are capabilities on Community & Pings options;
- setup existed only inside the generic **Edit Option** editor via **Toke Starter** / **Toke Notify** buttons;
- the manager exposed status but no direct role-mapping control, making the required setup effectively undiscoverable.

Correction on the active branch:
- Community & Pings Manager now exposes direct **Toke Starter** and **Toke Notify** controls beside the Toke channel controls;
- each direct control picks from enabled, safe existing Community & Pings options;
- selecting an option assigns that capability to exactly one option and removes the same capability from any previous option;
- one option may own both capabilities, so an existing Stoner option can be both starter and notification role;
- persistence remains the existing `community_pings_v2` model via `_save()`; no legacy duplicate role-ID settings are reintroduced;
- manager text tells admins to use **Add Option** first if the desired role is not already a Community & Pings option;
- regression coverage verifies exclusive capability reassignment and preservation of the other /toke capability.

Do not close #381 until exact-head CI passes, PR #382 is merged/deployed, and Android verifies Starter + Notify + Channel all show configured and `/toke` successfully posts.


## Backlog — Discord REST 429 pressure

Issue #384 — **Audit repeated Discord 429s during/after activity recovery**.

Production logs on 2026-10-01 show deliberate `discord_api_safety` recovery pacing during large activity-reconciliation passes plus separate raw `discord.http` 429 responses for repeated single-message GETs later in runtime. This is not on the /toke path and is intentionally backlogged under the single-task lock.

Do not investigate #384 until #381 / PR #383 is complete unless the user explicitly FORCE SWITCHes.


## Post-merge /toke persistence failure — capability ID round trip

PR #383 merged to production main as `ecad2b00295cd4b10f01e070e0e755560fe719d5`.

Android canary after deployment showed the new **Toke Starter** / **Toke Notify** controls could be pressed, but after refreshing the manager the mappings still displayed **Not configured**.

Root cause is in `community_pings_service._option_from_raw()`:

- canonical runtime capability IDs are `toke_start` and `toke_notify`;
- `CommunityPingOption.to_payload()` correctly persists those underscore identifiers;
- reload parsing incorrectly reused the generic human-facing `_slug()` helper;
- `_slug()` converts underscores to hyphens, so persisted `toke_start` / `toke_notify` reloaded as `toke-start` / `toke-notify`;
- `toke_role_ids()` checks for the canonical underscore constants, so it returned `(0, 0)` after refresh even though the database write succeeded.

Correction:
- capability identifiers now use a dedicated machine-ID normalizer that preserves underscores;
- hyphenated values produced by the historical parser bug are normalized back to the canonical underscore form for compatibility;
- service regression coverage now proves `to_payload() -> parse_community_pings() -> toke_role_ids()` preserves both mappings;
- UI-path regression coverage now proves direct Starter/Notify assignment survives the same save/reload round trip.

Active branch: `fix/toke-capability-roundtrip`.

Do not close #381 until this exact-head fix passes CI, merges/deploys, and Android confirms both role mappings remain configured after Refresh and `/toke` posts successfully.


## Swarm-health release ranking

- Movie Night voting/search/queue state is centralized under one canonical room owner.
- Torrent-backed movie results support multiple release/quality variants for the same title.
- Each variant carries live swarm health: seeds, leechers, total peers, seed/leech ratio, and a health label.
- Default variant ordering is swarm-first: user votes, then non-zero seed availability, highest seed count, seed/leech balance, verified quality/codec/source, and size.
- Zero-seed variants are retained for visibility but demoted below playable swarms.
- Live torrent status exposes seeds, leechers, peers, distributed copies, and seed/leech ratio for playback diagnostics.


## Late-join synchronization

Movie Night late joiners no longer enter the group buffering quorum immediately.

Contract:
- a viewer joining after playback has already started is marked `joining`;
- they remain an active room participant and can vote immediately;
- they do not influence shared buffer holds until actually synchronized;
- their browser seeks to the current room timestamp and buffers around that position;
- torrent piece priority is shifted to the current room position for the joining viewer;
- adaptive late-join readiness uses an 8–15 second target buffer depending on the torrent buffer plan;
- once position drift is within tolerance and the target buffer is available, the viewer becomes `synced`;
- only synchronized viewers join the group-buffer quorum;
- existing viewers continue playing while a newcomer catches up;
- switching to a new movie resets non-host viewers back to unsynced for the new stream;
- the host remains immediately authoritative/eligible;
- the Watch page visibly reports **Joining…** versus **Synced Viewer**;
- a late viewer with a poor connection cannot repeatedly freeze the room before synchronization;
- after synchronization, normal bounded group buffering applies to that viewer.

Regression coverage proves:
- late joiners remain outside buffer quorum;
- weak initial late-join buffer does not pause the room;
- adaptive buffer completion graduates the viewer;
- a graduated viewer can later participate in group buffering;
- media replacement requalifies non-host viewers for the new stream.

## PR #426 exact-head CI failure and repair

The latest master-audit head reached the full unit suite and failed only two Movie Night UI tests:

1. `_release_embed()` referenced `collaborative` without defining it after the solo-voting simplification.
2. The provider-browser safety regression still expected the old always-visible voting controls, even though this audit intentionally hides meaningless solo voting and relabels queueing to **Add to Queue**.

Repair:
- derive release-detail collaborative state from the already computed active-viewer set;
- keep release-vote counts hidden for solo/private rooms and visible only for true multi-viewer shared rooms;
- update the provider-safety regression to assert the current solo controls and explicitly verify no primary candidate button carries an external URL.

No provider runtime, torrent runtime, queue semantics, or collaborative multi-viewer voting behavior was weakened.