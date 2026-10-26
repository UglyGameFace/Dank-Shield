## ACTIVE FORCE SWITCH: Seamless outside-first Find Players (2026-10-26)

- **Single active task:** After merged persistent LFG panel PR #742, owner showed Discord screenshots proving main **Find Players** leads to only empty local sessions while Outside Players and Public Network are buried. Change BOTH Community Hub and its persistent panel so Find Players immediately fetches real external Atlas groups, host-approved cross-guild Public Gaming Network, and local sessions in a combined private finder; no hidden intermediate menu.
- **Code:** Query each source independently and concurrently; game search across all three and optional external region filter. Always show partial authorized results when one source fails, without claiming no gaming groups globally just because local guild has none. Present Atlas provider links, public network exact approved invite selection with click-time fresh recheck, and local session join in their distinct owner-controlled flows. Keep legacy source-specific browses as optional details; avoid invented native joins for external users.
- **Verified live blocker:** Connected Dank Shield Supabase project still lacks `public_lfg_opt_in`, `community_hub_public_lfg_directory`, and `dank_community_atlas_publications`. This PR cannot make outside public network appear without applying previously merged #736/#741 migrations to the actual production database and validating Discloud access. No unsafe direct production writes or schema migration in this UI PR.
- **Release:** One large PR, dedicated Community Hub tests and cross-feature CI; guarded migration plan separately, Android canary and correct release feed. OneBump and all unrelated tasks paused.

## ACTIVE FORCE SWITCH: Persistent public Community Hub LFG board (2026-10-26)

- **Single active task:** User returned from OneBump after PRs #740 and #741 merged and requested a permanently visible, interactive public LFG panel for all members. Implement in one large PR on `feat/community-lfg-persistent-public-panel-20261026`. Keep OneBump, Cinema, other projects paused.
- **Scope:** One staff-selected channel, one durable panel per guild; Use This Channel and Search-Safe channel picker; persistent shared buttons with private per-member Find, Create, My Groups, Outside Players, Public Network and Refresh; public-only sanitized session summaries, near-live bounded update with startup recovery, optional staff disable/move, no duplicate reposts, no blanket Discord member scraping.
- **Production blocker discovered:** Connected DankShield Supabase has sessions table but does **not** have PR #736 `public_lfg_opt_in` / directory RPC, or PR #741 `dank_community_atlas_publications`. This panel PR adds no SQL migration. Do NOT claim outside recruitment or external publishing live until authorized migrations and matching Discloud deploy.
- **Release:** Dedicated focused and full CI, mobile nonstaff canary, move/remove/restart test, Discord REST pacing; no production deployment changes from PR alone. Historical notes below are not active.

## ACTIVE FORCE SWITCH: LFG outside advertising (October 26, 2026)

- **Active:** User explicitly switched from OneBump to LFG after PR #740 was merged. One task only: host-authorized promotion of public Community Hub sessions to LFG Atlas. Branch `feat/lfg-atlas-consent-publishing-20261026`. Preserve OneBump and other projects for later.
- **Goal:** A real path to recruit gamers outside Discord: the session host or co-host explicitly enters region/duration and confirms POST using the guild's independently verified public invite. No automatic announcements, unsolicited DMs, scraping, paid token, or hidden publication.
- **Architecture:** Service-role-only durable publication ledger, host+guild+session consent SQL RPC, atomic claim, strict third-party HTTP, stored private external delete token, rate-limited worker, revoke on end/unshare/maintenance/expiration or owner request, zero automatic POST retries after uncertain outcomes. Published game must match a documented Atlas game. No new slash commands.
- **Gate:** Exact-head Community Hub Python/PG16/full CI and command-size regressions, then owner merge and guarded real Supabase migration before Discloud deployment. Atlas live HTTP endpoint was not reachable by the research tool, so require a live Discloud canary. No production external writes have been made.
- **Owner preference:** One cohesive big PR, not a stack of small PRs; no dev jargon in moderator-facing UI.

## ACTIVE: Smart Bulk Collapse for trusted OneBump adverts (2026-10-26)

- **Single active task:** Owner confirmed new approved adverts auto-collapse already works and requests uncapped-by-three Smart Bulk Collapse for existing posts, with progress, cancel, paced processing and restart recovery. Work in `feat/trusted-advert-smart-bulk-collapse-20261026` on merged PR #739; do not reimplement approved bot/channel setup.
- **Architecture:** New private SQL collapse-jobs ledger and atomic claim/lease functions, bounded 25-message scan/6-edit chunks, shared 12/min Discord REST limiter, saved cursor and counts, 20-second worker poll, revocation checks and cancel on staff request. No user/contact scraping, deletes, automatic OneBump bumping, or change to existing 24-hour advert retention worker.
- **UI:** Existing Collapse Previews modal ON+RECENT/RECENT now queues the background scan, Progress and Cancel controls show actual status; ON continues live suppression, OFF stops new suppression and cancels running bulk. Staff must explicitly request bulk hiding and understand OneBump fair-play risk.
- **Release:** Focused Python/PG16 migration/atomic-claim tests + full CI, protected DB migration, guarded Discloud main deployment and Discord Android canary. Nothing live before these. Paused LFG and unrelated bot work.

## ACTIVE: Trusted advertising embed collapse (2026-10-26)

- **Single active task:** PR #738 was merged into main. Owner now explicitly requests Discord embed collapse for its already-approved OneBump channel. Branch `feat/trusted-advert-embed-collapse-20261026`. Do not reopen LFG, Cinema, or duplicate OneBump setup.
- **Implementation:** New opt-in `suppress_embeds` on the existing exact bot+channel rule (default OFF), independent of cleanup, with Discord Manage Messages flag edit. New bot adverts and late embed updates; optional staff-only bounded recent-post action for existing adverts, no delete/repost/cross-channel bypass. Simple mobile setup modal; two layers of validation and REST pacing.
- **Important disclosure:** Collapsing Discord rich embeds can remove the join card and may conflict with OneBump fair-play visibility rules. Do not claim it selectively blurs the text or preserves the original advert display.
- **Release:** Exact-head Invite Shield UI/security tests, full CI and command limits, no SQL migration for this new setting, owner review, merge, Discloud rollout and real Discord Android canary. Existing 24-hour ledger must stay unchanged.

## ACTIVE FORCE SWITCH: OneBump × Invite Shield + optional ad retention (2026-10-26)

- **Single active task:** With LFG PR #737 merged and its CI successful, owner authorized the next feature, exact-bot advertising trust plus optional 24-hour-per-ad cleanup. Work only in `feat/invite-shield-trusted-adverts-retention-20261026` until validated. LFG future promotion writes and other features paused.
- **Real OneBump policy:** Its official rules require adverts visible at least 24 hours, warn against purge-bot deletions, and may penalize extensive deletion. Exact trusted bot/channel allowance is independent from cleanup. Cleanup defaults OFF, requires an explicit acknowledgement, uses 24/48/72 hours + 10-minute margin and may still affect OneBump standing.
- **Architecture:** Only exact bot account+approved channel bypasses the canonical Invite Shield delete decision. No username or blanket channel exemption, no automatic bumping or new command tree. Uses existing Search-Safe channel browser + Use This Channel. Private durable Supabase due-claim ledger and bounded worker; no arbitrary retrospective history purge.
- **Merge/deploy gate:** Dedicated focused Invite Shield tests, real PG16 claim and RLS checks, full Dank Shield CI, permission/privacy regression, operator-approved guarded migration then deployment, and owner canary. Do not claim the bot was deployed or ads deleted just because code merged.
- **Paused:** LFG #638 future external posting, Cinema, Watch Yo Mouf, other projects. Past active-task notes below are historical only.

## ACTIVE FORCE SWITCH: Free outside-player LFG discovery (October 26, 2026)

- **One active task:** Large LFG provider upgrade following merged PR #736. New branch `feat/lfg-free-outside-player-discovery-20261026`. Keep other Dank Shield and Cinema work paused.
- **Verified integration:** MinecraftLFG published public, keyless, read-only player-profile API; strict schema, bounded cache/request pacing, never contact/DM, never claim users are live online. User can browse editions in Discord Android. Live endpoint accessibility remains unconfirmed until deployment canary.
- **Additional free discovery:** LFG Atlas documented public gaming posts for 28+ games, plus manual public Reddit gaming communities with safe fixed links, not unauthorized Reddit API harvesting. The100.io remains optional token-gated; LFG Atlas published keyless multi-game read-only API now integrated, but live host reachability/response shape still require production canary.
- **Acceptance gates:** Dedicated provider and UI tests, Community Hub CI, full Dank Shield CI, command limits, security and Cinema separation; owner merge, guarded Discloud deploy, Android/user-facing canary. No Supabase data/migrations or new Discord commands.
- **Owner preference:** Big cohesive PRs only, no piecemeal adjacent sub-feature PRs. The objective is actually finding outside players and growing the server, while distinguishing player profiles from approved outbound recruiting.

