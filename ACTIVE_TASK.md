# ACTIVE TASK

## DS-TICKET-CAT-022 — Repair duplicate ticket categories across all existing servers

**Status:** IN PROGRESS
**Branch:** `fix/ds-ticket-cat-022-global-dedupe`
**Base:** current `main` after PR #175
**Started:** 2026-08-07
**Force-switch reason:** Existing guilds still contain stale or mismatched Dank Shield managed ticket-category rows, causing duplicate visible labels such as Support and Report a Member. Repair all existing guild data, make the canonical catalog authoritative, preserve intentional custom categories and selections, and permanently prevent duplicate member-facing labels.

## Confirmed production symptom

An existing guild's Create Ticket picker shows repeated member-facing labels, including multiple **Support** entries and duplicate **Report a Member** entries, alongside a broad legacy/default category set.

## Confirmed root causes before editing

1. `managed_category_service.canonical_category_key()` trusts a stored `managed_category_key` before the visible slug/name. A stale bot-owned row can therefore carry one canonical key while displaying another category's label.
2. `_catalog_reconcile_needed()` checks managed key presence/version/duplicates but does not verify that a managed row's slug/name/button label/description/intake type match the catalog entry for that key.
3. The v2 Supabase reconcile function only recovers legacy slug/name aliases from rows where `managed_by_dank = false`; stale bot-owned rows with missing/wrong keys can escape repair.
4. Runtime canonical-key dedupe alone cannot guarantee unique Discord labels when two differently keyed corrupted rows display the same label.
5. Legacy/raw category fetchers still exist in the codebase and must remain patched to the single managed-category service after startup/import ordering.

## Scope

1. Preserve `CATEGORY_SETUP_VERSION = 2` so existing owners are not forced to redo a valid selection only because the managed catalog repair version changes.
2. Introduce a separate managed catalog repair version and make runtime repair checks validate the full canonical managed-row shape.
3. Add a new idempotent Supabase migration that repairs every existing guild:
   - canonicalizes every Dank Shield-managed row to its authoritative catalog key/slug/name/button label/description/intake type/sort order;
   - recovers stale bot-owned rows using safe legacy slug/name evidence when the stored key is missing/invalid/mismatched;
   - removes true Dank Shield duplicates;
   - preserves intentionally custom owner rows and their enabled state;
   - preserves each guild's existing selected/enabled built-in choices wherever they can be mapped safely;
   - does not delete ticket channels, tickets, roles, or unrelated custom rows.
4. Add a final member-facing dedupe invariant so two options with the same normalized visible label cannot reach Discord even if database drift occurs later.
5. Ensure every live ticket-category loader uses the managed-category service and no later startup import restores an obsolete raw loader.
6. Add regression coverage for:
   - `Support` repeated 3x;
   - `Report a Member` repeated 2x;
   - right key / wrong visible label;
   - wrong key / right visible label;
   - missing managed key on a bot-owned legacy row;
   - valid custom rows that resemble but are not Dank-managed rows;
   - disabled categories staying disabled;
   - owner selection preservation;
   - migration/reconcile idempotency;
   - final Discord labels being unique;
   - startup loader ownership.

## Work plan

- [x] Inspect current managed catalog, canonical key logic, runtime dedupe, existing migration, public picker, clean picker, native panel fetcher, and startup patch owner.
- [ ] Inspect all migration smoke-test assumptions and category tests before writing the corrective migration.
- [ ] Implement separate managed catalog version + full-shape drift detection.
- [ ] Implement visible-label dedupe invariant without deleting legitimate custom data.
- [ ] Add corrective all-guild Supabase migration with idempotent reconciliation.
- [ ] Lock all live loaders to the canonical managed service.
- [ ] Add targeted Python + migration regression tests.
- [ ] Run compile/static validation, full unit suite, standalone tools, and managed-category SQL smoke test.
- [ ] Inspect final diff for unsafe custom-row deletion, selection resets, migration conflicts, or competing loaders.
- [ ] Open one PR and merge only after all validation/review gates pass.
- [ ] Deploy and live-check at least one previously affected existing guild before marking complete.

## Paused tasks

### DS-OPS-021 — Owner ticket authority, bulk moderation, and canonical join cards

Implementation merged through PR #174 with Welcome Card Studio emoji hotfix PR #175. Live production verification/follow-up remains incomplete. Paused by the user's FORCE SWITCH on 2026-08-07.

### DS-SETUP-020 — Entitled ID-verification setup selection and VC permissions regression

Merged in PR #171; only its live Discloud/Discord verification gate remains. Previously paused by FORCE SWITCH.

## Preserved backlog

- DS-STATS-019 — Durable Dank Stats invite-block counting; live verification pending.
- DS-SETUP-019 — Cleanup confirmation modal crashes.
- DS-MEDIA-001 — Klipy direct GIF unfurl listener.
- DS-RESET-001 — Owner-authorized Emergency Reset and bulk cleanup console.

## Definition of Done

DS-TICKET-CAT-022 is complete only after root-cause inspection, an idempotent all-existing-guild data repair, runtime duplicate prevention, custom-category and selection preservation, targeted/regression tests, SQL migration smoke validation, compile/static validation, cleanup/conflict review, one reviewed PR, merge, deployment, and live verification on a previously affected guild that the member-facing picker contains only the intended enabled categories with no duplicate visible labels.
