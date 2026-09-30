# Active Task

## Active task / outcome

**DANK-SHIELD-TICKET-CATEGORIES-023 — expose built-in managed ticket categories from /tickets**

FORCE SWITCH accepted from issue #367 Slice 3.

User-reported production defect:
- `/tickets → Categories` can inventory Dank Shield's stored/recognized managed ticket categories;
- **Sync Managed Catalog** restores/updates those rows but intentionally does not enable every category;
- the actual per-guild built-in selector exists only in the setup flow;
- a server can therefore have the full managed catalog stored while owners/staff see no direct way in `/tickets` to enable additional supported categories.

Production baseline: `main` = `3803bc9131fa187cb5a2ea24091f41220dc028ed` (PR #373 merged).

Active branch: `fix/ticket-managed-category-selection`.

Active PR: #374 — **Expose built-in ticket category selection in /tickets**.

## Paused prior task

Issue #367 Slice 3 — Configurable Verification Framework — is paused by explicit FORCE SWITCH.

Its policy foundation is already merged in PR #373. Do not continue the manager/CAS integration until this ticket-category fix reaches its own Definition of Done, unless another explicit FORCE SWITCH is given.

## Root cause

Dank Shield already has one canonical managed ticket-category catalog and one canonical per-guild selection authority:
- `tickets_new/managed_category_service.py` owns catalog rows and `ticket_category_setup_selected_keys`;
- `startup_guards/ticket_category_setup_guard.py` owns the existing full managed multi-select and preset shortcuts;
- member-facing ticket menus consume the resulting active rows.

The product gap is navigation/UI ownership:
- `public_ticket_command_center.py` exposes inventory, catalog sync, and custom-category CRUD;
- it never exposes the existing managed selector;
- `Sync Managed Catalog` is maintenance/repair, not enablement.

## Current implementation

- add a prominent **Choose Built-ins** action to `/tickets → Categories`;
- reuse the existing managed selector and `save_category_selection` persistence;
- add a ticket-manager context to the shared selector so it:
  - uses the existing Dank Shield ticket/staff permission scope;
  - returns to **Ticket Categories** rather than Setup Home;
  - keeps custom-category CRUD in the ticket manager instead of duplicating it;
  - preserves the normal setup context unchanged;
- clarify that **Sync Managed Catalog** repairs/updates definitions and does not enable all categories;
- keep save failures inside the ticket manager rather than routing to setup;
- add focused regression coverage.

## Safety / compatibility

- no new ticket-category database owner;
- no duplicate catalog;
- no automatic enable-all behavior;
- no deletion of existing custom categories;
- no change to member-facing routing semantics beyond the owner-selected built-in set;
- no global guild sweep;
- no Verification Framework changes in this branch;
- `/dank setup → Ticket Menu Options` must retain its existing setup permissions/navigation;
- `/tickets` ticket-context selection must preserve existing recognized-staff authority.

## Definition of Done

This fix is complete only when:
- `/tickets → Categories` visibly exposes built-in category selection;
- the picker shows the supported managed catalog and current saved defaults;
- saving changes updates the existing per-guild selection authority;
- the Create Ticket menu consumes that selection through the existing canonical loaders;
- ticket-context Back/Close/error behavior stays in the ticket area;
- normal setup-context navigation still behaves as before;
- exact-head CI is green;
- PR merge and post-merge production CI/deploy are green;
- Android owner/staff canary confirms another server can enable additional built-in categories without creating duplicates or custom copies.

## Validation status

PR #374 is open as the focused fix. The implementation reuses the existing managed catalog and saved-selection authority; no schema change is required.

Fresh exact-head CI is required after this task-record update.

## Next step

Validate PR #374 on its exact head. If green, perform final diff/branch hygiene, mark ready, merge with the exact expected head, then verify post-merge CI/Supabase/Discloud and run the Android canary on the affected server.
