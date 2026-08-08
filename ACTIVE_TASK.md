# ACTIVE TASK

## DS-COMMAND-UX-024 — Consolidate Dank Shield into action-complete mega menus

**Status:** IN PROGRESS — ROOT-CAUSE / PARITY AUDIT
**Branch:** `fix/ds-command-ux-024-mega-menu-consolidation`
**Base:** merged `main` at `f6d732f1f03eb5e2219351cd0e468ada281e1978`
**Started:** 2026-08-08
**Force-switch reason:** Dank Shield exposes too many redundant slash subcommands that duplicate actions already available through its UI. Consolidate the entire public command surface into a small stable set of entry commands and comprehensive mega menus while preserving every existing action, permission check, upload capability, moderation/ticket/setup function, recovery path, and existing-server compatibility.

## Confirmed root causes before editing

1. `public_command_hub.compact_public_dank_surface()` creates the intended button-first `/dank` Control Center, but then deliberately re-adds several direct shortcut commands and a `/dank welcome` subgroup.
2. `public_exit_compact_surface` runs after that compaction and adds three more direct Exit Card commands, so the final Discord autocomplete surface becomes noisier again even though the mega menu already owns those actions.
3. The normal public command profile still registers separate global families for `/mod`, `/ticket`, `/tickets`, `/ticket-intake`, `/ticket-category`, `/ticket-panel`, and `/verify` before the final compaction layer.
4. Existing implementation modules contain important services, listeners, permission checks, claim/owner authorization, setup compatibility, and safety logic. Removing those modules would risk deleting functionality; the correct fix is to compact only the final Discord-visible command tree after all implementation modules are loaded.
5. Moderation already has a complete private `Member Command Center` with member selection, verification, timeout, kick, ban, role actions, intelligence, cleanup, locks, notices, and bulk tools.
6. The persistent ticket panel covers common current-ticket actions but does not cover every `/ticket` action. The replacement ticket center must preserve the missing reopen/transcript/access/rename/lock/unlock/delete and lookup/history paths before the old grouped commands are hidden.
7. Ticket intake/category/panel roots are setup/administration surfaces and belong in a Ticket System mega menu rather than separate autocomplete command families.
8. `/verify` contains useful repair/status actions that must remain reachable through a verification center even after its subcommands are removed.
9. File attachment uploads are the legitimate exception: Discord buttons cannot open a native attachment option, so a minimal upload command must remain available.

## Target public command surface

- `/dank home` — primary all-features control center.
- `/dank upload` — one attachment command with an asset selector for Join background, Exit background, or custom font.
- `/mod` — standalone staff doorway to the canonical Member Command Center; no moderation subcommands.
- `/ticket` — standalone current-ticket controls center; no ticket action subcommands.
- `/tickets` — standalone ticket operations/setup center; no queue/setup subcommands.
- `/verify` — standalone verification center; no verification subcommands.
- `View Dank Profile` context menu remains available.

Every removed command action must still be reachable through buttons, selects, modals, or the consolidated upload command.

## Work plan

- [x] Force-switch task and create isolated branch from merged `main`.
- [x] Inspect final command registration/compaction order.
- [x] Audit current `/dank` mega menu and Welcome/Exit command re-expansion.
- [x] Audit `/mod` action surface and canonical Member Command Center safety path.
- [x] Audit `/ticket` persistent panel and grouped action gaps.
- [x] Audit `/tickets`, `/ticket-intake`, `/ticket-category`, and `/ticket-panel` action families.
- [x] Audit `/verify` grouped repair/status/panel actions.
- [ ] Implement one final public command-surface compactor after all existing registrars run.
- [ ] Consolidate Welcome/Exit attachments into one `/dank upload` command and move non-attachment shortcuts fully into the existing mega menu.
- [ ] Add Tickets and Verification as first-class mega-menu destinations.
- [ ] Add standalone `/mod` doorway to the canonical Member Command Center.
- [ ] Add action-complete current-ticket center and ticket operations/setup center.
- [ ] Add action-complete verification center.
- [ ] Update help/setup copy so hidden command names are not advertised as the normal workflow.
- [ ] Add exact final-command-tree regression tests and action-reachability tests.
- [ ] Run compile/static/full suite, standalone audits, command-size diagnostics, conflict/diff cleanup, and review.
- [ ] Merge only after every repository gate is green.
- [ ] Deploy merged `main` to Discloud and live-check final Discord autocomplete + representative actions from every center.

## Paused tasks

### DS-WELCOME-EXIT-023 — Welcome placeholder rendering and canonical Exit Card Studio

PR #177 merged to `main` as `f6d732f1f03eb5e2219351cd0e468ada281e1978` and was deployed successfully. Startup proved canonical Welcome/Exit ownership and Exit Studio registration. The final real join/leave live-event verification gate remains pending because the user force-switched to DS-COMMAND-UX-024.

### DS-TICKET-CAT-022 — Repair duplicate ticket categories across all existing servers

PR #176 merged with repository/SQL validation green; required affected-guild live verification remains pending.

### DS-OPS-021 — Owner ticket authority, bulk moderation, and canonical join cards

Merged through PR #174 plus Welcome Studio hotfix PR #175; remaining production follow-up is paused/superseded where applicable.

### DS-SETUP-020 — Entitled ID-verification setup selection and VC permissions regression

Merged in PR #171; live Discloud/Discord verification gate remains paused.

## Preserved backlog

- DS-STATS-019 — Durable Dank Stats invite-block counting; live verification pending.
- DS-SETUP-019 — Cleanup confirmation modal crashes.
- DS-MEDIA-001 — Klipy direct GIF unfurl listener.
- DS-RESET-001 — Owner-authorized Emergency Reset and bulk cleanup console.

## Definition of Done

DS-COMMAND-UX-024 is complete only when the public Discord command tree is intentionally small, every previously public action remains reachable through an authorized UI or the minimal upload command, existing safety/claim/owner/role/config guards are preserved, help text matches the new menu-first workflow, regression tests prove command-tree shape and action reachability, all CI/audits are green, the reviewed PR is merged, Discloud is deployed, and live Discord verification confirms the compact autocomplete surface plus representative moderation, ticket, verification, setup, and upload actions work end to end.
