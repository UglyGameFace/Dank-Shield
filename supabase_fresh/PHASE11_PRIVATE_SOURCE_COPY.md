# Phase 11: safe private source state copy (NOT cutover)

**Source:** original, live shared Supabase project `hyncpbvqyjojxhjzcdzg`, read-only. **Destination:** offline dedicated `viamriafkbqhquvrsnua`. This is one slice of the sole Dank Shield migration task. No Discloud/production environment edits, source deletes or public exports.

Owner read-only [source audit run #39485389338](https://github.com/UglyGameFace/Dank-Shield/actions/runs/39485389338) found on October 20 2026 **one** old moderation warning, **7** global private profile rows and **147** per-guild private profile override rows. All three corresponding tables are installed and still empty on the dedicated candidate. The schema migration itself did not carry their data.

The explicit manual tool `tools.stage_dankshield_private_source_state` has a hard **three-table allowlist**, exact old and new project identity validation, full column name/type/nullability and primary key verification at BOTH ends, target RLS and public privilege checks, empty-target requirement, maximum source row and total byte bounds, and an atomic destination transaction. It reads the old source using a repeatable-read **read-only** transaction. Its destination insertion preserves original UUID/Discord IDs, private platform JSONB, global/user/server settings, warning reason and timestamps without translating or inventing opt-in data. It rereads destination rows and verifies a full-record SHA-256 comparison **before commit**, but prints only the named-table row counts, not fingerprints or original data. On mismatch, transaction rollback.

This intentionally **excludes** `dank_live_profile_cards`: durable message IDs can only be restored after verifying each Discord message is still present and bot-authored. Also excluded: shared `audit_events` and `audit_logs` of unproven ownership, 531 operation jobs with potentially expired/unsafe leases, old invite-block events, unrelated Idle Grow Op records, moderation staff KPI history and all Cinema/Hub session state. A successful stage run is point-in-time, not a write freeze or latest-state guarantee.

## Owner workflow (Android-compatible)

First add a private **GitHub Actions repository secret** `DANK_FRESH_DATABASE_URL`, using the new dedicated Supabase **Postgres** connection string (database name `postgres`, port `5432`, SSL required; direct host or Supabase session pooler). **Never paste this URL/key into chat, a PR or logs.** Existing `DANK_SOURCE_DATABASE_URL` source secret stays unchanged. The program separately pins original `hyncpbvqyjojxhjzcdzg` and dedicated `viamriafkbqhquvrsnua`, rejecting a URL for the wrong database. New target needs an actual Postgres database URL, NOT an API URL or `anon`/service-role REST key.

After the PR's exact head CI passes and the PR is merged:
1. GitHub repo Actions → **Dedicated Supabase Private State Copy (manual)** → Run workflow on `main`, mode **preview**. No data writes. Confirm it shows only counts and `PREVIEW_ONLY`.
2. Verify its three per-table source counts match the expected current state. The original source can change, so do not require hardcoded older counts.
3. Start a **separate**, explicit manual workflow: mode **stage**, confirmation `IMPORT_DANKSHIELD_PRIVATE_SOURCE_ROWS_ONLY`.
4. Confirm output `COPIED_AND_VERIFIED`. No private value should appear. Independently inspect dedicated candidate counts. If stage fails, DO NOT loosen validation or re-run blindly; investigate privately.

**Important limitations:** The source is still LIVE and writers may append/change rows after this snapshot. Final Delta reconciliation/cutover verification is mandatory; source and dedicated projects must coexist until every required feature is proven. Zero real production credential changes are authorized. A dedicated independent backup with successful off-host restore and canary is also mandatory before cutover.

**Branch policy:** one temporary `migration/dankshield-private-source-copy-20261020` branch, subject to guarded nightly post-merge retirement. No mass branch deletion or recovery-branch pruning.
