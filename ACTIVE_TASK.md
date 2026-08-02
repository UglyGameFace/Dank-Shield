# ACTIVE TASK

## DS-SETUP-018 — Compact `/dank setup` navigation and clear testing flow

**Status:** VALIDATED — MERGE PENDING
**Branch:** `fix/setup-ui-consolidation`
**Pull request:** `#164`

## Scope completed

- Replaced the redundant Manage Setup → All Features & Settings path with one direct feature-area picker.
- Kept Setup Home compact and mobile-friendly with one obvious next action.
- Shortened plan, guided-step, configuration-check, help, and ticket-menu cards.
- Moved repair, restart, and troubleshooting behind Advanced.
- Preserved the canonical `/dank setup` command and all existing feature-service owners.
- Separated setup into four unmistakable stages:
  - Continue Setup — finish required configuration one item at a time.
  - Check Configuration — automatically validate saved roles, channels, choices, and permissions.
  - Test Features — verify actual member and staff behavior in Discord.
  - Finish Setup — unlock only after every enabled feature is explicitly marked tested.
- Added direct ticket-panel, test-ticket, and Verify-panel actions to the relevant feature tests.
- Hid disabled tests instead of displaying unnecessary OFF states.
- Kept test confirmations while navigating inside the same ephemeral setup session.

## Test-session safety

- Cache key: guild, setup owner, and ephemeral setup message.
- Expires after 30 minutes.
- Bounded to 512 sessions.
- Invalidates confirmations when the enabled test set changes.
- Clears on successful finish and test-flow close.

## Implementation ownership

- Presentation-only implementation lives under `stoney_verify/setup_ui/`.
- `commands_ext` retains only a compatibility entry point and the existing canonical setup gate.
- No second slash command, setup owner, event listener, schema migration, role deletion, or channel deletion was added.

## Validation — exact head before this task-record update

- [x] Full unit suite: `902 passed, 9 warnings in 645.59s`.
- [x] Python compilation passed.
- [x] Committed whitespace check passed.
- [x] Managed ticket-category SQL smoke test passed.
- [x] Claim-first ticket security suite passed.
- [x] Application command-size diagnostics passed.
- [x] Profile runtime diagnostics passed.
- [x] Public setup audit passed.
- [x] Canonical public command-surface audit passed.
- [x] Public command/friction audit passed.
- [x] Public invite/permissions audit passed.
- [x] Setup safety audit passed.
- [x] Dank Design Smart Auto-Detect audit passed.
- [x] Role-truth ownership audit passed.
- [x] Event-boundary ownership audit passed.
- [x] `/dank` payload remained `1675/8000` with nine canonical global commands.
- [x] Review concern about test confirmations resetting was fixed, covered by focused tests, and resolved.
- [x] Branch was zero commits behind `main`; no unresolved review threads remained.

## Remaining gate

- [ ] Final documentation-head CI rerun passes.
- [ ] Merge PR #164.
- [ ] Deploy/restart Dank Shield on Discloud.
- [ ] Live-verify compact Setup Home, direct feature picker, Check Configuration, Test Features, confirmation persistence, and Finish Setup gating.

## Backlog

### DS-MEDIA-001 — Klipy direct GIF unfurl listener

After DS-SETUP-018 is merged, deployed, and live-verified, inspect the existing message-event ownership path and add one moderation-safe listener that recognizes verified `klipy.com` page URLs, resolves the direct GIF asset without arbitrary-host fetching, and replies with the direct media or a rich embed. Include bounded requests, redirect/host validation, caching, bot-loop prevention, permission/error handling, and focused listener/parser/security regressions.

## Previous completed task

DS-TICKETS-017 was deployed and live-verified from the user-provided `/dank setup` and ticket-choice screenshots.

## Single Active Task Lock

Do not begin another unrelated repair until DS-SETUP-018 reaches its Definition of Done.