## ACTIVE: Community Hub outside recruitment and cross-server public LFG (2026-10-26)

- **Owner goal:** Let people outside a guild find explicitly advertised gaming groups and follow the host guild's verified invitation, so Community Hub contributes to real community growth. Not just local filtering; no speculative discovery of arbitrary Discord users.
- **Single active scope:** Large PR on `feat/community-lfg-public-recruitment-20261026`, building on merged #735. Public read-only LFG board on the existing Share Router Site listener and in-Discord cross-guild network, approved per-guild invite, separate host opt-in per session, revocation, guarded SQL and regression coverage. Existing Community Hub remains the sole session owner. No alternative LFG bot, site database, scraper, pings or Cinema.
- **Release gates:** No public consent defaults on. New additive Supabase migration required separately before runtime use. Exact-head focused Community Hub Python/PG16 CI, complete Shield CI, command sizes, diff/security review, guarded Discloud deploy, logged-out web/Android/Discord canary and verified invitation/revocation. Public page alone does not create organic traffic.
- **Paused:** Manual verification reminders, Watch Yo Mouf, Cinema standalone, other owner projects. Historical entries below are context, not active concurrent tasks.

## ACTIVE FORCE SWITCH: Community Hub Find Players / LFG #638 (2026-10-26)

- **Single active development task:** Resume the existing Community Hub **Find Players**, **Start Gaming Session**, and **Quick Match** system. Do not create a second bot, session store, or native channel/role picker. User explicitly resumed this after merged activity restart PR #734.
- **Current slice:** PR #735 adds mobile paging of 100 recent authorized local or opted-in HubLink partner sessions in the existing Find Players filter selector, 25 selectable groups at a time. Re-query current source on paging and keep current click-time permission safeguards.
- **Release gates:** Exact-head Community Hub tests, full CI, command-size checks, merge to main and independently verified Discloud rollout/Discord mobile canary. No live deployment, new SQL, external API writes, or auto role pings in this PR.
- **Next LFG work after this slice:** Verify native/local/partner browse UX end to end, then plan structured platform/region fields and further provider integrations only against supported authoritative data and approved APIs. Preserve game-specific role ping consent and /toke isolation.
- **Suspended:** Cinema standalone runtime and Discloud host-state verification, manual verification reminders, picker audit, other bot work. Historical statuses below are checkpoints only, not concurrent active tasks.

## Cinema source archive physically retired from Shield (2026-10-25)

- **Source:** 29 Cinema runtime/assets, 34 Cinema-only tests, seven archived Cinema docs and two diagnostics removed from Shield working tree. These 72 files were all present in `UglyGameFace/Dank-Cinema/main` prior to deletion; production modules and docs matched exact Git blob SHAs. Four Shield tests had intentionally changed retirement contracts; Cinema retains their original test implementations.
- **Preserved:** Shared guild-level Community & Pings notification-role metadata and its tests, Shield's Supabase migration history, Discord verification, Share Router, startup logic and databases. Nothing was deleted from production data or Discloud file storage by this Git change.
- **Branch cleanup:** Historical Cinema branch retirement candidates are deferred, not deleted, because their old source-on-Shield parity checks are invalid after extraction. Guarded final-two branch retirement aborts before deletion pending a new verified archive-specific proof. Unrelated cleanup remains governed by its existing owner gate.
- **Safe deploy:** Exact-sha CI and guarded Discloud code commit; production state, host process shutdown, Share Router video links, route 404 responses, backups and data preservation remain **unverified** until an actual host deployment. Never assume a merge stopped prior media processes.
- **Next:** Finalize PR for physical source retirement, check full CI and protect other modules, then verify authorized live deployment. Separately continue standalone Cinema app runtime/auth/DB migration; torrent engine remains inactive in its independent bootstrap.

## FORCE SWITCH active task: extract Dank Cinema from Dank Shield (2026-10-25)

- **Outcome:** Preserve Cinema source in `UglyGameFace/Dank-Cinema`; make the Discloud Dank Shield deployment start only non-Cinema services and continue ordinary Share Router.
- **Reason:** Discloud copyright notice of possible movie torrents; separate runtime services, not VPN-based masking or enforcement evasion.
- **Source main at switch:** `930f1796c3c1dd5d81d505f9ee4b4946de0952e7`. Branch `extract/disable-cinema-in-shield-20261025`.
- **Destination:** `UglyGameFace/Dank-Cinema`, draft PR #2, historical Cinema source/assets/migrations/docs/tests copied with source hash parity; two source files blocked during copy and documented. **Standalone Cinema runtime is NOT operational or deployable**; Shield shared dependencies, auth, and DB still need extraction.
- **Root cause:** `command_runtime.setup_hook` and `app.on_ready` both started `torrent_media_server`; that registered Cinema website, torrent endpoints, and feed worker. Share Router independently called torrent manager on message intake and shared the media server listener. `api_new/server` also registered torrent admin routes. Guild/member lifecycle and command navigation referenced Cinema.
- **Changes in this branch:** Create Share Router-only HTTP listener on existing port with only `/health` and `/share-video` routes; retarget pre-gateway and ready lifecycle, remove Share Router torrent processing and admin routes, retire `/cinema` and `/movie`, remove Cinema join/leave integration and navigation, exclude Cinema source from Discloud package, remove libtorrent dependency. Retain ordinary video sharing, all other Discord controls and saved data. No live DB wipe.
- **Validation required:** Full exact-head CI including command-tree sync and Share Router regression tests; deployed Discloud SHA, clean startup, route responses (Cinema/torrent endpoints should not exist), no libtorrent process/download/seeding, no dormant Cinema worker, share-video successful, configuration/secret cleanup and release rollback. **No live Discloud configuration/process was changed by GitHub edits.**
- **Known blockers:** Complete standalone port not yet runnable, two archived files missing, no access to live Discloud console here, git source cleanup/physical deletion still pending correctness review.
- **Current next step:** Finish regression/contract tests, draft PR, inspect CI and fix only extraction-related regressions, then guarded deploy and runtime verification.

## Suspended task checkpoint: manual verification reminders (2026-10-25)

- **Branch:** `fix/verification-manual-guild-role-audience-20261025`. Do not modify, merge, or deploy until resumed.
- **Requirement:** Full guild authoritative scan using saved custom Unverified role, channel or DM delivery without lifetime two-per-join restrictions, grouped up-to-25 selections, durable 6h cooldown, 1-minute/4-hour guild pacing, rechecks and reporting.
- **Evidence/root cause:** Prior preview used `fetch_members(limit=100)` mixed guild page and selected only 3; manual DMs prohibited and old PG claims had lifetime attempt cap.
- **Validation missing:** Exact-head CI incl PG16 concurrency/PG17 contracts; review, guarded Discloud deploy, live preview of 24 custom-role unverified members, channel/DM send and verification-transition acceptance.
- **Backlog:** LFG #638, remaining channel picker audit, Share Router unrelated enhancements and other features. Do not lose source branches or rewrite current user work.

## Active task: custom-role, full-guild manual verification reminders (2026-10-25)

- **Outcome:** Server owners can reach all eligible people with *their own saved* Unverified role, by channel or DMs, without lifetime two-per-join manual restrictions.
- **Root cause:** PR #727 preview called `fetch_members(limit=100)`, counted that mixed guild-member page as a recipient page and only selected 3; manual delivery disallowed DMs. A separate uncached role truth lookup could disagree with just-fetched saved settings. PG claims permanently stopped after 2 attempts per join.
- **Authoritative execution:** `/dank` Verification Center -> canonical `public_verify_reminder_ui` -> `verification_reminder_manual` -> existing shared `collect_membership_snapshot` (fully fetched + Discord recovery pacing) -> `pending_for_reminder` -> `role_truth.member_is_pending_verification(config=fresh_guild_config)` -> independent PG manual claims -> existing worker per-guild lock -> DM sender or canonical grouped mention sender. Automatic worker remains separate and unchanged in pacing.
- **Scope:** Whole-guild authoritative scan, saved role mappings including custom names/emojis and verified/staff conflict diagnostics; up to 25 selected per group, one channel post or individually paced DMs, no lifetime cap, durable 6h member cooldown, existing 1min/4hr guild batch pacing, rechecks, reports, regression tests, documentation.
- **Branch:** `fix/verification-manual-guild-role-audience-20261025` on latest main, not deployed.
- **Validation:** Pending exact-head CI (full pytest, PG16 manual concurrency, PG17 contracts, Discord menu, role truth). No live Discloud pings made.
- **Cleanup/conflicts:** Preserve PR #727 migration tables and claims, no new ledger; no second full-enumeration authority, reuse existing membership service.
- **Release gate:** Review diff, clean CI, merge, guarded Discloud deploy, live 24-member custom-role preview, targeted manual send and DM tests. Full scan must fail closed on incomplete REST source.
- **Backlog:** LFG #638 after this task's actual Definition of Done; separate unrelated channel-picker redesign remains backlogged.

