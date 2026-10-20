# Phase 15: staff moderation evidence and safe member cleanup defaults

**Original shared Supabase:** `hyncpbvqyjojxhjzcdzg`, live source and still authoritative.
**New dedicated Supabase:** `viamriafkbqhquvrsnua`, OFFLINE candidate only.

Current source audit (2026-10-20) confirms **3,369** staff_action_logs moderation-history rows with distinct bigint IDs, all associated with known guilds, with source row JSON totaling **18,723,875 bytes** (~17.9 MiB). The oldest entry dates to March 22, 2026; most recent entry May 2, 2026. All are older than 90 days, but that is **not permission to erase security evidence**. Their reason, actor, target, metadata and original timestamps must be preserved. A source-only `member_cleanup_settings` table has zero records and still matters to the bot's cleanup settings service; the safe empty/default contract requires confirmation and disallows low-confidence queue automation.

The source-shaped `public.staff_action_logs` and `public.member_cleanup_settings` tables are installed on the dedicated candidate with private RLS, backend-only service-role CRUD, no public permissions and no automatic TTL or pruning. No source rows are copied merely by applying this PR or its migration.

A **manual, owner-only** GitHub workflow in `.github/workflows/dankshield-staff-evidence-transfer.yml` is provided after this PR merges. It uses existing GitHub Actions repository secrets `DANK_SOURCE_DATABASE_URL` and `DANK_FRESH_DATABASE_URL`; never paste either in a chat. The action runs only on `main` for repo owner `UglyGameFace`, with two distinct modes:
1. `preview`: connects to pinned original/new databases, creates repeatable-read source read-only snapshot, validates old/new 11-field schema, UUID/bigint IDs, source PK uniqueness and guild ownership, protects privacy and checks the **candidate must be empty**. No row writes.
2. `stage`: separately entered exact confirmation `COPY_DANKSHIELD_STAFF_EVIDENCE_TO_OFFLINE_ONLY`. In a single destination transaction, captures bounded source `to_jsonb(x)::text` in original PostgreSQL (not JS Number), inserts all staff evidence, then compares the exact full serialized text SHA-256 **before committing**. Any mismatch rolls back. Logs ONLY source row count and success status, no moderation content.

This is a point-in-time preservation, not a production cutover. The original DB remains untouched and cannot be deleted yet. No old Discord actions are executed. Even after this copy the dashboard/current feed, missing legacy tables, voice/Cinema lifecycle, late source updates, backup/restore, guild-scale canary, and owner-approved Discloud cutover remain blockers.

**Branch policy:** one temporary PR branch `migration/dankshield-staff-evidence-20261020`, eligible for guarded nightly retirement only after exact-head merged PR.
