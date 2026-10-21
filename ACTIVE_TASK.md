# Active task: encrypted dedicated candidate recovery drill (2026-10-20)

One scoped migration branch: `migration/dankshield-candidate-restore-20261020`. PR #669 independently confirmed merged with **all six green checks**. New owner-only recovery workflow takes AES-GCM encrypted backup of the OFFLINE dedicated project, restores it to a disposable Supabase PG17 instance using existing hardened, credential-redacted helpers, checks 14 critical table count+hash manifests, and uploads encrypted-only artifact for manual private retention. No original source access or Discloud runtime changes. Dedicated candidate has 75 public tables, 4,756 history feed rows, 3,369 staff evidence rows, both command queues empty.

Current source has zero active jobs/commands/verification tokens, but 2 pending and 1 staff accepted voice session. The owner must resolve them with authoritative Discord flow before migration can be declared ready. Next mandatory: actual manual restore proof and off-runner retention, original writer freeze + read-only 34-table delta, reversible small-guild canary, owner-approved Discloud switch, and guarded merged-branch retirement. No production cutover.