## Current active task: manual verification reminders (2026-10-25)

- User validated deployment of PR #726 on main: Send Due Now works but only scans 100 mixed guild members, honors 24h first delay and confusingly reports zero without explaining how many are actually pending.
- Active task: separate, genuine staff-confirmed **Send Reminder Now** with mobile preview and next-100 pagination; independent manual PostgreSQL claim/budgets, 2 sends per join/12h cooldown, 1 batch/min and 4 batches/hour, max 3 selected per confirmation, guild/role/visibility/policy rechecks. The existing automatic scheduler, delay and attempt ledger remain unchanged.
- Implementation branch `feat/verify-manual-reminder-preview-20261025`. New manual migration is explicit, never run on bot startup; production manual sends fail closed until migration and credentials validated. No auto mass ping or DMs.
- Definition of Done: CI including live ephemeral PostgreSQL concurrency tests, UI regression (Android row/component count), compare deployed code after guarded main update, live Discloud DB migration and Android delivery with real pending test member, check member cooldown/verified transition and preserve existing DB.
- Pending release gates: exact-head full CI, reviewed diff and current production backup. No live schema changes or deployed code in this branch.
- Next major feature **LFG #638** remains backlog until manual reminders are validated. Remaining all-channel selector audit separately backlogged.

## Current channel-picker task: due verification reminders (2026-10-25)

- Single active task remains **channel-based feature setup**. After the shared Use This Channel fix, the user explicitly requested a manual **Send Due Now** action targeted to the **saved designated reminder channel**, not the chat where staff clicked.
- The new staff-only, two-step action uses the existing singleton Verification Reminder Worker and Discloud Postgres atomic attempts; it does not bypass the first-delay, repeat, max-per-join, Simple Verify role truth, permissions, visibility or verification rechecks.
- One manual press scans at most a normal 100-member page and consumes up to 3 guarded attempt slots; 60-second per-guild button throttle, shared scan lock/cursor with automatic background worker and no DM fallback.
- The button is an eligibility check, not a bypass to ping everyone. Attempts include Discord send failures and are described accurately. The automatic schedule continues normally.
- Preserve remaining channel-picker audit and defer unrelated Embed Studio/Bump Manager/Share Router work.

# FORCE SWITCH: Fix all channel pickers (2026-10-25)

- **Active task**: One shared, moderator-friendly **Use This Channel** path for channel-based configuration. Owner explicitly prioritized this after repeated failures to select text channels on Discord Android.
- The canonical Search-Safe `DankGuildResourceBrowserView` automatically gets a Use This Channel button on text-capable pickers; it reuses the existing `on_pick` callback, eligibility predicate and guild/owner checks, never auto-posts.
- Standalone native channel menus adopt `ui.current_channel.current_text_channel` and their original feature save/permissions paths, beginning with Verification Reminders, Post Builder, 4:20 announcements, Welcome/Join/Exit Cards, Live Profile Signature channels, Ticket Center/Panel/Staff Alerts/Intake and cleanup target selectors.
- **User-visible behavior:** button selects the exact invoking guild text channel; no Discord dropdown selection required. Feature-specific checks still apply, including private ticket alerts and destructive-cleanup confirmation. Multi-channel profile configuration adds current without deleting previously saved channels.
- **Remaining audit**: each existing native selector not yet covered must be migrated explicitly, with regression evidence, before describing this as complete across all feature screens. Embed Studio #722 already merged separately; preserve its artwork capacity protections.
- Previous active Embed Studio work is **paused** here. Bump Manager #713 and Share Router remain backlog. Do not do multiple active coding tasks.


## ACTIVE: Embed Studio managed artwork capacity (2026-10-24)

- **Only active task: Embed Studio**. PRs #716, #717, #718, #719, #720 and #721 confirmed merged into main. Code is not the same as feature activation: private Supabase migrations, object-storage bucket, flags, backups, shard canary and Android tests remain deployment gates.
- Current slice is atomic, owner-configurable artwork budgets and read-only orphan **candidate** auditing, not arbitrary restrictions on the separate Postgres database. Adds guild/global budget environment validation, service-role-only reservation/finalize/release/usage SQL, safe upload integration, optional Media Library capacity readout, audit CLI, focused tests and ephemeral PG17 contracts.
- Uncertain upload failures keep quota reserved until humans confirm the new object was removed. Published artwork is never automatically deleted to save space. No free-tier quota, billing tier or Discloud capacity is presumed.
- **Remaining within Embed Studio after this slice:** true high-volume per-record paginated media/template catalogs, recurring announcement campaigns, deeper collaborative editing, comprehensive retention/observability and production deployment canary. Do not prematurely update New/Updated with features that are not enabled.
- Bump Manager #713 and the suspended Share Router are still out of scope. Respect single-active-task lock.

## Embed Studio implementation sequence (2026-10-24)

- **Single active task: Embed Studio.** Three cohesive dependency-ordered PRs exist: #716 normalized private Storage v2 with per-guild CAS and manual legacy parity, #717 native mobile image/GIF uploads with separate Supabase object bucket, #718 indexed at-most-once one-shot scheduled announcements with owner-only cancellation and due-time staff/permissions rechecks. Merge in order **716 → 717 → 718**.
- The newly verified DankShield Supabase project `viamriafkbqhquvrsnua` was ACTIVE_HEALTHY with ~93 MB database data, 12 guild config rows, zero saved Studio blobs and zero Storage objects/buckets at the read-only audit. No migration was executed by drafting these PRs. Actual Discloud free storage has not been remotely measured.
- The durable private v2 library has a 1 MiB backend-aware validation budget, while the legacy guild config remains at 256 KiB. V2 currently retains per-section UI caps (50 media shortcuts, 12 templates, 8 draft owners). Future indexed page-level APIs are required before offering large unrestricted catalogs.
- All new operational functionality defaults OFF: `DANK_EMBED_STUDIO_V2_ENABLED`, `DANK_EMBED_STUDIO_UPLOADS_ENABLED`, `DANK_EMBED_STUDIO_SCHEDULING_ENABLED`. No live DB table/bucket, deployed runtime config or actual scheduled message is changed until migration, restores and canary checks pass.
- Exact-head checks, per-guild migration parity and Android permission tests remain **gates**; do not claim feature production complete before those checks.
- **Remaining feature work within the same task:** forms/action builder with persistent custom_id/RLS/intake destination safeguards, multi-moderator shared draft review/co-editing with versioned conflicts, optional live-refresh policies and indexed page-level catalog growth, asset quota/orphan auditing. Keep unbuilt features out of current New/Updated release announcements.
- Bump Manager #713 and paused Share Router remain strictly outside this workstream.

## Active Embed Studio checkpoint: guarded Storage v2 (2026-10-24)

- Merged PR #715 is on `main` at `d8974edf89edf7d848d9b8a3e9c171882c7e7a62`. Only active implementation task is **Embed Studio**. Bump Manager #713 and Share Router stay deferred.
- Verified new DankShield Supabase ref `viamriafkbqhquvrsnua`: database ~93 MB, 12 guild config rows, no current `embed_studio_v1` payload found in any of the four JSONB compatibility columns. The Discloud shared-storage free space is not measured.
- This PR introduces additive private indexed Studio item storage (drafts/templates/themes/media/published), versioned per-guild CAS, manual per-guild migration with parity checks, project-pinned opt-in adapter, negative authorization tests, and real PostgreSQL smoke CI. The default runtime continues to use legacy storage and **no live schema/deploy switch is automatic**.
- Finish SQL + Python exact-head CI and explicitly stage a test guild before enabling. Do not silently rewrite/clear original config or auto-fallback into legacy once v2 is enabled.
- **Remaining active slices**: durable media upload/object-storage (with persistent URLs), safe scheduled campaigns and worker integration, server-side pagination/unbounded metadata library redesign, dynamic refresh with moderation review, configurable forms/actions and collaboration. Maintain single-task lock; owner asked to execute the entire sequence with larger cohesive PRs.

