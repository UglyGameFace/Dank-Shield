# ACTIVE TASK

## Persistent interaction compatibility audit

**Status:** INSPECTION IN PROGRESS — READ CURRENT RUNTIME BEFORE REPAIR
**Branch:** `audit/persistent-interaction-compatibility`
**Base:** `5f51da0e338208538133f0b610c3e14a9c6f0bbc`
**PR:** not opened yet

## Outcome

Audit every persistent Discord interaction surface so buttons, views, modals, panels, fallback listeners, and restart registration remain usable after bot restarts without stale components bypassing current policy, dead startup guards pretending to own live behavior, duplicate handlers racing each other, or users getting stuck on obsolete UI.

## Scope

- Persistent `discord.ui.View` registration and stable `custom_id` ownership.
- Restart-safe component dispatch and fallback listeners.
- Panels/messages that outlive process restarts or configuration changes.
- Interaction authorization at the action boundary, not only when the panel is posted.
- Obsolete/stale component handling and clear failure/exit behavior.
- Duplicate interaction owners, legacy compatibility shims, and dormant startup-guard paths that overlap live runtime.
- Behavioral regression coverage for restart, stale-message, disabled-mode, and duplicate-dispatch cases.

No AntiNuke implementation changes, schema changes, billing work, broad setup redesign, or unrelated cleanup are in scope unless the interaction audit proves a direct dependency.

## Inspection rules

- Do not guess which startup guard is live; trace imports/call sites from `main.py` and `stoney_verify/app.py`.
- Do not activate the dormant startup-guard loader.
- Do not add new monkey patches, root runtime patch files, or second interaction owners.
- Prefer one canonical handler/registration path per stable component ID.
- Any repair must be the smallest structural fix supported by current execution-path evidence.

## Validation required before completion

- Enumerate persistent views/components and their live registration owners.
- Map stable `custom_id` values to exactly one canonical action path or document intentional fallback behavior.
- Prove stale components cannot bypass current guild configuration/authorization.
- Prove restart registration restores supported persistent interactions.
- Prove obsolete/unsupported components fail safely with a useful user-facing response where applicable.
- Run focused behavioral regressions plus full exact-head Dank Shield CI and relevant companion workflows.
- Final diff must contain only interaction-compatibility work plus this task record.
- Merge only through protected `main`, then verify post-merge canonical CI and gated production promotion ordering.

## Completed prior task

### Verification integrity audit repair

Completed and merged as PR #213.

- Final validated PR head: `0f9f6a32fa052571d4689d230226e04ec07861d7`.
- Merge commit: `5f51da0e338208538133f0b610c3e14a9c6f0bbc`.
- Final PR head passed full Dank Shield CI, unit tests, standalone checks, public/setup audits, Claim-first ticket security, Managed category SQL smoke test, Application Command Size Diagnostics, Dank Design Regression CI, Ticket Owner Emergency Override, and Profile Runtime Diagnostics.
- `main` remained protected with the required checks before and after merge.
- Post-merge Dank Shield CI run `34804743914` succeeded on the exact merge SHA.
- Only after canonical CI succeeded, `Deploy Supabase migrations` run `34805237703` started via `workflow_run` on the same SHA and succeeded, including immutable current-main verification, migration status, dry-run preview, and apply step.
- Basic Verify authorization is now enforced at the role-mutation boundary; Voice-only/disabled/ID states no longer collapse into Basic; legacy Voice aliases are recognized; stale Basic panels/buttons cannot silently grant access after mode changes.

### DS-AUD-009 — Release governance and production promotion safety

Completed and merged as PR #212. Canonical merge SHA `9112528e42e77ec348abe69d9207e37a64294380` passed post-merge Dank Shield CI before the gated Supabase production promotion ran successfully. `main` is protected with required pull-request checks.

## Suspended task

### DS-SEC-044 — Hostile bot re-entry race and integration persistence

Suspended by explicit FORCE SWITCH after PR #211 merged and exact-head CI passed. Remaining acceptance evidence: after deployment, repeat the hostile/GANG-Nuker re-entry test and confirm no destructive action lands before the hostile identity/integration is removed.

## Next step

Trace the live persistent-interaction registry from `app.py`, enumerate every registered view/listener/custom ID and its owning action path, then identify duplicate, dormant, stale, or unguarded surfaces before changing code.
