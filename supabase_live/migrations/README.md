# Production DankShield migrations after the database cutover

New SQL migrations belong here, with filenames `YYYYMMDDHHMMSS_description.sql`
and a timestamp strictly after `20261023014518`.

**Do not copy `supabase/migrations/` into this directory.** That is the
historical *old-project* chain. Likewise, `supabase_fresh/migrations/` was
the original new-project bootstrap, already applied under different timestamps.

The active GitHub Actions migration workflow:
1. Refuses any project ref other than live DankShield `viamriafkbqhquvrsnua`.
2. Fetches the new database's applied migration SQL into an **isolated, temporary**
   workspace; it never uses historical files from the old project.
3. Checks every remotely applied version against the unchanged original
   `supabase_live/production_baseline_versions.txt`, the reviewed
   `supabase_live/approved_postcutover_versions.txt`, and this directory's
   versioned migration files; unknown or missing versions fail closed.
4. Copies **only new** versioned migrations from this directory after
   checking no timestamp collisions and that committed SQL for already-applied
   versions still matches the remote.
5. Previews and applies with the existing exact-main, CI-green protected gate.

Until the matching GitHub Actions secrets are updated, the workflow is
intentionally blocked before link or push. No remote data or history is
modified during setup or baseline checks.

Never edit an already-applied migration. Add a forward corrective migration.

## Verified post-cutover migration history (2026-10-31)

The connected dedicated Supabase project's authoritative migration listing
contains three additional already-applied versions:

- 20261028232821: compact guild configuration RPC.
- 20261028234027: compact role-only configuration RPC.
- 20261028235128: compact atomic-patch response RPC.

Read-only inspection confirmed the three function signatures are SECURITY
INVOKER, callable by service_role, and not callable by anon. Their SQL source
definitions live in supabase_fresh/migrations/20261028000{1,2,3}_*.sql,
but remote applied SQL bytes have NOT been independently compared with those
files. As with the original baseline, the approved list acknowledges observed
migration-history versions, not proof of identical SQL. The verifier pins the
three approvals and fails closed on additional history. No migration repair,
SQL replay, or live database write is authorized by this change.

Separate blocker: GitHub Actions run 41107334032 passed exact-main and
project-ref checks but failed migration fetch with PostgreSQL 28P01 password
authentication failure. The owner must verify the production environment's
SUPABASE_DB_PASSWORD against the dedicated project's database password.
Never paste it into chat or commit it. Environment-level secrets override
repository-level secrets. Until authentication succeeds, the CLI fetch,
dry run and promotion remain unverified.

The October 30 manually installed advert, Atlas, and heartbeat schema
repairs require separate migration-history reconciliation; this approval
list does not claim to version or replay them.
