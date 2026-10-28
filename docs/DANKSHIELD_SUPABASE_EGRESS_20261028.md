# Dank Shield Supabase egress incident, 2026-10-28

## Owner objective

Stop exhausting the fresh dedicated Supabase project's **5 GB free uncached egress
quota**, without sacrificing live member truth, moderation, bot commands or
durable historical evidence. No paid upgrade, account recreation, or erasure.

## Evidence from project viamriafkbqhquvrsnua (read-only)

- Cycle 2026-10-20 to 2026-11-20: **6.72 GB of uncached egress**
  as seen in the dashboard 2026-10-28. This is network transfer,
  **not storage**; the live database was about 96 MB.
- One 24-hour edge-log window contained **103,314 requests**. The busiest:
  bot_commands GET 28,882; guild_configs GET 17,219; activity
  heartbeat POST 13,165. This is HTTP request volume, not billed bytes.
- A further 24-hour query of individual full-config GETs by guild showed
  a largest current full row of ~460 KB requested 2,939 times. Multiplying
  current serialized sizes by observed request counts for nine guilds
  gives ~1.55 GiB of **uncompressed potential config response volume**,
  not an exact billing measurement. Many responses omit Content-Length.
- That row repeats **114 KB** of JSON in each of settings/config/metadata/meta.
  Large nested keys include invite evidence hashes and a base64 welcome image.
  **Six of twelve guild rows have differing JSON fields.** Merging or deleting
  these fields without separate parity and recovery evidence risks data loss.
- The shared guild-config reader previously used select('*') and a 60 s cache;
  forced refresh bypassed the cache. That combination can retransmit the same
  entire configuration thousands of times.
- Direct SQL pg_dump/COPY activity is also present in pg_stat_statements;
  service-level egress attribution remains needed to explain the entire bill.

## Implementation and compatibility

1. Add a SECURITY INVOKER, service-role-only `get_dank_guild_config_compact`
   SQL RPC. It coalesces the four JSON fields in the **existing precedence**
   (settings, config, metadata, meta; flat columns override) and transfers
   their contents only once. All stored rows and columns remain untouched.
2. The RPC compares the guild's `updated_at` with a supplied known revision,
   returning a tiny `_not_modified` sentinel on a match. A first read and
   any changed revision still return the complete configuration.
3. The authoritative Python guild-config reader uses this RPC when installed,
   retains existing fallback for missing/broken RPCs, and continues to use
   current cache invalidation on saves. The previous 60-second TTL is
   unchanged. Forced refreshes still consult the database; they just avoid
   re-downloading unchanged large rows.
4. The 5-minute metrics worker keeps checking guild state, but identical
   role and staff snapshot upserts are suppressed in process, with an
   hourly authoritative resync. Failed writes are **never** marked synced.
5. Existing `guild_config_cache_snapshot` now exposes cumulative
   `cache_hits`, `compact_full_reads`, `compact_not_modified`,
   `legacy_full_reads`. The metrics worker logs them and the count of
   unchanged upserts skipped. This logging performs no additional DB query.

**No bot queue polling cadence, member-activity heartbeat interval, backend
authority, invite history, welcome image, or Discord permission is changed.**
Do not raise config cache TTL as a shortcut: immediate setup updates and
multi-process correctness still matter.

## Installation and release gate

1. Check that the configured production Supabase project matches the dedicated
   DankShield ref. Keep the previous deployed revision available as rollback.
2. Validate the SQL migration and its grants against the exact target project.
   Install `supabase_fresh/migrations/202610280001_dankshield_compact_guild_config_rpc.sql`
   through a reviewed migration, not a destructive `UPDATE` on guild configs.
   Function-only DDL is reversible; the stored data is never rewritten.
3. On a test or production service-role session, compare output for a guild
   with divergent JSON settings to the legacy merge, including nested keys,
   configured IDs, welcome-card bytes, and `updated_at`.
   Call again with the revision and verify `_not_modified`.
   Confirm anon/authenticated cannot execute the RPC.
4. Deploy the bot build containing the new reader and verify ordinary,
   forced, write-invalidated and RPC-outage paths. Observe the local counters
   showing compact reads and revision hits. In a rollback, old readers still
   work because no columns were removed.
5. Check Supabase Observability's **unified egress by service** plus the
   organization's billing page at 1h and 24h; compare against the pre-change
   measurements for the *same kind of window*. Count config GET and RPC calls,
   response latency, failures, and paid/unpaid bytes. Edge logs alone do
   **not** reliably give network bytes, especially for compressed responses.
6. Require stable bot responsiveness, correct guild-isolated configuration,
   working verification/tickets/Share Router/Invite Shield, and no increase in
   database errors before declaring production accepted.

## Operational guardrails

- Dashboard: https://supabase.com/dashboard/project/viamriafkbqhquvrsnua/observability
- Billing and usage: https://supabase.com/dashboard/org/tyqtsscyfhtjzjvkmefv/usage
- Guide: https://supabase.com/docs/guides/platform/manage-your-usage/egress

Watch **egress GB per day**, not just query counts. The 5 GB / 31-day
allowance averages about 0.16 GB/day. Before claiming sustainability, require
a 24-hour observed slope under this rate with headroom for traffic growth,
and alerts at 50%/75%/90% monthly usage. The overage already accumulated
in this billing cycle is not erased by software changes.

## Remaining planned work (NOT silently executed)

- Independently restore and verify source + dedicated guild-config histories,
  then reconcile the six differing four-JSON sets and transition writers to
  one authoritative JSON field, preserving reads and rollback.
- Move large welcome-card base64 assets to dedicated private storage with
  content hashes and lazy fetch, without leaking URLs or breaking upload,
  rendering or legacy config reads.
- Replace the Invite Shield legacy hash-array fallback *only after* proving
  the durable invite event ledger has equivalent coverage and recovery.
- Optimize other high-count worker polls with reliability-sensitive change
  signaling or adaptive cadence only after profiling; leave short-latency
  dashboard command and activity truth workflows intact meanwhile.
- Implement per-service usage alert delivery from an authorized billing/
  observability data source; local query counts are not billed egress bytes.
- Continue the separate old-source/Discloud PostgreSQL backup parity and
  migration gate; this optimization does **not** authorize cutover or deletion.
