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
3. Checks every remotely applied version against
   `supabase_live/production_baseline_versions.txt` plus this directory's
   versioned migration files; unknown or missing versions fail closed.
4. Copies **only new** versioned migrations from this directory after
   checking no timestamp collisions and that committed SQL for already-applied
   versions still matches the remote.
5. Previews and applies with the existing exact-main, CI-green protected gate.

Until the matching GitHub Actions secrets are updated, the workflow is
intentionally blocked before link or push. No remote data or history is
modified during setup or baseline checks.

Never edit an already-applied migration. Add a forward corrective migration.
