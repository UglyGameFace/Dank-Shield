# Phase 18: independently restore dedicated Dank Shield's encrypted candidate snapshot

**Original live Supabase:** `hyncpbvqyjojxhjzcdzg`. **Dedicated OFFLINE Supabase:** `viamriafkbqhquvrsnua`. **Production Discloud:** unchanged.

After PR #669 merged with six green checks, original and offline project data still match across all previously verified durable tables. The 4,756 activity-feed records preserve original 1,825 unequal meta/metadata pairs, 3,369 staff action references and 632 member event references. There are zero current open operation jobs, pending commands or live verification tokens. The original still contains **2 PENDING** and **1 STAFF_ACCEPTED** voice-verification sessions; DO NOT cancel/reissue them as a migration side effect. The owner opted not to import original external invite-block event history; shared audit_events/audit_logs ownership remains unproven.

## Manual encrypted recovery, no cutover

The owner-only `.github/workflows/dankshield-candidate-restore-drill.yml` runs on exact `main` ONLY via `workflow_dispatch`, with confirmation `RESTORE_DEDICATED_CANDIDATE_ISOLATED_ONLY`. It requires already configured secret `DANK_FRESH_DATABASE_URL` and `DANK_BACKUP_PASSPHRASE`. The workflow must NOT be started by automatic CI or untrusted PRs.

It uses the prior security-reviewed `stoney_verify.source_pg_backup.create_backup()` (which validates pinned project identity, PG17 backup clients, AES-256-GCM authenticated streaming, input/output bounds, no plaintext archive saved) to encrypt a fresh **dedicated candidate** snapshot. It then uses the pre-existing `tools.verify_source_backup_restoration.run_restore_drill()` to:
- Authenticate the **entire** encrypted archive and validate PostgreSQL TOC before SQL restore.
- Restore ONLY into an empty, fixed-name PostgreSQL 17 database on a separate **disposable GitHub Actions** `supabase/postgres:17.6.1.136` service.
- Suppress restored SQL in service logs, raw errors and command output; refuse remote/default production restore targets.
- Compare row counts and full source `to_jsonb(row)::text` hashes in **14 important tables** against the independently recovered copy: guilds, guild members, security actors, tickets, member events, staff evidence, activity feed, global profile preferences, Cinema saved media, Community Hub settings, sticky and quiet notices, and both **empty** execution queues.
- Fail closed on any missing critical table, change during backup, identity mismatch, restore error or count/hash mismatch.
- Upload ONLY the **encrypted** `.dsbak` file to a short-lived (7 day) GitHub Actions artifact after proof of restore. Never upload SQL/plaintext, raw credentials, or the original source. The owner must download this encrypted artifact to durable **private** storage, retaining the passphrase in a separate location. Expired GitHub artifacts are not backups. No user needs to send a passphrase to ChatGPT.

### What the drill proves, and what it does not

A passing actual owner-triggered run will prove an encrypted dedicated candidate SQL snapshot could be independently restored and compared in 14 critical tables. It will **not** prove full cloud-managed Storage, OAuth/secrets, Supabase project settings, network access/roles/grants (pg_dump uses `--no-privileges`), user-file backups, or bot behavior. Those require separate checks. The backup is a point-in-time snapshot; future source/destination changes must be caught by the read-only delta audit after original writers are quiesced.

The SQL backing service must never be Discloud or either existing Supabase project. No secret should be logged. No source data deletion, Discord actions, Discloud service config, production cutover or automatic branch deletion is performed.

## Final blockers to owner-authorized production switch

1. Owner-triggered recovery workflow successful with **off-runner durable encrypted copy** and separate key.
2. Source writer freeze and exact same-time delta audit (34 critical tables).
3. Authoritative staff disposition of the 3 voice verification sessions and any other live tokens/sessions; no fabricated completion.
4. Production auth/API permissions and network checks against new Supabase; do not blindly grant public table read/write access.
5. Test selected low-volume real Discord guild commands/features on target in a reversible canary, including tickets, verification, member tracking, moderation/invite protection, Community Hub, Cinema, profiles, and side effects.
6. Explicit owner approval to switch Discloud, then monitor and retain old DB for rollback. **100% and 10k–500k-guild load readiness not verified.**
