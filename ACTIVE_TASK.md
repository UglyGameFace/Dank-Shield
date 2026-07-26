# ACTIVE TASK

## DS-COMMAND-UI-004 — UI-first command overhaul with complete Profile and Welcome setup

**Status:** OWNER-APPROVED / FINAL EXACT-HEAD CI
**Branch:** `fix/profile-command-payload-limit`
**PR:** `#132`
**Base:** current `main`

## Single Active Task Lock

Do not switch to unrelated implementation work until this correction reaches Definition of Done or the owner explicitly force-switches tasks.

## Root causes confirmed

- `/dank` accumulated whole nested feature trees and exceeded Discord's 8,000-character application-command group limit.
- Profile signatures exposed privacy/platform toggles but not the full appearance experience discussed with the owner.
- Profile styling still borrowed welcome-card configuration internally.
- The welcome shortcut was removed from Profiles without a dedicated Welcome & Join setup home.

## Implementation

- Canonical `/dank home` control center with guided buttons for Setup, Protection, Welcome & Join, Profiles, Members, Design, Roles, Logs, Diagnostics, Status, and Help.
- Public slash surface compacted to seven predictable children: `home`, `setup`, `profile`, `status`, `diagnostics`, `help`, and the upload-only `welcome` group.
- A fail-closed serialized-payload guard blocks future `/dank` growth above 7,600 characters.
- Dedicated Welcome & Join setup area for static welcome/start-here messages, join-only cards, join/leave announcements, previews, health, and attachment-command guidance.
- Dedicated Profile Signatures setup area for channels, allowed information, profile panel/roles, server appearance defaults, previews, and cleanup.
- Member signature studio for theme, font, colors, background, layout, avatar frame, privacy, platforms, roles, preview, and reset.
- Profile style state is independent from welcome-card state. A server manager may explicitly import the Join Card look once; later changes stay separate.
- Attach Files is now required before image signatures can be enabled.

## Validation

- [x] Deterministic clean-main build completed and all temporary patch workflows, scripts, and diagnostics were removed from the committed tree.
- [x] Changed Python modules compiled.
- [x] Focused UI/profile/welcome regression tests passed.
- [x] `tools/test_dank_command_payload.py` proved the compacted live command tree is below the 7,600-byte safety limit.
- [x] Full-suite diagnostic completed: 706 passed and six stale behavior contracts were identified.
- [x] All six stale contracts were corrected and their affected regression modules passed.
- [x] Setup-safety ownership contract now recognizes the dedicated Welcome & Join destination.
- [x] PR #131 was cleaned to two non-conflicting diagnostic files, passed exact-head CI, and was squash-merged as `515123016cb72cefba93d452440798b81247d8f7`.
- [x] PR #132 was merged with current `main` and is conflict-free.
- [ ] Exact-head full unit suite, standalone checks, and every repository audit pass on the synchronized branch.
- [x] No unresolved review threads or submitted blocking reviews.
- [x] Owner explicitly approved merging PRs #131 and #132.

## Remaining action

Squash-merge PR #132 immediately after the synchronized exact-head CI succeeds.

## Backlog observation — not active implementation

The supplied deployment log also shows departed-member reconciliation treating `Guild.fetch_members()` as a normal iterable instead of an async iterator (`TypeError: 'async_generator' object is not iterable`). That remains backlogged until this active command/profile/welcome correction is complete unless the owner explicitly force-switches.