## Studio media capacity revision (2026-10-24)

- Owner correctly challenged conservative 12-link/100-KB caps. Inspection confirms Supabase RPC mirrors config keys into four JSONB compatibility columns and Studio submits a full-library CAS blob on writes. Inactive PostgreSQL guild-config repo has a 2-MiB row guard and 32-KiB patch guard; it must be redesigned before ever routing large Studio writes there.
- This PR expands media to **50 reusable links with 20-item dropdown pagination**, and shared Studio storage from 100,000 bytes to **256 KiB**. Bound is interim, *not* Supabase's maximum capacity. Do not imply 50 images are uploaded or hosted. Future durable/high-volume content requires per-record storage and indexed retrieval, not megabyte-sized guild configuration writes.
- Extend exact-head tests to check 20/20/10 browse pages, last-page navigation, storage bounds, legacy library compatibility, no unintentional data loss, and all published message protections. One active task remains Embed Studio; #713 is untouched.

## Current checkpoint: Larger Embed Studio Media Library + Smart Text PR (2026-10-24)

- **Only active implementation task remains Embed Studio.** Owner merged #714 (verified main `2b04b598a70cf67984d1b52aec86680f30f9d873`; 9 applicable checks passed). #713 Bump Manager remains backlog; Share Router remains suspended.
- **Single canonical editor:** Add a 12-link, named guild-local HTTPS Media Library in existing `embed_studio_v1` per-guild compare-and-swap storage. Can save, select for image/thumbnail/author/footer icons, and remove only after private confirmation. Never fetch/host files or alter published messages while editing.
- **Smart Text:** Four allowlisted tokens for server name, selected channel, Discord-reported member count and UTC date. Resolve once at private preview, validate full expanded project against Discord limits, then send exactly the previewed snapshot after existing identity/authorization/destination/revision checks. No arbitrary expressions, role pings, new slash root, DM sender, API polling or live-update worker. Saved drafts retain original placeholders.
- **Acceptance:** focused model/UI tests and full exact-head CI; check edit-post compatibility, legacy templates/themes/drafts/role buttons, storage outage/CAS conflict, URL limits, mobile component rows, and publish-time preview staleness. Merge/deployment and Android testing are not yet proven.
- **Still unfinished within Embed Studio:** native durable image uploads, scheduled/campaign publishing, automatic live updates, advanced forms/actions and real-time multi-moderator collaboration. #713 must not start until this task DoD or exact FORCE SWITCH.

## Current checkpoint: Larger, coherent Brand Themes PR (2026-10-24)

- **Active task remains Embed Studio.** Owner merged #712; confirmed main starts at fc590e232e084e89b084c850cdbe240b816e313d. No FORCE SWITCH. Backlogged #713 Bump Manager is untouched.
- **This PR:** Add the shared server Brand Themes collection to the same versioned `embed_studio_v1` CAS library, with 6 built-in color presets, 8 saved shared styles, all-embed apply, save current style, safe confirmed deletion, and existing editor autosave/Undo/Preview flow. No second editor, scheduler, public post, new slash root, or DB migration.
- **Validation plan:** exact-head core/UI theme tests, legacy draft/template/post compatibility, design CI, failed storage and concurrency paths, navigation audit, and clean PR diff. Deployment and Android acceptance remain unverified before owner merge.
- **Remaining inside Embed Studio:** richer media uploads/assets, scheduling/campaign publishing, truly live content, advanced forms/actions, and multi-moderator collaboration (shared themes in this PR are style reuse, not live co-editing).

# Active task: Complete Embed Studio (owner FORCE SWITCH, 2026-10-24)

## Current checkpoint: Mobile navigation quality and release notes (2026-10-24)

- **Owner feedback:** “New & Updated” omitted newly shipped work, button colors stopped communicating actions, features were poorly grouped, and explanations assumed a developer audience. Treat this as a visible acceptance failure of the same active Embed Studio discovery/UX release. Share Router's separate implementation task remains paused.
- **Verified root causes:** `RECENT_FEATURE_KEYS` was manually stale (7 legacy shortcuts); feature buttons used `manager_only` to choose blue, and Close used destructive red; Embed Studio had no direct Design & Branding registry entry. Category descriptions and Community Tools panel contained technical language and lacked practical task grouping.
- **Changes on same PR #712:** Add one canonical direct Embed Studio route into Design, keeping its Community Tools and Setup shortcuts; switch to dated `RECENT_UPDATES` for **merged** features only (#711, #709, #710, #708 and other confirmed built entrypoints), never claim #712 is deployed; group visible category descriptions by moderator jobs; simplify novice language and help/search copy; use blue for primary task, grey for browse/back/close, green for final public confirmation, red only for actual destructive actions. Reorganize Studio editor into create/buttons/saved-work/preview stages without changing owner permissions.
- **Future regression guard:** PR-only standalone `tools/test_recent_feature_nav_contract.py` requires a navigation registry review with public menu changes. `docs/NAVIGATION_ARCHITECTURE.md` records release-note truth, mobile layout and semantic-color policy. New targeted tests check recent notes, role-safe routing, main views and Discord component limits. This guard is a review reminder, not a runtime GitHub feed.
- **Safety/acceptance:** No new slash roots, duplicated routes, background listener, DM sender, DB migration, public post, role grant or Discloud change. Complete exact-head CI, compare against latest main, merge PR only when green, then check Android Discord menu and running Discloud SHA. Previously rendered Discord ephemeral messages must be reopened after deploy.
- **Remain within active Embed Studio task:** Finish validated portable JSON and its mobile upload canary, then advanced scheduling, assets, themes, dynamic content, and collaboration. Do not imply the entire product is completed by this PR.

## Current checkpoint: Portable JSON import/export (2026-10-24)

- **Still one active task:** Complete canonical Embed Studio. User merged PR #711; GitHub `main` merge `ac6034142db593ed588aface72ced5f6e5229d32`. Its five exact-head PR workflows (Dank Shield CI, Profile Runtime, Dank Design, Ticket Owner Emergency, Command Size) all passed. Real Discord/Discloud autosave acceptance remains unverified.
- **Current feature branch:** `feat/embed-studio-json-portability-20261024` from the exact merge SHA, no unrelated branch switch. Baseline had zero open PRs.
- **Requirement:** Mobile user must export a complete validated Studio project as a private JSON file, and import it using the existing `discord.py==2.7.1` native file-upload modal. No new slash root, external uploads, separate editor, public send, persistent table, or background job.
- **Execution and authority:** Existing `StudioHomeView` staff+guild+author view guard routes to `StudioJsonMenu`; import modal rechecks guild/user/staff, bounds attachment size and read timeout, validates a versioned envelope via `embed_studio_core`, rejects foreign-guild self-role IDs, stale concurrent editor mutation, and unsupported projects, then applies the validated draft and clears any tracked-message edit target. Existing `StudioSession.apply` and `_autosave` retain canonical mutation/persistence ownership; only ordinary Preview & Publish sends.
- **Tests/cleanup:** Dedicated `tests/test_embed_studio_portable_json.py` covers complete model roundtrip, role isolation, corrupt/oversized/duplicate/unsupported JSON, native UI component and safe draft mutation. Existing template, role and recovery tests must continue passing. Review exact-head compilation, full CI, final diff, Android file upload/export and declared running Discloud SHA before production-complete claims.
- **Scope after this PR:** Schedule publishing and jobs, uploaded image asset management, shared themes, dynamic variables, advanced actions/forms, collaborative editing. Avoid treating each small merge as total Embed Studio completion. Retain the suspended Share Router checkpoint below unchanged.

