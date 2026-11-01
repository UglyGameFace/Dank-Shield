# Single branch-retirement authority (November 1, 2026)

## Why the duplicate workflows were retired

The repository previously exposed three separate GitHub Actions workflows that could call
`github.rest.git.deleteRef`: the scheduled/push `retire-merged-temporary-branches.yml`,
the historical `guarded-merged-branch-cleanup.yml`, and the one-off
`guarded-final-two-branches.yml`. All three shared a concurrency group but
maintained different candidate manifests, proofs and confirmation semantics.
That was unnecessary overlapping deletion authority, especially after the
owner-reviewed reusable workflow successfully retired 56 archived branches.

The two older owner-dispatched workflows have been removed from the current
tree. Their exact original code, manifests and GitHub Actions history remain
available through the Git history; removing the workflow files does **not**
delete any branch, tag, deployment reference, archive or database record.

## Authoritative path

- `.github/workflows/retire-merged-temporary-branches.yml` is the **only**
  branch-deleting implementation. Its automatic path is limited to eligible
  merged temporary refs and conservative exact-head historical ancestors.
- `.github/workflows/owner-reviewed-branch-retirement-command.yml` is an
  owner-authenticated relay for preview and deletion requests. It has **no**
  Git ref mutation implementation of its own.
- Reviewed historical deletion requires a separately authenticated owner
  command, a resolvable owner-attested Discloud-linked ref, an exact SHA-bound
  preview token, merged PR proof (or exact immutable merge-tree equivalence),
  a 48-hour hold, absence of open PR dependencies, archive-tag SHA verification,
  and fresh main/branch leases.
- The 75-branch warning is informational. It is not permission to prune
  unknown refs and does not prevent a valid guarded cleanup batch.

## Evidence and release boundaries

On November 1, GitHub branch inventory decreased from 132 to 76 through
five verified guarded waves, deleting 56 refs and preserving their exact
SHA archive tags. The final owner-reviewed preview on main
`26a887627b5081190c55b0936960a8a69e29850d` reported
`eligible: 0` at 76 refs. This is **not** a claim that all remaining
branches are obsolete. The unmerged recurring-campaign migration and
divergent historical changes remain held for integration or explicit
supersession.

An owner attestation that Discloud is linked to GitHub `main` is not
independent proof of the running Discloud commit. No production release,
credential, host file, or Supabase database change is authorized by this
workflow consolidation. Production requires exact-main CI, independently
verified off-Discloud backup, deployment-risk approval, live canary and
tested rollback.
