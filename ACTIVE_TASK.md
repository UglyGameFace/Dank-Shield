# Active Task Lock — Dedicated DankShield Supabase migration (retention slice, 2026-10-20)

**One task only:** finish moving Dank Shield onto dedicated Supabase `viamriafkbqhquvrsnua`. Phase 3 PR #653 is merged to main. The immediate bounded subtask is safe lifecycle/retention and FK indexes, not an unrelated feature or premature cutover.

**Root cause:** two authoritative history triggers pruned all versions by age/rank, including staff manual restore points; the manual Python backup path did too. Expired tokens did not have a scheduled DB reclamation path. Two FK reverse lookup indexes were missing. The old DB experienced write/amplification bloat; no general event retention policy is authorized.

**Branch:** `migration/dankshield-fresh-retention-20261020`. Migrations `011` and `012`; rollback-only `retention_smoke.sql`; one Python cleanup parity change with focused regression test; and retention runbook.

**Validation status:** Before branch edits, 011-equivalent schema and token/manual cleanup passed on the dedicated candidate inside an explicit ROLLBACK; pg_cron 1.6.4 extension creation separately passed inside ROLLBACK. New migration smoke, cron job installation, exact-head CI, full source parity and deployment validation remain pending; do not mark complete based on these preflight checks.

**Safety:** Source shared Supabase is still the live bot database. No deletion of any original data, no cutover or Discloud env changes, and no changes to Aiven recovery. This retention slice does NOT remove moderation/ticket/identity evidence or member activity history without separately verified policies.

**Next:** versioned migration + smoke -> dedicated project apply and security/performance advisor retest -> PR exact-head CI -> owner merge -> remaining moderation/Community Hub/Cinema/profiles migration and final source catch-up/canary/rollback before cutover.

---

# Active Task Lock — Dedicated DankShield Supabase migration (2026-10-20)

**Only active task:** Move Dank Shield from the shared Supabase project to
dedicated project `viamriafkbqhquvrsnua`. Preserve current operational and
security data; omit disposable historical logs and expired tokens by owner
direction. Do not undertake unrelated feature work or create parallel
migration branches. Aiven backups remain available for recovery, not cutover.

**Merged:** PR #651 critical config/spam/hostile actor state (34 records);
PR #652 ticket/panel/current audit, atomic ticket numbering (349 records).

**Phase 3 branch:** Member/role/activity/verification contracts and
current config history restored (1,638 more records). Snapshot matches source
as documented in `supabase_fresh/PHASE3_MEMBERS_VERIFICATION.md`.
Critical activity batching, join audit, and config-history tests pass
inside rollback-only transactions. Fresh project now 34 tables and 2,021
snapshot records, about 16 MB allocated.

**Still blocked:** Full RPC/feature parity, latest member verification
workflow, moderation/invite metrics, Community Hub/Cinema/profiles,
authenticated/staff permissions, new-org bandwidth controls, any changes
since the snapshots, live Discloud canary/rollback and owner authorization.

**Safety:** Old Supabase is still live. No cutover/credential changes,
source deletes or removal of Aiven recovery copies authorized.


---

# Active Task Lock — Dank Shield Aiven PG18 auxiliary original staging (2026-10-19)

**Scope:** Continue only the current Aiven PG18 migration. No unrelated Community Hub/Discloud branch work. Preserve all source originals, minimize Aiven disk cost and avoid blind copying of Idle Grow data from the shared source.

**Latest verified checkpoints:** History workflow #5 SOURCE-VERIFIED 15,674 original config/history/moderation/invite event rows. Operational core workflow #6 SOURCE-VERIFIED 997 rows across 15 tables (Aiven allocated 48.20 MiB total, 1.11 MiB operational). Guild Config State Seed workflow #2 SUCCESS, 12/12 originals full-row matched, offline revision 0, Supabase remained production.

**Current isolated candidate:** owner-only original staging for 18 *other* ticket/member/verification tables. At the Oct 19 source audit, they held 1,843 rows/3.64 MiB. Preserve raw PK/JSON/timestamps and credential-like verification/session tokens securely, but NEVER use staged tokens as live authentication. All 18 source primary key shapes were inspected from PG metadata. Use the private dank_migration.aux_staged_originals table, exact row/text SHA parity, immutable/no-prune, read-only repeatable-read source, fresh reread, disk quota bounds and manual confirmation.

**Release:** PR exact-head regression+PG17 integration CI -> merge -> manually preview -> install private schema -> preview -> stage -> inspect SOURCE-VERIFIED & Aiven storage. No automatic cutover or deployment.

**Still blocked:** remaining source-table ownership + canonical schema/RPC/runtime feature conversion, privilege and expiration rules, final live write delta, fresh independently stored encrypted source backup, production Discord canary, rollback, explicit owner cutover. Supabase source unchanged; Discloud Auto Deploy OFF.

---

# Active Task Lock — Dank Shield Aiven PG18 operational staging (2026-10-19)

**User directive:** Continue the ongoing Dank Shield Aiven PostgreSQL 18 migration, preserving every original record, minimizing quota consumption, and no unrelated project switching.

**Completed:** 12/12 original guild configurations were verified in the private Aiven shadow. Compressed history workflow #5 (2026-10-19) reported SOURCE-VERIFIED for 15,674 original history/security records. Private archive schema installed. No production cutover or Supabase deletion.

**Current isolated slice:** stage the 15 existing operational SOURCE_KEYS tables (997 live source rows at Oct 19 audit, ~682 KiB original JSON) into new private Aiven staging while retaining original PKs, full JSON text and digests. Preserve 10 legacy guild_role_rules with nonstandard guild IDs and 5 ticket_notes with NULL guild ID. Existing staging, shadow, guild_config_state, history archive, voice data, public schema, and Discord runtime remain untouched.

**Release sequence:** PR exact-head CI + lifecycle registration -> owner-only read-only preview -> owner-confirmed private schema install -> read-only preview -> owner-confirmed staging -> verification and stage storage audit. All stage source reads remain read-only. Changes during import cause rejection; late production writes require final catch-up.

**Still blocked:** no complete runtime PostgreSQL repository routing, feature-specific canonical tables, source write freeze, verified final live parity, rollback acceptance or explicit production cutover. Discloud Auto Deploy OFF. The existing optional offline guild-config state seed must be separately confirmed; this slice uses a new table so it does not interfere with that seed's no-extra-staging guard.

---

# Active Task Lock — Dank Shield Aiven PG18 lossless history archive (2026-10-19)

**User-directed return to database migration:** On October 19 the owner explicitly redirected work back to the database migration after an unrelated Community Hub continuation. Database is now the single active task. Community Hub PR #645 was merged separately while this database slice was being prepared; its previous task record is retained below. No new Community Hub work belongs to this migration slice.

**Verified checkpoint:** Earlier Aiven shadow preview/import succeeded for 12/12 guild-config full original rows; no runtime or Discloud cutover occurred. Current main includes owner-only offline guild-state seed workflow but no evidence of successful live seed dispatch. Source Supabase remains authoritative.

**This bounded slice:** Full-fidelity, compressed, immutable history archive for guild_config_versions (920 source rows), staff_action_logs (3,369), activity_feed_events (4,756), and dank_invite_block_events (6,629), using a private Aiven schema and manual preview/install/archive steps. Source PK, timestamps, guild IDs, original JSON, and SHA256 verification preserved; all 3,369 moderation-feed links must be recoverable. The archive is not the canonical queryable live repository, an independent offsite backup, or a dedup deletion policy.

**Safety:** Source transaction is read-only repeatable-read; Aiven PG18 service/TLS identity and guild-shadow required, exact owner confirmations, 30k-row/180MiB raw/90MiB compressed limits, guarded destination disk headroom. No source delete, pruning, startup schema mutation, new bot runtime routing, shared voice data change, or Discloud Auto Deploy. No production cutover.

**Acceptance:** Merge only after exact-head focused tests and full CI, then owner manual preview, schema install, preview and compressed archive import with verified counts and Aiven database size. Live source can still change after a snapshot, so late-write catch-up, fresh independent restorable encrypted backup, tickets/activity/canonical repositories, rollback and real Discord canary are still prerequisites. PR #645 is already merged and is not an active task in this slice.

---

# Active Task Lock — Community Hub game-role picker acceptance (2026-10-19)

**Active outcome:** Close the remaining Community Hub game-role setup UX mismatch while preserving the one-active-task lock. PR #643 was merged, but Discord/Discloud production acceptance remains unverified. The merged native role dropdown did not use the shared Search-Safe resource browser required for styled/aliased custom roles.

**Execution path / authority:** Staff Dashboard → Game Role Pings → Configure Game modal → shared `DankGuildResourceBrowserView` for role discovery → `community_hub_game_role_pings.configure` for click-time validation and durable guild-config CAS. No second discovery engine, native role picker, startup workaround or duplicate storage path.

**Focused changes:** Use existing shared owner-locked browser with semantic current/styled/previous-name search and Back; filter to the 25-or-fewer eligible Community & Pings Notification roles from the current guild config. Preserve staff check and existing `configure()` re-read/CAS at selection. Add focused tests for decorated names, explicit eligibility, and revoked staff authority. Keep /toke, CHEERS, Movie Night, role sends, cooldown claims and database owners unchanged.

**Validation:** PR #643's historical exact-head failure was a synthetic test fixture including the active session ID in its prior-claim list; current `main` already corrected that fixture, so do not reintroduce a duplicate fix. New picker tests and CI are pending; a green PR alone does not prove live Android/Discord/Discloud behavior. No production deployment, Supabase write or PostgreSQL cutover authorized.

**Queued next task:** The 12/12 Aiven PostgreSQL 18 guild-config shadow checkpoint is preserved. Remaining data migration and bot runtime cutover remain queued, with Discloud Auto Deploy OFF until separately authorized and validated.

---

# Active Task Lock — Community Hub game-specific role notifications (2026-10-18)

**Current continuation:** Issue #638, manual game-role recruitment. Base `main@02645b66009c736a59c85bed72cad159eca7b889`; one branch `feat/community-hub-game-role-pings-20261018`. Prior PRs #640 (external read-only), #641 (source/search), and #642 (play style/mic filters) merged, exact-head checks green, but live Discloud/Android acceptance and The100.io token/migration remain unverified.

**Scope:** Staff-only game-to-role mapping from enabled Community & Pings Notification options, separate from /toke, CHEERS and Movie Night roles. Manual **Call Game Crew** only from an active public Community Hub group, with host/co-host/staff authorization, per-game and guild-wide CAS cooldown claim, one call per session, bounded per-hour budget, and click-time rechecks. No automatic pings, new database schema, external joining, new site files, or legacy role recreation. Production remains untouched.

**Validation gates:** focused role eligibility and CAS-failure tests, Community Hub CI, full CI, app-command size, manual Discord/Discloud acceptance. Missing CAS RPC or storage must fail closed. Quiet hours are not implemented by the current Community Hub notification service, so role sends are deliberate only with strict cooldowns until a verified policy owner exists.

---

## Previous Community Hub LFG checkpoint

# Active Task Lock — Community Hub unified Find Players discovery (2026-10-18)

**Current continuation after merged PR #641:** Session play-style and mic-policy preference filters for local and approved HubLink partner groups. Branch `feat/community-hub-session-preference-filters-20261018` from `main@ca869ac4438e23a35dbdaf4b5407b0f33e92f70c`; issue #638. No modifications to The100.io credentials, native Quick Match, /toke, CHEERS, Cinema, website, or database migrations.

**Current continuation history:** Source-aware game searching for local sessions, approved HubLink partners, and opt-in read-only The100.io. Current main base `9daf428828a5aed190d8fef1addda0ea29e2b6b3`. One development branch: `feat/community-hub-unified-game-search-20261018`; active issue #638. No /toke, CHEERS, Cinema, website, schema or production deployment changes.

**Current behavior being built:** Source navigation within Find Players (Local / Partner / External), short game-name search modal on Android and desktop, bounded server-side result reads, known game aliases, provider-only outbound links, and click-time partner consent rechecks. Native Quick Match stays local. No unverified provider features, automatic join, global pings or source mixing.

**Release conditions:** exact-head full CI, Community Hub focused regressions, app-command payload audit and final diff review. Discord/Discloud canary and The100.io credential + migration status remain unverified independently. Keep any PR draft until checks pass.

**Previously merged:** #640 read-only external The100.io discovery and #639 CHEERS owner controls are on main. The following first-slice history and suspended /toke checkpoint remain for traceability.

---

## Previous first slice: The100.io opt-in read-only discovery

**Authorized FORCE SWITCH**: "Community Hub LFG Integration" with reason "Prioritize unified external gaming group discovery and matchmaking." Previous /toke overhaul is **suspended**, not completed; all original task records remain below. This is the only active coding task.

**Outcome / bounded slice**: extend the authoritative existing Community Hub interface with an optional *read-only* The100.io session browser. This is an independent external source; no external result becomes a Dank Shield session or can be joined through Community Hub. No role pings, automated matching, new listeners, or separate bot.

**Base and branch**: `main@46a0f75458c3cf61d4e8f359c1328aa9e06de8ee`; `feat/community-hub-the100-readonly-20261018`. Backlog scope: issue #638, with later integrations, filtering, role notification wiring, and website work preserved rather than mixed into this first release slice.

**Inspected execution path / authority**:
- `public_command_surface_v2` routes to `commands_ext/public_community_hub.py`; `CommunityHubView` owns member panels, `FindPlayersView` owns local and partner selections, `HubSettingsView` owns staff settings.
- `community_hub_service.py` reads/writes Supabase `dank_community_hub_settings` and uses RPCs for session/availability/partner authority. `community_hub_runtime.py` owns Discord session resources and recovery. None of those are replaced.
- Existing `community_pings_v2` is not altered; /toke role, Cheers, and session mechanics stay isolated.
- The100.io documents `GET /api/v2/gaming_sessions` with an authenticated personal or supporter token (Rails `Token token=...`); write-capable Discord Bot API requires separate approval. The adapter is deliberately read-only and must be enabled per guild.

**Changes staged**:
- Dedicated external provider adapter with HTTPS-only API and sanitized ID-derived URLs, bounded response/timeouts, invalid/denied response handling, and no external writes.
- New `external_the100_enabled` per-guild default-off column migration and service settings allowlist.
- Existing Community Hub → External Groups / Find Players → External Groups member UI; read-only source-labeled paged links; staff toggle in existing settings; server and host gating.
- Focused source and safety tests.

**First-slice historical status:** PR #640 merged into main; CI passed. Live provider authorization, migration execution, Discloud deployment and Discord mobile acceptance have not been verified. Do not label this live/complete.

**Validation gates / next**: run focused pytest plus existing Community Hub, app command size, and full CI; inspect final diff for inadvertent changes; verify DB migration compatibility and missing-token/off-state fail-safe. Only after CI and explicit release gating test on Discord mobile/desktop plus Discloud host and provider authorization. No production deployment or disruptive migration authorized.

**Suspended /toke checkpoint**: PR #567 merged 2026-10-14 (head `a7b05a70b5a6cdfaf1139695e96e2f962ad874b0`). Six associated workflow runs reported success. Active-task record below still requires real Android/Discloud acceptance and work on cross-process storage, quiet hours, scale, and documented live migration blockers. Do not silently switch back, delete its work, or claim task completion.

---

# Active Task Lock — /toke indexed VC routing and no-op suppression (2026-10-14)

**Active task:** Continue complete Dank Shield /toke overhaul, not the
suspended Supabase-to-Discloud PostgreSQL migration. PR #566 merged; this
slice begins from `main@bb72726da18f09b389754e25d3e8775d1d987d76` on
`feat/toke-voice-index-and-noop-20261014`. No changes to PostgreSQL,
Supabase, Cinema, Voice Safety, or production deployment.

**Problem verified:** Before this slice every voice-state burst scanned
`all_active(guild)`, which parsed the *entire global* session JSON and
checked unrelated session cards. Frequent voice updates also re-edited cards
with unchanged room membership. Both are poor for scaling and Discord REST
budgets.

**Implementation:** `toke_sessions.active_in_voices(guild_id, channels)`
uses a transient, derived guild+voice index. Successful atomic JSON writes
rebuild it, and a path+inode+mtime_ns+size signature invalidates it when
an external process atomically replaces the source file. Expired sessions
never route, corrupt files fail closed instead of using stale cache, and
returned snapshots are deep copies. Discord VC debounce uses only indexed
affected sessions, and skips redundant edits for unchanged final member
snapshots. Explicit departures still bypass suppression to handle rapid
join+leave. Fingerprint cache is LRU-bounded to 2,048 message entries and
never owns attendance or session state.

**Tests:** Dedicated new cases cover per-guild/channel matching, hot
lookup without rereading JSON, external atomic replacement/corruption,
expiry/end, unexpected fast departures, unchanged events, and bounded
no-op cache. Existing Toke tests and repository-wide Python CI remain
release gates, followed by live Android/Discloud acceptance.

**Limitations:** Source remains one JSON file with a single-process asyncio
lock. Mutating session actions still reread/rewrite that file; no claim of
cross-process consistency or support for 10k+ servers. Actual PG17-backed
per-shard session repository, fairness under extremely large voice bursts,
quantitative Discord REST load testing, and guild-specific optional quiet
hours/presets remain later /toke slices after validated safety gates.
No monkey patches or blind datastore migrations.

---

# Active Task Lock — /toke private reminder UX and delivery (2026-10-14)

**Active user directive:** Continue complete Dank Shield /toke overhaul, audit
for further improvements and avoid stopping after a small UI fix. Explicit
FORCE SWITCH from Discloud PG17 migration remains in effect; no database
cutover, migration, irreversible DB work or unrelated features permitted.

**Verified baseline:** PRs #560, #561 and #565 merged to `main` with applicable
CI green. Exact current slice base `main@dc2944e1cab97b0ab98fb6f953fee68a39745346`.
Work branch: `feat/toke-opt-in-reminders-20261014`.
Merged/deployed are different: Android/Discloud live acceptance not verified.

**Current slice:** A one-click member-driven **Remind Me** toggle on both
regular embeds and inline-media layouts, with ephemeral consent/cancellation.
Only users with the configured Toke community/notification role may opt in.
A private DM is attempted once around ten minutes before the scheduled end
(only when >2 minutes remain, and after the session has existed one minute).
No unsolicited role re-pings, no broadcast follow-up, no new channels,
no stored message bodies. At most 20 unique reminder subscriptions per
session and at most one globally claimed member every existing 15-second
maintenance sweep (~4 attempted DMs/min). The claim is persisted atomically
before the Discord request, preventing repeats across restarts, permission
changes or delivery failures. Guild membership, role possession, session
liveness and delivery time are rechecked before the DM. Disabled DMs fail
quietly, not through retry storms. Reminders do not extend the session and
an extension does not reset the one-attempt cap. `/toke` remains usable
without choosing reminders. Legacy sessions have no recipients unless
a member explicitly opts in.

**Independent acceptance gates:** Focused and repository-wide CI, Discord
component row count, media preservation, single-process persistence,
role revocation, ended/expired session suppression, consent cancellation,
member departed case, duplicate recovery and direct Android/Discloud canary.
This PR is not production-ready until green and deployed+verified separately.

**Further audit findings and ordered backlog (same one task):**
1) The JSON store still full-reads and atomically rewrites all guild sessions
   using one in-process `asyncio.Lock`. Not suitable for concurrent
   processes; cannot claim 10,000-guild reliability. Need bounded indexed
   store and verified PG17 migration later, without any parallel cutover.
2) VC gateway events still iterate `all_active(guild)` and issue card edits
   for every matching session. Introduce bounded active-channel indexing,
   debounce fairness, provider REST-budget measurements and no-op suppression.
3) Configurable non-intrusive per-guild notification cadence/quiet-hour
   policy, private presets and opt-out controls; do not add noisy @role DMs.
4) Accessibility/mobile, invite/member role changes, orphaned channels,
   restart exact-once card recovery and sustainable retention/metrics.
5) Run actual Android/Discord/Discloud canary and load tests. Do not
   equate green CI with production acceptance.

