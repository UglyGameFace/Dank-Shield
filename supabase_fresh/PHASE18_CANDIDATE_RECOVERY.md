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


## Actual GitHub run failure and diagnostic hardening (2026-10-21)

The owner's first dedicated-candidate restore execution [run 39538439329, job 119020402982](https://github.com/UglyGameFace/Dank-Shield/actions/runs/39538439329/job/119020402982) failed at **step 9: Encrypt dedicated candidate dump and prove isolated restore**. GitHub confirms steps 1–8, including the disposable Supabase PG17 service, pinned main checkout, PG17 CLI adapters and private database logging checks all passed. The runner emitted `DEDICATED_RECOVERY_DRILL_FAILED: RecoveryDrillError` before uploading any encrypted archive. This **does not certify recovery**, and the available logs do not identify which particular protected restore failure occurred.

The pre-existing `tools.verify_source_backup_restoration.RecoveryDrillError` already uses fixed, credential-free `code` values. Unfortunately, the outer candidate wrapper discarded them and logged only the exception class. The focused hotfix restores **strict allowlisted** diagnostic codes and code-owned phases (`reading_candidate_manifest`, `creating_encrypted_backup`, `authenticating_and_restoring_to_disposable`, `comparing_restored_records`). Unexpected error content, raw SQL details, user IDs, actor data, connection URLs and passwords remain suppressed. Tests assert that sensitive arbitrary exception strings cannot appear in GitHub output.

**Do not rerun the same failing workflow before this merged diagnostic hotfix**, since it will still suppress the actual recovery cause. On the next guarded owner-run, inspect its allowlisted `DEDICATED_RECOVERY_DRILL_FAILED:` code. If the classified code points to a PG17 extension, role or schema incompatibility, fix that exact problem without broadening source access or skipping the full restore/checksum gates. A diagnostic-only passing PR is NOT proof the encrypted backup or restore succeeded. The old source and Discloud are unchanged.

The hotfix also classifies duplicate objects, unique conflicts, foreign-key errors and absent schema dependencies into safe fixed categories rather than printing raw pg_restore stderr. This remains diagnostic, not permission to ignore failed recovery or drop database objects blindly.


## Follow-up failed restore and safe object diagnostics (2026-10-21)

Owner-triggered [run 39545281507, job 119041952947](https://github.com/UglyGameFace/Dank-Shield/actions/runs/39545281507/job/119041952947) completed the candidate manifest read and encrypted backup generation, then failed when restoring SQL to the disposable PG17 target. The classified error was `pg_restore_sql_failed_unclassified`. Authentication/TOC checks passed, but **no full restore or critical row comparison passed**, and the encrypted archive upload was skipped.

The source-side candidate reports PostgreSQL 17.11, with 75 public tables and managed Supabase objects in other schemas. Supabase documents that raw `pg_dump` can include platform-managed objects that are difficult to replay into another instance. This is a **possible explanation, not a diagnosed SQL statement**; do not exclude managed objects or relax the atomic restore gate based only on this hypothesis.

The next safe diagnostic reports a whitelisted `DEDICATED_RECOVERY_FAILED_TOC_KIND` (for example, `TABLE DATA`, `SCHEMA` or `EXTENSION`) if `pg_restore` includes a recognizable TOC entry, and recognizes additional fixed SQL failure categories (transaction, permission, dependency, collation and data constraints). It must never print source/table/role names, raw SQL, SQL errors, secrets or TOC entry numbers. `unknown` remains an allowed diagnostic response, **not permission to assume a cause or mark restore complete**. The backup/restore isolation, verification and ciphertext-only artifact gate are unchanged.

The next action is an owner-only rerun **after** this diagnostic PR is validated and merged. Fix the specific SQL restore incompatibility disclosed by the next failure category and TOC type, then rerun to prove a genuinely successful restore before any production cutover. Preserve this one active recovery task; do not start unrelated cleanups during it.


## Third failed owner drill: no TOC cause revealed (2026-10-21)

Owner-run [39590159207 / 119188823728](https://github.com/UglyGameFace/Dank-Shield/actions/runs/39590159207/job/119188823728) again passed manifest collection, encrypted backup creation, and authenticated archive checks, then failed the disposable restore. Logged results:

- `pg_restore_sql_failed_unclassified`
- `DEDICATED_RECOVERY_FAILED_TOC_KIND: unknown`

**An `unknown` TOC kind plus the broad legacy error classifier does not prove a failed SQL operation.** The previous classification marked any generic client `error` as failed SQL, incorrectly conflating network, archive and process failures. The actual failure is not yet determined.

This targeted follow-up adds an explicit `postgres:17` Docker-to-disposable-database connection preflight, separate allowlisted client transport/archive/invocation vs SQL error categories, and an integration regression job that creates a synthetic, private-data-free custom PostgreSQL archive, authenticates an AES-GCM encrypted copy, streams it through the same isolated restore path and verifies the synthetic row. The container is the same pinned disposable `supabase/postgres:17.6.1.136` target as the owner workflow.

**Required validation:** A successful synthetic CI path distinguishes local environment wiring from candidate-specific SQL objects but does not certify the live candidate archive. Do not drop schemas, skip rows, relax strict transaction/error handling, or cut over production. Wait for all PR checks to pass; then rerun only the owner-only isolated candidate drill on `main`, interpret the new concrete result, and fix its proven root. Retire the temporary branch after merge.
