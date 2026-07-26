# ACTIVE TASK

## DS-PROFILE-STUDIO-LIVE-005 — Complete live Profile Signature studio smoke correction

**Status:** CLEAN IMPLEMENTATION / EXACT-HEAD CI REQUIRED
**Branch:** `fix/profile-followup-payload`
**PR:** `#134`
**Base:** current `main`

## Single Active Task Lock

Do not switch to unrelated implementation work until the Profile Signature studio opens, previews, saves, and navigates without Discord component or follow-up payload errors.

## Scope

- Correct the deployed `/dank profile` Preview failure.
- Inspect the shared response path used by appearance, privacy, platform, reset, and server-default actions.
- Remove the same unsafe optional payload behavior from the sibling Welcome & Join and `/dank` hub helpers introduced in the same UI overhaul.
- Add focused regression coverage and run the complete repository validation gate.

## Root cause

`_preview()` defers the interaction before rendering. The shared private-send helper then called `interaction.followup.send()` with `view=None` and `embed=None` keys still present. Discord.py rejects `None` for follow-up `view`, even though the initial interaction response path tolerated it.

## Changes

- Optional `content`, `embed`, `view`, and `file` fields are included only when present.
- The same safe payload construction is applied to Profile Signatures, Welcome & Join, and the compact `/dank` hub.
- Regression tests exercise the actual deferred/follow-up helper path and verify absent optional keys.

## Validation

- [ ] Focused regression tests pass.
- [ ] Changed Python modules compile.
- [ ] Full unit suite passes.
- [ ] Standalone checks and every repository audit pass.
- [x] Branch is conflict-free with current `main`.
- [ ] Live Discord smoke confirms profile Preview and at least one appearance save.

## Cleanup

- [x] Temporary patch transport files were removed before final validation.
- [x] No compatibility shim, monkey patch, duplicate helper, or temporary runtime path remains.

## Backlog

- Fix departed-member reconciliation consuming `Guild.fetch_members()` as a normal iterable instead of an async iterator.
- Review contradictory worker startup log wording after the active profile task reaches Definition of Done.
- Enable automatic sharding before scaling toward the configured 100+ public guild expectation.
