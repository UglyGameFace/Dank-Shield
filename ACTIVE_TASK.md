# ACTIVE TASK

## DS-PROFILE-STUDIO-LIVE-006 — Make Profile Signature themes visibly apply

**Status:** CLEAN IMPLEMENTATION / EXACT-HEAD CI REQUIRED
**Branch:** `fix/profile-theme-application`
**PR:** `#135`
**Base:** current `main`

## Single Active Task Lock

Do not switch to unrelated implementation work until selecting a Profile Signature theme visibly changes the saved preview and the deployed live signature.

## Live finding

The owner selected **420 Lobby Neon** and the confirmation stored the theme key, but the preview remained beige/gray. The member's inherited `profile` color mode continued overriding the theme's lime/purple palette, and the compact renderer reused one generic decoration for every built-in theme.

## Root causes

- The theme picker updated only `signature_theme`.
- Existing color/background overrides remained active, so the selected theme was mostly hidden.
- The profile renderer ignored each theme's motif and drew the same circles/diagonal lines for every preset.
- The server profile default used the invalid key `default` rather than the canonical built-in default.

## Changes

- Theme selection now atomically applies the chosen theme, theme colors, and theme background.
- Selecting **Server Default** restores the complete inherited server look.
- Every built-in theme receives a distinct compact motif while preserving readability.
- The server default theme key now points to the canonical built-in default.
- Regression tests prove 420 Lobby Neon renders lime/purple accents and all built-in motifs are distinct.

## Validation

- [x] Focused theme tests pass on the exact materialized source bundle.
- [x] Changed Python modules compile.
- [ ] Full unit suite passes on the clean exact head.
- [ ] Standalone checks and every repository audit pass on the clean exact head.
- [x] Branch is conflict-free with current `main`.
- [ ] Deployed Discord smoke confirms changing between at least two themes produces visibly different previews.

## Cleanup

- [x] Temporary materialization files were removed before final validation.
- [x] No runtime shim, monkey patch, duplicate renderer, or temporary migration path remains.

## Backlog

- Fix departed-member reconciliation consuming `Guild.fetch_members()` as a normal iterable instead of an async iterator.
- Review contradictory worker startup log wording.
- Enable automatic sharding before scaling toward the configured 100+ public guild expectation.