**Guardrails:** Do not edit core Discloud/Supabase workflows, Discord
application identity, Voice Safety, Cinema or Share Router. Preserve
privacy and existing /toke artwork and functionality; never use monkey
patches or hidden fallback. Advance only after exact-head CI review.

---

# Active Task Lock — /toke voice lifecycle and opt-in idle policy (2026-10-14)

**Active task:** Continue the user-authorized complete Dank Shield /toke
overhaul. PRs #560 (accurate session coordination) and #561 (host handoff)
are merged into main. Their GitHub checks passed, but live Discloud/Android
acceptance remains unverified. The prior Supabase-to-Discloud PostgreSQL
migration is **suspended**, not abandoned. Its complete rollback, backup,
private database and SSD capacity constraints are preserved below.

**Current focused slice:** Branch `feat/toke-voice-lifecycle-20261014`
from `main@d970c206b2826863cb825358b0d82472be650598`. Add durable host
observed/away/rejoined status based only on verified VC membership or real
departure events, not merely missing host at session launch. Preserve guest
attendance when the host leaves; do not auto-elect a host, DM/ping again, or
terminate a sesh for host absence. Provide an opt-in owner-controlled
empty-voice-room auto-close (0 disabled by default; 5–120 minutes); snapshot
the selected policy on new sessions only. Start the empty timer only after
real session-associated voice presence was observed, reset it on human
re-entry, never count bot-only occupancy as people, and never auto-close if
the VC is missing/inaccessible or has human members. A bounded 15-second
existing worker examines at most 25 due records, resamples live VC, and
closes through a durable atomic session-store operation; terminal edits keep
the existing queue and backoff. Legacy sessions default to disabled.

**Boundaries:** No runtime monkey patching, Supabase/PostgreSQL work, voice
bot data changes, Discord command tree expansion, or production deployment.
The current JSON store remains authoritative. Server settings persist via
the existing canonical guild-config interface. The full /toke overhaul
also needs better notifications, scalable VC event indexing, presets,
measured analytics, true concurrent-shard durable storage, and integration
acceptance when the DB is unblocked. No claims of 10k-guild readiness.

**Validation/next:** Tests added for host departure with remaining members,
host rejoin, never-occupied sessions, disabled default, age/expiry gates,
host handoff, absent VC, modal policy validation and opt-in worker closure.
Run exact-head CI, fix regressions and keep PR draft until all checks pass.

---

# Active Task Lock — /toke host handoff and lifecycle controls (2026-10-14)

**Single active task:** Complete Dank Shield /toke overhaul, authorized by
the owner's explicit FORCE SWITCH on 2026-10-14. Discloud PostgreSQL migration
remains *suspended* with all safety/backup/storage checkpoints below preserved.

**Verified previous slice:** PR #560 merged into main and all eight applicable
exact-head checks passed, including 3,584+ repository-wide tests and dedicated
/toke validation. Production deployment/canary is not verified.

**Current slice:** From `main` after merged PR #560, branch
`feat/toke-host-handoff-20261014`. Add a private Manage → Transfer Host
native member picker and confirmation; require chosen human still belongs to
the guild and has the configured Toke starter role; recheck actor manager/host
authorization at both selection and confirmation. Persist handoff atomically
with expected-current-host CAS so stale staff confirmations cannot overwrite
another transfer. Existing RSVPs, session time, inline media, cooldowns and
expiration state are not reset, and no extra notification pings are sent.
Display current host distinctly from original announcement author, honoring
hidden participant-name settings. Fix initial End Sesh button authorization to
read the *current durable host*, including old Discord persistent views after
handoff. Keep explicit End Sesh confirmation.

**Validation:** Tests added for concurrent/stale transfer, guild isolation,
expiry, restart persistence, role revocation, private UI and latest-host
permission, plus compatibility with inline V2. Exact-head CI, Android and
live Discloud integration are still required; do not declare deployment.

**Boundaries:** No Discloud database access/migration, Supabase writes,
hidden fallback/monkey patches, unrelated feature change or production branch
cutover. No live database-backed /toke session store in this slice.

**Next:** Finish focused tests and repo-wide checks, fix only verified
regressions, open draft PR, and proceed to quiet-session/reminder policy and
scalable active-session event indexing after this slice is validated.

---

# Active Task Lock — Complete /toke overhaul (2026-10-14)

**Authorized FORCE SWITCH:** User explicitly suspended the Discloud PostgreSQL
migration because Discloud support is blocking operator steps. This /toke
modernization is the **only active implementation task**. Existing PostgreSQL
migration work is preserved, not undone, and production remains untouched.

**Active outcome:** Develop the complete /toke improvements in validated slices
while avoiding the unfinished Discloud database cutover. Current focused slice:
truthful distinct Cheers/Joining RSVP semantics; unique-member capacity;
session-specific VC selection with publish-time access checks, including the
GIF composer; host/staff +15/+30 extension capped at four hours from creation;
accurate session-specific attendees vs whole-VC occupancy; durable bounded
retry/retention for closed-message edits. Preserve inline media, buttons,
explicit End Sesh confirmation, restart restoration and historical attendance.

**Base and execution path:** `main@86a48adfa4191750c860275aabd1d58f33206902`,
production's last confirmed independent branch snapshot is
`aaa-cinema-pre-pregateway-bind@0401552e859afbed7c17845366f87752b638235c`.
Work branch: `feat/toke-lifecycle-and-coordination-20261014` off main.
`public_command_surface_v2` registers `public_toke.open_toke_command` ->
`_publish_toke` -> `toke_sessions.save_new`; `toke_live_ui` handles
persistent session interactions, composer, on_voice_state_update,
expiry and view recovery. The durable session store is currently the
JSON file `DANK_TOKE_SESSIONS_FILE`, with one local process lock.

**Findings:** Previously `going` also added `cheers`, but capacity checked
only Cheers. Session roster included unrelated VC occupants as participants.
Every inaccessible expired card could attempt a Discord edit every 15 seconds,
and unsynced terminal sessions were retained indefinitely. No per-session
VC override or host extension control existed.

**Changes on current branch:** Distinct RSVPs and union capacity, truthful
voice counts/roster, bounded extension, optional selected VC in slash/draft,
persisted exponential closure retry and seven-day terminal retention.
Targeted regression tests updated and added. No PostgreSQL/Supabase migration,
startup shim, shared storage hook, Cinema or Voice Safety source is changed.

**Validation status:** Code changes authored; independent local runtime,
exact-head CI, Discord integration and Android/Discloud acceptance are pending.
Do not call this deployed, production-ready or complete until verified.
Check original branch ancestry and final PR diff before merging. Do not
enable auto-deploy or alter Discord bot token/application identity.

