# ACTIVE TASK

## DS-TICKET-CAT-037 — Restore rich ticket choices through setup review

**Status:** IMPLEMENTED / DATABASE RECOVERY PROVEN / FINAL EXACT-HEAD CI PENDING
**Branch:** `fix/ticket-category-preserved-selection-198`
**Base:** `5d90ddcd8cbdde1233fdbdcac5faba2dd5194445` (`main`, squash merge of PR #197)
**Started:** 2026-09-11

## User-visible failure

The public Create Ticket panel is no longer limited by the interaction bugs repaired in PRs #196/#197, but a guild under ticket-category setup review can still show only the three safe starter choices: **Appeal**, **Report a Member**, and **Support**.

The affected production guild reports that the prior intended menu included richer categories such as **COD Modding Services** for older legacy Call of Duty titles only, **Partnerships**, **Report a Member**, and **Report Staff**.

## Authoritative category owner

`stoney_verify/tickets_new/managed_category_service.py` is the canonical managed-category identity, reconciliation, setup-selection, and live-menu source. The repair remains per guild, preserves owner-created custom categories, and does not enable every built-in category globally.

## Proven root causes

### 1. Preserved selection was stored but ignored while review was required

PR #193 changed `require_dank_ticket_category_setup(...)` to preserve `guild_configs.ticket_category_setup_selected_keys` during future forced review. However, `_state_from_rows()` still derived `active_rows` and `selected_keys` only from the current `ticket_categories.is_enabled` flags.

A forced review had already reduced those row flags to the starter trio. Therefore the database could correctly retain the previous owner-confirmed selection while the member-facing menu ignored that evidence and still rendered only three choices.

### 2. Older destructive review reset could erase the saved selection entirely

The original category-selection migration reset managed rows to `report`, `appeal`, and `support` and replaced `ticket_category_setup_selected_keys` with an empty array when setup was invalidated. Guilds hit by that older path cannot be repaired only by reading the current config row.

Durable `guild_config_versions` history stores per-guild `ticket_categories` snapshots. The older reset updated ticket rows and then guild config in the same transaction. The recovery identifies that exact destructive-review transaction and restores only the same guild's final ticket snapshot from before it, avoiding partially-reset intermediate state and any cross-guild inference.

### 3. Rich category wording drifted

Historical repository evidence shows the COD category was intended for legacy COD modding/lobby services, including BO1/BO2/BO3, WaW, MW2/MW3, Ghosts, Zombies, modded/challenge lobbies, unlocks, recoveries, and RGH/JTAG. Later wording widened it into generic COD/Warzone/current-title support.

The canonical staff category also drifted to the vague visible label **Staff Complaint** although the intended member-facing choice is **Report Staff**.

## Implemented repair

- Bumped the managed catalog to v4 without creating a second category owner.
- Restored visible **COD Modding Services** wording and legacy-only COD intake guidance.
- Restored visible **Report Staff** while preserving the existing `staff-complaint` internal key/routing compatibility.
- Kept **Report a Member**, **Partnerships**, and the rest of the rich managed catalog distinct.
- `_state_from_rows()` now projects a surviving owner-confirmed `ticket_category_setup_selected_keys` selection into the member-facing menu while setup review remains required.
- Setup review remains required until an authorized owner/admin confirms it; restoring the menu does not falsely mark setup complete.
- If the saved keys were erased by the older destructive reset, the new migration recovers only from the same guild's version history and only with matching destructive-review evidence.
- A surviving saved selection is authoritative and now also realigns the underlying managed row enablement/default flags instead of leaving the database on the starter trio while the UI projects a different state.
- Future `require_dank_ticket_category_setup(...)` calls preserve and keep the known managed selection enabled instead of collapsing it to the starter trio.
- If no trustworthy prior selection exists, the safe starter behavior remains unchanged.
- Enabled owner-created custom categories remain preserved and are not globally replaced or force-expanded.

## Permanent changed files

- `ACTIVE_TASK.md`
- `.github/workflows/ticket-category-rich-recovery-sql.yml`
- `stoney_verify/tickets_new/managed_category_service.py`
- `stoney_verify/startup_guards/ticket_form_default_templates_guard.py`
- `stoney_verify/startup_guards/ticket_category_schema_bootstrap_guard.py`
- `supabase/migrations/20260911113000_restore_rich_ticket_category_selection.sql`
- `tests/test_ticket_category_setup_selection.py`
- `tests/test_ticket_category_reset_preserves_selection.py`
- `tools/audit_ticket_category_menu.py`

Temporary patch/hardening machinery was removed from the branch before final validation.

## Validation evidence

Focused Python validation on Python 3.11.16 / Ubuntu 24.04:

- changed Python files compile successfully;
- `tests/test_ticket_category_setup_selection.py` + `tests/test_ticket_picker_legacy_default_reconciliation.py`: **38 passed, 1 warning**;
- `tools/audit_ticket_category_menu.py`: **PASS**;
- `git diff --check`: PASS on the validated implementation patch.

The permanent **Ticket Category Rich Recovery SQL** workflow passed against PostgreSQL 16 on the production-code head before the final stale-test correction. It:

- reproduced a surviving saved-selection review state;
- reproduced the old destructive review that erased saved keys;
- upgraded through v3 + preservation;
- applied the v4 migration **twice**;
- recovered the rich historical selection;
- realigned surviving selections to row flags;
- kept setup review required;
- verified **COD Modding Services**, **Report Staff**, **Report a Member**, and **Partnerships**;
- verified managed catalog v4 and no cross-guild leak.

A stale ownership regression then failed only because it asserted the preservation migration must remain the final bootstrap migration. It was corrected to require repair → preservation → v4 recovery ordering. No production code changed for that test correction.

## New regression guarantees

- required-review state restores a preserved managed selection instead of the starter trio;
- required-review state with no preserved selection still falls back safely to the starter trio;
- canonical labels include **Report a Member**, **Report Staff**, **COD Modding Services**, and **Partnerships**;
- COD Modding Services uses legacy-title/modding guidance and does not advertise Warzone/current-title support;
- direct-DSN bootstrap registers the v4 recovery migration after preservation;
- dedicated PostgreSQL CI proves preserved-selection and erased-history recovery plus idempotency.

## Safety invariants

- No guild ID is hardcoded.
- No global "enable all categories" migration is introduced.
- Recovery reads and writes only the explicit guild being processed.
- Non-empty surviving owner selection always wins over history recovery.
- Historical recovery requires the older destructive-review evidence before restoring a pre-reset snapshot.
- Unknown/custom rows are not adopted as managed categories by display name.
- Setup remains required until explicitly confirmed.
- PR #196 Confirm/create safety and PR #197 missed-select recovery remain untouched.

## Final validation required before merge

- permanent Ticket Category Rich Recovery SQL workflow green on the exact final head;
- existing managed-category SQL repair workflow green;
- full repository pytest suite;
- Ticket Category Menu Sanity and all other triggered exact-head workflows;
- Python compile and committed-diff whitespace checks;
- final compare against current `main` with zero base drift;
- final scoped diff, conflict-marker, review-thread, and review-state inspection;
- squash merge only after the exact final head is green.

## Production acceptance after deploy

1. Startup applies/recognizes the v4 category migration without SQL errors.
2. A guild with a surviving saved selection shows that rich selection even while the setup-review notice remains.
3. A guild whose old selection was erased is restored only when its own pre-reset version-history evidence exists.
4. The affected ticket picker shows the recovered intended choices rather than only Appeal / Report a Member / Support. Expected historical names include **COD Modding Services**, **Partnerships**, **Report a Member**, and **Report Staff** when those were in the recovered selection.
5. COD Modding Services intake asks about older/legacy titles and modding services, not Warzone/current-title general support.
6. Create Ticket → category selection → Confirm → exactly one ticket channel still works through the #196/#197 runtime path.
7. If no trustworthy saved/history selection exists, the starter trio remains rather than inventing a configuration.

## Suspended / backlog

- **DS-TICKET-036 production acceptance:** PR #196 is merged; Confirm/create runtime still needs final deployed end-to-end acceptance together with this category repair.
- **PR #197 production acceptance:** missed category-select recovery is merged and post-merge CI green; validate on the deployed build together with this task.
- **DS-INVITE-035 final hardening acceptance:** merged; corrected RSS/invite telemetry remains a separate observation task.
- **Join-context Supabase schema mismatch:** missing `entry_confidence` remains backlog.
- **Server Design setup regression/full audit:** remains backlog.

## Next step

Freeze this record-and-code head, require every exact-head workflow green, perform final compare/scope/review checks, then mark PR #198 ready and squash-merge. Production acceptance follows on Discloud.
