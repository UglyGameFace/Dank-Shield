# Phase 8: Dank Cinema private Library and Feed database parity

**Only the new dedicated, offline Supabase candidate** `viamriafkbqhquvrsnua` is targeted. The shared original Supabase remains the running bot's authoritative store, with no Discloud deployment or secrets changed.

Migration `202610200019_dankshield_cinema_library.sql` restores the four canonical migrations, in **their original dependency order**:
1. `supabase/migrations/20261004123000_dank_cinema_library.sql`
2. `supabase/migrations/20261006031500_dank_cinema_feed_personalization.sql`
3. `supabase/migrations/20261006050000_dank_cinema_library_intelligence.sql`
4. `supabase/migrations/20261006051500_dank_cinema_standalone_playback.sql`

Together they define **10 backend-owned RLS tables**: Cinema user preferences, user media watchlist/progress/favorites/ratings, guild source discoveries, notifications, feed rules, private feed discoveries, source health, movie sessions (standalone/private/watch-party), custom lists and list items. No new download/torrent provider, playback UX, network port or background worker is introduced.

All table grants are explicitly narrowed to backend `service_role` SELECT/INSERT/UPDATE/DELETE; no public, anonymous or normal authenticated access, no table TRUNCATE permission. No new RPC is needed for this specific source feature. The rollback-only smoke validates schema, RLS and permissions, member progress idempotent upsert, standalone playback persistence, notifications, source records, user collections, and cross-guild boundaries, with `SET LOCAL ROLE service_role`.

**Critical gap: actual Cinema data is not yet migrated.** The candidate's 10 new tables deliberately start empty. The original source's user watch history, watchlist, positions, notifications, media ratings, source/feed configurations, provider health, private-feed rules, and active session state must be inspected independently. Do not silently reset a Cinema user's preferences or playback position. Before switching credentials, verify original source per-table counts/fingerprints; import only the current relevant records, reconcile writes since snapshot, and explicitly decide which active playback leases can be resumed or should be gracefully expired. No client cookies, signed OAuth state or private provider credentials should be imported as ordinary library records.

**Still missing for full production cutover:** moderation `warns` source truth, Community Hub configuration and live sessions, private Profile Cards consent data, staff KPI history, later Invite Shield totals, other feature contracts, source-to-candidate parity and retention correctness, backup/restore, quota/egress and a small Discord canary. Legacy full invite-block events and unrelated Idle Grow Op data are intentionally excluded. Existing confirmed AntiNuke bad actors must be preserved.

**Branch hygiene:** single scoped `migration/dankshield-cinema-library-20261020` branch, guarded post-merge retirement after exact GitHub CI. Do not delete production/backup branches and do not treat a PR merge as a production database cutover.
