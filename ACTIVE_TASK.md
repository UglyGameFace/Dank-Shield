# ACTIVE TASK: dedicated Supabase read-only cutover audit

**Single migration workstream:** `migration/dankshield-cutover-audit-20261020` (one temporary PR). PR #668 merged after all six checks. Independent direct SQL verified exact **4,756** activity-feed records, original metadata divergences (1,825), 3,369 staff evidence links and 632 member activity links. Source remains live; new dedicated Supabase has 75 public tables, exact copied durable state, empty new execution queues.

Build private read-only repeatable-read source/target audit of 34 high-value tables and original live-work blockers. Original live voice-verification: **2 PENDING + 1 STAFF_ACCEPTED**. No verification tokens alive, active jobs/commands, recent Cinema or unclosed Hub sessions. DO NOT abandon/cancel those voice sessions without Discord authority. Two original shared `audit_events/audit_logs` tables still lack Dank Shield ownership proof; don't import. Preserve owner's decision not to copy external invite-block event history.

Next phases: owner runs final preview; resolve legitimate open sessions; source writer pause + delta reread; independent encrypted backup/restore drill; real Discord feature/canary and rollback; explicit owner-approved Discloud cutover. Guarded merged-branch retirement should remove only exact PR-tip merged ephemeral branches; never touch main or production/backup refs.

**NO production Discloud changes, source deletions, Discord actions, or cutover authorization.**
