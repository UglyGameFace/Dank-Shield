# ACTIVE TASK

## DS-SETUP-018 — Compact `/dank setup` navigation and clear testing flow

**Status:** ACTIVE
**Branch:** `fix/setup-ui-consolidation`

## Scope

Make `/dank setup` seamless on mobile without removing useful information:

- one compact Setup Home with one obvious next action,
- no redundant Manage Setup → All Features & Settings navigation layer,
- one feature-area picker instead of a wall of category buttons,
- short embeds that do not repeat component labels,
- normal setup shows only required information,
- repair, diagnostics, and technical details stay under Advanced,
- testing clearly separates readiness checks, test actions, and final completion,
- every interaction edits the same ephemeral setup message where the owned callback supports it.

## Findings

1. Setup Home repeats status, enabled features, recommended action, and issue summaries even when most of that information is already represented by the primary button.
2. Manage Setup and All Features & Settings are two consecutive hubs describing the same destinations.
3. The All Features embed lists every section and the view repeats every section as buttons, producing a very large mobile card.
4. Setup plan selection duplicates plan descriptions in both the embed and select options.
5. Test Your Setup mixes instructions, launch/post actions, review navigation, and Finish Setup without recording which feature tests were actually confirmed.
6. Setup Check lists every passing and disabled feature, which hides the blockers and next action.
7. Guild IDs and repeated timestamp/footer routing text add noise to owner-facing setup cards.

## Planned implementation

- Add a compact reusable feature-area select that routes directly to the existing owned setup sections.
- Put the feature picker directly on Setup Home and remove the redundant intermediate management hub from the normal path.
- Keep Change Plan, Review, and Advanced/Repair available without crowding the home card.
- Reduce setup plan, guided-step, health-check, and feature-hub embeds to concise status and one decision.
- Replace the test wall with a checklist-style test center:
  - one clear purpose statement,
  - only enabled test areas,
  - direct action buttons where Dank Shield can launch/post a test,
  - explicit owner confirmation for tests that require a second account or observation,
  - Finish Setup enabled only after all enabled test areas are acknowledged,
  - ability to reset/retest after future changes.
- Preserve existing ticket, verification, security, logging, design, welcome, profiles, backup, recovery, and setup-state ownership.

## Definition of Done

- [ ] Normal setup path has no redundant hub hop.
- [ ] Home and feature screens remain compact on mobile.
- [ ] Test flow clearly distinguishes automatic readiness from real feature testing.
- [ ] Finish Setup cannot be mistaken for an automatic test result.
- [ ] Existing setup routes and feature callbacks continue working.
- [ ] Focused behavior tests cover compact navigation and test confirmation state.
- [ ] Full test suite, compilation, command-size diagnostics, setup audits, and whitespace checks pass.
- [ ] Cleanup inspection confirms no competing setup home/test owner remains.

## Backlog

### DS-MEDIA-001 — Klipy direct GIF unfurl listener

After DS-SETUP-018 reaches its Definition of Done, inspect the existing message-event ownership path and add one moderation-safe listener that recognizes verified `klipy.com` page URLs, resolves the page's direct GIF asset without arbitrary-host fetching, and replies with the direct media or a rich embed. Include bounded requests, redirect/host validation, caching, bot-loop prevention, permission/error handling, and focused listener/parser/security regressions.

## Previous completed task

DS-TICKETS-017 was deployed and live-verified from the user-provided `/dank setup` and ticket-choice screenshots. The live menu showed the managed category selection, unique category labels, edit controls, fallback control, and setup completion state from PR #163.

## Single Active Task Lock

Do not begin another unrelated repair until DS-SETUP-018 reaches its Definition of Done.
