# Current active task: diagnose dedicated candidate recovery failure (2026-10-21)

**Single temporary branch:** `fix/candidate-recovery-failure-code-20261020`. Owner manually ran dedicated backup/restore GitHub Actions run `39538439329`; steps 1–8 passed, step 9 failed `RecoveryDrillError` and encrypted archive upload was skipped. Existing wrapper erased the exception's safe error code. Implement strict allowlisted recovery error categories and static phase labels, with zero source/candidate DB writes or Discloud changes; verify CI and merge the one PR before rerun. This does NOT claim recovery passed. Next run should report a classified failure so the actual root cause can be fixed, or pass full restore with matching critical hashes.

Retain unresolved 2 PENDING and 1 STAFF_ACCEPTED voice verification states. Candidate offline and source live. Never switch production before successful independent restore, final delta, reversible canary, explicit owner approval. Nightly retirement must clean merged temporary branches only.

---

