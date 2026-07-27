# ACTIVE TASK

## DS-PROFILE-STUDIO-LIVE-009 — Restore live signature delivery

**Status:** CLEAN IMPLEMENTATION / EXACT-HEAD CI REQUIRED
**Branch:** `fix/live-profile-signature-runtime-delivery`
**PR:** #139
**Base:** current `main`

## Confirmed findings

- The delayed runtime now uses the triggering message author first, but same-speaker cooldown still trusts stored database state without confirming the referenced Discord card exists.
- A missing/deleted old signature can therefore suppress every new message during cooldown while nothing is visible in Discord.
- Runtime configuration reads use a potentially stale cached guild config.
- Several skip paths still provide inadequate production evidence.

## Scope

- Verify stored card existence and bot ownership before cooldown suppression.
- Remove stale state when the referenced card is missing.
- Refresh guild configuration for live message evaluation.
- Log configured-channel permission, render, send, stale-state, cooldown, and success outcomes.
- Preserve the clear member-facing Live Signature ON/OFF control from PR #137.

## Validation

- [x] A real message author posts even when `guild.get_member()` returns `None`.
- [x] Missing stored cards never suppress a replacement signature.
- [ ] Existing valid same-speaker cards remain cooldown-suppressed.
- [ ] Failed send/state-write safety remains intact.
- [x] Focused tests and changed-module compilation pass.
- [ ] Full unit suite and repository audits pass on exact clean head.
- [ ] Deployed designated-channel message produces `✅ live_profile_card posted` and a visible signature.

## Backlog

- Fix departed-member reconciliation async-generator handling.
- Review contradictory worker startup wording.
- Enable automatic sharding before 100+ public guilds.
