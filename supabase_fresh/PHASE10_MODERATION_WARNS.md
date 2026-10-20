# Phase 10: source-confirmed warning schema

**Only one task:** dedicated Dank Shield Supabase migration. The owner-approved original-source metadata audit [run #39485389338](https://github.com/UglyGameFace/Dank-Shield/actions/runs/39485389338) passed at 2026-10-20 19:40Z.

The source `public.warns` has **one current row** at snapshot time and these **seven** source columns:
`id uuid PRIMARY KEY DEFAULT`, `guild_id text NOT NULL`, `user_id text NOT NULL`, `username text NULL`, `reason text NOT NULL`, `source_message text NULL`, `created_at timestamptz NULL DEFAULT`. Defaults are not disclosed by the metadata audit; this migration uses standard Postgres `gen_random_uuid()` and `now()`, with exact source names, types, PK and nullability. No unproven source CHECK/trigger assumptions are made.

The bot `stoney_verify/modlog.py::_sb_select_warn_count_sync` counts by `guild_id` and `user_id`. The new `202610200020_dankshield_warns.sql` restores the missing table with a composite count index, RLS and backend-only CRUD, never auto-expiring moderator evidence. Separate rollback-only SQL smoke proves service-role behavior and isolation. **Actual source warning is NOT imported:** it must later transfer securely with original UUID, guild/member scope, reason, source_message and timestamp under a verified live-source snapshot/delta check. Do not emit such details to GitHub or chat.

**Other observations from audit:** source `staff_metrics` has 34 rows and 9 columns, with nullable metrics/name fields; new candidate `staff_metrics` currently has 11 columns and stricter NOT NULL checks. Historical import must handle that divergence explicitly rather than assume the 34 records will import untouched. The source `audit_events` (84 rows) and `audit_logs` (17 rows) have no `guild_id`; `audit_logs` additionally has `token` fields. Since source Supabase is shared with Idle Grow Op, do NOT claim those records belong to Dank Shield, import or print them before proving application ownership. `raid_events` is a different 7-column original schema and had **zero rows**; no runtime dependence was found requiring it in this warning-count slice.

There are 9 source Hub guild settings, 147 source profile guild overrides, 7 source global profiles, 13 Cinema watch sessions, 10 saved media rows and 531 operation jobs. State import, consent preservation, session recovery, evidence isolation, storage/egress/load checks, backups and real Discord canary are all still pending. No Discloud, source DB, cutover credentials or other bot data changed.

**Branch safety:** one scoped temporary `migration/dankshield-warns-parity-20261020` branch, use guarded post-merge nightly retirement; do not delete historical recovery or production-linked refs.
