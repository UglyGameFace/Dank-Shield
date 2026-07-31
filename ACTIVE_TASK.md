# ACTIVE TASK

## DS-TICKETS-016 — Make the Create Ticket panel single-owner

**Status:** IMPLEMENTED — EXACT-HEAD CI PENDING
**Branch:** `fix/ticket-panel-owner`

## Problem

The clean Create Ticket button has two possible owners in the same process:

1. its registered persistent `discord.ui.View` callback,
2. a compatibility `on_interaction` fallback that waits 150 ms.

Under Discord/API latency, both paths can enter before the response becomes marked complete. The old runtime hardening also introduced broader regressions by overriding:

- the native atomic ticket-number allocator,
- managed/custom category loading,
- the complete managed catalog with seven legacy rows,
- menu ownership by guild/user for 45 seconds instead of by interaction ID.

That could produce duplicate private menus, confusing duplicate notices, hidden managed/custom categories, and non-atomic runtime numbering despite the durable Supabase counter migration.

## Required behavior

- One Create Ticket handler execution per Discord interaction ID.
- Duplicate delivery of the same interaction returns silently.
- A second legitimate button press receives a fresh menu immediately.
- The persistent view is the only listener when it registered successfully.
- The fallback listener remains available only if persistent-view registration failed.
- Runtime hardening never overrides category loading or ticket-number allocation.
- The native managed catalog, custom categories, COD category, and durable RPC allocator remain authoritative.

## Implemented

- Replaced the stale broad hardening module with a narrow single-owner guard.
- Added interaction-ID locks and completed-interaction TTL cleanup.
- Removed the redundant fallback listener after successful persistent-view registration.
- Preserved the fallback when persistent registration fails.
- Removed all runtime overrides of `_next_number`, `_rows`, `_load_rows`, and `_ticket_num`.
- Added focused executable concurrency and registration tests.
- Added a fast `Ticket Panel Single Owner` GitHub Actions workflow.

## Validation

- [ ] Focused single-owner workflow passes.
- [ ] Exact-head full Dank Shield CI passes.
- [ ] Application Command Size Diagnostics passes.
- [ ] Review feedback is clear.
- [ ] PR merges with the exact tested head SHA.
- [ ] Discloud accepts the production commit.
- [ ] Live button press produces one category menu and a second fresh press remains responsive.

## Previous completed task

DS-TICKETS-015 durable ticket numbering is merged, deployed, and verified in production. Migration `20260731141000` matches local/remote history and an ordinary linked dry run reports production up to date.

## Single Active Task Lock

Do not begin another unrelated repair until DS-TICKETS-016 is merged and deployed.