## Single Active Task Lock
- **Active:** Finish Dank Shield's existing mobile Embed Studio without creating a second editor. Owner explicitly FORCE SWITCHED from Share Router on 2026-10-24. Only Embed Studio may be investigated, modified or validated until this task reaches its Definition of Done, or another exact FORCE SWITCH is supplied.
- **Baseline:** `main` at `b1e657a0d335661972128ebb76b106f118884f65` after merged #710; work branch `feat/embed-studio-durable-draft-recovery-20261024`. User work on other branches and deployment are untouched.
- **Verified current ownership:** `stoney_verify.embed_studio_ui.open_embed_studio` is canonical through Community Tools and Server Setup. `embed_studio_core` validates layouts/templates/posts; `embed_studio_roles` is the persisted role action owner; `guild_config` CAS persists per-guild `embed_studio_v1`. #580 and #581 provide rich editor and persistent self-role controls; do not duplicate them.
- **Current sub-slice:** Restore edits safely after a Discord view timeout/restart. Add bounded, per-editor-owner draft persistence in the canonical library, automatic saving after validated mutations, optimistic revision conflict detection, fail-closed errors, and regression coverage. Avoid remote media fetches, unauthorized automatic posting or a parallel service.
- **Validation requirement:** Focused tests, existing CI workflows, legacy library/template/role/edit compatibility, storage outage/concurrency/limits, and clean diff; production Android/Discloud acceptance is separate. Do not claim fully finished until all applicable evidence exists.
- **Following Embed Studio work (same single task):** audit remaining approved gaps (scheduling/campaign publication, dynamic embeds, media management, themes, JSON import/export, forms/action safety and collaboration), implement carefully without a second runtime owner, then validate. Those are NOT silently claimed implemented by this draft-recovery slice.

## Suspended previous task: Share Router
- **Outcome so far:** PR #709 merged at `775bce69f41d99392fc24efd1651327f7a764c57`, adding existing text channels via Search-Safe, non-destructive Keep Original routing, per-route filters, Facebook mobile URL patterns/diagnostics and pinned yt-dlp. PR #710 merged to `main` at `b1e657a0d335661972128ebb76b106f118884f65`, introducing bounded alternate progressive formats and installed JS-runtime/EJS support.
- **Execution path:** Existing on-message Share Router handler, guild-scoped route JSON, media resolver / yt-dlp and sending/fallback owner. No new worker, schema or root slash command from those PRs. Managed proxy deletion semantics remain separate from ordinary-channel preservation.
- **Validation:** Both are merged in GitHub; deployed Discloud version, Android real routing, problematic Facebook examples, compression acceptance and full universal media provider matrix are not proven in this record. No deployment or live canary performed by this Embed Studio task.
- **Cleanup/conflicts:** Maintain #390 as wider media-provider backlog. Compression/transcoding not implemented by #710; investigate CPU, size, visual-quality safeguards only after Embed Studio is complete. Do not reopen / touch Share Router code here. Verify exact actual branch/CI/deploy state when resuming.
- **Resume next steps:** Check merged SHA/current Discloud version, targeted real URL failures and video-format fallback acceptance, then select one bounded remaining Share Router scope under a new authorized task.

---

## Archived prior checkpoint (preserved verbatim below)

# Active task: Share Router existing-channel non-destructive forwarding (owner-requested switch, 2026-10-24)

## Current checkpoint: Use Existing Channel with Search-Safe selection

- **Owner explicitly switched** from Action & Reminder Center, following merged #708. Verified main at start `4f7756a163d32780acd544991c9aebf5bcac5b5d` with no other open PRs. Action Center v1 is code-complete; leave its internals and all unrelated work unchanged.
- Fix the cause of channels not appearing as Share Router sources: the original **Add / Change Route** source browser only displayed registered proxies/legacy routes. Add **Use Existing Channel** for ordinary guild text channels through the **existing shared Search-Safe picker** (styled names, aliases, Discord IDs, restricted channels as its existing resolver supports), without native Discord channel selectors.
- **Non-destructive mode**: an existing channel stays visible and retained. Save `delete_source=False` and explicitly suppress cleanup at runtime including dedupe/error paths; no hidden private proxy requirements for ordinary public channels. Existing sources never require Manage Messages. The bot still needs source view/history + destination view/send. Check source and destination under the existing guild lock. Never forward from age-restricted source to non-age-restricted destination.
- **Proxy mode unchanged**: managed hub sources must remain temporary, hidden, non-NSFW, and clean forwarded source posts. Preserve old routes, registration, downloads, direct memes, source/target privacy and sender permissions.
- Reuse existing on-message gateway handler, route JSON storage, route health, and existing shared Search-Safe browser. No new worker, separate database, migration, branch proliferation, or slash root. Add regression coverage to Share Router Production Reconciliation CI.
- Acceptance: exact PR head automated CI including Share Router routing safety, no original deletion, source permission checks, NSFW boundary, cross-guild isolation; provide user one mergeable PR. Do not claim deployed until after owner merges and Discloud runs it.

---

## Previous completed task: Action & Reminder Center v1


## Current checkpoint: 4K automatic Member Setup reminders (v1 code-complete candidate, 2026-10-23)

