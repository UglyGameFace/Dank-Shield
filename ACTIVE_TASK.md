# ACTIVE TASK

## DS-PROFILE-SIGNATURE-COMPACT-002 — Compact forum-style profile signatures

**Status:** FINAL VALIDATION / LIVE OWNER REVIEW
**Branch:** `fix/compact-profile-signatures`
**PR:** #130
**Base:** current `main`

## Single Active Task Lock

Do not switch to unrelated implementation work until this correction reaches Definition of Done or the owner explicitly force-switches tasks.

## User-reported failures

- The live profile output was a large stacked Discord embed instead of a compact signature.
- The profile setup incorrectly included an **Add Welcome Channel** shortcut and welcome-channel status.
- Welcome/join configuration and profile signatures were mixed despite being separate systems.

## Root cause

`render_live_profile_card()` imported the detailed `_profile_card()` command embed and copied its full role/date field stack. The profile setup also treated the saved welcome channel as a convenience shortcut, creating cross-feature clutter.

## Delivered correction

- Replaced the stacked profile embed with a 1080×220 horizontal PNG signature.
- Reused only the configured welcome-card visual language: theme, palette, font family, custom colors, and optional custom background.
- Kept welcome/join channels, commands, event behavior, and ownership separate.
- Limited the signature to a circular avatar, display name, server label, and at most two compact rows of selected role/date/platform chips.
- Retained validated platform link buttons below the image.
- Removed **Add Welcome Channel** and saved welcome-channel status from the active profile setup UI.
- Kept member-first privacy, field restrictions, debounce, replacement cooldowns, durable ownership, restart reconciliation, and verified bot-only deletion safeguards.
- Kept the detailed profile available through its dedicated private/detail flows rather than forcing it into every live signature.

## Validation completed

- [x] Actual live render/send path and setup caller inspected.
- [x] Root cause identified in the canonical runtime.
- [x] Compact image renderer implemented.
- [x] Live and preview sends support attachment-backed images.
- [x] Add Welcome Channel and welcome-channel status removed from the active profile setup.
- [x] Welcome/join systems remain unchanged.
- [x] Focused renderer test passed.
- [x] Focused runtime lifecycle tests passed.
- [x] Focused privacy/interaction tests passed.
- [x] Focused setup-separation tests passed.
- [x] Focused integration and safety tests passed.
- [x] Temporary diagnostic workflow removed.
- [ ] Final exact-head full CI and repository audits pass.
- [ ] Branch is conflict-free with current `main` at final head.
- [ ] Owner reviews the corrected live Discord appearance.
- [ ] Merge requires explicit owner approval.
