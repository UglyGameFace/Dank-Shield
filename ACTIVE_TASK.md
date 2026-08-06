# ACTIVE TASK

## DS-SETUP-020 — Entitled ID-verification setup selection and VC permissions regression

**Status:** MERGED — live Discloud/Discord verification pending
**Merged PR:** `#171`
**Main commit:** `c81a4b5d27c1aacdbfcc44b576ffbfee6931861e`
**Started:** 2026-08-06

## Scope

Reported from guild `1357215261001912320`, which has explicit access to ID/Web Verification.

1. Let the owner independently select Simple Verify, Voice Verify, and entitled ID/Web Verify.
2. Never force Simple Verify merely because Voice Verify or ID/Web Verify is enabled.
3. Protect setup toggles from stale interaction snapshots and concurrent writes.
4. Require/create only the roles, channels, and permissions needed by enabled modules.
5. Never require or create a Simple Verify channel while Simple Verify is OFF.
6. Enforce a session-locked Voice Verify channel baseline while granting active participants voice and video/screenshare access.
7. Preserve strict entitlement separation for ordinary guilds.

## Root causes and findings

- Setup dependency logic treated Simple Verify as the verification master switch and forced it ON for specialized verification modes.
- Several callbacks saved stale whole-state snapshots, allowing rapid or old interactions to overwrite newer choices.
- Guided/default setup used the aggregate verification flag when deciding whether a Simple Verify channel was required.
- Voice Verify permission repair had multiple writers and did not consistently recover uncached channels.
- Old configured-role overwrites could retain `connect`, `speak`, or `stream` grants after role/config changes.
- The per-guild setup lock cache held strong references indefinitely.

## Implemented changes

- [x] Added canonical setup state for independent Simple, Voice, and ID/Web verification modules.
- [x] Kept ID/Web Verify entitlement-gated for approved guilds.
- [x] Serialized per-guild setup edits and rejected stale interaction state without overwriting saved choices.
- [x] Limited the Simple Verify channel requirement to Simple Verify only.
- [x] Consolidated Voice Verify baseline permission reconciliation.
- [x] Added uncached configured-channel recovery with `fetch_channel` fallback.
- [x] Removed stale role-level voice/video grants while preserving active per-member session grants.
- [x] Granted active Voice Verify participants Discord video/screenshare permission.
- [x] Replaced the unbounded strong lock cache with weakly held locks.

## Validation completed

- [x] Entitled ID-only and ID+Voice state normalization.
- [x] Ordinary guilds cannot self-enable ID/Web Verify.
- [x] Voice Verify dependencies enable Tickets/Logs without enabling Simple Verify.
- [x] Stale setup interactions refresh instead of overwriting newer state.
- [x] ID/Voice-only setup does not request a Simple Verify channel.
- [x] Baseline Voice Verify roles cannot connect, speak, or stream outside an active session.
- [x] Active requester/assigned staff session grants include voice and video/screenshare.
- [x] Uncached Voice Verify channel recovery regression.
- [x] Stale role-overwrite cleanup regression with active member overwrite preservation.
- [x] Setup lock-cache release regression.
- [x] Temporary workflows, encoded payloads, migration tools, and validation PRs removed/closed.
- [x] Every PR review thread resolved.
- [x] PR `#171` squash-merged into `main`.

## Remaining live gate

- [ ] Pull current `main` and deploy to Discloud.
- [ ] In guild `1357215261001912320`, confirm ID/Web Verify and Voice Verify can be selected while Simple Verify remains OFF.
- [ ] Confirm Continue Setup does not ask for or create a Simple Verify channel in that configuration.
- [ ] Confirm an active Voice Verify session grants requester/assigned staff connect, speak, and video/screenshare access, then removes those member grants when the session ends.
- [ ] Confirm unrelated/stale roles cannot connect, speak, or stream.

## CI note

GitHub did not emit a fresh repository Actions run for the connector-created final head or squash merge. The final review findings were fixed and focused functional regressions were added, but post-merge repository Actions are still an external validation gap. Do not call DS-SETUP-020 fully complete until the live deployment checks above pass.

## Cleanup status

- [x] Removed one-shot workflows and encoded migration payloads.
- [x] Removed temporary direct/finalizer tools.
- [x] Closed temporary PRs `#172` and `#173` without merging.
- [x] Removed the conflicting/stale Voice Verify permission paths identified by review.
- [x] Confirmed PR `#171` contains only runtime code, tests, and this task record.

## Blockers

- Requires the user environment to pull/deploy `main` to Discloud and perform the live Discord flow.

## Backlog preserved by the Single Active Task Lock

### DS-STATS-019 — Durable Dank Stats invite-block counting

Merged and code-revalidated, but live Discloud/Discord verification remains pending. It stays paused until DS-SETUP-020 passes live verification.

### DS-SETUP-019 — Cleanup confirmation modal crashes

Backlog only. Do not begin before DS-SETUP-020 reaches its Definition of Done unless the user issues the exact force-switch instruction.

### DS-TICKET-020 — Guild owner final override for ticket actions

GitHub issue `#168`. Backlog only.

### DS-MEDIA-001 — Klipy direct GIF unfurl listener

Backlog only.

## Definition of Done

DS-SETUP-020 is complete only after implementation, tests, regression checks, syntax/static validation, cleanup, conflict inspection, merge, deployment, and live guild verification all pass.
