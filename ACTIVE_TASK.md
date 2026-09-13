# ACTIVE TASK

## DS-SEC-043 — Security containment policy separation

**Status:** IMPLEMENTED ON BRANCH / FINAL CI PENDING
**Branch:** `fix/security-contain-strict-policy-separation`
**Base:** `ebd53ac316dc40929a9d21935a62dbe45b438adc`
**PR:** #210

## Outcome

Separate normal Contain from the optional Strict Lockdown tier so ordinary staff configurations do not block AntiNuke from being enabled.

## Implemented

- Normal Contain no longer applies the delegated-authority readiness gate.
- Unknown/untrusted actors keep canonical first-strike behavior.
- Explicitly trusted staff keep configured bounded thresholds in normal Contain.
- Strict Lockdown is a separate persisted owner-only option and applies the restrictive readiness policy.
- Strict Lockdown readiness blockers name the exact role/overwrite permissions causing the block.
- Protection Center gets a separate Lockdown control and explains Contain vs Strict Lockdown.
- Generic permission guidance was replaced with guidance based only on the readiness failures that actually exist.
- Switching to Alert disables Strict Lockdown because the stricter tier only applies to Contain.
- Previously merged identity, audit, quarantine, configuration-history, and self-origin protections remain in the startup chain.
- No automatic staff-role permission rewriting and no broader Discord permissions were added.

## Validation

- Focused policy-separation regressions added.
- Strict readiness regressions updated for normal Contain vs Strict Lockdown.
- Startup ordering regression verifies final policy installation after app/UI import and before bot run.
- Full exact-head GitHub CI remains the final merge gate.

## Definition of done

- A normal Contain server can enable AntiNuke while legitimate staff roles retain management permissions, subject to Dank Shield's own readiness/hierarchy requirements.
- Strict Lockdown refuses activation while delegated restricted authority remains and reports the exact reason.
- Normal trusted staff behavior and Strict Lockdown first-strike behavior are independently regression-tested.
- Exact-head CI is fully green and the final PR diff contains no unrelated changes.

## Next step

Wait for exact-head PR #210 CI, review any failure at the exact failing path, and mark ready only after the full validation gate is green.
