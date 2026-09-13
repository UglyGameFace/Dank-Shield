# ACTIVE TASK

## DS-SEC-043 — Security containment policy separation

**Status:** IN PROGRESS
**Branch:** `fix/security-contain-strict-policy-separation`
**Base:** `ebd53ac316dc40929a9d21935a62dbe45b438adc`

## Outcome

Separate the normal containment policy from the optional maximum-restriction policy so ordinary staff configurations do not block protection from being enabled.

## Scope

- Keep normal containment compatible with explicitly trusted staff.
- Make the maximum-restriction readiness gate opt-in and owner-controlled.
- Report the exact server permission that causes a readiness blocker.
- Correct Protection Center guidance so it distinguishes bot requirements from server-policy blockers.
- Preserve all previously merged identity, audit, quarantine, and configuration protections.
- Add focused regression coverage and run full exact-head CI before merge.

## Next step

Implement the policy separation and validate it on this branch.