- Owner merged #707. Verified main `f60be754d57ba6ec330acec0406bad92b0ff814f`; no other open PRs. Single active task remains Action & Reminder Center. This is the requested larger, final major implementation PR (#708 expected).
- **User requirement:** Unverified members are automatically included by the EXISTING Simple Verify reminder worker when the guild enables its verification policy; they never need personal reminder opt-in. DO NOT send separate verification reminders through Action Center or require opt-in for Unverified status.
- **Separate Member Setup condition:** self-service private member opt-in still governs optional Member Setup DM reminders. Staff can enable/disable automatic Member Setup DMs from **/dank → Actions & Notifications → Schedule & Readiness**. Toggle is persisted in existing guild settings via CAS, defaults OFF, requires published owner setup revision, active builder condition and reachable private PostgreSQL ledger. No Discloud environment variables for normal guild use.
- Reuse the existing profile-store opted-in-only JSONB feed (max 5/page), existing owner completion/exemption/consent preflight, quiet hours, scheduled delays, existing claim-first private PostgreSQL transaction and attempt/day budgets. Background worker starts after protected startup only, 120s quiet period, fixed scan/poll/claim caps and limited REST lookups of opted-in IDs only; no full guild member sweeps.
- On each delivery, canonical readiness + policy is checked before atomic claim; fresh policy, current membership, consent, completion and quiet hours checked AGAIN after claim before sending one link-free DM. Uncertain claims or outages block sends. Claimed failures never retry immediately, protecting against duplicates after crashes/restarts.
- Runtime monitoring reports guild-private aggregate counters in staff panel; persistent attempt/cooldown safety comes from existing private PG. No new schema/migration/new verification sender/new slash command. Automated Member Setup remains default OFF until each server owner explicitly enables it.
- **Acceptance is code-complete:** exact-head CI, integration tests and security checks; no real member DM test required at this stage. Owner will test later and file bugs. Do not claim production-verified or close whole post-audit epic (#367).

## Current checkpoint: 4J integrated bounded Member Setup dry run (2026-10-23)

- Owner confirms #706 merged. Verified main `d85a5449b0815864e4b5b25b43306309fd0c872b`; no other open PRs.
- **One larger connected Action Center slice:** staff-only **Schedule & Readiness → Preview Opted-in Members**; bounded 5-profile pages from existing profile-store JSONB opt-in feed. The service gets the current published Member Setup revision and checks cached members through the existing `inspect_member_setup_readiness` (fresh consent/completion + quiet-hours/delay/caps + actual private PostgreSQL history). No new eligibility owner, scheduler, claim or send loop.
- At most 2 concurrent expensive per-member reads, each with an 8-second timeout; missing guild member cache is "not_cached" rather than guessed departed/eligible and **never triggers Discord REST fetch**. Staff private summary includes counts only, no individual user IDs. Failed source/ledger state never becomes due. Versioned per-guild opt-in is authoritative and rechecked by the existing readiness owner.
- Paged Next / Restart / Back preserve server and invoking staff identity, with rechecked staff authority, fresh revision and per-click server isolation. A full page suggests more might exist but never means more are actually due.
- **Absolutely no new automatic sender, scheduled job, reservation, PostgreSQL change, DMs or rollout switches.** User explicitly deferred live single-member canary. Keep all process-local canary gates off.
- Acceptance: focused end-to-end fake owner + outage tests, exact head Action Center + full CI, live staff mobile preview after merger (no deliveries). Broader production activation is a separate **approval-gated** integration.

## Current checkpoint: 4I bounded Member Setup consent source (2026-10-23)

- Owner merged PR #705, confirmed on main `c29964f4699be93c8eac44ca4f3764b3226fa135`; no other open PRs.
- **Only active development task:** prepare real Member Setup reminder pipeline without another independent scheduler. The existing `dank_profile_guild_settings` table already stores per-member/per-guild opt-in, but legacy `list_profile_guild_settings` scans all profiles. Slice 4I adds a **bounded (1–10), server-filtered, JSONB `settings @>` opt-in cursor page**, retaining the existing profile storage adapter and its TEXT-snowflake index/order.
- `read_member_setup_opt_in_page` reuses canonical `action_member_consent.parse_consent`; only explicitly opted-in, versioned records enter the private candidate feed. It is not a due queue or approval to send. Any malformed settings/backend outage fail closed.
- **Not yet wired to any worker, scheduler, server delivery switches, Discord API sweeps or sending.** A subsequent slice can connect these bounded pages to the existing action readiness/canary/claim safety, but automatic delivery remains disabled. Owner deferred the live one-person canary.
- No migration, schema/index additions, DSN changes, new slash commands, Discord REST bursts, background process, or changes to existing Verification, Ticket, Member Activity, Quiet Notice, 4:20, or /toke senders.

## Current checkpoint: 4H saved notification policy status (2026-10-23)

- Owner reported PR #704 merged. Verified main is `67fd502e3471e9c5f6ea9eb6354d9ec01c7a08e1` and all eight exact-PR tests passed.
- **Only active task:** Action & Reminder Center, read-only saved policy checks from its existing **Notification Systems** directory. Source-of-truth stays with each existing notification owner.
- This slice reads only three per-guild policies through canonical existing readers: Simple Verify, Quiet Server Notice, and **guild-wide** It's 4:20 Somewhere. Member Activity, Ticket Automation and /toke remain explicitly "owner-managed / not inspected" until source-owned read-only status adapters can truthfully distinguish outages from off states. No member subscriptions, recipients, message histories or other guilds scanned.
- Show saved enabled/disabled/not configured/unavailable, **never "worker running" or "delivery healthy"**. Independent 8-second timeouts fail closed; exception text and DB credentials are not shown to staff.
- No new scheduler, reminder worker, claim, DB write, REST send, secret, migration or new Discord slash command. The live one-person canary remains deferred with all Action Center delivery gates off.

## Current checkpoint: 4E member self-status (2026-10-23)

- **Only active development task:** Action & Reminder Center, 4E member-facing read-only setup DM eligibility. Branch `feat/action-center-4e-member-status-20261023` starts from verified main `92dc95c062847ee9ab2c1cef926d24bf1df248ec`. No other epic work or branch cleanup.
- The owner explicitly postponed live one-member testing. **Do not enable** `DANK_ACTION_CENTER_CANARY_SEND`, target-guild/member/revision, or approved-source env gates, reserve claims, dispatch DMs, or start any scheduler. Release SHA independent host attestation and candidate canary approval remain deferred.
- Private production PostgreSQL backup/recovery test succeeded; the approved 4C + 4D.4 additive migrations completed with all three `ready` checks true, counts `(0,0,0,3)`. Discord Readiness Report corroborated both tables. This supersedes older "unapplied" sections below, which are historical.
- Next safe slice: expose the canonical one-member read-only eligibility reason to the member from My Reminder Preferences, with timeout, per-member UI identity, no private error leaks and no new delivery path; focused regression and exact-head CI required.

## Slice 4F: inventory existing notification owners (2026-10-23)

- PR #702 (4E) confirmed merged into main `f8214a17049d3ff1bcb934836f653cffb8959a54` with six successful PR checks.
- The owner requested **reusing existing reminder systems instead of creating a seventh independent scheduling engine**. One active task: staff-only existing-system directory with canonical registry routes and explicit owner boundaries. Branch `feat/action-center-4f-notification-directory-20261023` starts from the exact merged main SHA above.
- Verified distinct existing owners: Verification Reminders (private Discloud PG), Member Activity Notices (member notice records), Ticket Automation, Quiet Server Notices, It's 4:20 Somewhere scheduler, and /toke one-session opt-in reminders. Their independent processing and persistence remain unchanged.
- No live canary: owner explicitly deferred it. `DANK_ACTION_CENTER_CANARY_SEND` and recipient/source gates stay OFF. No scheduler activation, production data mutations, code unrelated to Action Center, new slash roots, or new system duplicate.
- Acceptance: action-directory owner map/route parity, staff and privacy checks, action-focused + full CI, merge/deploy verification before feature use.

## Slice 4G: own Actions & Notifications navigation category (2026-10-23)

- Owner reported PR #703 merged. Verified final main `32135a4fd8ee628518577948ae04b1b614b4507e` and no open PRs before starting.
- Only active task: move the existing Action & Reminder Center **from Onboarding & Access** into a first-class `📋 Actions & Notifications` category on `/dank home`. Expose five registered entrypoints that call existing owners: Action Inbox, Notification Systems, Action Builder Draft, Schedule/Readiness Draft and My Reminder Preferences.
- Preserve Verification, Verification Reminders and Member Setup under the original Onboarding & Access category. Preserve existing system owners, their original feature routes, click-time staff authority and guild isolation. No duplicates of scheduler, policy, completion or delivery logic.
- Absolutely no runtime background worker, Action Center canary activation, scheduled sending, new DB mutations or secret changes. Live one-member test remains deferred at owner's request.
- Acceptance: focused registry/Action Center navigation tests, all exact-head CI, Android category visibility and click-time authority checks after safe merge/deployment.

## Current single-task lock
The owner explicitly FORCE SWITCHED to **Issue #367, Action & Reminder Center**, after PR #691 merged. The previous 4:20 task and guarded branch-retirement task are now both paused. Do not mix further 4:20 work, branch cleanup, Cinema, or unrelated verification framework implementation into this slice.

## Slice 4D.5B: Operator-approved live source lock (active, 2026-10-23)
- PR #699 (4D.5A) verified merged at `8b5ab0e27c807c0fcbd4c8b420bd84d9077b4e3b`, all six workflows successful; Discloud deployed commit and private migrations remain **unverified/unapplied**.
- Active branch `feat/action-center-4d5b-release-lock-20261023`; the single-task FORCE SWITCH lock remains Action & Reminder Center, with all unrelated projects paused.
- Add absent-by-default `DANK_ACTION_CENTER_CANARY_APPROVED_SOURCE_FP` exact lowercase 16-character CI-source fingerprint requirement to the existing four send/guild/member/revision gates. Must match actual running source digest, including before claim and immediately prior to DM, or refuse send. Approval must come from independently reviewed exact-release CI, never from the self-reported bot value alone.
- Owner-only Readiness Report displays the source-approval match **separately** from external GitHub main attestation, which remains unverified until deployment proof. Missing, uppercase or stale source lock cannot authorize a DM.
- Extend existing focused canary/owner/readiness regressions. No new workers, scheduler, Supabase or private PostgreSQL migration, Discloud config, secrets, claims or live DMs.
- **Still blocked**: verify stoneyverify deployed SHA/fingerprint, review and explicitly authorize the two private PostgreSQL migrations on the actual private Discloud PG database, opt in one non-exempt pending member, configure the exact one-person canary gates and approve a single monitored DM. No implicit production authorization in a GitHub merge.

## Slice 4D.5A: Non-sending deployment and database preflight (active, 2026-10-23)
- PR #698 (4D.4) is VERIFIED MERGED at `1d12e05d408bd5e7198d607422aad44a80c71927` and all seven workflows passed. No open PRs existed at the start of this slice. This does NOT prove Discloud deployed that SHA.
- Active branch `feat/action-center-4d5-live-readiness-20261023` starts from that exact merged main; single active task remains **Action & Reminder Center** only.
- Add app-owner `Readiness Report` inside existing Owner Canary Console, accessible even when exact-target flags are disabled. Read-only source fingerprint/declared SHA observation, private claims and audit schema SELECT readiness, dedicated PG DSN presence boolean, target flags matched-to-guild boolean; no secrets/user IDs exposed.
- Expand runtime source fingerprint to include action canary, audit, claim, consent and permission-critical files; print equivalent CI fingerprint so owner can compare with Discloud. A runtime SHA/fingerprint remains **unverified against current GitHub main** until actual deployed release evidence is supplied.
- Fix owner console button authority: Discord application ownership is canonical; no unrelated guild staff-role prerequisite inside the owner-only view. Entry navigation remains staff-scoped.
- New CI tests cover default-off/DB-unconfigured behavior, wrong-target rejection, failures without private data exposure, read-only proof and owner permission. No server scan, new worker, migration, auto-DM, Discloud deploy or env variable changes.
- **Production gates still pending:** verify deployed SHA/fingerprint from Discloud; separately approve the private 4C claim and 4D.4 audit migrations; verify a genuinely opted-in, non-exempt member with pending published Member Setup revision; separately approve exact recipient and live one-shot canary. No production actions from this PR.
- Paused: 4E/4F, 4:20, Cinema, Verification Framework, member lifecycle and branch retirement.

## Slice 4D.4: app-owner manual canary console + durable audit (active, 2026-10-23)
- Verified PR #697 (4D.3) merged as `cc98488482da22e505b089e931ee8e8c1618d1ba` with 6/6 checks green. Current branch: `feat/action-center-4d4-owner-canary-console-20261023` on that exact main.
- Expose **Owner Canary Console** only from Action Builder → Safety Schedule; actual Dank Shield application owner verified via `interaction.client.is_owner(user)`, not a configured staff role or server Administrator. Typed confirmation `SEND guild/user/revision` is one-shot, and all 4 target-locked process-local canary gates still have to match. None of those gates are set by this PR.
- Require durable `requested` PostgreSQL audit record *before* calling the existing strict single-user claim dispatcher, and final audit status after; fail closed if either 4C claims ledger or separately staged 4D.4 audit schema is missing. No message content in audit.
- The additive `postgres/migrations/202610230002_action_center_canary_audit.sql` plus `tools/migrate_action_canary_audit_postgres.py` are staged but **never applied automatically to production**. Original 4C claim migration remains unapplied. No auto-DMs, schedulers, root slash commands or broad member fetches.
- Extend focused and isolated PostgreSQL CI, owner permission and confirmation replay regression, lifecycle/retention ownership; verify full CI at PR head before merge, then Android/Discloud/real DB rollout via separate explicit owner authorization. Keep owner 4D.5 canary rollout and general 4E/4F feature work paused.
- Single active task lock remains Action & Reminder Center 4D.4. All unrelated tasks paused.

## Slice 4D.3: disconnected manual single-member canary (active, 2026-10-23)
- Verified PR #696 (4D.2) merged as `688e0c600a16bab5579b10c7f5d3e73a098b312b`, all six checks green. Branch `feat/action-center-4d3-canary-dispatch-20261023` starts from that exact main.
- Add **unwired** `action_canary_delivery.py` for one Member Setup user/revision only, requiring exact 4-way process-local canary gates before any claim, cached membership proof, 4D.2 durable readiness and atomic private PostgreSQL reservation. After reservation, recheck user opt-in, canonical Member Setup completion, guild schedule/quiet hours, same recipient/revision and environment gate; cancel any uncertain outcome without retry.
- Canary limits strictly ONE attempt per member/revision and ONE per guild-local day. DM text is fixed, minimal and mention-free. Existing verification reminder worker is unchanged.
- No public slash/button, guild scan, startup hook, scheduled worker, deployment configuration, secrets, live DMs or private PostgreSQL migrations in this slice. The 4C claim tables remain **not applied** to production.
- Focused tests and CI cover opt-out/completion after claim, exact gate revoke, day rollover, failed Discord send, no callers, denied claims, isolated real PG17 concurrency.
- **Single active task remains Action & Reminder Center 4D.3.** Paused: 4:20, Cinema, wider Verification Framework and branch-retirement work. Post-merge approval for any true canary rollout requires a separate, audited human-authorized invocation and live DB migration review.
- See `docs/ACTION_REMINDER_CENTER.md`.

## Slice 4D.2: actual private ledger readiness (active, 2026-10-23)
- PR #695 (4D.1) merged into `86d44bb9e135d6862c61c3becc10f0cdb58f7f00`, with all six checks green. Member consent remains explicitly opt-in per guild; zero automatic Action Center reminder DMs enabled.
- Branch `feat/action-center-4d2-ledger-readiness-20261023` starts from that exact merged main; this is the **only active development task**.
- A safe per-user/revision per-guild/day PostgreSQL claim **read-only** adapter returns zero counters only for a successful empty query, never a failed/missing DB/migration. Existing 4C claim schema is **not** installed to production through this PR.
- `action_delivery_readiness.py` performs the 4D.1 canonical preflight before and after actual ledger reads, comparing guild schedule/draft and applying true retry cooldown, attempts and guild daily budget. **delivery_authorized always False**; no atomic reservation, new worker, notifications or broad member sweep.
- Existing Action Builder → Safety Schedule gains staff-only `Check Delivery Readiness` for the requesting staff member, via existing authorization checks. Staff exemptions and unprovisioned ledger produce honest blocked results.
- Focused CI, real disposable PG17 tests, full bot tests, mobile UX and Discloud commit verification remain gates. Other tasks (4:20, Cinema, verification framework, branch retirement) remain paused.

## Slice 4D.1: private member reminder consent and no-send preflight (active, 2026-10-23)
- PR #694 (4C) merged as `311d4890e920b279348c71125e2c868f6e20c08b`, with all seven workflows passing. The manual private PostgreSQL DDL has **not** been applied, and no Action Center automatic delivery has been activated.
- Active branch: `feat/action-reminder-center-4d-member-consent-20261023`, based on the exact merged main.
- Member-owned per-server, default-OFF Member Setup DM preference uses existing private `profile_guild_settings` and verified write/reread. The Action Inbox gains a private My Reminder Preferences page with enable/stop controls and click-time owner identity checks.
- `stoney_verify/action_delivery_preflight.py` rechecks canonical Member Setup exemption/completion/revision date, per-member consent, draft inclusion and schedule preview. Its `delivery_authorized` is always false: no sender, no runtime claim, no new worker, no schema or secrets change.
- Focused regressions check per-guild privacy, failed writes/reads, unknown schema, stale/complete requirements, no double verification reminder ownership, and never-authorized sends.
- **Single active task remains Action & Reminder Center 4D.1.** Previous 4:20, Cinema, full Verification Framework, member lifecycle and guarded branch cleanup remain paused. Do not enable a sender in this PR; independent 4D.2 delivery rollout needs explicit migration and canary.
- Validate exact-head focused/full CI and Discord Android member UX before merge. Discloud SHA still needs real-world acceptance.

## Slice 4C: Scheduling and anti-spam safety policy (active, 2026-10-23)
- PR #693 (4B) merged into `c3a5ba5852a44a460e8120681da3b4b20591ff0e`. Active branch `feat/action-reminder-center-4c-safety-20261023` starts at that exact SHA.
- 4C writes **only preview-only scheduling limits**, not enabled reminder automation: first wait, repeat cooldown, max attempts, quiet hours in a validated IANA timezone, per-guild daily cap.
- New `stoney_verify/action_schedule_policy.py` is the separate CAS-saved guild schedule namespace, strict `enabled: false`, source-backed dry-run decision; `public_action_schedule.py` offers staff-only edit and personal preview through existing Action Builder.
- Staged **manual-only** private PostgreSQL DDL and dormant transactional claim adapter for future Member Setup revision reminders in `postgres/migrations/202610230001_action_center_delivery_reservations.sql` / `stoney_verify/action_center_postgres.py`. The existing verify reminder worker/ledger remain canonical; there is **no new worker, production migration, or live notification** in 4C.
- Validate exact PR head, fast and full regressions, staged SQL smoke, Android mobile click-time authority, Discloud deployed SHA. Do not claim 4C is live or start 4D delivery before explicit post-merge acceptance.
- All unrelated tasks remain paused by single active task lock.

## Slice 4B: Action Builder and Conditions (active, 2026-10-23)
- PR #692 (4A) merged in `8ed1455321ef52c80b15ada83db4f7bb53c38fbb` with all eight CI workflows passing. Discord/Discloud acceptance remains a deployment gate.
- New active branch: `feat/action-reminder-center-4b-policy-20261023` from that exact merged main.
- Per-guild **draft-only** policy in `action_builder_policy.py`: allowed source conditions `pending_verification` and `member_setup_revision`; strict pending-member scope; preview-only/manual schedule; authoritative severity never downgraded.
- Config stored in the existing `guild_configs` feature namespace `action_reminder_builder_v1`, validated and saved via atomic `compare_and_swap_guild_config_key`; revision conflicts and unknown schema fail closed. No new table, migration, worker or secret.
- Staff-only mobile `commands_ext/public_action_builder.py` linked from the canonical private Action Inbox, with edit, preview, refresh and back; recheck staff authority at click-time; preview only the requesting staff member's live canonical truth. New stored flags always `delivery_enabled: false`.
- **Single active task lock** remains Action & Reminder Center 4B. Verification Framework, 4:20 follow-ups, Cinema, member lifecycle and branch-retirement work stay suspended. Do not start 4C scheduled delivery until this PR passes full CI and mobile acceptance.
- See `docs/ACTION_REMINDER_CENTER.md` and `tests/test_action_builder_policy.py`.

## Slice 4A: one authoritative private Action Inbox
- Baseline `main` SHA: `7f2c981b8952341c3556f71136cc752ca0968fac`.
- Feature branch: `feat/action-reminder-center-foundation-20261022`.
- `action_center_service.py` reads pending verification from canonical role truth and review-needed Member Setup from `public_member_setup._member_payload` so access-gate grandfathering, exemption, and completion truth are retained. Recompute at each open; unknown sources fail closed.
- `commands_ext/public_action_center.py` renders the owner's private mobile inbox with only source-derived actions. Staff shortcuts delegate to existing Verification Reminders and Member Setup managers after click-time staff checks.
- Register one canonical `action_reminder_center` route in `navigation_registry.py` and `public_command_surface_v2.py`. No new slash root, independent worker, database table, scheduler, unsolicited ping, persisted completion, or access mutation.
- Action contract: Target → Condition → Schedule → Delivery → Action → Completion. Slice 4A restricts to member pending conditions, on-open read-only private delivery, and completion exclusively in original owners.
- Rollout gates: fast focused tests, exact-head full CI, registry parity, Android Discord canary with 2 pending/cleared cases and role changes, Discloud live SHA evidence. No production data/secrets changes.
- Future slices after explicit completion: validated shared action-adapter policy, configured schedule & throttle, durable claims/recheck at delivery, staff tasks, events, notifications. Do not call this full Action & Reminder Center complete just because the inbox ships.

## Paused preceding 4:20 work, retained verbatim for audit


## Active outcome
Build version 2 of issue #687 after the owner merged PR #688: richer world clock, quick and join-active canonical /toke integration, opt-in server daily celebrations with quiet hours and notification-role controls, and personal city/timezone/DM preferences. This is a continuation of the SAME active 4:20 task; branch-retirement remains suspended.

## Authority and execution path
- Current main at branch creation: `830e961eab0e2e348d4b606402aea4bfa455f691` (merged PR #686).
- `commands_ext/public_command_surface_v2.py` owns the public 11-item application-command tree and the canonical /dank home router. `navigation_registry.py` owns discoverability.
- `commands_ext/public_toke.py:open_toke_command` owns existing per-guild starter/ping authorization, permission validation, durable cooldowns, media composer, and session posting. Never replicate its outbound session path.
- `guild_config.get_guild_config` and `compare_and_swap_guild_config_key` own safe per-guild feature state writes.
- **Requirement interpretation:** separate world-time activity integrated via existing /toke, not a new slash command. On-demand board with manager-controlled non-pinging public share. Auto announcements are deliberately not enabled; no scalable scheduler exists for this feature and the owner did not choose a cadence or consent model.

## Changes in feature branch
- Pure `stoney_verify/somewhere_420_clock.py`: DST-aware 4:20 AM/PM in IANA cities, grouped next waves.
- `stoney_verify/commands_ext/public_somewhere_420.py`: on-demand world-board, private refresh/toggle, canonical /toke handoff and staff-only public sharing guarded by atomic per-guild one-hour claim.
- Add feature to canonical registry and compact nav router, without adding command roots or startup workers.
- Regression tests `tests/test_somewhere_420.py`; product/acceptance documentation `docs/ITS_420_SOMEWHERE.md`.
- No production database migration or Discloud changes.

## Validation status
- Pending CI, focused tests, production-path integration canary, clean diff check. Do not claim deployed or complete before evidence.
- Test permission errors, concurrency and config-write failures, 4:20 DST edges, and /toke integration.
- Release through one reviewed PR; evaluate CI after each commit.

## After first-release merge: version 2 work (2026-10-22)
- PR #688 merged into main at `85896fdeafbe725acc076816c1402b064df90d12`; original 4:20 launch implementation is on main.
- Current feature branch `feat/somewhere-420-celebrations-20261022` begins exactly at that merged SHA.
- New `somewhere_420_schedules.py`, `somewhere_420_runtime.py`, and SQL migrations introduce service-role-only schedule storage and atomic at-most-once daily delivery claims. Worker starts only from the existing guarded background-workers path; claims due records without scanning guilds.
- `commands_ext/public_somewhere_420_settings.py` provides Search-Safe server channel selection, explicit opt-in timezone/quiet/role settings, and personal favorite/DM options. `public_somewhere_420.py` adds next-wave focus, quick-to-text-only Toke, existing-sesh lookup, and panel links.
- Public /toke parameters must stay unchanged; a private wrapper calls the same existing /toke handler's guard path.
- **Migration not applied by PR:** only enable daily schedules after correct Supabase deployment, RLS/claims validation, focused CI, full regression checks and real Android Discord canary. Do not claim 10k concurrent punctual sends without load test.
- Dedicated `.github/workflows/somewhere-420-ci.yml` validates SQL and focused Python; full CI still applies.
- Status: work in feature branch, not yet merged/deployed. Confirm CI and schema safety before merge.

## Version 3: role-only gentle heads-up (October 22)
- The owner confirmed merged PR #689 is running in Discord but Daily Server Settings reports missing database migration. Connected read-only queries confirmed neither known Supabase project has `dank_somewhere_420_schedules`; deployment migrations are blocked by two remote-only historic schema-migration versions, which MUST NOT be removed blindly. Those two already-applied historical SQL statements have now been mirrored exactly to their original filenames on this PR branch, without changing migration history or executing DDL. Confirm production migration preview before deployment.
- Active feature branch `feat/420-gentle-headsup-20261022` starts at merged main `252ccc08b7d7c24e8586b4c373ce8d5e74ca663e`.
- **Owner update:** eligible people are automatically included while holding the exact server-selected @Stoners role, with a prominent DM Stop reminders button and member menu Stop/Resume. Staff still explicitly enable this role-based server program; other guilds are not automatically opted in.
- One at-most-once 4:15 PM server-local reminder wave, no 4:20 repeat, no public pings, at most one DM per user per local day even across multiple servers. All durable opt-outs/claims are private tables; role resolution uses shared Search-Safe picker, not native Discord pickers, and actual role membership checked at send time. No mass guild fetch.
- A second migration `20261022201500_somewhere_420_role_heads_up.sql` depends on v2 schedule table and must be applied on the actual active production DB before staff can enable the feature. Merging code does not apply it.
- Conservative wave limit 250 cached role members per guild per day, at most two waves concurrently with paced DMs and a hard 4:20 cutoff, for Discloud REST safety. Large-guild delivery and cache-completeness requirements remain acceptance gates, not solved by optimistic claims.
- CI and mobile canary required. No migrations or production data writes are authorized through this documentation; do not silently repair unrelated migration history.
- **Single task lock remains 4:20 improvement**, guarded branch retirement stays paused.

## Suspended task (preserve for resumption; NOT active)
**Guarded retirement of remaining Dank Shield branches**, previously active in `ACTIVE_TASK.md`.
- Last reviewed main `830e961eab0e2e348d4b606402aea4bfa455f691`; existing PR #686 merged and GitHub Action runs #39847982721 (preview) and #39848036329 (delete) succeeded. 33 branches remained after completed deletion.
- Previous active branch was `chore/retire-last-proven-ancestor-20261022`, merged in #686; reviewed original 22-ref batch had already retired. No unfinished proposed branch-retirement PR remains.
- Next work: independently evaluate 24 diverged branches carrying unique code and 8 zero-ahead known-good/backup/recovery refs before archive-first retirement; protect `main` and live Discloud rollback refs. Example held unique branch: `feat/cinema-rolling-supervised-worker-20261014` adds a file not on main. Do not delete by age/name or run broad deletion modes until proof.
- Branch-cleanup tests and exact-SHA archive safeguards remain in `.github/workflows/guarded-merged-branch-cleanup.yml`; suspended validation/cleanup status: reviewed 22+1 batch succeeded; remaining 32 non-main historical heads unreviewed for safe retirement.
- Discloud bot, source/candidate DB, and all recovery credentials remain untouched. Dedicated recovery drill #39721416434 passed, but no database production cutover authorized.
- Backlog: resume this retirement only after current 4:20 feature reaches its full Definition of Done, or another explicit FORCE SWITCH.

## Additional backlog
Discord developer compatibility tracker is scheduled weekly, read-only; do not start improvements mid-feature.

## Risk/blockers/next step
Current code is on a temporary feature branch, no merge/deploy. Need confirm all CI suites, verify user-facing navigation route from /dank home, and run a real Discord test where reasonably possible. Automatic scheduled announcements remain intentionally out of scope.
