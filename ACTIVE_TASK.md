# Dank Shield Active Task

## Active task / outcome

**DANK-SHIELD-430 — Complete Dank Cinema as a full premium streaming platform**

Production baseline:
`main@c785c5eefc9e9f0b1c6792e7546eca3c8f9703a5` (PR #436 merged green).

Active branch:
`feat/430-cinema-full-platform`

Issue:
**#430 — Dank Cinema premium theater / full-platform work**

Outcome:
Finish the full Cinema product contract on top of PR #436 without replacing the existing MovieNightRoom, torrent runtime, provider registry, Watch sync authority, Private/Watch Party lifecycle, Pass Host, or real browser capability controls.

## Scope

This remains one active implementation task covering:

- signed Discord-linked full Cinema site;
- movie + TV + season + episode catalog identity;
- durable watchlist, playback progress, history, Continue Watching, Watch Again, preferences, and notifications;
- canonical movie/episode playback from website and Discord;
- exact TV resume / next episode / autoplay-next;
- Home, Search, Details, My Stuff, Profile, Notifications, Feed Center, and responsive navigation;
- real Queue Add / Remove / Reorder / Play Next / Clear inside the Theater;
- unified TMDB/provider identity and guild adult-content policy;
- truthful source health/capabilities;
- High / Standard / Lite visual quality with automatic capability selection;
- responsive TMDB artwork;
- single composed Cinema branding lockup;
- responsive phone / tablet / desktop / ultrawide Theater and full-site behavior;
- focused regressions, migration validation, exact-head CI, final diff cleanup, and Discloud canary.

## Status

**Implementation is substantially complete in PR #437. Exact-head CI remediation/validation is active.**

The branch is currently based exactly on production `main@c785c5e...` and is **0 commits behind**. The repository's canonical Python/unit CI and the new Cinema SQL workflow trigger on pull requests/main, not feature-branch pushes, so executable exact-head CI is still pending until the PR is opened. No completion/merge claim is valid before that CI and the production canary.

## Findings / root cause

1. **PR #436 completed the Theater slice, not the full platform contract.**
   - durable user library/profile/history and full-site discovery surfaces were still missing;
   - TV identity existed only partially at playback boundaries.

2. **The new Cinema library service initially contained latent runtime failures.**
   - preference defaults, cache/lock state, and `InvalidCinemaState` were referenced but undefined;
   - the module imported successfully while first real persistence calls could fail.

3. **Progress writing existed but resume restoration did not.**
   - Watch already checkpointed durable progress;
   - new sessions did not restore it into canonical room authority;
   - restoring per viewer would conflict with Watch Party sync, so only the host applies saved resume and updates the shared room clock.

4. **TV needed canonical episode lifecycle instead of title arithmetic.**
   - Series → Season → Episode now resolves from TMDB identity;
   - next episode crosses real season boundaries;
   - Next Episode only appears when the exact episode has a playable source.

5. **Media identity had duplicate authorities.**
   - Discord-specific release matching and title-based candidate reuse could diverge from the website;
   - same-title remakes could collide;
   - shared Cinema identity/playback services now own TMDB identity, exact provider matching, preferred-source selection, and candidate lookup.

6. **Several visible surfaces were technically populated but not truthful.**
   - Watch Party Picks were Top Rated movies rather than real party media;
   - full-site source health referenced a nonexistent property;
   - an external/reference source was labeled as in-app Search capable;
   - one Search button changed text without triggering search;
   - Queue Add required leaving the Theater;
   - notification invite actions were persisted but ignored by the UI.

7. **The duplicated/sliced branding defect was structural.**
   - the visible header reconstructed one approved lockup from separate mark/wordmark crops;
   - Theater and full site now render one transparent composed 1200x278 lockup; split variants remain only for icon/favicon contexts.

8. **Guild adult-content policy was Discord-only.**
   - the full site, provider rows, Queue search/add, direct Details/Play, and saved library needed the same owner;
   - the existing `movie_night_preferences` toggle is now consumed across surfaces and canonical adult identity is persisted with library rows.

## Execution path

Catalog identity:
`Cinema UI / Discord / Theater Queue -> cinema_catalog -> TMDB -> cinema_media_identity`.

Provider/playback:
`canonical media identity -> cinema_playback_service -> media_source_resolver -> MovieNightRoom -> TorrentMediaManager -> signed Watch stream`.

User state:
`signed Discord Cinema identity -> cinema_library_service -> service-role Supabase`.

Watch progress:
`Watch player checkpoints -> /movie/{room}/progress -> cinema_library_service`;
new playback restores only through host authority -> canonical room seek -> viewers follow room sync.

TV continuation:
`episode identity -> cinema_catalog.get_next_episode -> exact SxxExx provider search -> shared playback service -> same MovieNightRoom`.

## Changes

- Added/extended `cinema_catalog.py` for normalized movie/TV identity, Home discovery, Details, seasons/episodes, recommendations, and real cross-season next-episode resolution.
- Added `cinema_media_identity.py` as the shared movie/episode/provider identity owner, including exact episode notation and shared adult filtering.
- Added `cinema_playback_service.py` as backend-neutral playback orchestration used by Discord, website Play, Queue Add, and TV transitions.
- Fixed `cinema_library_service.py` runtime defaults/caches/locks and added canonical media lookup.
- Added durable host-controlled resume restoration and debounced seek/checkpoint behavior to the existing Watch progress route.
- Added proper TV Next Episode availability, transition, autoplay-next, episode progress, and preferred-source behavior.
- Added real signed full-site playback into an existing host-owned Cinema room; website session creation remains Discord-owned.
- Added real Home rails with empty-section suppression, including real Watch Party Picks rather than recycled catalog rows.
- Added full movie/TV Details, exact episode continuation/navigation, Search, My Stuff, Profile, Notifications, and Feed Center.
- Completed in-Theater Queue management: Add Title search, exact TV episode search, playable-source validation, Added By, Play Next, reorder, remove, and clear.
- Made notifications guild-scoped and live Watch Party invites actionable through fresh signed Join Theater URLs.
- Unified the existing guild adult-content setting across Discord, full-site Search/Details/Play, provider releases, Theater Queue, and saved library surfaces.
- Made source capabilities/health truthful and category-aware across Theater and full-site Feed Center.
- Applied persisted playback speed, preferred source, audio/subtitle language, autoplay-next, and visual-quality preferences to real runtime paths.
- Added adaptive High/Standard/Lite visual behavior and responsive TMDB `srcset/sizes`.
- Replaced split visible branding composition with one composed transparent Cinema lockup on Theater and full site.
- Removed the unused full-site generic progress-write API so Watch remains the sole playback-progress authority.
- Added dedicated `.github/workflows/cinema-platform-sql.yml` to replay and validate the new Cinema migration on PostgreSQL without touching production.
- Added focused regressions in `tests/test_cinema_full_platform.py` and `tests/test_movie_night_web.py`.

## Validation / results

Completed pre-PR evidence:

- current `main` remains exactly the branch merge base: `c785c5eefc9e9f0b1c6792e7546eca3c8f9703a5`;
- feature branch is 0 behind main;
- final scope inspection shows only #430 Cinema files, tests, migration, workflow, and this task record;
- full-site `cinema_site.js` has been parsed successfully with a JavaScript parser after the major UI/Feed/notification changes;
- stale split-brand selectors/visible mark+wordmark composition removed;
- stale title-based catalog candidate lookup removed from website/TV playback paths;
- stale custom-only full-site provider-search path removed;
- stale Feed Center test ownership repaired around the shared service;
- migration security reviewed against current Supabase guidance: service-only tables use explicit grants/revokes plus RLS;
- no production Supabase project was mutated because the connected account exposed only an unrelated/ambiguous project;
- dedicated Cinema SQL smoke workflow now verifies migration idempotence, constraints, RLS, grants, representative rows, notification dedupe, and client-role denial.

Pending executable evidence:

- Python compile/unit suite cannot run locally in this tool environment because the repo cannot be cloned over network here;
- canonical `ci.yml` does not support feature-branch dispatch; PR #437 now supplies the required exact-head CI trigger;
- the first `Dank Cinema SQL` PR run proved migration replay/RLS/grants, then failed because the disposable test `service_role` lacked Supabase's RLS-bypass behavior; the harness was corrected to use `BYPASSRLS` without weakening production RLS;
- live Discloud + Android/Samsung + desktop/tablet/ultrawide canary remains required after a CI-green deploy.

## Cleanup / conflicts

- One canonical `MovieNightRoom` authority remains.
- One torrent/media runtime remains.
- One provider registry/resolver remains.
- One Cinema Feed service owns Feed Center state/mutations.
- One Cinema library service owns persisted user state.
- Watch is the sole durable playback-progress writer.
- Shared Cinema identity/playback services replaced duplicate Discord/site matching logic.
- No title-only candidate reuse for canonical TMDB media.
- No visible fake Cast/PiP/subtitle/audio/Next Episode controls were introduced.
- No unrelated Dank Shield feature files are present in the branch diff.

## Blockers / risks

- Exact-head repository CI is not yet available because workflows run on pull requests/main; PR creation is required for that validation.
- The Supabase migration must be deployed before durable library features work in production.
- Live MovieNightRoom state still remains process-memory authority across bot restarts; that is pre-existing architecture and not disguised as solved by the user-library migration.
- Provider availability and swarm health remain external conditions; UI now reports real availability rather than inventing it.
- Final real-device/Discloud canary is still required for Samsung Browser desktop-mode transitions, fullscreen/orientation, background/foreground, sync, and production migration/runtime acceptance.

## Backlog

No required #430 master-contract item has been moved to backlog to make this branch appear complete. Unrelated Dank Shield projects remain outside the active task.

## Exact next step

Use PR #437's exact current head for canonical Dank Shield CI plus Dank Cinema SQL validation, repair only evidence-backed failures, verify the final head remains mergeable/green, then perform the Discloud production canary before any completion or merge-ready claim.

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

## Post-#442 canary defect — missing Discord /cinema command surface

Production baseline:
`main@d004d4ea1cdab1dffbbb59f289e4e84419b401bf` (PR #442 merged; post-merge Dank Shield CI, Cinema SQL, Ticket Owner Override, and Supabase migration workflows green).

Android/Discord canary evidence:
- the user cannot see a `/cinema` command or the planned Cinema shortcut commands;
- current production exposes only the legacy `/movie` Cinema doorway.

Root cause:
- the website `/cinema` route was implemented, but the Discord public registrar still intentionally compacted the global application-command tree to the old 10-item contract;
- `public_command_surface_v2` rejected any root outside `captions/dank/mod/movie/role/ticket/tickets/toke/verify`;
- the planned branded Discord command group therefore never reached global sync.

Active correction branch:
`fix/cinema-discord-command-surface`

Correction contract:
- keep `/movie` unchanged for backward compatibility and its optional magnet/.torrent attachment fields;
- expose one branded `/cinema` application-command group;
- expose `/cinema home`, `start`, `private`, `join`, `leave`, `queue`, `info`, and `vote`;
- all shortcuts reuse the existing MovieNightManager, room lookup, queue, vote, status, sync, and lifecycle owners;
- do not create duplicate room state, queue state, voting, persistence, or playback authority;
- `/cinema join` never creates a room;
- `/cinema leave` never ends a room and preserves the existing host-away lifecycle;
- private-session access remains fail-closed.

Validation required:
- command-contract and command-friction audits;
- command group regression proving all eight children and Yes/No vote choices;
- existing `/movie` parameter regression remains green;
- full Dank Shield CI and all triggered companion workflows on the exact PR head;
- branch 0 behind current main and final diff hygiene;
- after merge/deploy, Discord global command sync must visibly expose `/cinema` and all children while `/movie` still works.

Do not claim this canary defect resolved until the exact-head CI, merge/deploy, and live Discord autocomplete check pass.


## PR #443 first exact-head CI failure and correction

Exact head `7d22c069632e74927b73beaa8a5a40a843a55707` reached the complete unit suite and finished with **2842 passed / 2 failed**.

Both failures were stale final-command-tree expectations:
- `tests/test_command_ux_024_reassertion.py` still required the pre-Cinema root set;
- `tests/test_welcome_card_live_command_tree.py` still required the pre-Cinema root set.

The actual final command tree was correct in both failures and contained:
`captions, cinema, dank, mod, movie, role, ticket, tickets, toke, verify`
plus the `View Dank Profile` context command.

Correction:
- update both stale root-set expectations to include `cinema`;
- strengthen the live command-tree regression to require `/cinema` to be an `app_commands.Group`;
- require exactly the intended children: `home/start/private/join/leave/queue/info/vote`;
- preserve the existing assertion that all legacy fast doorways remain standalone commands.

No runtime Cinema implementation changed in this correction. Validate the new exact head before marking PR #443 ready.


## PR #443 command-contract sweep after first CI failure

The first CI failure stopped before standalone tool/audit steps, so the same command-surface root cause was checked across remaining validation/runtime documentation.

Additional stale pre-Cinema contracts found and corrected:
- `tools/test_dank_command_payload.py` still rejected any final root set containing `cinema`; it now expects the canonical Cinema root.
- `public_help_group.BORING_PUBLIC_TARGET` did not include `cinema`, which would have falsely labeled the new canonical command as unexpected in the command audit UI.
- the compact Home help embed did not advertise `/cinema`.
- `CLAUDE.md` and `docs/COMMAND_NATIVE_OWNERSHIP_AUDIT.md` still described the old ten-item public surface.

These are not unrelated cleanups. They are the same command-surface contract that caused the first #443 CI failure and are required so runtime diagnostics, standalone validation, and architecture documentation agree with the registrar.

Next step:
- validate the new exact head through full Dank Shield CI and all companion workflows;
- patch only evidence-backed failures;
- if green, verify branch is 0 behind main, final diff hygiene, then mark PR #443 ready for review;
- after merge/deploy, verify Discord autocomplete exposes `/cinema` and all eight children while `/movie` remains available.


## PR #443 production canary evidence — standalone Cinema Home + mobile header

Samsung Browser production screenshot after the post-#442 deployment showed two remaining Cinema defects while opening Dank Cinema without hosting a session:

1. the full Cinema shell loaded, but Home failed with `Dank Cinema requires membership in this Discord server.`;
2. mobile header controls used placeholder-style glyphs, including a literal diamond notification icon and a `?` profile fallback.

Root cause — standalone Home:
- standalone Discord OAuth already requests `identify guilds` and receives the user's current guild list;
- that exact guild list is signed into the short-lived `dank_cinema_guilds` cookie;
- despite that fresh Discord proof, `_site_identity` and `cinema_open_guild` immediately required another bot-side `guild.fetch_member()` check;
- a transient/cache/REST failure therefore rejected valid standalone browsing even though Discord OAuth had just proven the user shares the guild with Dank Shield.

Correction:
- accept the signed short-lived OAuth guild list as standalone browsing membership proof only for the exact guild and only while Dank Shield still shares that guild;
- if no valid OAuth guild proof exists, retain the live bot membership fetch and fail closed;
- targeted OAuth login with a current guild no longer performs a redundant second member REST lookup immediately after Discord's own `/users/@me/guilds` result;
- signed watch links/session flows without fresh OAuth guild proof still retain the live membership check;
- preserve exact-guild scoping and add regressions for both allowed and wrong-guild cases.

Root cause — header:
- `cinema_site.js` literally defined `bell: "♢"` and old text glyphs for navigation;
- when Home failed before Discord profile payload arrived, the avatar fallback rendered `?`.

Correction:
- replace header/mobile navigation glyphs with local inline SVG icons;
- use a real bell icon, real profile fallback, and real bottom-nav icons;
- strengthen icon contrast/borders on mobile;
- bump Cinema CSS/JS asset query versions to `v=3` so Samsung Browser does not reuse the old five-minute cached assets.

Acceptance:
- direct standalone `/cinema` login can browse Home/Search/My Stuff/Feeds/Profile with no active room;
- exact server membership remains required;
- no active Movie Night/Private Session is required merely to browse Cinema;
- header shows recognizable notification/profile icons, never a diamond or question-mark placeholder;
- full exact-head CI and companion workflows must pass before merge/deploy.


## Active follow-up PR after #443 merged

PR #443 merged before the Samsung standalone-Home screenshot fixes were added, so those post-merge commits were moved onto a clean branch from current production `main@21f910444979eae5a92a0026736a01ba96d478b1`.

Active branch:
`fix/cinema-standalone-home-mobile-header`

Active PR:
`#444 — Fix standalone Dank Cinema Home and mobile header icons`

PR #444 contains only the screenshot-backed canary correction:
- standalone OAuth guild proof reuse for browsing without an active room;
- exact-guild denial retained;
- live membership fallback retained when fresh OAuth guild proof is absent;
- real SVG header/mobile-nav icons;
- no diamond notification glyph;
- no question-mark profile placeholder;
- stronger mobile icon visibility;
- Cinema site CSS/JS cache-bust to `v=3`;
- focused regressions.

Do not merge #444 until exact-head CI and every companion workflow are green and the branch remains 0 behind main.


## Production canary follow-up — /cinema home signed link does not establish browser login

Observed after merged PR #444:
- user runs `/cinema home`;
- presses **Open Dank Cinema**;
- the Discord button URL is correctly generated as a signed guild/user deep link;
- Samsung Browser reaches Cinema but is not established as signed in and falls back into the membership/sign-in failure path.

Root cause:
- `MovieNightHubView` already calls `cinema_site_url(guild_id, user_id)` and includes `uid/exp/sig`;
- `_site_identity` validates that signature successfully;
- before returning the first HTML response and minting the HttpOnly session/identity/guild cookies, it still required `_fetch_site_member()`;
- a member REST/cache miss therefore rejected the valid signed Discord deep link before the browser-session exchange could complete.

Active correction:
- valid signed Discord Cinema links may perform the one-time initial browser-session exchange without a second member REST lookup;
- Dank Shield must still currently share the exact target guild;
- after the initial page response, normal HttpOnly session, identity, and short-lived guild proof cookies are issued;
- unsigned/expired links do not bypass normal auth/membership checks;
- existing OAuth exact-guild proof behavior remains unchanged.

Regression requirements:
- `/cinema home` Open Dank Cinema button must contain exact guild/user `uid/exp/sig`;
- valid signed entry must return the Cinema page and set session, identity, and guild proof cookies even when member REST is unavailable;
- signed entry must fail if Dank Shield no longer shares the target guild;
- full exact-head CI and companion workflows must pass before merge/deploy.

## Production canary follow-up — Cinema APIs eject valid sessions after guild proof expiry

Observed after merged PR #445 on Samsung Browser:
- `/cinema home -> Open Dank Cinema` successfully reaches the new Cinema shell and real icons;
- after the short-lived guild proof ages out, Home/Profile APIs return `Dank Cinema requires membership in this Discord server`;
- the exact-guild browser session cookie itself is still valid.

Root cause:
- `CINEMA_GUILDS_TTL_SECONDS` is 15 minutes;
- once that signed guild-list proof expires, every Cinema API request calls the bot-side member lookup again;
- the old member lookup collapsed every failure mode into `None`;
- Discord `NotFound` (definitely not a member), rate limiting, forbidden member REST, timeouts, and transient HTTP failures were therefore all treated as the same "member left" result;
- the browser was ejected from a valid exact-guild Cinema session because Discord REST was unavailable, not because Discord confirmed membership ended.

Active correction branch:
`fix/cinema-membership-session-resilience`

Correction contract:
- membership checks become tri-state: `present`, `absent`, or `unavailable`;
- only definitive `discord.NotFound` or the canonical `on_member_remove` event means absent;
- temporary REST/API failures are availability problems, never proof of departure;
- an already-valid exact-guild Cinema session survives temporary membership REST unavailability;
- identity-only access without an exact-guild session still fails closed when membership cannot be proven;
- canonical `on_member_remove` immediately revokes Cinema access for that guild/user;
- canonical `on_member_join` restores access for a rejoined member;
- fresh signed Discord Cinema links and fresh OAuth guild proofs refresh the positive membership state;
- successful membership verification is cached for five minutes and temporary-unavailable state for one minute to avoid hammering Discord REST from Home/Profile/Feeds/Search loading in parallel.

Acceptance:
- Home, Profile, Search, My Stuff, and Feeds remain usable for the active exact-guild Cinema browser session after the 15-minute OAuth guild proof expires;
- temporary Discord REST failure does not produce a false `requires membership` error;
- definitive member absence still returns 403;
- member-remove immediately revokes an existing session and rejoin restores it;
- wrong-guild sessions/proofs remain rejected;
- exact-head CI and all companion workflows must pass before merge/deploy.

## Evidence-backed follow-up — verified Cinema session is the authorization artifact

Evidence from the 2026-10-05 10:49 Samsung/Discord screenshots:
- `/cinema home` produces a signed URL containing the exact guild ID, user ID, expiry, and HMAC signature;
- the URL expiry shown in Discord is 2026-10-05 20:49:41 UTC, so it was valid at screenshot time;
- Samsung Browser loads the Cinema HTML shell and the deployed real SVG icons;
- the first Home API call then returns `Dank Cinema requires membership in this Discord server.`

What is proven from current main:
- that error text can only come from session revocation, session membership state `absent`, or identity membership state `absent`;
- screenshots alone do not distinguish those server-side branches, so no branch-specific claim is justified without Discloud request logs;
- every `dank_cinema_session` cookie is minted only after an authoritative entry proof succeeds: signed Discord guild/user link, Discord OAuth shared-guild proof, or verified guild-open flow.

Correction contract:
- a valid exact-guild Cinema session cookie is the authorization artifact for Cinema API browsing;
- Home/Profile/Search/My Stuff/Feeds do not call Discord member REST again while that session remains valid;
- the bot must still share the exact guild;
- canonical `on_member_remove` remains the immediate in-process revocation owner;
- REST `NotFound` may deny an identity-only/open-guild request but no longer creates a process-lifetime session revocation;
- identity-only access still requires fresh membership proof and remains fail-closed;
- Cinema exact-guild session TTL is bounded to six hours, matching the signed Discord entry window, instead of seven days;
- old longer-lived session cookies are rejected by the stricter validator after deployment;
- `/health` exposes `cinema_auth_contract: signed-session-v3` so production deployment can be proven directly.

Do not claim fixed live until exact-head CI is green, the PR is merged/deployed, `/health` reports `signed-session-v3`, and `/cinema home -> Open Dank Cinema` loads Home plus Profile on Samsung without a membership error.

## PR #447 first exact-head CI failure

Exact head `18df68c13dfb128b857c96bff41864ade1b69612` compiled successfully and ran the full unit suite, finishing with `2850 passed / 3 failed`.

Evidence-backed failures:
- `test_cinema_session_is_bounded_to_signed_entry_window` exposed a real implementation miss: `cinema_session_value()` still clamped caller TTL to 30 days even though the validator/constant had been changed to six hours.
- `test_exact_guild_session_survives_temporary_membership_api_failure` and `test_member_remove_revokes_existing_cinema_session_and_rejoin_restores_it` omitted a `_bot_guild()` fixture, so the newly intentional exact-guild bot-presence guard rejected the synthetic guild.

Correction:
- clamp `cinema_session_value()` directly to `CINEMA_SESSION_TTL_SECONDS` with a five-minute minimum;
- add the missing exact-guild bot fixture only to those two tests;
- do not change the signed-session-v3 authorization contract.

Re-run exact-head CI before marking #447 ready.

## Production canary follow-up — signed entry proof was discarded before Cinema API calls

Evidence from the 2026-10-05 11:33 Samsung screenshots and repository history:
- Cinema HTML shell renders and every tab first shows its loading skeleton;
- every tab then fails with the same membership error;
- API routes are correctly nested under `/cinema/{guild_id}/api/...`, so the cookie Path shape is not the mismatch;
- JavaScript fetches use `credentials: "same-origin"`, so credentials are not explicitly omitted;
- commit `af8c6e286acf5211af87e2d7cfff6ca728e80d4a` changed `AUTH_QUERY = window.location.search` to stripping `uid/exp/sig` and forcing `AUTH_QUERY = ""` after the page load.

Root cause:
- the top-level signed Discord link authenticates the HTML request;
- the SPA then deliberately deletes the signed guild/user proof from the visible URL and from its API transport;
- if Samsung Browser does not present the newly minted HttpOnly Cinema cookie on the immediate SPA API request, every Home/Profile/Search/My Stuff/Feeds call becomes identity-only and falls into the membership gate;
- this explains the identical skeleton-then-membership-failure pattern across every menu surface.

Active correction branch:
`fix/cinema-samsung-signed-api-fallback`

Correction contract:
- capture `uid/exp/sig` in JavaScript memory before stripping them from the visible address bar;
- continue removing the bearer values from browser history immediately;
- reuse that signed proof only for same-origin `/cinema/{guild}/api/...` calls during the current page lifetime;
- keep `credentials: same-origin` so the normal HttpOnly session remains the preferred path;
- signed fallback must obey canonical member-remove revocation and bot-guild presence;
- bump `site.js` to `v=4` to defeat Samsung Browser asset caching;
- expose `/health` marker `cinema_auth_contract: signed-session-v4` so production deployment can be proven directly.

Acceptance:
- `/cinema home -> Open Dank Cinema` loads Home on Samsung without a membership error;
- Home, Search, My Stuff, Feeds, Profile, and Notifications all use the same authenticated API transport;
- visible browser URL no longer contains `uid`, `exp`, or `sig` after page boot;
- signed fallback cannot bypass a real member-remove event;
- exact-head CI and companion workflows pass before merge/deploy.

## Evidence phase — browser-neutral Cinema auth observability

Why this phase exists:
- repeated production screenshots prove the Cinema shell loads but API-backed Home/Search/My Stuff/Feeds/Profile can still fail with the same membership message;
- browser standards do not support the previous guesses: same-origin fetches send cookies, the session Path covers deeper Cinema API paths, SameSite=Lax is valid for same-site/top-level navigation, and history.replaceState does not reload the document;
- current tooling cannot reach the public Discloud hostname from the coding environment, so live request headers/cookies cannot be inspected remotely from here.

Active branch:
`fix/cinema-auth-observability`

Contract:
- do not change authorization decisions in this phase;
- add `/cinema/{guild_id}/api/auth-debug` that reports only safe status labels/booleans, never raw cookie values, signatures, Discord IDs, or bearer tokens;
- distinguish signed query missing/invalid/valid;
- distinguish Cinema session cookie missing/invalid/valid;
- distinguish identity cookie missing/invalid/valid;
- distinguish guild-proof cookie missing/invalid/valid;
- report selected auth source, bot-guild presence, canonical member-revocation state, whether any Cookie header reached the server, request secure flag, and forwarded protocol;
- on any Cinema SPA API failure, automatically fetch this safe diagnostic state and append it to the visible error card;
- log the same non-secret diagnostic summary server-side for Discloud logs;
- bump client asset cache to `site.js?v=5` and health contract to `signed-session-v5-observable` so deployment can be proven.

Acceptance:
- exact-head CI and companion workflows green;
- after deploy, `/health` reports `signed-session-v5-observable`;
- reproduce the failure once in any browser/device and capture the displayed `Diagnostic:` line or matching Discloud `cinema_auth_debug` log;
- only then modify auth behavior according to the observed state.

## Evidence-backed root cause — Cinema used guild cache as installation authority

Production diagnostics from `signed-session-v5-observable` reported:
- `signed=invalid`;
- `session=missing`;
- `identity=valid`;
- `guildProof=invalid`;
- `source=identity`;
- `botGuild=no`;
- `revoked=no`;
- `cookieHeader=yes`.

What this proves:
- browser cookies are reaching aiohttp (`cookieHeader=yes`);
- the identity cookie validates with the current server secret;
- the canonical member lifecycle did not revoke the user;
- the server-side guild gate fails because `cinema_site._bot_guild()` calls only `bot.get_guild(guild_id)` and treats a gateway cache miss as proof that Dank Shield is not in the guild.

Discord.py behavior relevant to the fix:
- `Client.get_guild()` is cache-backed;
- Discord REST guild/member fetches are the authoritative fallback when cache state is missing;
- a cache miss must not be treated as guild absence.

Active correction branch:
`fix/cinema-guild-rest-resolution`

Correction contract:
- add `_resolve_bot_guild()` with cache -> Discord REST fallback;
- resolver returns `present`, `absent`, or `unavailable`;
- Discord REST NotFound/Forbidden means bot genuinely absent;
- HTTP/rate-limit/timeout/transport failure means temporarily unavailable, never absent;
- positive REST guild resolution is cached for five minutes, absent for one minute, unavailable for 30 seconds;
- Cinema member verification uses the resolved Guild object and can then call `fetch_member()` normally;
- signed links, exact-guild sessions, targeted OAuth, and identity-only access all use REST-confirmed guild presence instead of cache-only presence;
- targeted OAuth distinguishes `user is not in guild` from `Dank Shield is not installed in guild`;
- user-facing errors stop blaming member status when the bot itself is absent;
- diagnostics report `botGuildRest=present|absent|unavailable` separately from `botGuildCache=yes|no`;
- `/health` marker becomes `signed-session-v6-guild-rest`.

Acceptance:
- exact-head CI and companion workflows green;
- after deploy `/health` reports `signed-session-v6-guild-rest`;
- reproduce `/cinema home -> Open Dank Cinema`;
- if gateway cache still misses but REST confirms the guild, Home/Search/My Stuff/Feeds/Profile must load;
- if REST confirms the bot is absent, Cinema must explicitly report that Dank Shield is not installed instead of `requires membership`.