**Suspended migration checkpoint:** PRs #545–#553 were merged to main,
including staged PG17 data migration, shadow/parity/CAS repositories,
disposable PG17 integration tests and an Android-friendly *manual* encrypted
Supabase-source backup workflow (#553). User has not performed an independently
verified full live-source encrypted backup/restore or approved a live cutover.
Discloud's actual SSD disk allowance, backup/restore capability, private
schema/voice-data ownership and required credentials remain unverified with
support. Existing source Supabase RPC rollout/production safety and canary
gates also remain pending. No live Supabase deletion, Discloud database copy,
voice-bot DB mutation or main production cutover is authorized. Resume from
`main@86a48adfa4191750c860275aabd1d58f33206902` and recheck newer
commits before any migration activity; validate backup, disk and rollback first.

**Remaining /toke backlog (same task, not concurrent implementations):**
validated DB-backed session repository once core PG17 migration is ready;
active-VC event indexing without duplicate authority; host absence/handoff
policy; smart opt-in notification and quiet hours; owner presets/settings;
lightweight bounded engagement metrics, fuller Android/mobile and
10,000-guild scale validation. Stage these after the current slice's DoD.

**Next:** Open draft PR for the focused slice, run exact-head CI and
regression checks, resolve defects without patches or hidden fallbacks,
then review diff and complete controlled integration validation.

---

# Active Task Lock — Discloud PostgreSQL guild settings parity (2026-10-13)

**Current task:** After merged PR #547, implement an explicit standalone
PostgreSQL 17 guild-configuration repository for **shadow read parity only**.
Do not monkey-patch the runtime Supabase client, `guild_config` methods or
any Discord library. No live storage routing or deployment change yet.

**Base:** `main@a67d5b1f8dd96a1cdace47ca3c0f58104e6e473e`, including merged
PR #546 storage audit and #547 bounded first-slice staged transfer.
All five of PR #547's exact-head GitHub workflows completed successfully,
but an isolated PG17 integration test was not run.

**Implementation on `feat/discloud-core-config-shadow-20261013`:**
`postgres/migrations/202610130003_guild_config_shadow.sql` explicitly creates
`dank_core.guild_config_shadow`, separate from voice-bot/reminder schema.
`stoney_verify/postgres_guild_config_shadow.py` verifies complete JSON
payloads against canonical SHA256 from phase-1 `dank_migration.staged_records`,
enforces unique guild identities and matching source IDs, detects tampering
and conflicts, imports atomically, and refuses unknown destination DB or
insufficient verified disk headroom. Separate operator tool defaults to a
read-only inspection. Maximum 500 guilds and 12 MiB in this first slice;
never advertise that limit as a 10,000-guild implementation.

**Required gates:** Existing database current disk quota/backup capability,
independent recoverable source backup, isolated real PostgreSQL 17 schema
and integration run, staged row parity, source-to-target read/write/CAS
semantics, shared DB user/schema isolation, voice-bot data invariants and
actual rollback are not verified. The shadow repository must never be
wired into live Discord config reads before all those pass.

**Next:** Complete exact-head CI for this PR; separately test against a
controlled PG17 DB and implement the canonical transactional read/write/CAS
repository and config-history/storage optimization without monkey patches.

---

# Active Task Lock — DankShield-DB critical-data staging phase 1 (2026-10-13)

**Non-negotiable architecture instruction from owner (2026-10-13): NO MONKEY PATCHES.**
Implement the Discloud PostgreSQL transition and ongoing optimization as real,
reviewable source changes with explicit interfaces, a stable repository/storage
abstraction, schema-versioned migrations and integration tests. Do not modify
third-party library classes, functions, module globals or bot runtime methods
through monkey-patching; do not add hidden interceptors, copy-pasted fallback
paths or startup-time SQL repairs. Test-only mocking through pytest fixtures is
permitted and does not authorize runtime monkey-patching. Preserve all voice
bot and Dank Shield data, require verified rollback, and distinguish safe
automatic housekeeping (autovacuum, bounded retries, health checks, idempotent
recovery, approved retention jobs) from destructive/locking operations
(VACUUM FULL, REINDEX, unknown corruption repair, deleting audit records).
Never claim the database automatically repairs corruption or cannot fill.
Enforce this rule in PR reviews and tests before any production redeploy.

**Active task:** Build a bounded and verifiable, explicit-only first transfer from
the shared source Supabase project into the owner's existing private Discloud
PostgreSQL 17 `DankShield-DB`. No second database, no concurrent production bot
instance, no rewrites of voice-bot tables, no deletion, and no switchover yet.

**Base:** merged PR #546 added capacity auditing, with all GitHub checks green.
`main` remains at `e74fb818ecaf08cf901b48b6257f2d9a677f92f2`
at task start. PR #545 had previously reconciled production-only /toke history.

**Phase-1 implementation:** An isolated `dank_migration.staged_records`
PostgreSQL schema, the explicit `tools/stage_dankshield_core_data.py` operator
command and `stoney_verify/core_data_stage.py`, an audited whitelist of
16 core tables with verified PKs, bounded source REST pagination (100 rows
per page by default), default 12 MiB / hard 32 MiB source staging budget,
per-row and table SHA256, idempotency, source re-read and PostgreSQL parity
checks in an atomic transaction, and 70%-of-verified-disk budget guard with
4x overhead allowance. No apply action runs on import, bot startup or CI.
The tool keeps the original `record_member_activity` or Supabase repositories
untouched; stage records are **not** live records or a complete source backup.

**Validation:** Python dry-run and isolated Postgres 17 regression tests are
authored. A dedicated PostgreSQL 17 Actions workflow could not be created
through the available GitHub integration; therefore regular CI alone cannot
validate the SQL integration. The `RUN_CORE_PG_INTEGRATION` test remains
skipped in ordinary CI and must pass in an isolated PG17 environment before
any live staging. No Discloud private-VLAN connection or DB writes have been
attempted.

**Release blockers:** Actual Discloud disk allowance unknown (512 MiB in
screenshot is RAM); source immutable/full backup and restore test not yet
performed; 16-table stage does not cover all sensitive/legacy/Cinema data;
Supabase REST paging is not an atomic source snapshot; PostgreSQL runtime
repositories and precise concurrency/SQL-RPC parity not implemented.
The code in `main` must not be redeployed until the *full* supported core
migration and live health/canary checks pass.

**Next:** Run exact-head CI and PG17 integration; confirm disk allowance and
private-runner availability; create encrypted/recoverable source backup, then
stage first tranche and verify hashes under an approved deployment action.
Subsequent slices must port runtime repositories, ticket and moderation
policies, counters, activity fail-closed, community, Cinema and historical
archives before any `main` cutover.

---

# Active Task Lock — Existing Discloud PostgreSQL migration and capacity safety (2026-10-13)

**Active outcome:** Reuse already-provisioned `DankShield-DB` on the same Discloud
private network as the bot/voice service. Preserve voice-bot records and migrate
the important Dank Shield data **without importing unbounded Supabase history
or exhausting a small PostgreSQL instance**. Do not create a second database
or replace the live Supabase client by an environment-variable swap.

**Verified infrastructure:** owner screenshot confirmed PostgreSQL 17,
private VLAN, 0.25 vCPU, 512 MB RAM and 47 MB storage used. The total disk
allowance, backups and current voice-bot table/role ownership remain unknown.
The connected source is the shared IdleGrowOp Supabase project.

**Read-only source evidence:** 87 public tables, roughly 107 MB allocated;
~40 MB `activity_feed_events` (including 14 MB indexes) and ~38 MB
`guild_config_versions` (including ~37 MB TOAST). The latter has ~17,525
cumulative inserts and ~16,297 deletes. Source data has not been modified.
Supabase billing-cycle quota type remains unverified; allocated database
bytes alone do not prove its cause.

**Current deliverable:** On
`feat/discloud-postgres-storage-preflight-20261013`, prepare a read-only
Postgres size/bloat diagnostic with explicit optional true disk quota
thresholds; migration runbook preserving all valuable primary and audit
records with a verified archive of bulky history before any selective import;
CI tests and documented config. No live Discloud connection, backup/restore,
data copy, DDL or deletion has occurred. Do not claim storage cannot
fill or that main is ready for Discloud redeployment.

**Main continuity:** Main/production reunification PR #545 was merged at
`a3474387d12daa8bdb401db4833e2984a12202b9` and branch history
is preserved. PR #540's Supabase batch-RPC migration is still missing the
two RPC functions on the source database and its committed SQL contains
invalid dollar-quote delimiters. Do not redeploy main while the database
compatibility/rollout gate is unresolved.

**Next:** Confirm actual Discloud **disk** allowance and its existing schema
ownership, build and test the direct PostgreSQL repositories/migrations,
verify source backup and selective restore, then perform controlled main
cutover with measured latency, disk and activity fail-closed behavior.
The production cutover watch must stay blocked until verified.

---

# Active Task Lock — Main/Production Reunification for Discloud (2026-10-13)

**Active outcome:** Merge the current Discloud-connected `aaa-cinema-pre-pregateway-bind`
history into `main` using a true two-parent merge commit while preserving
the five production-only `/toke` source/test files and all main-only work.
**Do not switch Discloud to main yet**: first pass exact-head CI, then verify
PR #540's required Supabase batch RPC migration on the actual Dank Shield
database, and only then perform a controlled cutover and two-shard canary.

**Source snapshots:** main `fcebe42ebf7addd3afd4d3afaf2bf8c875f0a1a7`;
production `0401552e859afbed7c17845366f87752b638235c`. Both are parents of the integration candidate.
Production's `/toke` blob SHAs are reused unchanged. No modifications to
Cinema, Share Router, verification, voice moderation, or Supabase migrations
in this reconciliation diff. Preserve the previous lock history below.

**Next step:** Exact-head CI, review two-parent ancestry and diff, merge the
integration PR only after checks, and validate the actual Dank Shield Supabase
migration before telling the operator to redeploy on `main`.

---

# Active Task Lock — Single-process sharding scalability (2026-10-13)

**Active task/outcome:** Enable safe future two-shard Discloud canary by
replacing the four sequential all-guild maintenance loops with bounded,
measured worker pools while reusing the existing `DankAutoShardedBot`.
A production shard flip is NOT part of this code PR.

**Scope:** `activity_tracker._heartbeat_loop`, `metrics_sync_worker`,
`ticket_automation_worker`, `spam_guard` startup warming, one shared
bounded guild-sweep helper, regression tests and canary runbook. No migration,
new bot identity, second process, voice/media changes or unrelated cleanup.

**Execution path and findings:** `command_runtime.create_discord_bot`
already selects `DankAutoShardedBot` using `DISCORD_AUTO_SHARD` and
optional `DISCORD_SHARD_COUNT`. Four process-wide loops still iterate the
aggregated `bot.guilds`; the activity worker originally slept 60s before
each serial cycle, metrics ran all guilds sequentially per interval, ticket
sweeps could overlap manual invocations, and SpamGuard performed startup
settings reads serially inside `on_ready`.

**Changes:** Fixed-size async workers, heartbeat elapsed/overrun logging,
global breaker fail-closed coverage and start-to-start pacing; role-before-staff
metrics sync with honest DB failure and per-cycle outage short-circuit; paginated
indexed enabled-guild ticket discovery (avoids a per-guild disabled SELECT),
per-guild ticket automation locks plus sweep jitter; nonblocking, bounded
SpamGuard warming with stop-on-repeated-persistence-failure behavior.
`docs/SHARDING_CANARY.md` specifies 2-shard settings, measurements and
rollback. Production sharding env is unchanged.

**Validation/status:** Implementation is on
`feat/shard-guild-loop-scaling-20261013`. Targeted concurrency/cancellation
tests and existing full CI must pass on the exact PR head; integration,
long-duration heartbeat timing at 3k+ guilds, and a live two-shard Discloud
canary cannot be certified by code review alone.

**Cleanup/conflicts:** Existing guild caches, startup recovery coordinator,
ticket policy and Discord REST limiter stay authoritative. The new helper
only schedules local periodic work; it does not bypass protection rules.
Check the final diff, branch drift and user changes before merging.

**Suspended task:** Member activity batching PR #540 merged into `main`
(commit `5cec09aed4e7221055b4c25d3fb9275b0e1261e2`) with exact-head
GitHub CI green. The Dank Shield **production** Supabase batch RPC migration,
receipt/storage verification and live Discloud failure/restart acceptance
remain unvalidated. Do not assume this work is deployed or safe to enable;
the only connected Supabase project observed was unrelated IdleGrowOp.

**Blockers/risks:** Global Supabase capacity remains shared across loops;
bounded guild concurrency is not a throughput guarantee. Real 60s heartbeat
acceptance, RAM per 1,000 guilds, shard-ready behavior, command sync and
Discloud rate-limit validation require a controlled operator canary.

**Backlog:** None added beyond suspended PR #540 validation and the planned
auto-shard count step after the two-shard canary.

**Next:** Open draft PR, inspect exact-head CI/test failures, repair only
in-scope regressions, review diff. After staging and DB preconditions,
canary with `DISCORD_AUTO_SHARD=true` and `DISCORD_SHARD_COUNT=2`; then
remove count pin only after healthy measurements.

---

# Active Task — Voice Safety #517: broader VC coverage

**Active outcome:** Expand server-level Voice Safety VC selection to any eligible
normal VC with two or more humans; keep the receiver and consent architecture
fail-closed, and report capacity honestly. This is a scope/policy slice, not a
working Watch Yo Mouf audio receiver or unlimited simultaneous VC monitoring.

**Branch:** `feat/517-voice-coverage-policy-two-humans-20261013` from
`main@dd136911`. Main remains canonical, with production mirrored to
`aaa-cinema-pre-pregateway-bind@148a175` after merged PR #536.
Do not modify the production branch as part of this draft.

**Execution path:** Staff `/dank protection` -> `public_voice_safety` stores
`voice_safety_coverage_mode`; canonical `community_voice_caption_runtime`
handles voice state/start/stop/restore; `voice_companion_bridge` provides
read-only signed member eligibility to Watch Yo Mouf. The central
`voice_safety_coverage` policy applies to all three, preserving old `single`
mode when unset.

**Findings:** Old config hardcoded one VC and launched even with one human.
One Discord application identity still owns at most one VC per guild, and
Watch Yo Mouf's audio worker is not implemented. No new audio admission,
third-party provider usage or extra bot identity is enabled in this change.

**Changes:** Add All/Single scope control, require two humans excluding bots,
choose busiest eligible normal VC, stop below threshold, hand off the released
receiver to another eligible room, expose truthful waiting-room counts, and
align read-only companion policy. Keep prior single-VC config and safety gates.

**Validation status:** Pending exact-head focused and whole-repository CI,
Python syntax, regression checks, diff/cleanup review. Live Discloud, DAVE
receive, Gemini, concurrent guild operation and Android UI tests unavailable
from static CI and remain required before audio activation.

**Cleanup/conflicts:** Reuse one existing CommunityVoiceCaptionManager;
do not add additional receivers, compatibility routes or bypass any consent
checks. Previously drafted permanent-opt-out branches are out of scope and
must not be merged as-is after branch reunification.

**Risks/blockers:** Other qualifying rooms must wait until that bot identity
releases its current room. True multi-VC concurrency needs additional
authorized Discord bot identities and durable cross-process fencing plus
revocation. Bot permissions, channel changes, staff confirmation of paid
Gemini processing and adult-use eligibility remain operator requirements.

**Next:** Open draft PR, run exact-head CI, repair any failures in this slice.
Only after validation, follow with the separate receiver-pool coordination
work *within #517*, never claim comprehensive coverage early.

---

# Active Task Lock — Toke Inline Media (2026-10-13)

**Authorized FORCE SWITCH:** Suspend Facebook downloader/Share Router #390 (merged work and outstanding live downloads/provider tests remain preserved, not reopened). Active outcome: show the actual GIF/image inside the /toke card, without blue page links, tiny thumbnails, duplicated media, or oversized naked attachments. Do not alter Voice Safety #517 or branch reunification during this task.

**Production base:** `aaa-cinema-pre-pregateway-bind`, the historically confirmed Discloud auto-deploy branch. **Task branch:** `fix/toke-embed-share-media-20261013`; draft PR until validated. Current Discloud running SHA remains unverified. Main/production reunification from the prior checkpoint is suspended, not discarded.

**Observed failure/root cause:** Android screenshot 2026-10-13 shows "Open shared GIF or image" instead of the GIF. `_publish_toke` explicitly formatted non-direct Tenor/Giphy share-page links into embed.description rather than resolving an inline media asset. The existing direct image embed path and uploaded-photo/GIF Components V2 MediaGallery path must remain distinct: the latter prevents the Discord attachment:// PATCH duplicate previously observed and retains medium/wide inline media in the card.

**Changes:** Resolve public HTTPS GIF/image sharing pages from any domain exposing direct OpenGraph/Twitter image metadata, with a five-second timeout, 256-KiB HTML cap, max three safety-checked redirects, and the existing public-only DNS connector to block private network targets. Accept safe image MIME redirects without a filename; preserve fast Giphy canonical-GIF path and normal Tenor URLs. Pending Discord composer now also accepts a *single* public HTTPS page URL, while ordinary messages remain untouched. Prefer animated GIF assets; never publish link-only media, and fail before posting with actionable instructions when metadata is missing or blocked. No new config/env variables or media storage. Preserve uploaded animated GIF/gallery (not the old Section thumbnail), role mention, live Cheers/Joining/VC/Refresh/expiry, composer cleanup semantics and view restoration. Tests include generic providers, private network rejection, HTML caps and source filtering.

**Validation/status:** Source changes and tests on task branch; exact-head regression/full CI and real Android/Discord visual acceptance are still pending. Verify native composer Tenor/Giphy, /toke media: share-page, direct CDN and gallery uploads; ensure no linked-only card, no duplicate/oversized attachment, no tiny thumbnail, media remains after Cheers, Refresh, End and restart. Check GitHub PR diff/mergeability and Discloud deployed SHA before declaring success.

**Next step:** Open draft PR targeting production branch, inspect exact-head CI, repair scoped failures, then run Android/Discloud canary. No claims of production completion without canary.
---

# Active Task Lock — Main/Production Reunification (2026-10-13)

**Active task:** Safely reconcile `main` and `aaa-cinema-pre-pregateway-bind` into one real merge commit, with a draft PR targeting `main`. Do not deploy or switch Discloud yet.

**Baseline:** `main@8e4bf66`; production branch `92f1a14` (including merged #534). Both are parents of the merge candidate. Preserve all production features and valid main changes without resets or force-pushes.

**Findings:** The supplied patch's intended tree is byte-identical to production for 123 of 134 changed files. Eleven files need conflict resolution; verify changed content and CI on the exact candidate. Reconcile GitHub Actions branch filters/diff checks; use consistent PostgreSQL image sourcing.

**Status:** Integration candidate only. Requires exact-head CI, diff/cleanup review, Discloud isolated canary, and live Discord/Cinema/Share Router/verification acceptance. Do not claim complete or redirect production until these pass.

**Suspended task:** Voice Safety #517, including merged #534, remains suspended engineering work until branch reunification's full Definition of Done; #534 is explicitly incorporated in this candidate.

**Next step:** Inspect exact-head GitHub checks, repair only failures inside this reconciliation scope, validate live behavior on an isolated instance, then coordinate a controlled main-branch production cutover with rollback to `584e655`.

---

# Suspended Task Record — Voice Conduct Moderation #517 (FORCE SWITCH 2026-10-12)

**Active task:** Build a verified, privacy-conscious AI voice conduct moderation mode using one existing DAVE/SSRC caption receive pipeline. User force-switched from Share Router #390 to #517. This work remains active and has NOT met production Definition of Done.

**Current branch/PR:** PR #520 merged at `4b085f8d6c7805491665b8462d74ae4b8d08d7f6`; verification-consent/auto VC PR #521 then merged at `7087b2926f85c2b5a7a4b68effba038a5200f573` on `aaa-cinema-pre-pregateway-bind`. **Neither merge substitutes for live DAVE/Discord/Discloud and privacy acceptance.** Screenshot on 2026-10-12 exposed unregistered new navigation routes. **Draft PR #523**, `fix/517-navigation-discoverability-20261012` based on the merged #521 integration branch, restores direct AI Voice Safety discoverability and existing verification reminders, Share Router download, and Toke settings routes. Adds registry-based **New & Updated** Home shortcut and corrects stale category descriptions. Only registry/router/UI entry metadata, docs, tests and a canonical Share Router download settings entry wrapper changed. No media download, toke attendance, Cinema playback, voice capture, or permission logic reconstructed. This is an active #517 integration polish subtask, not a FORCE SWITCH to #390.

**Current requirement:** The user has existing rules displayed before verification and wants automatic monitoring in configured voice rooms without a per-VC Opt In press. The default Basic Verify button only said to read the rules and did not record voice consent, and verified roles can be staff-granted. Therefore **never infer voice processing consent solely from Verified roles or a rules-channel message**. Basic Verify now displays a versioned AI audio/Gemini notice and Agree & Verify. Only a verified click on the disclosed panel records one-time agreement using existing per-guild member settings. Previously verified members must accept once on the current public Voice Safety notice. Opt Out durably revokes; reads always refresh, cannot use caption preference. On join/restart, the existing session manager admits only saved consent, rechecking again after Gemini prewarm to prevent withdrawn-user races; disconnect and Stop revoke live buffer admission. If opt-out persistence fails, stop the guild's voice monitoring and require operator to reconcile prior stored consent.

**Always-on behavior, bounded pilot:** When host and guild are explicitly enabled, existing canonical voice-state path can start the selected VC on new join; one on_ready listener restores configured occupied VCs within configured max session capacity. Auto-start sessions release their slot when the last user leaves. Staff Stop persists disable so auto-start will not re-enable unexpectedly; if write fails, operator must turn off the host gate. Only one selected VC per guild and default four simultaneous guild caption/voice sessions per process. Initial receive negotiation takes time and may miss immediately spoken words; no promise of indefinite simultaneous 10k voice rooms.

**Policy blocking conditions:** Current Gemini unpaid API usage may be used for model development and is discouraged for sensitive/private data. Its 2026 API terms also restrict clients likely to be accessed by under-18 users. On #521, Voice Safety requires all three host confirmations before operation: `DANK_VOICE_SAFETY_ENABLED`, `DANK_VOICE_SAFETY_GEMINI_PAID_CONFIRMED`, `DANK_VOICE_SAFETY_ADULT_ONLY_CONFIRMED`, and the preexisting `DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED` DAVE soak gate. Flag assertions are operator attestations, not proof of actual billing or age. Avoid sending user speech to Google Free Tier; do not deploy without privacy/platform/legal review. No auto sanctions, no second voice listener.

**Validation:** Targeted Community Hub CI includes Voice Safety + Basic Verify source and regression tests; exact-head CI and whole bot CI required. Need consented real Discord audio, overlapping speakers, DAVE epoch/transport reconnect, member opting out while model in-flight, bot restart, verified-role-only denial, staff emergency Stop, Google quotas, API billing and data terms, Discloud CPU/RAM and correct voice permissions. Measure false positives and latency. No VoicePatrol superiority claims without benchmark. Review final diff and remove only directly relevant conflicting code.

**Suspended task preserved below:** Share Router #390 with merged PRs #513/#514/#515, provider/media DM/hosted video live canaries outstanding, actual Discloud runtime SHA and persistence unknown, scaling beyond one instance unproven. Leave untouched. **Backlog:** advanced Voice Safety sanctions, multi-VC distributed workers, local privacy-preserving inference, provider benchmark after #517 pilot acceptance.

**Next action:** Finish #523 exact-head UI/CI validation, live Android `/dank home` new menu canary and actual Discloud SHA check; PR #523 remains draft until accepted. Resume #517 real DAVE/Gemini privacy acceptance afterwards. The suspended Share Router #390 checkpoint below is preserved.

---

# Dank Shield Active Task

## REUNIFICATION RECORD — MAIN/PROD BRANCH MERGE 20261013

**Branch:** `reconcile/main-reunification-20261013` (based on `main@8e4bf66`), merging production `aaa-cinema-pre-pregateway-bind@584e655`. Goal: return Discloud production to `main` as the single authoritative branch. No reset, no force-push, no discarded work.

**Pre-merge verification (all four historical regressions):** the mitigations that caused production to leave `main` were verified present and correct in `main` before merging — Discloud REST recovery budgets (`discord_api_safety.py`, 40 req/30s + 120s quiet window), pre-gateway `:8080` site bind in `setup_hook` (PR #468), lazy libtorrent init with side-effect-free route registration (PRs #471/#472), and the composite interaction-timeout mitigations (`chunk_guilds_at_startup=False`, global-sync budgets, event-loop lag watchdog). The 9 safety-critical startup/lifecycle files were byte-identical or purely additive on the prod side; zero conflicting logic.

**Main-side work integrated (103 commits):** ticketing public-panel privacy (#527); Toke per-guild live sesh + GIF composer (#503 — note: prod had already absorbed #503 via `01955af` and evolved it, prod side taken everywhere); Share Router custom proxy sources (#502), dynamic upload caps (#497), TikTok shortlinks (#494), forwarding isolation (#493); Cinema playback HTML handling (#486), episode search year fallback (#485), Nyaa RSS size validation (#484).

**Conflict resolution (15 files):** Toke (`toke_live_ui.py`, `toke_sessions.py`, `public_toke.py` + tests + workflow), Share Router (`share_router_runtime.py`, `public_share_router.py` + tests), Cinema (`cinema_site.py`, `cinema_site.js` + tests). Every resolution keeps both sides' features; prod side taken where it was a strict superset of main's. Full per-file resolution notes live in the merge commit message.

**Rollback point:** production `584e655`, main `8e4bf66`. The reconciled branch must pass exact-head CI and a controlled Discloud canary before production switches branches. Do not declare the migration complete on merge alone.

---

## CURRENT SCALING SLICE — SHARE-ROUTER-390-DURABLE-FAIR-DOWNLOAD-QUEUE-20261012

**Authorized continuation:** The user asked whether Download channels would run concurrently for 10,000+ servers, whether upgrading Discloud helps, then directed "ok continue". This is still the explicitly FORCE SWITCHED Share Router #390. No other development task may start.

**Production base:** `aaa-cinema-pre-pregateway-bind@e915dbb419d49b94cd6c235f6f87d0f2ca036cbd`, after PR #514 merged with green GitHub workflows. Actual deployed Discloud SHA and live download/hosting canary remain **unverified**. Draft PR #515 on `feat/share-router-390-fair-durable-download-queue-20261012`.

**First scaling slice:** Keep one canonical `route_message` listener; enqueue per-guild opt-in video download requests into bounded local SQLite with only guild/channel/member/message IDs, no URLs or media bytes. Two existing bounded media workers process jobs, global capacity 256, per guild 10, at most one pending per member and one running per guild; round-robin guild preference. Unstarted jobs expire after 6h, queued work survives a process restart **only when local data persists**, and crash-interrupted running jobs are dropped to avoid duplicate DMs. Worker rechecks current saved download channel, actual message/member and all canonical permissions/limits before delivery. No extra slash commands, media resolver, streaming stack or database migration.

**Additional performance fix:** Reuse cached read-only Share Router config when routing each Discord message; invalidate after writes or file changes to avoid O(total guild config) JSON parse per event. Existing admin writes retain their file format. Staff Download Settings may show queue depth/limits for diagnostics.

**Not a 10k guarantee:** This is a single-instance SQLite queue, not a distributed broker. Other instances won't share a queue or the already-local signed-video files. Four hosted transfers and two download workers remain; truly scalable deployment needs Discord gateway sharding, shared queue/leases, separated workers, durable object storage/CDN, resource/egress accounting and load tests. Upgrading Discloud CPU/RAM alone does not remove these software caps. See `docs/share-router-download-scaling.md`.

**Acceptance pending:** exact-head scoped Share Router/whole bot CI, durable queue fair admission and crash/restart tests, config cache tests, Discloud persistent volume and runtime SHA, guild opt-in/permission DM canary, burst test and sharding plan. PR #515 stays draft until these pass. Do not claim production release or 10k readiness.

---

## CURRENT SUBTASK — SHARE-ROUTER-PRIVATE-DOWNLOAD-20261012

**User continuation inside FORCE SWITCH #390 (Oct 12):** Dedicated opt-in Download channel, private DM delivery, and real above-DM-limit signed HTTPS Watch/Download links with adjustable expiry and easy staff mobile setup.

**Production base:** `aaa-cinema-pre-pregateway-bind@3811d8a3018930a8f9fcdb82c7d30ed0b3189bce` (Share Router #513 merged). Draft PR #514 on `feat/390-download-channel-private-dm-20261012`; still **unmerged/unproven live**. No other feature authorized.

**UI:** Share Router manager → **Create Download Channel** (per guild) → **Download Settings** → choose 1 hour / 24 hours / 7 days (default) / 30 days (max) → **Test HTTPS Hosting**. Disable Downloads preserves channel but does not revoke already-issued links. If site URL not configured, set the already-existing `DANK_MEDIA_PUBLIC_BASE_URL` to public HTTPS Discloud site once in environment and restart. No new database or extra web server. User sees explicit readiness and configurable storage caps. The status test writes a tiny fake video payload, GETs it from the configured HTTPS origin, then revokes it; no link/token is exposed in Discord.

**Delivery:** Existing canonical `share_router_runtime.route_message` dispatches the configured channel only. Reuse existing provider resolver, public-only SSRF guards, bounded HTTP download/remux; DM video attachments <=19MiB. When a larger video is safely staged, DM unguessable **Watch** (inline media extension, byte range) and **Download** (attachment disposition) links with random 256-bit bearer token/hash in sidecar and selected TTL. URL expiry checked on every access. Files are private local temp storage, not Supabase/Postgres; background cleanup every 15min, bounded rotating cleanup, 2 simultaneous ingestion / 4 HTTP transfers; 120sec member cooldown; 128 MiB file / 512MiB total default, hard configurable maximum 256MiB file/2GiB storage, 256 MiB free disk reserve. Fail to original-source link only when hosting unusable, not as fake file delivery. Upload/DM failures preserve source post; if a signed asset was already staged and DM fails, revoke it.

**Privacy / constraints:** Bearer link is transferable by anyone holding it, so it's **not** Discord identity-bound; Discord may cache previews after expiry. Strict storage quota refusal, no indiscriminate cleanup of unexpired files. Discloud's actual disk persistence after redeploy, public Cloudflare ingress, egress and storage quotas **not yet validated**, so cannot promise 7/30-day durability before actual host canary. Discord native inline rendering not guaranteed, only HTTP video playback link and browser fallback. Avoid serving private/copyright-unauthorized data. No startup SQL or duplicate service.

**Acceptance:** scoped exact-head Share Router + full CI and HTTP endpoint tests; staff Android setup/expiry selector, one-file DM and one staged large file, range/expiry/revoke, blocked DM, high concurrency, 413, low disk, Discord link preview and download across Android/desktop, Discloud restart/redeploy storage. Only then remove draft/merge and close #390 if full real provider canary also green. Documentation `docs/share-router-private-downloads.md`.

---

## CURRENT ACTIVE TASK — SHARE-ROUTER-PROVIDER-PLAYBACK-390-20261011

**Explicit FORCE SWITCH (Oct 11):** "Complete Share Router provider compatibility and inline playback under #390." Reason: finish the existing universal resolver, test real providers and fix unsupported-media fallbacks without rebuilding working components. All other work including Server Stats, verification, Cinema, security intelligence and issue #367 is suspended.

**Production base:** `aaa-cinema-pre-pregateway-bind@cf47c10ed3e85b5435179fd9ce5721b4423f37d8` (PR #512 merged). Actual live Discloud deployed SHA and cross-client routing acceptance remain unverified. Work only on `fix/share-router-provider-playback-390-20261011` targeting this production integration branch. Draft PR #513 tracks this slice.

**Existing canonical ownership:** `share_router_media_resolver.py` recognizes providers/canonical URLs, selects progressive/merge/manifest and returns safe metadata. `share_router_media_network.py` guards public-network requests. `share_router_media_remux.py` and `share_router_manifest_proxy.py` own bounded stream-copy remuxing and safe manifest requests. `share_router_runtime.py` remains the only source-to-destination policy/relay/upload owner. Never add another forwarding command, bypass source permissions/NSFW or allow mentions, bypass upload caps, or change unrelated routes.

**First audited defects:** (1) H.264/AAC format could lose to higher resolution HEVC/AV1 or unknown-size formats, reducing inline cross-client success; (2) a first failing provider link masked a later valid manifest or separable A/V candidate; (3) unsafe media hosts could be recognized as "direct"/supported before rejection at download time. Apply codec/known-size ranking, prefer later remux-capable candidate over earlier link-only, reject private/credential-bearing/nonstandard-port media during recognition. Do NOT promise every codec/provider or client plays inline; keep canonical link fallback where upload/playback is unavailable. Add deterministic fixtures and exact-head full CI.

**Acceptance remaining:** check GitHub exact-head CI, unit and provider fixture matrix including X/Twitter, TikTok, Instagram, YouTube/Shorts, Reddit, Twitch, Facebook, Vimeo, Streamable, Imgur, Tumblr, Bluesky, Pinterest and direct media; verify live public sample metadata/extraction and permission boundaries on the actual Discloud deployment (without collecting passwords/links containing private tokens); verify native attachment preview on Discord Android and desktop and link-only fallback on oversized/private/auth-protected/live media. These live external canaries are not proven by mocked CI. Check provider-specific diagnostics and current boost-tier upload caps; keep draft until reliable checks.

**Previously merged and preserved:** Reminder PRs #510, #511 and #512 have landed, and the user confirmed live PostgreSQL migration succeeded via Discloud console. Live reminder canary still unverified; leave untouched under this FORCE SWITCH.

---

## PRIOR TASK — VERIFY-REMINDERS-DISCLOUD-POSTGRES-20261011

**User continuation after PR #510 merge:** DankShield-DB PostgreSQL 16 was provisioned in Discloud (private connection only, reported online with 512 MB RAM / 0.25 CPU and 46 MB used); user saved Dank Shield VLAN enabled with internal hostname `dankbot`. Task remains **Unverified Member Reminders** under Single Active Task Lock. No unrelated fixes.

**Code branch:** `fix/verify-reminders-discloud-postgres-20261011`, from Discloud GitHub integration branch `aaa-cinema-pre-pregateway-bind` merge commit `f4eb863505cf8478100e3401ce3243a108df4f78` (PR #510); Discloud running SHA still needs verification.

**Root cause:** PR #510 uses Supabase RPC `claim_dank_verify_reminder` for durable send cooldowns while the user's actual Discloud PostgreSQL instance has no connected app driver/schema. Bot app already uses Supabase `guild_configs` and `role_truth`; these remain the canonical verification/configuration owners. Supabase's migration workflow runs from `main`, not Discloud's deployment branch, so PR #510 merge did not guarantee the original Supabase reminder schema. The new Discloud Postgres is accessible only via private VLAN, not from hosted GitHub CI.

**Required behavior and changes:** Retain the shipped staff Verification Center, per-server settings, Simple Verify eligibility and safe DM/channel delivery. Replace the single delivery-ledger owner with PostgreSQL 16 one-statement atomic `INSERT .. ON CONFLICT WHERE .. RETURNING`. Add `psycopg[binary]`, private DSN `DANK_VERIFY_REMINDER_DATABASE_URL` in Discloud env, `VLAN=true`/`HOSTNAME=dankbot` in the deployment file. Provide an idempotent standalone PostgreSQL migration and opt-in one-off operator migration command, never startup DDL. Feature fails closed when URL, table, connection, permissions or claim are unavailable. No automatic fallback to Supabase or a second verification authority.

**Changes on branch:** `verification_reminder_postgres.py`, replacement storage calls in `verification_reminders.py`, reminder UI messaging, `postgres/migrations/202610110001_verify_unverified_reminders.sql`, `tools/migrate_verify_reminders_postgres.py`, updated dependencies, `discloud.config`, example env/docs, unit and PG16 integration tests/workflow.

**Validation and blockers:** Existing #510 exact-head CI was green before merge. Follow-up PostgreSQL branch full CI and PG16 atomicity tests pending until PR workflow completes. Actual private database connection, credentials, same-cluster reachability, live schema migration, recovery after restart and mobile Discord canary cannot be proven from GitHub and are unverified. Do not request database secrets in chat, edit Supabase schema, or claim live delivery until proven. If the Discloud UI offers no authorized private-network command runner to execute the migration, identify a supported operator-run SQL path; do not invent one.

**Expanded user requirement (same database integration task, Oct 11):** New Discloud PostgreSQL should never silently grow with useless historical records. Adopt forward-looking storage governance for ALL future Discloud PostgreSQL feature tables, but do not start an unrelated legacy Supabase purge. Pre-existing `docs/PRIVACY_POLICY.md` §7 remains authoritative. Added `postgres/data_lifecycle.json` + `tests/test_postgres_data_lifecycle.py` gate requiring each new table to register purpose, owning cleanup service, minimal fields, TTL/preservation rule, bounded cleanup, backup class and migration; document contract at `docs/discloud-postgres-data-lifecycle.md`. No universal destructive sweeper. Reminder rows are only guild/member IDs, join time, last claim and attempt count. An hourly bounded sweep may remove a record after >=90 days inactivity only when Discord REST reports Unknown Member 10007, with SQL membership-generation and age guards. Live members are NEVER expired merely by age, preventing recycled spam budgets. Verify Discloud storage limits and backup/restore before future irreplaceable migrations; capacity warning thresholds are guidance, not shipped alerts.

**Existing bad-actor owner confirmed (Oct 11):** `stoney_verify/anti_nuke_hostile_actor_runtime.py` already implements persistent guild-scoped confirmed hostile-actor reputation in Supabase table `guild_security_actor_reputation` plus a local mirror, tracking first/last seen, incident count, classification, source, related ID and explicit clearance. It is authoritative today; do not collect a duplicate PostgreSQL registry or run blanket TTL against it. The forward PostgreSQL lifecycle contract now requires `retention_class`, `historical_reuse` and `review_interval_days` in addition to bounded owner-specific cleanup, preserving purposeful longer-lived security, moderation, settings and recovery history. The distinct future security intelligence feature remains backlog-only, with evidence confidence, corrections, review and per-guild privacy constraints.

**Next action:** Finish exact-head reminder+retention CI, PG16 SQL workflow, and final scoped diff inspection. Confirm Discloud private database config/migration without sharing secrets. Canary Simple Verify role changes, member departures and role/Discord/DB outages; only then remove draft status and merge.

### Backlog — Bad actor historical intelligence (NOT active)

**User requirement (Oct 11):** Dank Shield should remember documented bad actors so that a planned additional security feature can use an established history rather than starting cold. Preserve meaningful historical security evidence from indiscriminate database expiry: store authenticated Discord IDs, guild scope, exact source event, timestamp, action, severity/confidence, outcome/reversal, and provenance only where necessary. Keep a per-guild vs cross-guild disclosure/authority model, false-positive corrections and appeals, explicit retention and deletion authority, minimal PII, deduplication, capacity and privacy safeguards. Do not treat reports/allegations as verified facts, or auto-punish without safeguards. This is a distinct evidence collection/assessment capability, **not** authorization to implement it while PostgreSQL reminder integration PR #511 is active. No new bad-actor collection or security enforcement is claimed. Investigate existing AntiNuke/raid/spam/moderation reputation authority and migrations only after this task's Definition of Done or exact FORCE SWITCH. The broad PostgreSQL data lifecycle policy must allow justified durable evidence instead of indiscriminate short TTLs.

### SUSPENDED — PR #509 Server Stats live acceptance
The prior Server Stats code was merged into the deployment branch before PR #510, but real Discloud-native deletion and reconciliation acceptance remains pending. No code changes to Server Stats are permitted inside this active task.

---

## PRIOR TASK RECORD — VERIFY-UNVERIFIED-REMINDERS-20261011



**Force switch (Oct 11):** Implement Dank Shield Unverified Member Reminders, starting with existing per-guild Simple Verify and its verified/unverified role truth. No separate verification authority; no unrelated feature work.

**Branch:** `feat/verify-unverified-reminders-20261011`, based on documented Discloud branch `aaa-cinema-pre-pregateway-bind` at `50f5f69dbdc6f99ecc383cf67644e328b1a015bc` after Server Stats PR #509 merged. Discloud's actual deployed revision remains unconfirmed.

**Status:** PR #510 draft, implementation and regression candidate on branch. Exact-head CI, live Discord/Discloud/storage acceptance still required. Nothing enabled in a server. As of 2026-10-11, Schema Authority SQL, Ticket Owner and Dank Design checks are green on current head; full CI remains pending.

**Storage decision:** User recalled plans to use Discloud's database and questioned unlimited storage. Official Discloud Docs confirm managed PostgreSQL/MySQL/MongoDB/Redis templates and local SQLite support, but Diamond's *unlimited bandwidth* does not document unlimited database storage. This PR currently stores delivery attempts in Supabase because it is the existing config owner; do NOT migrate the entire bot's persistence speculatively, and keep PR draft until supported Discloud database instance and safe persistence/backup/migration strategy are identified. Per-guild settings still use existing guild_config. Avoid merging another Supabase dependency if moving this ledger to a provisioned Discloud database is chosen.

**Execution path / source of truth:** Existing `/verify` staff center -> opt-in `Unverified Reminders` manager; `guild_config` CAS per-guild settings -> `verification_modes.basic_verify_allowed_for_guild` -> `role_truth.member_is_pending_verification` -> Discord fresh member role check -> service-role-only Supabase atomic claim -> private DM with saved verify-channel/panel link. Native worker installed once in `app._start_workers_once`; no new verification state model.

**Scope:** Delay (1–720h), repeat (12–720h), max (1–5 per join), optional wording, previews and enable/disable with explicit per-server authorization. Default disabled; configured unverified/verified roles and exact saved verification channel required. Paginated member scans, bounded guild/pass and DM claims, no public pings, no automatic fallback to Voice/ID Verify, and fail-closed on DB/Discord errors.

**Changes:** `stoney_verify/verification_reminders.py`, `commands_ext/public_verify_reminder_ui.py`, app + Verify Center integration, SQL migration `20261011000100_verify_unverified_reminders.sql`, targeted tests.

**Validation / blockers:** Run focused and full GitHub Actions at exact head, review final diff; apply Supabase migration before enabling (RPC + ledger probe fail closed), then verify one overdue pending member receives a DM, already-verified/bot/staff/missing-role members do not, configured link resolves, repeat cooldown/attempt cap work through restart, and disabled/DB-down state sends no message. Actual Discloud SHA, migration and live Discord tests are not yet confirmed. Avoid enabling all servers automatically.

**Oct 11 exact-head CI follow-up:** CI at previous head `346881e` had 3,158 passing / 1 failing `test_attempt_budget_counts_even_failed_dm`, while compile, Schema Authority, Ticket Owner and Design checks passed. Root cause was missing `name` attribute in test fake guild; real Discord guild always has one. While checking the failed test, discovered a production scanner bug: a per-pass cap could advance the page cursor past as-yet-unprocessed members. Fixed both on the same branch and strengthened the channel mention / stale panel link regression tests. Current head `c57829e`: Schema Authority, Ticket Owner and Design green; Profile and full Dank Shield CI in progress at last check. Do not assert full CI success until it is confirmed.

**DB risk/decision:** Discloud docs support PostgreSQL templates and VLAN; Diamond does not establish unlimited storage. Actual database provisioning/credentials, quota and backup have NOT been confirmed. Existing role truth and guild config still depend on Supabase REST. Reminder delivery's new Supabase migration is currently unprovisioned. Keep PR draft and feature disabled until a tested durable store is chosen and wired safely, and do not silently claim the whole bot has migrated.

**Next:** Confirm new-head CI; resolve any failures; inspect final PR diff. Once a Discloud PostgreSQL instance exists, implement only the chosen reminder-ledger adapter/migration as part of THIS task, run database atomicity/restart tests and canary Simple Verify DM/mention checks. Leave Server Stats, Toke, Cinema and unrelated code alone.

### SUSPENDED — Server Stats accuracy (PR #509 merged)
PR #509 `fix/dank-stats-ticket-reconciliation-20261011` was merged Oct 11 to `aaa-cinema-pre-pregateway-bind` at `50f5f69dbdc6f99ecc383cf67644e328b1a015bc`. It adds safe native-delete reconciliation in ticket repository/events, Server Stats refresh loop/staff actions and regression tests. Prior report: 3,150 tests and relevant checks passed; exact live Discloud runtime branch/SHA and native delete online/offline, Refresh Now, DB failure/recovery still require acceptance. No Server Stats code changed in this task. Preserve original detail below.

---

## PREVIOUS TASK — DANK-STATS-NATIVE-TICKET-DELETE-20261011

**User-approved force switch:** "Fix Dank Shield Server Stats accuracy" because staff can delete ticket channels directly in Discord, missed events or bot downtime can leave stored counters stale, and all displayed counters must remain grounded in authoritative data.

**Outcome:** Reconcile native ticket deletions safely, recover missed channel-deletion events, and keep existing Server Stats accurate without inventing closures or destabilizing other subsystems.

**Branch:** `fix/dank-stats-ticket-reconciliation-20261011` based on documented Discloud Auto Deploy branch `aaa-cinema-pre-pregateway-bind`, after merged PR #508 (merge SHA `57d194714b3c1b4088259a63acc5b9387fa691fb`). **Discloud's actual running branch and SHA require independent verification.**

**Status:** Implementation candidate staged; targeted/full CI, exact-head reviews and live Discloud acceptance pending. Production not changed by this branch.

### Root cause and execution path

- Existing `stoney_verify/app.py` registers `ticket_events.setup(bot)`; `ticket_events.py` already handles `on_guild_channel_delete` by close-then-delete through `tickets_new.repository` and explicitly refreshes `security_stats` on durable success.
- Missing events while offline, lost Discord gateway events, and transient DB failures leave rows `open`/`claimed` despite vanished channels. Server Stats currently polls all saved lifecycle rows every 10 minutes and takes `max(DB open, visible active channels)`; its live-channel **floor can raise a false zero but cannot lower a stale DB count**.
- `security_stats.py` refresh loop can reconcile before counting without adding another independent worker. State must only be deleted on authoritative REST 404 / Discord error code 10003, never on cache misses, 403, 5xx or DB failure. Ghost/channel-less ticket rows and surviving alternate thread/channel identities must be preserved.
- A native deletion is an irreversible delete, not necessarily a completed closure. `Closed Tickets` counts records presently in `closed` status, not a cumulative count of all tickets ever resolved; deleted records are excluded. Do not fabricate extra closed tickets in the display merely to increase the number.
- Other security counters use durable source registries and current Discord member counts. Do not widen this ticket-state task into unrelated metric redesign.

### Changes

- `tickets_new/repository.py`: new narrow paginated non-deleted ticket identity reader with explicit failure result and complete scan cap, no schema migration.
- `ticket_events.py`: reuse the canonical external deletion close/delete transition for normal channels and raw thread delete events; reconcile absent IDs through confirmed Discord REST 10003; preserve live, ghost and unknown/permission-blocked rows; cursor/25 REST checks per guild per pass prevents starvation or API pressure; skip individual stat edits because the existing periodic worker refreshes once.
- `security_stats.py`: call reconciliation from existing 10-minute enabled-display refresh loop before metric reads. Live channel delete events continue their immediate refresh path.
- `commands_ext/public_server_stats.py`: Enable/Repair and Refresh Now reconcile first and surface an explicit uncertainty warning instead of claiming complete accuracy when DB/Discord checks fail.
- `tests/test_ticket_stats_missing_channel_reconcile.py`: regression scenarios for pagination, DB failure, missed native deletion, cached/live/ghost/legacy safety, forbidden/non-10003/5xx/timeouts, alternate channel identity, fair budget, persistence failure, raw thread events, and staff Refresh Now feedback.

### Validation, cleanup, risks and next steps

- Run exact-head focused and relevant full GitHub Actions validation and review final diff; avoid claims of complete before results.
- Test real Discord native deletion while online and offline/restarted, including Refresh Now and Enable/Repair; verify open/claimed counts decline only after deletion is confirmed and persisted; verify historic closed count semantics and DB recovery retry. Check logs and actual Discloud SHA.
- Discord API and DB outages cannot establish a missing ticket; retain an uncertain row until later recovery. No transcript can be recovered if the channel disappeared before bot could capture it. Audit-log actor attribution remains out of scope unless needed for reliable state.
- Scoped changes only; no Cinema, Share Router, Toke, or other moderation changes. Before merge, inspect branch conflicts and latest main/production drift.

### Suspended task checkpoint — Toke mobile status sync

Prior active task `TOKE-MOBILE-STATUS-SYNC-20261011` used `fix/toke-mobile-time-state-sync-20261011` and PR #508 targeting `aaa-cinema-pre-pregateway-bind`; GitHub reports PR #508 merged at `57d194714b3c1b4088259a63acc5b9387fa691fb` (Oct 11). Implemented absolute Discord timestamps, a Refresh snapshot and coalesced VC-count updates to avoid Android-relative-clock freezing/lost voice events. PR body reports exact-head 74 targeted tests passing and incomplete broader/live Android acceptance. The user reported the time refreshed only on app force-close; no confirmed Android/Discloud acceptance was supplied. Preserve Toke files untouched; resume validation only after another exact FORCE SWITCH.

---

# Dank Shield Previous Task Records

## CURRENT ACTIVE TASK — TOKE-MOBILE-STATUS-SYNC-20261011

**User report:** In Discord Android, the ended Toke card kept saying a frozen "Closed 5 seconds ago" until force-closing Discord. Members also need accurate VC participation counts when several join within seconds.

**Branch:** `fix/toke-mobile-time-state-sync-20261011` based on the production auto-deploy branch `aaa-cinema-pre-pregateway-bind`, after merged PR #507 (`c3040079eab39b73b9c1d5fb1f528ded3a2cee66`).

### Root causes confirmed
- Toke used Discord relative timestamp markdown `<t:UNIX:R>` for both "Ends" and "Closed". Discord Android has a long-standing, documented issue where relative time display can freeze until client interaction/restart. A bot cannot force a clock in an already-open message to tick, or detect when a member merely views a message.
- The Toke voice-state listener dropped every update for a guild within a five-second throttle, including final member joins/leaves. No trailing synchronization was scheduled. That could leave VC counts stale until a button or unrelated event refreshed the public card.

### Scoped fix
- Use **static localized Discord times** `<t:UNIX:f>` for "Scheduled end" and "Closed at". Fixed times do not depend on mobile relative countdown behavior, remain timezone-aware, and remain accurate when an app view is cached.
- Active cards show a **fixed last-checked time** and instruct members to use Refresh for on-demand live status. The expiry worker still transitions to a grey/closed card and removes buttons at the configured time (polling at 15-second intervals, plus Discord API latency).
- Replace lossy guild-wide VC throttle with one 2-second coalescing worker per guild, collecting changed channel IDs. A second pass is guaranteed if events arrive during an edit, eliminating lost final VC changes without triggering an API edit per rapid voice event.
- No per-view activity event exists in Discord; do not promise an automatic refresh just because a member scrolls to the card. No constant periodic edit loop, which would be rate-limit- and scale-hostile.
- Change only Toke UI, Toke tests, the targeted regression workflow and this checkpoint. Preserve merged Toke #507 GIF, expiry, roster, Share Router, Cinema and profile card behavior.

### Acceptance gates
- [ ] Exact-head focused tests for absolute timestamps, mobile stale-clock cases, grouped VC updates, late arrivals, guild isolation and old Toke regressions.
- [ ] Exact-head full Dank Shield CI, security and design checks.
- [ ] Merge into Discloud's `aaa-cinema-pre-pregateway-bind` only when safe.
- [ ] Real Android acceptance: inspect active and ended session without restarting Discord; validate fixed local clock, automatic end-state, Refresh button counts, and burst of several VC joins/leaves.

**Status:** In progress, PR draft. Not merged or deployed; Discloud revision and real mobile behavior unverified.

---

## PREVIOUS TASK — TOKE-SINGLE-GIF-20261011

**Owner report:** Toke posts show the same GIF as a large attachment above the card and as an image inside the card after status updates. Screenshot Oct 11, Android. User asks to fix the duplicate.

**Branch:** `fix/toke-single-gif-preview-20261011` from Discloud Auto Deploy branch `aaa-cinema-pre-pregateway-bind`. Isolated Toke fix; other projects paused.

### Confirmed cause and supported solution
- `_publish_toke` sent an uploaded GIF as `file=upload_file` AND `embed.set_image(attachment://filename)`. It then `sent.edit(embed=render_card(...))` to add persistent Cheers/VC data and edits again on reactions. Discord API issue #5675 describes how PATCH/edit causes attachment:// images to display twice.
- GIF uploads and ordinary keyboard files now appear **once** as native message attachments, never again in an edited embed. A public Tenor/Giphy GIF CDN URL is instead displayed **inside the embed**, without file upload.
- GIF-picker message extraction prefers persistent Tenor/Giphy CDN media from source embeds over their public viewing-page link. User source is deleted only after full successful post; avoid embedding short-lived Discord attachment CDN URLs from messages that will be deleted.
- If only a non-direct viewing-page URL is available, keep a clickable link **inside the card description** instead of `message.content`, preventing an additional standalone unfurled preview.
- Existing one canonical send, session persistence, Cheers, Joining, VC tracking, roles, authorization, and cooldowns remain unchanged.
- Never use `suppress_embeds` to solve this, as it also hides the intended Toke embed.

### Expanded owner report: expired cards and confusing crowd layout
- At the 60-minute limit, the public Toke card continued to show a green active status with a relative timestamp in the past; buttons could produce a Discord "didn't respond in time" message after bot restart.
- Root cause: `end_expired()` collected `active=True` copies **before** marking sessions inactive. The worker therefore reused a stale snapshot. Other code paths could mark the storage inactive before the worker saw an expiry, permanently skipping the public edit. Startup restored only active button views, leaving stale expired buttons unhandled.
- A durable `close_pending` reconciliation queue now covers expiration, explicit End Sesh, legacy inactive records and failed card edits across restarts. It is only acknowledged after a terminal card update removes all components. Old callbacks remain registered until closure can be delivered. Worker retries every 15 seconds.
- Every interaction now acknowledges before storage access or Discord edits and returns a friendly ended response for old buttons. Public ended cards change title, color and description, preserve media, show final counts, and remove participation controls.
- Active card displays a compact stats field (Cheers, Joining and live VC with channel), at most three unique members with small status icons, and links longer rosters to Who's Coming. No repeated decorated names or verbose labels beside each member. The paged roster uses the same concise icons.
- New 8-case regression suite validates restart recovery, stale clicks, durable retries, automatic/manual ending, and 35-person mobile layout. Full CI, Android visual acceptance, and Discloud deployment proof still required.

### Validation
- New end-to-end mocked send/edit regression checks on upload / direct public media / Tenor view-page link, plus existing Toke tests.
- CI and actual Android visuals pending; no claim client-specific presentation is guaranteed until verified.
- PR #506 merged into production branch at 7b71a8a32be2a358efd50a3758b4e462a808a540. Preserve its complete single-line profile-renderer implementation and archived checkpoint below; no profile, Share Router or Cinema files altered by GIF fix.


---

## PREVIOUS TASK — PROFILE-USERNAME-SINGLE-LINE-20261011

**User-approved switch:** PR #505 (deduplicated Toke names) was merged to the documented Discloud Auto Deploy branch `aaa-cinema-pre-pregateway-bind`. The user then authorized the separately scoped profile-card layout fix. Share Router and Toke remain preserved and are not being modified in this PR.

**Branch:** `fix/profile-name-single-line-fit-20261011` from `aaa-cinema-pre-pregateway-bind`.

**Outcome:** Display the **whole profile-card display name on exactly one line** in Classic, Minimal, Spotlight and legacy compact layouts, including ornate Unicode/emoji combinations. Prevent textual truncation, ellipses, wrapping, and overlaps with avatar/platform/brand fields. Work inside the current fixed 1400x300 / legacy dimensions.

### Confirmed root causes
- The modern renderer uses fixed-size emoji rich text then `_fit(...)` to replace long names with `…`, creating `UglyGa…` in screenshot.
- The shared `welcome_card_typography_engine._fitted_tile` always shortens text to `...` if it cannot fit at `min_size`; this also affects styled, no-emoji names and the legacy renderer.
- Previous name input was hard-capped to 72/80 code points, which can bisect extended emoji sequences and result in partial text. Valid Discord display names are taken whole with a defensive 2048-character ceiling.

### Scoped implementation
- Shared typography engine: add an **opt-in** `preserve_text=True` setting. First reduce font size within the existing normal range; if still too wide, draw the whole name once and uniformly scale its tile to the allotted single-line rectangle. Other welcome/profile metadata typography keeps existing defaults.
- Modern rich emoji names: measure complete emoji/text tokens, select the largest fitting font, or uniformly rescale the full composed raster (never `_fit` or split grapheme sequences). Respect fixed avatar/brand/platform regions.
- Legacy compact renderer: use the same full-name preserve mode.
- Platform username/handle text in standard and platform-focused themes also uses a single complete line instead of the prior two-line `_draw_complete` wrapping path. Server branding and role labels keep their established rules.
- Regression tests: exact long emoji screenshot pattern, mixed Unicode, styled effects, all layouts, safe pixel bounds, legacy, and non-name typography compatibility.

### Remaining gates
- Exact-head profile-name-focused CI, full CI, asset/visual regressions, and real Android view at a representative phone width.
- Confirm a truly extreme name must shrink in fixed-space geometry; no claim that arbitrary text can remain simultaneously fully readable at normal font size on a fixed-width card.
- PR remains draft until tests and live acceptance. No Toke, Share Router, Cinema, or unrelated modules changed.

### Suspended checkpoints
Share Router #502 and Toke #503 were restored to the production branch through PR #504; Toke name duplication was addressed by PR #505. The earlier Cinema restart/branch divergence remains a separate recorded project. No further changes to those areas in this task.

---

## PREVIOUS TASK — DANK-SHARE-ROUTER-PRODUCTION-RECONCILE-20261010


**User force switch:** Toke overhaul PR #503 was merged into `main@fee272e35e75a1eed0f4c2d6f7a21350173fffae`; user explicitly switched back to Share Router to make the live feature work. Preserve Toke and Cinema; do not implement unrelated features during this task.

**Branch:** `fix/share-router-prod-branch-reconcile-20261010` based on historically documented Discloud Auto Deploy branch `aaa-cinema-pre-pregateway-bind`. **Important:** Discloud's *current* selected branch/SHA is still not verified because the connector has no Discloud access. Do not change it blindly.

**Outcome:** Restore BOTH merged PR #502 (custom Share Router and clean proxy forwarding) and PR #503 (Toke sessions, normal GIF composer, visible participants, VC support and per-guild settings) onto the confirmed Discloud Auto Deploy branch without touching Cinema code. Fix proxy cleanup API misuse, and keep existing default routes and media behavior.

### Root cause and evidence
- Screenshots showed the older six-button Share Router menu with no **Create Proxy**. This matches `aaa-cinema-pre-pregateway-bind` exactly: old `public_share_router.py`, hardcoded four sources and no per-guild `custom_sources`; `main` includes these.
- `docs/DANK_CINEMA_PLAYBACK_READINESS.md` recorded Discloud Auto Deploy following `aaa-cinema-pre-pregateway-bind`, **not** `main`. This was captured October 8 and is evidence, not proof of current Discloud settings.
- Both branches have diverged by hundreds of commits, including Cinema/streaming changes on the production branch. **Do not point Discloud at `main` without reviewing that runtime change.**
- Confirmed legacy post-forward source deletion used unsupported `discord.py==2.7.1` `message.delete(reason=...)`, swallowed by `except Exception: pass`. New code uses `message.delete()` with durable, per-guild cleanup retry.
- Old UI's **Create / Repair Hub** was never a custom-source creator. "2/2 healthy" means saved route checks only, not live version or successful media delivery.
- "Dank Shield is thinking" can come from a deferred button before a response; screenshots alone do not establish a timed-out callback. The old repair's after-defer status render is outside its try/except and needs an explicit error state if it fails.

### Scope and changes
- Transfer Share Router identity, runtime, UI and regression tests from merged PR #502; preserve `share_router_media_resolver.py` and every Cinema file.
- Restore Toke PR #503 files only: `public_toke.py`, `public_community_pings.py` (8-line menu addition), `public_command_surface_v2.py` (2-line listener registration), `toke_sessions.py`, `toke_live_ui.py`, and Toke-specific tests/workflow. No Cinema code, media resolver, or unrelated startup changes.
- Validate: creating sources per guild, browse/search >25, permission-check live source and destination, keep built-in four, configure destinations, preserve legacy routes, accurate cleanup diagnostics, delete-source rollback on error, and restart persistence.
- Use one canonical Share Router runtime and one instance of each listener. Avoid config/environment resets or new Supabase migrations.
- PR targets the documented Discloud branch so merging it there can produce a candidate without changing Cinema. Do not claim production release until current Discloud branch/SHA confirmed.

### Acceptance & status
- **In progress**: both Share Router #502 and Toke #503 source changes ported onto one Discloud-branch recovery candidate. Exact-head combined CI and real Android/Discloud acceptance pending.
- Require focused and full CI on exact head, a fresh /dank → Community Tools → Share Router panel with **Create Proxy** and Custom proxies count, successful + failed source forwarding, and empty proxy after successfully relayed share.
- The user supplied an Oct 10 Discloud dashboard screenshot confirming Auto Deploy follows `aaa-cinema-pre-pregateway-bind`, with last deployed #499. The PR base is correct. Merge only after exact-head validation; verify full deployed SHA afterwards.
- Toke PR #503 is merged into `main` but was missing from production. This recovery restores it alongside Share Router; neither is considered live until actual branch deployment and Discord interaction acceptance.

---

## CURRENT ACTIVE TASK — CINEMA-HTML-INSTEAD-OF-JSON-REGRESSION-20261010

**User evidence:** 2026-10-10 19:16 ET Samsung Browser at `stoneyverify.discloud.dev`, Cinema TV episode card `E3 • OutKast`; bottom toast shows `Unexpected token '<', "<!doctype "... is not valid JSON`. This is a JSON parser consuming HTML on an API call. Exact originating request URL/status/content type is not observable from screenshot.

**Production branch at investigation:** `aaa-cinema-pre-pregateway-bind@965633cce170f0d222d8edf5f9777f5a50d40788`, merge of PR #500 at 23:17:11 UTC, AFTER screenshot (~23:16:49 UTC). Therefore new subtitle code cannot explain this screenshot. GitHub `discloud/commit` status is success, but deployed process SHA and live network body not independently verified.

**Root cause confirmed in code:** `stoney_verify/assets/cinema_site.js` production helper `api()` assumed every HTTP success was JSON and called `response.json()` regardless of content type or redirects. A main-branch-only fix and its test `tests/test_cinema_api_response_contract.py` already handled HTML/redirect/malformed-JSON cases, but were not merged into the Discloud branch. This is a cross-branch regression; passing CI on that branch did not cover the missing contract. The *upstream reason* why one API request returned HTML (login/redirect, proxy/origin route, or temporary gateway response) is not proven. Backend `cinema_play_api` returns `web.json_response` normally; do not label the episode corrupt or guess the proxy status.

**Task/branch:** Isolated branch `fix/cinema-api-html-json-contract-discloud-20261010`, based on exact Discloud GitHub SHA `965633cce170f0d222d8edf5f9777f5a50d40788`; dedicated draft PR until tests, root-cause analysis and browser/Discloud acceptance.

**Patch:** Port the proven main-branch response guard to Cinema's actual production JS (content-type validation, unexpected redirects, bounded HTML errors, malformed JSON, sanitized method/path/status logging); port Node-backed JS regression from main; bump `site.js` version 18→19 for Samsung/other browser cache refresh. Do not auto-retry the POST /play request because it may create a room/torrent session and could duplicate work. No playback, Cinema auth, torrent, sync, or subtitle API logic changed.

**Unresolved acceptance:** Obtain actual failing API method/path/status/Content-Type/redirect and origin logs from a recurrence. Exact-head CI, review, Discloud deployed SHA, live Samsung Browser reproduction and ideally second browser validation required before calling this fixed. The patch makes the error diagnosable and prevents raw JSON parse leakage; it cannot guarantee the upstream HTML source is repaired.

## PREVIOUS TASK — CINEMA-SUBTITLE-SUPPORT-20261010 (MERGED)

**Status:** PR #500 merged into Discloud branch `965633cce170f0d222d8edf5f9777f5a50d40788` on 2026-10-10 23:17:11 UTC. Pending separate live subtitle acceptance.

## ARCHIVED TASK DETAILS — CINEMA-SUBTITLE-SUPPORT-20261010

**Authorized transition:** After merging PR #499, user explicitly said “let's do it now” then “continue implementing” subtitles. Do not switch tasks without user directive.

**Production baseline:** Discloud GitHub branch `aaa-cinema-pre-pregateway-bind@714e2af63b7cfa0fa1837c3e9e0832445246816d` (PR #499 merged; running Discloud SHA not independently confirmed).

**Working branch/PR:** `feat/cinema-subtitle-foundation-20261010` → draft PR #500, targeting the exact production branch. Never merge until exact-head CI and authenticated browser validation.

**Implementation in branch:**
- `stoney_verify/cinema_subtitles.py`: strict SRT → WebVTT conversion; valid text-only stream discovery (SRT/SubRip, ASS/SSA, mov_text and WebVTT; exclude bitmap PGS/VobSub), bounded 35s/1MB FFmpeg extraction through signed 127.0.0.1 torrent endpoint, max 2 parallel jobs, 8-file short-lived cache.
- `stoney_verify/movie_night_web.py`: same signed user/room, guild membership and access checks as main Theater; per-viewer `/subtitle`, optional `/subtitles/search` and `/subtitles/provider` endpoints, state now advertises verified tracks/provider availability, responsive subtitle menu, local SRT/VTT browser file picker, viewer-local caption delay/size, and attach-only HTML `<track>` media integration. No video URL/session, audio URL, room clock, host action or viewer sync mutations.
- `stoney_verify/cinema_library_service.py`: per-member/per-guild `subtitle_language_by_guild` with authenticated `guild_subtitle_language` preference updates. Local uploads never leave the viewer's browser.
- `stoney_verify/cinema_subtitle_providers.py`: SubDL v2 exact metadata/language helpers and paid-plan validation, including TV-labeled exact episodes.
- `stoney_verify/cinema_subtitle_router.py`: three-source provider system (SubSource API v1, Wyzie Subs, SubDL), authenticated guild/tenant-specific account assignment from Discloud JSON, multiple accounts for distinct authorized guilds, deterministic one-key-per-provider/guild (no same-provider quota circumvention), parallel fallback/combined results, per-account conservative rate budgets + provider 429 cooldown, opaque viewer/guild/session-bound selections, safe signed download host allowlists, exact release+episode matching and bounded ZIP text extraction. Search and download jobs are coalesced between 20 concurrent viewers and cached; independent providers remain operational when one fails.
- `tests/test_cinema_subtitles.py` covers conversion security/limits, verified embedded text, on-demand extraction cache, authorized endpoint, user/guild scoping and SubDL v2 controls. `tests/test_cinema_subtitle_router.py` covers three-provider search, SubSource schema, Wyzie URL restrictions, quota non-evasion, 20-viewer query/download coalescing, safe archives and scoped opaque selections. `tests/test_cinema_cross_mode_theater_contract.py` verifies 20-person subtitle state without changing shared timeline. `tests/test_movie_night_web.py` updated for new preferences and route.
- `docs/DANK_CINEMA_SUBTITLES.md` documents setup, fallback and acceptance.

**Known incomplete verification:** Newest exact-head CI pending; real Discloud/Android desktop browser track rendering, FFmpeg against real partially buffered torrent, optional SubDL authenticated real API response/download, native fullscreen subtitling and 20-viewer live subtitle/audio soak not verified. Do not claim user-facing completion/deployment until each evidence gate is satisfied. Provider requires an actual approved key, not in repo.

**Next single action:** Resolve exact-head CI/regression failures if any; inspect final diff then complete authenticated standalone/private/watch-party browser acceptance; merge only after required gates.

## PREVIOUS TASK — CINEMA-FULLSCREEN-PORTRAIT-RECOVERY-20261010 (MERGED)

**Result:** PR #499 merged as `714e2af63b7cfa0fa1837c3e9e0832445246816d`. Live multi-browser fullscreen and 20-person audio load acceptance remain separate from code merge.

## ARCHIVED TASK DETAILS — CINEMA-FULLSCREEN-PORTRAIT-RECOVERY-20261010

**Source:** User screenshot Oct 10 16:57 Eastern, Samsung Browser, authenticated standalone Mad Max: Fury Road playback. Video displays decoded frames and user confirms clear audio; after entering fullscreen and returning to portrait, inline video stage remains visually too tall/distorted. Need correct layout for standalone and shared Theater in Private Sessions and Watch Parties without restarting a running torrent or altering audio and sync.

**Live production baseline:** GitHub Discloud branch `aaa-cinema-pre-pregateway-bind@07aad2db0c376a8395725a5dbbaa403d7552f045` with `discloud/commit` success. PR #498 smart automatic selection merged, exact-head five workflows green. Running Discloud process SHA and per-mode Android viewer experience not independently verified.

**Task branch:** `fix/cinema-fullscreen-portrait-recovery-20261010` based on that production SHA.

**Root cause / fix:** Shared `stoney_verify/movie_night_web.py` previously set fixed pixel `videoStage.style.height=Math.round(rect.width*9/16)+"px"` in resize/fullscreen and removed it depending on a fullscreen signal that could include stale native `video.webkitDisplayingFullscreen`. Browser rotation can report transitional landscape dimensions or stale fullscreen compositor when leaving fullscreen. New code removes JS pixel locks, relies on CSS responsive 16:9 aspect ratio with explicit inline state and actual stage `document.fullscreenElement` checks; adds orientation-change, parent ResizeObserver and deferred settle rechecks. No `video.src`, `video.load`, seek, player session or audio/host timing mutations. Focused regression tests in `tests/test_cinema_fullscreen_portrait.py`.

**Scope and acceptance:** Fullscreen landscape -> inline portrait -> fullscreen again (3+ cycles), Samsung Browser and another desktop/mobile browser, while video and per-viewer audio continue; no black frame, crop, stretched video, extra-height stage, player click breakage, host/viewer clock jumps, duplicate torrent sessions. Also verify Private Session and Watch Party use same shared Theater and independent viewer audio; **standalone evidence alone cannot certify modes not yet tested**. Complete exact-head CI, test and patch audit, merge/deployment status before user rechecks browser.

**Research and contract evidence:** MDN Fullscreen API requires `Document.fullscreenElement` truth after the fullscreenchange event; `ScreenOrientation.lock` is limited support and may reject, so orientation is optional. Browser `HTMLMediaElement.play()` may reject NotAllowedError on audio and native `audioTracks` lacks uniform browser support. Runtime investigation confirms `/movie/{room_id}/watch` uses one `_watch_html` for all modes, each authenticated via signed room/UID URL plus guild membership and `MovieNightManager.user_can_access`. Standalone is host-only, Private is invited/host-only, Watch Party allows eligible guild members. A common `_state_payload` emits room-authoritative clock + viewer-specific `stream_consumer`, signed media and FFmpeg audio track URLs; `movie_night_preferences` fetches user preferences for current guild. The shared page's Sync gesture initiates both video + compatibility audio before awaiting async network response. Cross-mode runtime contract regressions added in `tests/test_cinema_cross_mode_theater_contract.py` for all modes and two viewers, with signed page access, separate consumer/audio URLs, same shared clock/host authority and language scoping. No live two-device browser proof exists yet, so do not claim decoded audio/video or exact A/V lip-sync guarantees.

**Status:** Isolated fullscreen patch and multi-mode contract tests committed to draft PR #499; exact-head CI re-running. Discloud production remains at prior SHA until merge. Confirm CI and realistic browser/device acceptance before declaring complete.

## PREVIOUS TASK CHECKPOINT — CINEMA-SMART-AUTOMATIC-SOURCE-SELECTION-20261010 (MERGED)

**Result:** PR #498 merged to Discloud branch `aaa-cinema-pre-pregateway-bind@07aad2db0c376a8395725a5dbbaa403d7552f045`. Exact-head GitHub checks for its PR head `5ffd3082373b5d353b3d5bbcf4fcb43737bc2f7f` passed all 5 workflow groups; `discloud/commit` success. User independently verified standalone Mad Max video/audio. Private Session and Watch Party real-mode acceptance remains unverified.

## ARCHIVED DETAILS — CINEMA-SMART-AUTOMATIC-SOURCE-SELECTION-20261010

**FORCE SWITCH authorized:** User wrote `FORCE SWITCH: Complete Dank Cinema automatic smart selection`, reason prioritize intelligent selection over remaining episode-picker acceptance.

**Outcome:** Deterministic, conservative automatic movie/episode source choice based on verified compatibility when known, quality/file-size efficiency and bounded swarm evidence, plus bounded safe startup fallback without host/viewer state loops. No false promises of codec proof before torrent bytes arrive.

**Source/branch:** `fix/cinema-smart-auto-selection-20261010` created from exact Discloud GitHub deployed branch `aaa-cinema-pre-pregateway-bind@b93dc9bae8cf1f9d2eef58636565a392e38a7b10` (PR #496 merge). GitHub `discloud/commit` reported success, real runtime SHA not independently validated.

**Status:** Root-cause investigation and implementation committed on isolated task branch; targeted regression tests added. Pre-merge CI and browser/Discloud acceptance pending. Not deployed.

**Implementation checkpoint:** `cinema_playback_service` now uses one source ranking policy for automatic site and next episode selection: confirmed compatibility, size/resolution efficiency, audio, recent measured download performance when available, reported seeds with saturation, and soft provider preference. Sequential automatic startup tries no more than three eligible candidates while retaining room token/host/candidate baseline; explicitly chosen manual sources are unchanged. On automatic launch the selected torrent file is checked, wrong-episode entries excluded, browser-risky codecs/containers rejected before room commit, and leases released for newly rejected sessions. `movie_night_web` carries live peer/download metrics and verified codecs into existing same-source room variants, `MovieNightManager.add_variant` preserves that evidence on provider refresh. No post-commit automatic room swapping, parallel torrents, or video transcoding. Targeted tests in `tests/test_cinema_smart_selection.py`; existing site mocks updated for new explicit automatic flag.

**Per-user/per-guild language integration (same active task):** Existing `cinema_library_service` persists each member's `audio_language_by_guild` and global default; `movie_night_web` independently loads audio track preferences and signs viewer-specific track URLs. Smart ranking now reads the authenticated host's user ID and room guild ID, prefers verified matching audio tracks using browser-consistent aliases, and preserves explicit `auto` overrides without forcing other viewers' audio or trusting provider filename language claims. Site and next-episode call sites pass the authenticated guild ID; fallback uses the same scoring. Regression tests cover different users, two guilds, multilingual audio tracks, unknown labels, explicit auto override, and season-zero specials. Subtitle preference is currently a separate global default; do not claim it is guild-scoped or silently expand this task into subtitle settings.

**Outstanding evidence and risks:** Torrent filename is only negative confidence, not proof of actual codec; FFprobe waits for downloaded bytes and happens asynchronously, and only previously started sources have measured throughput. Host watch-party content never changes automatically after a successful commit. Actual Discloud CPU, decoder support per browser, and live A/V playback cannot be certified by CI alone. Verify session lease cleanup and all regression suites before merge; inspect final diff and stale compatibility logic.

### Execution path and root cause found
- Provider responses cap results at 25/100; PR #495 moved compatibility-aware lookahead earlier, but still relies on unverified release names.
- `cinema_site.cinema_play_api` and `movie_night_web.movie_night_next_episode` call `select_preferred_variant` and then `start_room_variant` once. Failures generate errors instead of trying a different eligible release.
- `MovieNightManager.ranked_variants` is seed/vote driven; `select_preferred_variant` filters risk-2 then lets remembered provider preference override any better candidate. Actual file bytes/codecs cannot be known until `TorrentManager.start_magnet` downloads torrent metadata and later asynchronously probes downloaded file pieces.
- `TorrentManager.status` exposes **actual** download rate / connected peers, but only for sessions already started; provider-reported seeds are never equivalent to current live throughput. Verified codec metadata propagates later in `movie_night_web._state_payload`.
- Room authority, leases, owner decisions and signed watch links must remain untouched by unsafe retry logic.

### Suspended task checkpoint (do not resume without new FORCE SWITCH)
`CINEMA-EPISODE-RELEASE-PICKER-20261010` PR #496 merged at `b93dc9bae8cf1f9d2eef58636565a392e38a7b10`, pre-merge exact-head workflows five green, GitHub Discloud deployment commit status success. In-scope backend API/frontend/asset/test code in six files. Remaining: live Android/desktop user acceptance (Choose Release visible for Carrie S01E01, select source, signed watch first video frame and audible audio, ordinary Resume, different browser). Blocker: no authenticated Android browser or direct Discloud runtime access. Git clean PR merged; no live acceptance claim. Next after resumption: collect browser evidence, repair any same-root failures, complete DoD.

### Acceptance and risks
- Shared policy must respect verified vs unverified data, incompatible-codec exclusions, provider limits, real vs reported swarm metrics, download caps, quality/file-size defaults, and soft preference.
- Retry only bounded, sequential, **before** a successful room-media commit. Do not auto-swap shared media after playback has started or let a viewer force host state.
- Preserve manual source choice as explicit override, session signing, exact identity, adult policy, lease cleanup and race guards.
- Tests, compile, full CI, security and diff inspection, Discloud deployment SHA and Android + another browser A/V acceptance required. Never claim perfect universal playback or that unstarted torrents have measured download speeds.

**Next:** Implement smallest correct shared selection/fallback mechanisms and targeted regression tests; do not touch unrelated tasks.

## SUSPENDED CHECKPOINT — CINEMA-EPISODE-RELEASE-PICKER-20261010

**Outcome:** Enable release selection for exact TV episodes on Dank Cinema, preserving automatic Play/Resume while allowing manual selection after no-compatible-release errors.

**User evidence:** Oct 10 12:58 PM Eastern Carrie S01E01-S01E08 details screen. Every episode has Play/Resume, but no selectable release; the bottom Playback Source panel says to choose an episode, despite no actual episode-release UI.

**Branch:** `fix/cinema-episode-source-picker-20261010` from Discloud auto-deploy branch `aaa-cinema-pre-pregateway-bind@812834b8ee5592026e80dc4a23b4bb82a0659f25`, after PR #495 merged and successful Discloud commit status. Running-process SHA and real provider inventory still require verification.

**Status:** Backend/API/frontend work and focused tests committed in isolated branch; exact-head CI and runtime/browser acceptance pending. Not merged or deployed.

### Root cause and execution
- Movie details API returned source choices; TV details returned `sources: []` and an instruction without controls. Season cards only called `playOnSite(episode)` without a source-selection path.
- The existing `/api/play` already accepts an opaque `source_choice` and validates it against real exact-episode provider variants. The missing part was authenticated episode-specific listing and a click path that supplies the choice.
- New `GET /api/episode-sources/{series_id}/{season}/{episode}?tmdb_id={id}` requires the same guild/member identity, catalog episode ID and adult-content policy. It caches a bounded 90-second one-shot source snapshot already supported by the canonical Play route and returns only opaque source-choice IDs, never source URLs.
- Site season cards gain opt-in `Choose Release`; no provider lookup occurs merely by loading a season. Source choices show provider, reported seeds, video compatibility caution, and explicit play; the existing Play/Resume stays automatic.

### Tests / validation / protection
- Focused tests cover opaque exact-episode results, lower-seed non-risky candidates, manual source choice with no duplicate search, invalid episode ID, guild adult policy, and JS event hookup.
- Preserve exact session authority, browser player, torrent streaming, room permissions, source providers, feed settings and manual bypass of preflight for host-chosen releases.
- Run exact-head CI, Python compile/full tests, UI/asset regression, static checks and diff review before merge. Accept production only after playing a real episode from both automatic and manual source choice and confirming first rendered frame plus audio on Android and a second browser. Available candidates may still be unverified or genuinely incompatible.
- No Discloud changes are authorized until review and validation gates pass.

**Next:** Create scoped draft PR to `aaa-cinema-pre-pregateway-bind`; await exact-head CI and inspect final diff, then verify live on Android.

## PRIOR CHECKPOINT — CINEMA-AUTO-SOURCE-RESULT-WINDOW-20261010

**Outcome:** Ensure high-seed HEVC/MKV results do not crowd lower-seed, potentially browser-compatible releases out of the existing per-provider selection window for Dank Cinema automatic playback.

**Branch:** `fix/cinema-auto-source-compatible-results-20261010` from `aaa-cinema-pre-pregateway-bind@86d1a4f1dcee575b17d61597903fdf4d2e200a2e` (PR #492 merged). No production change or Discloud deployment.

**Status:** Implementation and focused regression tests committed on isolated branch. Exact-head CI, full relevant validation, live candidate evidence and signed playback/first-frame acceptance pending.

### Root cause / execution
- Provider JSON normalization cut to the first 25 raw entries in `media_source_resolver._search_one`, before Cinema knew video risk. RSS extraction also stopped at 25.
- An index sorted by reported seeds can thus provide 25 HEVC/MKV variants while a potentially compatible H264 result in the same response is never passed to the existing automatic chooser. Site Play/Resume then receives only risk-2 candidates and PR #490's correct preflight refuses all instead of starting known-risk video.
- Actual Discloud running SHA, real Carrie source metadata, and existence of a compatible release have not been independently confirmed. No claim a suitable source always exists.

### Scope and changes
- Keep per-source output 25, total output 100, bounded response bytes, SSRF-safe provider fetching, exact title/episode identity, adult rules, source preferences, lease/host authority and manual override unchanged.
- Inspect at most 100 already returned items per provider, prioritize risk-0/risk-1 entries ahead of risk-2 using the *same* helper as room ranking, maintain original order within risk class.
- Move existing risk scoring from `MovieSourceVariant` to `media_metadata.browser_video_risk_key` with a delegating method. This prevents duplicate conflicting codec policies.
- Expand RSS iteration lookahead to the existing 100-result bound without adding HTTP retries or extra provider requests.
- Regression tests: candidate after slot 25 survives in JSON; stable ordering and upper bound; RSS candidate after slot 25 survives; existing source preflight and source normalization suites remain authoritative.

### Validation / blockers / next step
- Source editing done through GitHub connector; local checkout unavailable (container has no GitHub network route).
- Await exact-head CI and diff review, including existing media metadata, room ranking, source resolver, Cinema and browser playback suites.
- Retest automatic episode and movie selection against actual provider data. Manual source choice and prior known-risk HEVC fail-closed semantics must remain unchanged. Verify on Android and a second browser to first frame/audio before claiming end-to-end playback.
- This task does not enable HEVC transcode, guarantee a compatible source exists, change RSS provider configuration, or fix unrelated A/V or website failures.
- Next: Open scoped PR into actual Discloud auto-deploy branch, run checks, review diff. Do not merge/deploy before validation.

## PRIOR CHECKPOINT — CINEMA-LEGACY-PLAYBACK-GLITCH-TRUTH-20261009

**Production baseline (verified):** Discloud auto-deploy branch aaa-cinema-pre-pregateway-bind at 48f4053ed329e84ec18b45728241c6ca6a844ccf; PR #490/#491 merged and deployed. Forked fix/492-cinema-playback-state-legacy-loop-20261009 from this exact SHA. No change to production until PR CI and real device validation.

**User evidence:** Oct 9 ~11:24 PM Eastern, Samsung Internet The Theater / Mad Max: Fury Road. Torrent 100%, 0 B/s, seeds/leechers labeled "reported" 421/49, media verified H264 + MP4, browser canPlayType says probably, JS health says "Video playing" while timing says "Video paused • embedded audio" and scene is black/glitchy. Browser startup metadata 358ms, playing event 9.09s, initial frame callback 9.34s. Server first request and first byte **both** around 282.7 seconds after session origin: does not prove 282 seconds of download latency; browser request occurred late. 100%/0B/s can be a completed torrent, not failure. Reported seeds are provider counts, not live connections.

**Confirmed old-code conflicts:**
- streamHealthLabel is based on s.state intent + video.readyState + past first_frame, and pause events refresh some controls but not Stream Health. "Video playing" may be a stale label while the HTML video is paused.
- Browser without requestVideoFrameCallback could mark first_frame simply because currentTime increased, falsely claiming actual rendered video.
- applyState host polls every 2 sec and awaits video.play() while a promise can remain pending during buffering; multiple state responses may overlap rather than coalesce.
- Ordinary HTML video play/pause events call hostAction(resume/pause) even when caused by player recovery, remoteApply timing races or involuntary media events. The custom buttons already handle explicit user actions; PiP/native fullscreen interactions need separate treatment.

**Fix:** Only call host play once while pending; never await it in state reconciliation; keep explicit Play user gesture and native PiP/fullscreen host controls; guard involuntary media events from mutating shared host state; base health on actual paused/readyState and track ongoing requestVideoFrameCallback frames, including frozen playback after a 5s gap; timeupdate without frame callback reports clock advancement, not a confirmed video frame. Preserve signed source, torrent byte routes, cast, FFmpeg AAC, watch-party clock, role permissions and per-guild user audio.

**Validation:** New Node regression executes rendered Theater JS with unresolved Promise, multiple simulated polls, media pause/play event forwarding and active vs stalled video health. Existing stale tests updated to assert removal of old echo. CI, source/security checks, and actual Samsung Browser (first frame, 2min continuous video + audible embedded/AAC audio, seek 1min, pause/play, background restore) required. No claim of universal browser compatibility until real testing. Examine whether overlapping viewer play promises and CDN source range performance remain concerns if tests show glitches.

**Single active task only:** Do not switch to Basic Verify, Dr STONE, or unrelated Dank Shield projects without explicit FORCE SWITCH.

## PRIOR CHECKPOINT — CINEMA-UNSUPPORTED-AUTO-SOURCE-PREFLIGHT-20261009

### Browser-settings resilience, same active Cinema task, stacked companion PR #491

User approved handling browser dark/forced colors, accessibility and text sizing, Data Saver, reduced motion, mobile/desktop-mode layout, background suspension, network reconnect, audio permissions, PiP/fullscreen and strict browser privacy. Opera/Opera GX, Chrome, Edge, Firefox, Safari, Samsung Internet, Brave, Vivaldi and other modern browsers are targets, not all certified by real playback.

Existing correct code already implements signed video/audio, per-viewer language, prefers-reduced-motion, Auto/High/Standard/Lite effects, Data Saver and hardware hints, ResizeObserver/viewport/orientation recovery, fullscreen/PiP capability detection, codec advisories, user-gesture audio recovery, offline/page-wake, background-gated polls and private no-store authenticated HTML. Preserve it, don't duplicate it.

Companion branch fix/491-cinema-browser-settings-resilience-20261009 is based on PR #490. It adds forced-color and higher-contrast support without recoloring actual video/poster imagery; observes local non-identifying forced contrast, reduced motion, Save-Data, network and visibility changes and re-evaluates decorative Auto quality only; gives Advanced Details an environment readout; and debounces pageshow/visibilitychange/online recovery bursts to prevent repeated media restarts. Never read the user's extensions, fingerprints, cookies, DRM, hardware acceleration or battery health. Cannot bypass actual autoplay/security rules. New rendered-browser JavaScript tests exercise the actual functions. Existing #490 source-risk test fixture was also updated in both branches after exact-head CI identified one obsolete mock contract.

No codec transcode changes, permissions, torrent, host/viewer, source/ranking, signing, database or Discord changes in this companion. Keep both PRs draft until CI and actual cross-browser A/V tests. An HTML page opening in Chromium is not validation of Opera GX, Safari, Firefox or real video/audio decode.

**Reason:** At 8:32 PM Eastern, Mad Max: Fury Road still had a black video element after automatic source selection. Advanced Stream Details now positively identify `hevc / video/matroska` and Samsung Browser's `canPlayType` reports no native decoder. User asks why Cinema auto-selected the release and why FFmpeg cannot support it.

**Verified production baseline:** `aaa-cinema-pre-pregateway-bind@09affbf295a0642605aa7eb4c0532925507268cd`. PR #489 merged and Discloud status success Oct 10 00:16:18 UTC. New branch `fix/490-cinema-unsupported-source-preflight-20261009` forks this exact production SHA; do not overwrite other production features.

### Root cause and focused prevention

- `cinema_site` materializes provider results and calls `select_preferred_variant` **before torrent starts**. `MovieSourceVariant.browser_video_risk_key` added in #489 only used `metadata.verified.video`; provider search metadata deliberately sets `source_reported_verified=False`, and FFprobe runs asynchronously *after* torrent pieces arrive. Thus most releases were scored `1 = unknown` before launch even if `release_name.video_tags` contained HEVC/x265. A remembered preferred provider could override the ranking entirely. Later browser detection only warns after the source was committed.
- New ranking rejects only **high-confidence negative release-name/container clues** (HEVC, x265/H265, .mkv, .avi, .mpeg/.mpg). Such clues never prove that another source is safe. Unmarked releases still remain unknown until a real probe, and Android-specific capabilities cannot be inferred from server-side filename alone.
- `select_preferred_variant` excludes risk-2 variants from **automatic** site and next-episode selection, including a dangerous preferred provider. If all releases are risky, it fails with a useful compatibility error rather than knowingly starting a black screen. Explicit host-picked variants and original room authority/signatures are unchanged.
- Targeted regression tests cover HEVC/x265, unsafe containers, unknown-versus-verified risk, preferred-provider override, only-unsupported-source fail-closed.
- **This is not a universal video conversion claim.** HEVC-to-H264 requires decode/re-encode; changing MKV to MP4 without conversion cannot make an unsupported HEVC decoder work. Discloud shows 1.46 vCPU, 1.46 GB RAM with peaks of 80% CPU and 85.9% RAM; enabling unbounded FFmpeg video workers endangers Discord bot stability. Do not activate transcoding without measured bounded concurrency and real browser evidence.

### Acceptance

- New exact-head CI, source-choice tests, final security/diff review; no unrelated changes.
- On Android test auto source ranking for multiple candidates, all-risky sources refusal, explicit manual behavior, per-guild language and AAC sync unchanged. When provider metadata hides codec, later probe/browser diagnostic must still surface issue; no false universal-playability claim.
- **Still open:** reliable audio synchronization and source-specific HEVC video fallback. Do not mark Cinema broadly fixed solely on source preflight or CI. Keep prior verification/Dr STONE work suspended.

### Prior history

## HISTORICAL CHECKPOINT — CINEMA-AUDIO-LANGUAGE-AUTOPLAY-TRUTH-20261009

**Scope:** Continue active Dank Cinema AAC audio stability task after Oct 9 7:12–7:13 PM Eastern user screenshots. Issues: incorrect movie heading ("Portuguese 5.1" for Mad Max artwork), English 7.1 track switching, distracting Audio permission button, black video with "Video playing" badge, automatic release selection, and language preference per user per guild. No FORCE SWITCH to another project.

**Confirmed Discloud baseline:** aaa-cinema-pre-pregateway-bind @ e8773215fb29654decdc7baf8c3dfbf8481eb174. PR #488 merged and deployed successfully at 23:08:44 UTC Oct 9 before screenshots. Branch fix/489-cinema-audio-language-title-truth-20261009 is based on this production SHA.

### Confirmed code issues and scoped changes

- _state_payload serialized room candidate title despite canonical catalog movie_metadata. Prefer canonical movie title to release tags.
- Verified audio track metadata has language and title. Existing sidecar selection didn't persist preferred language (only native audio selections saved GLOBAL default_audio_language). Add per-user/per-guild preference in existing Cinema user profile JSON, bounded by authenticated room guild ID. Respect viewer-only selection; never change shared room media just to honor one viewer's language.
- Start selected AAC track within real user gesture; browser may still require gesture on restricted autoplay, so show recovery only when genuine playback block is reported.
- Stream health said "Video playing" before already-tracked first_frame video callback. Display honest waiting-for-frame status and limited, safe video/AAC clock/readiness diagnostics in Advanced Stream Details to enable empirical live debugging.
- MovieSourceVariant browser_audio_risk_key marked mixed AAC/DTS audio safe just because any stream was AAC, whereas actual browser_audio_compatibility requires sidecar if any codec is unsupported. Align ranking; add deterministic mixed-codec test. Do not change seed/quality weights blindly.
- User reports audio skipping even with healthy torrent download speed. Screenshots do not prove exact FFmpeg/decoder/sync fault. The new readout allows checking video/audio clocks, stalls and AAC error without exposing signed media URL. No claim of audible sound recovery without live multi-minute tests.

### Acceptance and safety

- Exact-head GitHub CI pending, tests for per-guild isolation and language matching, mixed-codec ranking, title truth and audio recovery only on browser failure.
- Before merge: examine complete diff; validate correct title, English selection, no permission prompt during normal Play, correct first frame, audio/video synchronization after seek and buffering, Android plus another browser, host and viewer. No torrent engine/room permissions/auth changes.
- Stay on this single Cinema audio task. Dr. STONE HTML-JSON issue and Dank Shield self-verification remain suspended with prior checkpoints below. Preserve PR #487 and #488 already deployed; do not merge main blindly.

---


## HISTORICAL CHECKPOINT — CINEMA-AAC-AUDIO-STABILITY-20261009

### October 9 continuation: AAC skipping / time drift after PR #487 deployment

**Status:** Investigating and validating. PR #487 merged as `e4d0846a65b7e65fae776842f319ef3c6d9b8d7b` and Discloud `discloud/commit` confirmed successful Oct 9 22:03 UTC. New audio drift/skipping complaint is on that running production baseline. The previously fixed audio recovery UI is still part of the active task. No FORCE SWITCH was issued.

**Branch:** `fix/478-cinema-sidecar-av-sync-20261009`, forked directly from confirmed Discloud branch `aaa-cinema-pre-pregateway-bind@e4d0846a65b7e65fae776842f319ef3c6d9b8d7b`. Production remains untouched.

**Concrete code path and reproducible defect:** The HTML5 video emits `waiting` or `stalled` while `video.paused` may be false; the deployed handlers merely changed the notice text and did not pause the independent AAC sidecar. Its `currentTime` could therefore advance while video was buffering. The deployed `syncCompatAudio` then interpreted resulting drift greater than 2.5 seconds as a reason to discard the AAC source and launch a new FFmpeg transcode every eight seconds. Those source replacements can produce audible jumps. This is grounded in code, but the specific user's live media timing and FFmpeg output have not yet been inspected.

**Scoped proposed behavior:** Treat video readiness, seeking, and buffering as authoritative for advancing the sidecar. Pause AAC on video `waiting`, `stalled` and `seeking`; resume only with a playable video clock. Eliminate periodic FFmpeg-source reloads for ordinary drift and use bounded audio playback-rate corrections. Preserve explicit seek re-anchor and per-viewer gesture recovery. Keep torrent provider, permissions, session signing, and audio-track routes unchanged.

**No speculative throughput change:** An experimental change from FFmpeg `-re` to `-readrate 2` was immediately reverted before PR creation because no session evidence indicates faster-than-normal playback. Final branch should have no changes in the audio transcode backend.

**Regression validation:** New `tests/test_cinema_av_clock_sync.py` executes actual rendered Theater handlers under Node to reproduce unpaused video stalls, prevent audio advancement, verify video recovery without source reload, test drift correction and one explicit seek re-anchor. Exact-head CI and live Android/desktop audible first-frame, stable speech sync over multiple minutes, seek and buffering, host/viewer are still required. Do not claim real playback fixed from mock tests alone.

**Next:** Open scoped draft PR, run exact-head CI. Acquire live signed-session reproduction or safe browser diagnostics (video/audio clock, readyState, stalls, FFmpeg errors) before merging/deploying. If actual timing differs from the code-proven failure, revise the solution rather than layering timeouts.

**Outcome:** Restore usable AAC compatibility audio and consistent per-viewer recovery controls without disrupting video, room synchronization, privacy, or existing source selection. Reported Carrie S01E01 video plays while audio is silent; the Enable audio control appears/disappears between adjacent browser screenshots.

**Actual production branch baseline:** `aaa-cinema-pre-pregateway-bind@70356d1560e8047ddc1dc3dd9f698ee323f5c2ce`, confirmed Discloud successful commit status on Oct 9 UTC. Source differs from `main`: PR #483 added source-aware audio tracks and the Enable audio button to the production branch, not main. Do not substitute main or overwrite unrelated production work.

**Implementation branch:** `fix/478-cinema-aac-audio-stability-20261009` based on that actual deployment branch.

### Investigation / execution path / root causes

- Movie Night `_state_payload` publishes verified audio tracks and per-viewer signed AAC compat URL. Audio sidecar runs `ffmpeg` from the signed torrent stream via `/media/torrent/audio/{token}/{filename}`. Theater `applyCompatAudioState` loads a hidden audio element while keeping canonical video clock; `startCompatAudioFromGesture` requests playback on a user click.
- PR #483's `refreshAudioPermissionControl` tied visibility to transient `compatAudio.paused` and `video.paused`, not durable audio permission state. So the recovery row flickers during background playback transitions even while its browser-blocked message remains.
- `syncCompatAudio` continually checked video-versus-audio drift and reloaded the AAC stream after a 750 ms gap, including while its audio `play()` was still pending for buffer or permission. That can interrupt play promises, cause repeated FFmpeg work, and prevent stable audio start. The original sidecar-error path cleared the signed URL and mode, so the next state poll could recreate a broken stream.
- Current screenshots prove video first frame and audio warning, but do not prove the underlying FFmpeg emitted decodable AAC or actual browser permission state. Live signed browser/Discloud test remains required.

### Scoped changes

- Keep the AAC recovery button visibly available while that audio path is active and the viewer has not intentionally muted; label it Enable audio when paused/blocked and Restart audio otherwise.
- Track in-flight audio starts and actual gesture recovery so polling never interrupts startup or repeatedly retries a blocked `play()`. Avoid destroying a loaded audio source on each user click. Retire outdated async completion paths via sequence identity.
- Require the audio element to have playable data and a meaningful drift before automatic restart, paced by an existing-start timestamp.
- If an original AAC sidecar reports `error`, preserve the signed mode and require explicit user retry instead of restarting on every state poll; preserve verified alternate-track fallback to original.
- New `tests/test_cinema_aac_recovery.py` runs the rendered Theater's JS behavior under Node and checks the original-error path; existing Movie Night and torrent tests still apply.

### Validation and remaining blockers

- CI and targeted suite: pending exact-head pull-request workflow.
- Live audible result for Carrie S01E01, another movie/episode, host and viewer, Android Samsung Browser and at least one other engine: **not verified**. Do not equate `Video playing` or an AAC `play()` Promise resolving with audible output.
- No source search, provider registry, catalog, bot verification, Discord interactions, DNS, auth, storage, or room-mode code touched.
- Before merge: exact-head CI, inspect regression failures, compile/static checks, cleanup and final diff. Production deployment only to the confirmed branch after checks; user must validate real sound.

### Suspended tasks / backlog

- **Dr. STONE episode playback HTML-vs-JSON:** PR #486 merged to `main@9a7b543abc0a5e205db46616cf12b50a8c6053fd` with all six CI families green. Discloud was not confirmed running that commit because it deploys a different branch. Live authenticated exact-episode first frame and HTTP response status/origin remain unverified. Suspended by exact FORCE SWITCH on Oct 9; do not use an unrelated branch merge to import it.
- **Dank Shield self-verification:** investigation suspended earlier. Basic Verify immediate-ack vs advanced verification-ticket mode and the currently saved guild configuration require log-based production validation; no fixes were made.
- Separate poster/anime metadata, feed discovery, general torrent throughput and episode provider compatibility are not part of this AAC fix.

**Next step:** Run browser-behavior regression and full exact-head CI, inspect final diff, then canary production on the existing Discloud branch. If audio remains silent with controls working, collect signed AAC response status/bytes, FFmpeg output and browser media errors to diagnose the separate server/codec path without guessing.

---


## HISTORICAL CHECKPOINT — DANK-CINEMA-PROVIDER-COMPAT-20261008

**Outcome:** Validate the existing Nyaa/structured-provider pipeline without duplicate adapters and repair demonstrated episode-query incompatibility, while preserving canonical media identity and all existing playback/Discord behavior.

**Active continuation branch:** `fix/cinema-episode-search-year-fallback-20261009` (from `main@a6aa8d4b02e0720f50a8b80fc232136b5b522960`).

**Status:** PR #484 (Nyaa RSS size parsing) merged to main; its PR-head workflows passed. Production deployment of that size change is not verified. User screenshot confirms live Nyaa and ThePirateBay results for a Bleach movie, not universal TV episode availability. This branch adds a single episode-query fallback plus three regression tests. Exact-head CI and Discloud/browser acceptance are pending.

### Scope / execution path / root cause

- Existing Admin Add Search Provider → `prepare_example_search_url` → guild config registry → `search_movie_sources` → safe provider HTTP/RSS/JSON resolver → `search_exact_episode_sources` → `filter_outcome_for_catalog` → canonical room playback.
- Nyaa RSS reports human-readable sizes (e.g. `1.1 GiB`); the shared parser now normalizes them following PR #484 without a Nyaa-only duplicate parser.
- For TV episodes, `episode_search_query` adds the series premiere year, while `episode_release_matches` already allows correctly identified episodes whose release names omit that year. Search providers and RSS title matching can therefore return zero before the canonical matcher sees otherwise-valid candidates.
- A year-free retry is attempted only after the year-qualified exact search yields no accepted releases, and only when a premiere year exists. The canonical series/season/episode/adult filter is retained. No change to movie search, direct playback, provider credentials, torrent speed, streaming runtime, or storage.

### Changes / validation

- `stoney_verify/cinema_playback_service.py`: bounded yearless episode-provider retry; do not mask an original no-source result if the optional second request raises.
- `tests/test_cinema_full_platform.py`: coverage for recovery of yearless SxxExx, avoiding a second request on a successful first query, and rejecting a wrong episode after a broader search.
- Current branch vs main: exactly these two runtime/test files plus this active task record. No automatic Discloud deployment.
- Static execution path reviewed; CI tests, full suite, cross-browser live playback, network reachability and Discloud exact-head acceptance still pending.

### Cleanup / conflicts / limitations

- No competing source resolver, new provider type, alternative session model, database migration, startup change or auth workaround.
- This addresses a deterministic query inconsistency; it does not prove why every title reported unavailable in production. Pending live logs could reveal additional provider failures.
- Name/year ambiguity for identically named series, anime numbering conventions, native-script release titles, artwork gaps, and mobile size/codec ranking require separate carefully scoped work after this active task finishes.
- Do not merge into the existing Discloud Auto Deploy branch until reviewed and tested. Do not reset configured sources.

### Suspended work / backlog

- Prior Cinema task: misleading resumed/ready state at low torrent completion and unexpectedly low actual download throughput despite seeds. Preserve its checkpoint; resume only after this task reaches its Definition of Done or authorized FORCE SWITCH.
- Anime metadata/poster source matching (TMDB + identity-verified anime catalog), bounded cached artwork, feed placeholder handling, and mobile-aware automatic source ranking remain backlog, not mixed into this patch.
- Other provider formats surveyed: AniRena/Tokyo Toshokan RSS; Torznab (Jackett/Prowlarr); YTS, EZTV IMDb lookup, APIBay; external/private indexers requiring auth or non-structured HTML are not assumed compatible.

**Next step:** Open draft PR, run exact-head CI and inspect the final diff. After successful validation, arrange controlled promotion to the existing Discloud branch, then test an authorized exact TV episode and verify both first-frame playback and the live no-source diagnostics.

---

## HISTORICAL CHECKPOINT — DANK-SHIELD-GLOBAL-INTERACTION-OUTAGE

**Outcome:** Restore Dank Shield process stability and Discord responsiveness across all guilds. Cinema feature work remains inactive until the shared production process is stable.

**Status:** Current evidence points to a post-ready Discloud aggregate Discord REST shutdown during restart recovery. A focused timing mitigation is in validation; production acceptance is not yet passed.

**Current production evidence:**
- The Oct 7 01:53 boot loads the Discord token and Supabase, binds the site, connects Gateway shard 0, registers commands/views, and reaches healthy on_ready at about 176 MB RSS.
- Departed-member recovery completes across all nine guilds.
- The latest exported runtime log then stops at the first `activity_restart_reconcile` slot acquisition at 01:54:09. The log was downloaded roughly twelve minutes later, so the absence of the normal process-health heartbeat after that boundary is consistent with a hard host termination.
- The repository has a prior confirmed Discloud incident (Sep 21, fix commit `6e6f2bc728027518906b05fa878b4eb41b40f986`) where startup Discord REST recovery caused `App shutdown - Rate limit exceeded: 301/300 req/30s`.
- Current activity-history recovery still defaults to starting only 20 seconds after on_ready, inside the same 30-second provider window as panel/member/invite/startup REST work.
- PRs #471/#472 removed eager libtorrent initialization from bare site startup. Production still went offline afterward, so Cinema/libtorrent startup was a real defect but not the complete outage cause.
- One earlier rebuild also lacked `DISCORD_TOKEN`/Supabase environment values; later boots load them correctly, so that separate deployment-env failure is not sufficient to explain the current post-ready crash loop.

**Active branch:** `fix/defer-activity-recovery-outside-discloud-startup-window-20261007`

**Focused change:**
- Keep Gateway, slash commands, components, live activity listeners, moderation, and the website immediately available.
- Move restart-gap Discord history reconstruction from the 20-second default to 75 seconds after on_ready, outside Discloud's initial 30-second aggregate REST window.
- Keep the existing bounded/single-flight recovery budget and fail-closed activity semantics.
- Document the production value in `.env.example` and regress the startup delay contract.

**Validation gate:** Exact-head CI must be green before merge. After deployment, production must stay online beyond the previous 20–30 second crash boundary, emit process-health heartbeats, then run activity recovery without a Discloud crash. Only after that should fresh `/dank home` and persistent panels be canaried.

**Do not:** rotate the Discord token again, restore the removed custom domain, add duplicate interaction callbacks, or resume unrelated Cinema feature work while this incident is active.

**Next step:** Open the focused PR, run exact-head CI, merge only if green, then inspect the same-SHA Discloud boot through the 75-second recovery boundary.

---

## Historical task notes (retained)

## Active task / outcome

**DANK-CINEMA-CUSTOM-DOMAIN-ORIGIN — keep the Discloud Site origin reachable before Discord ready**

Production baseline:
`main@7fdc3312f16dae869e1ccbe36ef2c66383b0e2c4` (PR #467 merged).

Active branch:
`fix/cinema-site-pre-gateway-bind`

Outcome:
`stoneyverify` must bind the canonical Dank Cinema public listener on `0.0.0.0:8080` during Discord's native `setup_hook`, before gateway/on_ready work can delay the process. This keeps both `stoneyverify.discloud.dev` and the verified custom domain `cinema.the420lobby.com` backed by a live origin while preserving the single shared Dank Shield/Cinema process.

## Scope

- Preserve the existing single `TYPE=site` deployment and canonical media/Cinema runtime.
- Do not create a second Discord bot process or a duplicate Cinema backend.
- Start only the existing `torrent_media_server` earlier in the same event loop.
- Preserve the current `on_ready` path as a retry/fallback.
- Do not change DNS, source selection, playback startup, audio, feeds, or unrelated Cinema behavior.

## Status

**Root cause confirmed from production logs; implementation and regression test are on the branch. Exact-head CI and Discloud canary remain.**

## Findings / root cause

1. Production successfully loads the Discord token and connects shard 0 to the Discord gateway.
2. The expected `🎞️ Torrent media server started on 0.0.0.0:8080` line never appears.
3. `stoney_verify.app.on_ready` is the only production caller of `_start_torrent_media_server_once()`.
4. Therefore the Discloud `TYPE=site` listener is gated behind Discord `on_ready`.
5. Discloud presents the origin as maintenance/offline while port 8080 is unbound, and the process can be recycled before the web listener ever becomes healthy.
6. DNS for `cinema.the420lobby.com` has already verified successfully, so DNS is not the active failure.

## Execution path

Before:
`main.py -> bot.run() -> Discord login/gateway -> on_ready -> start_torrent_media_server() -> 0.0.0.0:8080`

After:
`main.py -> bot.run() -> native setup_hook -> start_torrent_media_server() -> 0.0.0.0:8080 -> command cleanup/gateway -> on_ready fallback`

## Changes

- `stoney_verify.command_runtime._DankCommandOwnerMixin.setup_hook` now starts the existing Cinema/media listener immediately on the Discord client's live event loop, before stale command cleanup and gateway readiness.
- Failures in the early start are logged but do not block Discord startup; the existing `on_ready` path can retry.
- `stoney_verify.app._start_torrent_media_server_once` recognizes an already-ready pre-gateway listener and does not start a duplicate server.
- Added a regression test asserting media bind happens before command cleanup in `setup_hook`.

## Validation / results

- Static execution-path inspection confirms the only prior production start occurred in `on_ready`.
- Production logs confirm Discord gateway connection without any media-listener startup line.
- Regression test added on branch.
- Exact-head GitHub Actions: pending.
- Live Discloud `/health`, custom-domain root, OAuth callback/login, and Android browser canary: pending deployment.

## Cleanup / conflicts

- No second HTTP server, room model, provider stack, Discord client, retry loop, compatibility shim, or duplicate deployment path was added.
- Existing `on_ready` ownership remains only as an idempotent fallback.
- DNS/custom-domain records are intentionally unchanged.

## Blockers / risks

- The branch is not complete until exact-head CI passes and Discloud proves the origin stays online.
- If port 8080 still fails to bind, the next evidence must come from the new pre-gateway startup log rather than another DNS change.

## Backlog

- Blank Audio Track dropdown remains separate.
- Remaining healthy-swarm startup-speed work remains separate.
- Feed artwork enrichment remains separate.
- Transient Cloudflare/origin 502 remains separate unless the same early-origin root cause reproduces it.

## Next step

Open a focused PR, run exact-head CI, repair only evidence-backed failures, then deploy and verify:
1. `https://stoneyverify.discloud.dev/health`
2. `https://cinema.the420lobby.com/health`
3. `https://cinema.the420lobby.com`
4. standalone Discord OAuth login and the existing Discord **Open Dank Cinema** shortcut.

---

## Previous active task / outcome


## Active task / outcome

**DANK-CINEMA-HOME-RESUME — make the Home hero Resume button actually resume the exact saved movie/episode**

Production baseline:
`main@38ad43110e1c1b122332a82ba2b03c0262b97b02` (PR #466 merged).

Active branch:
`fix/cinema-home-resume`

Outcome:
When Cinema Home promotes a partially watched title in the hero, pressing **Resume** must start the canonical standalone playback for that exact saved movie or TV episode and let the Theater restore saved progress. It must not silently route back to Home or require the user to reopen Details first.

## Status

**Root cause is confirmed and fixed on the branch. Focused regressions are added; exact-head CI and production canary remain.**

## Exact root cause

1. The Home backend labeled the hero action **Resume**, but encoded it as:
   `{"kind":"details","media_type":<saved media type>,"tmdb_id":<saved tmdb id>}`.
2. For the production reproduction, the saved item was **The Office S01E02**, whose library media type is `episode`.
3. The client therefore attempted to navigate to `#details/episode/<episode tmdb id>`.
4. The router intentionally accepts Details only for `movie` and `tv`:
   `if (view === "details" && ["movie", "tv"].includes(parts[1]) ...)`.
5. The invalid `details/episode/... ` route immediately fell through to `go("home")`.
6. Because the user was already on Home, this appeared exactly as reported: pressing **Resume did nothing**.
7. The existing `playOnSite()` path already supports `movie` and exact `episode` playback and is the correct canonical path. Episode playback requires `series_id`, season, episode number, and episode TMDB id; progress records already preserve that series identity in metadata.

## Fix

- Home Resume actions are now **direct-play actions**, not mislabeled Details actions.
- Movie Resume carries:
  - `media_type=movie`
  - exact movie `tmdb_id`.
- Episode Resume carries:
  - `media_type=episode`
  - exact episode `tmdb_id`
  - canonical `series_id`
  - season number
  - episode number.
- The client hero handles `action.kind === "play"` by calling the existing `playOnSite(action, resumeButton)` path.
- No duplicate playback API or room path was added.
- Legacy/malformed episode progress that lacks a canonical `series_id` no longer emits a knowingly invalid Resume action.
- Cinema JS asset version is bumped from v16 to v17 so browsers do not keep the broken hero handler cached.

## Validation added

Focused regressions verify:
- movie hero Resume becomes a direct-play action;
- exact TV episode Resume preserves series/season/episode/TMDB identity;
- malformed episode rows do not build an invalid `details/episode/... ` action;
- the client hero routes play actions through `playOnSite()`;
- the updated JS asset version is served.

## Scope / compatibility

Dank Cinema remains cross-browser. This fix uses the same existing website API and normal button/navigation code on Chromium, Samsung Internet, Firefox-family browsers, Safari/WebKit, mobile, tablet, and desktop.

No source-selection, torrent startup, reconnect, audio, feed-artwork, or unrelated Cinema behavior is changed here.

## Backlog / preserved work

- Audio-track selector blank dropdown.
- From Your Feeds canonical TMDB artwork enrichment.
- Transient Cloudflare/origin 502 if it recurs independently.
- Any remaining startup-speed work continues from the already merged first-byte/startup work; this Resume task does not rewrite it.

## Next step

Open a focused draft PR, run exact-head CI, repair only evidence-backed failures, then production-canary the exact Home **Resume** reproduction for a partially watched TV episode and a movie.

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

## PR #450 first exact-head CI failure

Exact head `c442acac070002834da445d109daa08832d84bd9` compiled successfully and ran the full suite, finishing with `2857 passed / 1 failed`.

Failure:
- `test_discord_signed_cinema_link_still_requires_bot_to_share_target_guild` still mocked only the retired cache-only `_bot_guild()` helper, while the production path now uses `_resolve_bot_guild()`;
- the new positive REST guild cache also revealed a real lifecycle edge case: without explicit invalidation, a recently removed bot could remain REST-confirmed in memory for up to five minutes.

Correction:
- canonical `on_guild_remove` now calls `note_cinema_guild_remove()` to drop positive REST guild state immediately and mark the guild absent;
- canonical `on_guild_join` calls `note_cinema_guild_join()` to clear absence and seed positive guild state;
- member verification caches for that guild are cleared on bot removal;
- the signed-link regression now mocks the new tri-state resolver instead of the retired cache-only helper;
- a focused regression proves guild removal invalidates positive REST cache and guild rejoin restores it.

Do not merge #450 until the new exact head completes green.

## Evidence-backed follow-up — /cinema must be guild-installed, not user-installed

Production evidence after merged PR #450:
- Cinema diagnostics report `botGuildRest=absent` and `botGuildCache=no` for the guild route;
- Discord simultaneously allows `/cinema home` to execute in that server;
- browser transport is not the blocker (`cookieHeader=yes`, identity cookie valid, revocation false).

Discord install-context evidence:
- Discord supports apps installed to a user account; those apps can run commands inside servers without the bot being installed as a server member;
- server-installed apps appear as server members and have guild integration context;
- discord.py 2.7 exposes `Interaction.is_guild_integration()` / `is_user_integration()` and `AppInstallationType` on command groups.

Repository root cause:
- `build_cinema_command_group()` did not set `allowed_installs` or `allowed_contexts`, so `/cinema` inherited the Discord application's install defaults;
- if the application allows user installs, Discord can expose `/cinema` through that personal install even though Cinema requires the server-installed bot;
- Cinema then correctly fails REST guild verification because the bot user behind that integration is not a member of the target server.

Active branch:
`fix/cinema-guild-install-context`

Correction contract:
- `/cinema` root group sets `allowed_installs=AppInstallationType(guild=True, user=False)`;
- `/cinema` root group sets guild-only allowed contexts (no DM/private-channel contexts);
- every `/cinema` child has a runtime guard using `Interaction.is_guild_integration()` so stale user-install command copies fail before generating a website link;
- runtime logs record only safe booleans: guild context, guild install, user install, and application/client identity match;
- command payload/fingerprint changes because discord.py serializes integration types, forcing a global command resync;
- `/health` marker becomes `signed-session-v7-guild-install`.

Acceptance:
- exact-head CI and companion workflows green;
- after deploy `/health` reports `signed-session-v7-guild-install`;
- `/cinema` must be offered only through the server-installed app;
- stale/personal `/cinema` invocation must show the explicit server-install requirement instead of opening the site;
- a guild-installed `/cinema home` interaction must generate Open Dank Cinema and the web runtime must resolve that guild through Discord REST.

## Evidence-backed root cause — Discord snowflake rounded by JavaScript Number

Production logs on 2026-10-05 exposed the exact guild-ID corruption:
- real Discord guild from the `/cinema home` signed link and recovery logs: `1514374173517152418`;
- Cinema API/auth logs requested guild: `1514374173517152500`;
- the web runtime then received Discord REST `NotFound`, making `botGuildRest=absent` truthful for the wrong rounded guild ID.

Root cause in current main:
- `_site_html()` serialized `guildId` and `userId` as JSON numbers via `int(...)`;
- `cinema_site.js` declared `CinemaBoot` as `{guildId:number,userId:number}`;
- Discord snowflakes exceed JavaScript's IEEE-754 safe integer range;
- parsing the boot payload as JavaScript Number rounded the last digits;
- `API_BASE = /cinema/${BOOT.guildId}/api` therefore called every SPA API under the wrong guild route.

Why this explains every diagnostic:
- signed query becomes invalid because the signature was issued for the exact guild ID, not the rounded one;
- exact-guild session cookie is missing because its cookie Path is scoped to the exact guild route;
- guild proof is invalid because the API request uses the wrong guild;
- Discord REST returns NotFound because the rounded guild ID does not exist;
- identity cookie remains valid because it is user-only and not guild-route scoped;
- cookieHeader remains yes because cookies in general still reached aiohttp.

Active correction branch:
`fix/cinema-snowflake-string-ids`

Correction contract:
- serialize Discord `guildId` and `userId` as decimal strings in the Cinema boot payload;
- declare both fields as strings in the client;
- build `API_BASE` from the exact string with no Number/parseInt conversion;
- leave ordinary numeric values such as TMDB IDs, seasons, ratings, durations, and hardware counts unchanged;
- client audit confirms the only other Discord-shaped client identifier is `room_id`, already handled with `String(...)`;
- bump Cinema JS asset to `site.js?v=7`;
- unify health and in-page diagnostics at `signed-session-v8-snowflake-safe`.

Regression:
- use exact production-sized IDs `1514374173517152418` and `629459300854661120`;
- assert `_site_html()` emits quoted string values and never numeric snowflake literals;
- assert client typedef/default/API base keep the IDs as strings;
- assert no `guildId:number` or `userId:number` remains.

Acceptance after deploy:
- `/health` reports `signed-session-v8-snowflake-safe`;
- a fresh `/cinema home` link for guild `1514374173517152418` must cause API/log routes to use that exact same ID, never `1514374173517152500`;
- Home/Search/My Stuff/Feeds/Profile must no longer fail from the rounded-guild membership error.

## Production follow-up — Cinema responsive layout and mobile scale

Observed after the snowflake/auth fix succeeded:
- Cinema details now load successfully, proving the auth chain is working;
- on a phone, the whole SPA can render visually shrunken, with tiny header/nav/details content that resembles a desktop page scaled down;
- the mobile bottom navigation is visible, proving the mobile breakpoint is active even while the visual scale is too small.

Scope:
- layout and viewport only;
- do not change auth, playback, Watch Party/Private Session logic, providers, queues, or persistence.

Responsive correction contract:
- keep desktop content capped at 1560px and preserve the desktop multi-column experience;
- preserve tablet breakpoints at 1199px/820px and phone breakpoints at 620px/390px;
- strengthen viewport meta with `initial-scale=1` and `minimum-scale=1` while leaving zoom-in available;
- add `interactive-widget=resizes-content` for supporting browsers so the keyboard can resize layout content cleanly;
- normalize text-size adjustment across mobile browsers;
- guarantee shell/page/grid children can shrink without horizontal min-content blowouts;
- collapse phone details into a readable stacked hero with controlled backdrop height;
- make cast and media rails touch-scroll cleanly;
- make source/feed/error copy wrap anywhere instead of expanding the page;
- make bottom navigation use equal minmax tracks with larger touch targets;
- use a two-column phone result grid and a one-column phone source/feed layout;
- cache-bust Cinema CSS to `site.css?v=4`.

Cross-browser intent:
- same CSS/HTML path for Chrome/Chromium, Samsung Internet, Firefox Android, Safari/iOS, desktop Chrome/Edge/Firefox/Safari;
- no user-agent sniffing and no Samsung-only rules;
- real desktops remain governed by viewport width and the existing 1560px content cap.

Acceptance:
- phone portrait/landscape opens at readable scale and does not look like a miniaturized desktop page;
- header logo/actions and bottom navigation remain thumb-readable;
- details title/poster/overview/actions stack without horizontal overflow;
- Cast and Similar Titles scroll horizontally instead of forcing page width;
- source rows wrap safely;
- tablet layout remains balanced;
- desktop and ultrawide retain intentional multi-column spacing and do not inherit phone stacking;
- exact-head CI and companion workflows green before merge.

## Feed Center results — source manager was not exposing discoveries

Production observation after responsive layout #453:
- Feed Center renders configured source cards, health, Refresh/Edit/Disable/Delete controls;
- the page does not render actual entries discovered by those feeds;
- EzTV can show Online/last refresh while the user has no visible result list.

Repository evidence:
- `refresh_feed()` already resolves real structured/RSS entries and records them through `record_feed_discoveries()`;
- discoveries are already persisted in `dank_cinema_feed_discoveries` with title, source, category, TMDB identity when resolved, poster/backdrop metadata, first_seen_at, and last_seen_at;
- `list_recent_discoveries()` already exists;
- `/api/feeds` previously returned only source definitions plus source-local `newly_discovered` title strings;
- `renderFeeds()` rendered only source-management cards.

Active branch:
`fix/cinema-feed-results`

Correction contract:
- `/api/feeds` returns up to 36 real recent discovery rows under `results`;
- merge freshly refreshed in-memory results ahead of durable results so Refresh updates the page immediately;
- durable results survive restarts through the existing discovery table;
- canonical TMDB-matched results expose title/media type/TMDB ID/poster/backdrop/year/rating/overview;
- unresolved releases still expose real release title/source/category and real seed/file-size stats when available in the current runtime;
- raw playable refs/magnets/torrent URLs are NOT returned in the Feed Center result payload;
- Feed Center renders `Latest Feed Results` above `Sources`;
- TMDB-matched result opens in-app Cinema Details;
- unresolved result routes to in-app Cinema Search instead of an external browser;
- empty state explicitly instructs the user to Refresh an enabled RSS/structured source;
- CSS uses compact responsive result cards: 3 columns desktop, 2 tablet, 1 phone;
- cache-bust site JS to v8 and CSS to v5.

Acceptance:
- refreshed EzTV/RSS/structured source visibly produces result cards in Feed Center;
- previously persisted feed discoveries appear after restart/redeploy;
- result cards show source/category and canonical poster/details when TMDB matching succeeds;
- unresolved releases stay usable through in-app Search;
- source management remains below the results section;
- exact-head CI and companion workflows green before merge.

## Evidence-backed Feed Center defect — static RSS refresh was filtering for `movie`

Production evidence:
- Feed Center results UI is deployed and visible;
- EzTV reports Online and a fresh timestamp but `Latest Feed Results` remains empty.

Code-path proof:
- EzTV is configured as a static RSS/Atom source;
- `refresh_feed()` defaulted Custom-category refreshes to the literal query `movie`;
- `_search_url(..., static_feed=True)` fetched the static feed URL itself;
- `_read_structured_items_limited()` passed the same `movie` query into `_extract_feed_items()`;
- `_extract_feed_items()` rejected every RSS item whose title did not match `movie`;
- zero matches returned `variants=()` with no error, so Feed Center marked the source Online while recording zero discoveries.

External documentation check:
- EZTV documents `https://myrss.org/eztv` as an RSS feed intended for clients to automatically download all published items;
- the resolver already supports EZTV-style magnet URI and `.torrent` references.

Active branch:
`fix/cinema-feed-refresh-unfiltered-rss`

Correction contract:
- static RSS/Atom Feed Center refresh with no explicit query fetches the endpoint unchanged and ingests all playable entries;
- blank query means no title filter for RSS/Atom extraction, while a real explicit query still filters titles;
- structured JSON search sources keep their category-based default query behavior (`movie`, `tv`, `anime`, `documentary`);
- source search during movie/details discovery remains query-filtered and unchanged;
- Feed Center source cards expose exact playable-result count for the most recent refresh;
- if a feed is reachable but contains zero playable magnet/.torrent entries, show that explicit warning instead of only `Online`;
- cache-bust Cinema JS to `site.js?v=9`.

Regression:
- static feed URL accepts blank refresh query;
- a two-entry RSS fixture with titles that do not contain `movie` returns both entries for blank refresh and zero for explicit `movie` filter;
- Feed Center RSS refresh passes an empty query;
- JSON Movies refresh still passes `movie`;
- UI renders result count and discovery warning.

Acceptance after deploy:
- Refresh EzTV;
- source card must report a truthful playable-result count;
- when playable feed entries exist, Latest Feed Results must populate;
- if count is zero, the card must explicitly say the reachable feed returned no playable magnet/.torrent items, which moves the next investigation to feed payload compatibility rather than pretending discovery succeeded.

## Feed Center usability follow-up — search, pagination, and poster enrichment

Production observation after #455:
- Feed Center now ingests and displays EzTV RSS results correctly;
- result list has no Feed Center search UI;
- result list has no pagination and grows as one continuous page;
- TMDB-matched titles such as CIA/FBI show posters, while unresolved releases such as `Collision 2026 S01E21 ...` render without artwork and fall back to Search in Cinema.

Evidence:
- `search_discoveries()` already exists and general Cinema search already uses feed discoveries, but Feed Center never exposed it;
- Feed Center API previously loaded a bounded recent batch only, with no page/offset/count contract;
- `Collision (2026)` is a real TV title with TMDB identity 331033, so at least some blank Collision cards are enrichment misses rather than unavailable artwork;
- episode-style release names with a year were searched as e.g. `collision 2026`; the resolver did not force TV for Custom-category `SxxExx` releases or retry a year-stripped title.

Active branch:
`fix/cinema-feed-search-pagination-enrichment`

Current status:
- PR #456 is the single active implementation task and remains draft;
- exact-head CI previously reached 2870 passed / 1 failed;
- the only failure was the Feed Center provenance copy regression;
- current head restores the original real-discovery provenance sentence while preserving the new result count/search/pagination;
- branch is 0 behind main and mergeable;
- PR #457 (EZTV/provider identity routing) is suspended until #456 reaches its Definition of Done.

Expanded RSS / Feed Center contract:
- Latest Feed Results groups duplicate releases by canonical title/episode and compares source, quality, codec, seeds, size, release group, and first/last-seen history;
- My Feed combines personal Feed Rules, watchlist matches, private-feed discoveries, quality upgrades, new episodes, and queue suggestions;
- personal Feed Rules support followed titles, actors/creators, genres, studios, saved searches, filters, collections, routing, and alert modes;
- guild-scoped Collection/Route rules require Manage Server; personal rules remain owner-only;
- user-private RSS/Atom and structured JSON sources reuse the existing safe source validator/resolver and persist only to service-role-only private discovery storage;
- private discoveries never enter the guild-wide discovery table;
- user preferences persist playable-only behavior, minimum seeds, preferred resolutions/codecs/languages, feed alert mode, and queue-suggestion preference;
- new-episode/feed-match alerts use the existing Cinema inbox with dedupe and instant/daily/off behavior rather than unsolicited DMs;
- source trust is derived from durable refresh success/failure and playable-result history;
- Add to Queue uses the canonical MovieNightRoom queue and exact source resolver, and only succeeds for the current signed-in host of an active room;
- Feed Rules include title, franchise, actor/creator, genre, studio, saved search, filter, collection, routing, and private-source matching;
- release filters include playable-only, minimum seeds/peers, resolution, codec, language, HDR, subtitles, excluded terms, source/category, and minimum/maximum file size;
- shared and private structured feeds participate in a durable bounded auto-refresh scheduler (15-minute default, 5-minute floor, paced requests, bounded batches); private refresh remains owner-isolated;
- raw magnet/torrent/source refs remain excluded from Feed Center and personalization payloads;
- a new service-role-only migration owns Feed Rules, private discoveries, and durable source health; Cinema SQL CI replays and audits both Cinema migrations.

Correction contract:
- database-backed Feed Center pagination uses PostgREST exact count plus range/offset, default 8 results per page and max 24;
- search covers both canonical discovery title and `metadata.release_title`, so `FBI`, `Collision`, or `S01E21` can match;
- `/api/feeds` accepts `q`, `page`, and `page_size` and returns a pagination object with total/pages/previous/next;
- Feed Center renders a search field, Search/Clear controls, total result count, and Previous/Page/Next controls;
- episode-looking releases (`SxxExx` or `NxM`) force TV matching even when source category is Custom;
- episode discovery tries a year-stripped query first (e.g. `collision`) and then the original cleaned query (`collision 2026`);
- unresolved persisted discoveries receive a bounded TMDB enrichment retry (max 4 per page, 30-minute cooldown), and successful poster/title/TMDB matches are persisted;
- genuinely unresolved cards receive an intentional branded placeholder instead of an empty image hole;
- cache-bust Cinema JS to v10 and CSS to v6.

Acceptance:
- Feed Center search can find by canonical title and release token;
- eight-result pages expose truthful total and navigation;
- page navigation queries storage instead of client-slicing a fixed recent batch;
- Collision-style episode releases can resolve as TV and gain TMDB poster/details when metadata exists;
- existing general Cinema search, source refresh, playback refs, and source management remain unchanged;
- exact-head CI and companion workflows green before merge.


## ACTIVE — Cinema Library Intelligence / bot-native Trakt-style experience

Production baseline:
`main @ 3e86f4ffd701fc1801bc492331393c23d84b8bd3` (PR #456 merged)

Active branch:
`rebuild/cinema-library-intelligence-clean`

Active PR:
`#459 — Build bot-native Cinema Library intelligence`

Isolation note:
- PR #458 was closed after a separate standalone website-playback task was interleaved onto its branch.
- The full mixed state is preserved at `backup/cinema-library-standalone-interleaved-20261006`.
- PR #459 contains only the Library intelligence task.

Single active task:
Build a bot-native Cinema Library intelligence layer. This is intentionally scoped to activity that happens inside Dank Cinema/Dank Shield; no cross-platform scrobbling or external Trakt-style account synchronization.

Ownership contract:
- Cinema Library owns durable user state: watch history, progress, watched/unwatched, watchlist, favorites, ratings, custom lists, rewatch counts, stats, and recommendation signals.
- Dank Theater remains the only playback/progress authority and writes Library activity through one service path.
- TMDB remains canonical identity/metadata and supplies seasons, episode dates, genres, cast, studios, franchises, and recommendation candidates.
- Feed Center remains availability/release intelligence and overlays whether a Library/recommendation/upcoming item currently has playable discoveries.
- My Feed continues to own feed-driven notifications; Library may supply watchlist/history/rating/favorite signals to personalize those matches.
- Notifications remain inside the existing Cinema inbox; no unsolicited DM behavior is introduced.

Implementation goals:
- durable first-watch / last-watch / completion / rewatch tracking without writing a history row on every heartbeat;
- favorites and 1–10 ratings on canonical movie/TV items;
- custom personal lists with ordered items;
- manual mark watched / unwatched while preserving canonical progress behavior;
- richer My Stuff views: Continue Watching, Watchlist, Favorites, History, Watch Again, Lists, Stats;
- Because You Watched and Recommended For You rails derived from Dank Cinema activity only;
- upcoming / next-episode intelligence from TMDB for followed/watchlisted/in-progress TV;
- Feed availability overlay for watchlist, recommendations, and upcoming episodes;
- new-episode availability stays feed-driven, not air-date-driven;
- watch-party/private-session context recorded in watch events for stats;
- group recommendation support must be derived from active room participants without exposing one user's private Library to another user;
- mobile-first UI remains usable on Samsung Browser while desktop/tablet/ultrawide remain first-class.

Definition of Done:
- migration is idempotent and service-role-only;
- existing progress/watchlist contracts remain backward compatible;
- exact-head CI + Cinema SQL + companion workflows green;
- no raw playback/source refs added to Library payloads;
- existing Feed Center and Theater ownership boundaries remain intact.
