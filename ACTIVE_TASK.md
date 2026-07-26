# ACTIVE TASK

## DS-COMMAND-UI-004 — UI-first command overhaul with complete Profile and Welcome setup

**Status:** IMPLEMENTATION / VALIDATION
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

## Validation required

- [ ] One-time patch workflow succeeds and removes all temporary patch files/workflows.
- [ ] Changed Python modules compile.
- [ ] Focused UI/profile/welcome tests pass.
- [ ] `tools/test_dank_command_payload.py` proves the exact live tree is below 7,600.
- [ ] Full unit suite and every repository audit pass.
- [ ] PR is zero commits behind `main` with no unresolved review threads.
- [ ] Live Discord smoke proves slash sync succeeds and the new menus open correctly.
- [ ] Merge requires explicit owner approval.

## Backlog observation — not active implementation

The supplied deployment log also shows departed-member reconciliation treating `Guild.fetch_members()` as a normal iterable instead of an async iterator (`TypeError: 'async_generator' object is not iterable`). That remains backlogged until this active command/profile/welcome correction is complete unless the owner explicitly force-switches.
