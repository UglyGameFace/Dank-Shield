# Active Task

## Active task / outcome

**DANK-SHIELD-MOVIE-NIGHT-EASY-SOURCES — zero-setup catalog + provider search**

Production baseline:
`main@a88f19441476a3fa5ee47b87bbf09f856a4d8690` (PR #396 merged).

Active branch:
`feat/dank-cinema-dual-provider-modes`

Status:
**Implementation in progress; validation pending.**

### Scope / required outcome

1. Preserve the canonical Movie Night torrent runtime, direct magnet playback, direct `.torrent` playback, voting, shared leases, and session termination.
2. Make normal Movie Night title search work **without requiring a server owner to obtain or configure a feed URL**.
3. Add deployment-level **TMDB catalog matching** so users can choose the exact movie/title/year/poster without guild owners handling API URLs or credentials.
4. Add TMDB/JustWatch **where-to-watch discovery** for the selected catalog movie, with explicit JustWatch attribution; this is informational and must not be treated as a direct playback URL.
5. Add one built-in, no-key playable search provider limited to Internet Archive's `feature_films` collection, returning Archive-hosted `.torrent` references into the existing variant/playback pipeline.
6. Keep built-in search and custom source results merged into the existing candidate/release ranking model. Do not create a second playback stack.
7. Keep direct host-supplied magnet and `.torrent` input as universal provider-independent fallback paths.
8. Demote custom feeds to **Advanced Custom API** setup instead of presenting them as required.
9. Let an admin paste a working example search URL instead of hand-writing `{query}`; detect common search parameters and test the endpoint before saving.
10. Preserve HTTPS-only, no embedded credentials, private-network blocking, DNS revalidation, response-size caps, redirect validation, and guild-config CAS ownership.
11. Do not store the TMDB deployment token in guild config or expose it in Discord UI.
12. Do not add provider-specific scraping/reverse-engineering for unauthorized streaming sites. Provider architecture remains compatible with future authorized APIs/personal libraries once their credential storage is designed safely.
13. Brand the entire public surface as **Dank Cinema** so catalog, provider, queue, result, direct-media, and setup flows feel native to Dank Shield while preserving required third-party attribution and source identities.
14. Support **both provider modes**: structured JSON providers feed normalized playable releases into Dank Engine, while external search-link providers open the provider's own query-results page without scraping or ingesting HTML.
15. Keep external search-link providers separate from playback results and never treat a web page as a playable release.
16. Add regression coverage for TMDB identity/watch metadata, built-in search scoping, Archive torrent-reference normalization, automatic example-URL conversion, provider-mode migration/partitioning, external search rendering, zero-setup provider UI, Dank Cinema branding, and aggregate search behavior.

### Findings / root cause

- The merged source UI still made **custom JSON feeds** look like the normal path even though most server owners do not already have one.
- Obtaining a reusable API/feed URL is not consistently easy, so the fastest safe path is a built-in no-key provider plus direct magnet/.torrent playback.
- The resolver already has the correct public-network safety boundary and one canonical result model, so the fix belongs there rather than adding another scraper/runtime.
- Existing custom source URLs can stay compatible; only the admin onboarding path needs automatic query-template detection and pre-save testing.

### Execution path / changes so far

- `stoney_verify/movie_catalog.py`: added deployment-level TMDB exact-movie search plus region-scoped legal availability discovery from TMDB/JustWatch; catalog metadata is separate from playback media.
- `stoney_verify/media_source_registry.py`: added working-search-URL normalization for common query parameters.
- `stoney_verify/media_source_resolver.py`: added built-in Internet Archive Feature Films search, custom endpoint probing, and aggregate built-in + custom search.
- `stoney_verify/commands_ext/public_movie_night.py`: provider screen now separates catalog, playable built-in, direct magnet/.torrent, and advanced custom APIs; Search / Vote can use TMDB to choose the exact movie before searching playback providers.
- Catalog-only matches remain usable even when no provider has a release; the host can attach a magnet or `.torrent` afterward.
- Existing custom API management remains Edit / Enable / Disable / Remove through the canonical guild registry.
- `.env.example` and production docs now define `DANK_TMDB_READ_TOKEN` and `DANK_TMDB_WATCH_REGION`.
- Public Movie Night UX is now branded as **Dank Cinema**: **Dank Catalog**, **Dank Watch**, **Dank Archive**, **Dank Direct**, **Dank Engine**, and **Dank Provider Lab**. Underlying providers remain explicitly identified where attribution or source provenance matters.
- Custom provider registry now has backward-compatible provider modes: **JSON** for structured/playable resolver input and **external** for browser search-result links. Legacy saved providers default to JSON.
- Provider Deck now exposes **Add JSON Provider** and **Add Search Link**. External links are query-templated but never fetched by Dank Shield.
- Movie candidate pages expose **Search Elsewhere**, and searches with no playable result can fall back to enabled external provider buttons without converting those pages into releases.
- Search-link provider modal acknowledgement now occurs before the guild-config CAS write; success, persistence failure, and CAS-conflict paths all terminate by editing the deferred original response instead of risking a Discord interaction timeout or orphaned thinking state.
- External provider links now enforce Discord's 512-character link-button URL limit; setup validates against the full supported 180-character query budget so a provider cannot save successfully and later fail only when the Search Elsewhere view renders.
- Regression coverage is being extended in the catalog, media-source, and public Movie Night test suites.

### Validation / cleanup / blockers

Pending before completion claim:
- targeted source/Movie Night tests;
- full repository CI at exact PR head;
- branch-vs-main diff and import/dead-reference review;
- production canary after merge/deploy remains an owner runtime check.

Latest validation findings:
- review of the first PR #398 head found the new Search Link modal could call `save_media_source_registry()` before acknowledging Discord; the modal now defers before persistence and terminates the deferred original response on success/failure/conflict;
- review also found external provider templates could be accepted up to 1000 characters even though Discord link-button URLs are capped at 512; the renderer now rejects overflow and setup validates using the maximum supported query length;
- regression coverage locks both interaction acknowledgement ordering and the Discord button URL budget;
- all earlier-head CI results are superseded; exact-head CI for the current branch head is required before merge.

Merged baseline:
- PR #396 merged after all exact-head workflows passed at `eb75bacc4b479a0ed0a896cb4c1b695a068bea98`;
- the current branch contains only the post-merge dual-provider follow-up requested after that merge.

No unrelated task is active.

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
