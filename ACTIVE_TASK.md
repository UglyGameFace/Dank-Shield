# ACTIVE TASK

## DS-PROFILE-SIGNATURE-COMPACT-002 — Compact forum-style profile signatures

**Status:** IMPLEMENTATION AND VALIDATION
**Branch:** `fix/compact-profile-signatures`
**Base:** current `main`

## Single Active Task Lock

Do not switch to unrelated implementation work until this correction reaches Definition of Done or the owner explicitly force-switches tasks.

## User-reported failures

- The live profile output was a large stacked Discord embed instead of a compact signature.
- The profile setup incorrectly included an **Add Welcome Channel** shortcut and welcome-channel status.
- Welcome/join configuration and profile signatures were mixed despite being separate systems.

## Root cause

`render_live_profile_card()` imported the detailed `_profile_card()` command embed and copied its full role/date field stack. The profile setup also treated the saved welcome channel as a convenience shortcut, creating cross-feature clutter.

## Scope

- Replace the full embed dump with a small horizontal PNG signature.
- Reuse the configured welcome-card visual language only: theme, palette, font family, and optional custom background.
- Do not reuse welcome/join behavior, channels, commands, or event ownership.
- Keep member privacy, platform link validation, debounce, cooldowns, durable ownership, restart reconciliation, and deletion guards unchanged.
- Remove every welcome-channel control and status line from profile setup.
- Keep the detailed `/dank profile` data available through its dedicated private/detail flows rather than stuffing it into the live signature.

## Intended compact layout

- 1080×220 horizontal image.
- Circular member avatar.
- Display name and server label.
- At most two short rows of selected role/date/platform chips.
- Optional validated platform link buttons below the image.
- No stacked profile fields, role count, giant date blocks, or join-event copy.

## Definition of Done

- [x] Actual live render/send path and setup caller inspected.
- [x] Root cause identified in the canonical runtime.
- [ ] Compact image renderer implemented.
- [ ] Live and preview sends support attachment-backed images.
- [ ] Add Welcome Channel and welcome-channel status removed from profile setup.
- [ ] Welcome/join systems remain unchanged.
- [ ] Focused renderer, lifecycle, interaction, and setup tests pass.
- [ ] Full CI and repository audits pass.
- [ ] Branch is conflict-free with `main`.
- [ ] Merge requires explicit owner approval.
