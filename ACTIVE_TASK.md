# ACTIVE TASK

## DS-SEC-044 — Hostile bot re-entry race and integration persistence

**Status:** IMPLEMENTED ON BRANCH / VALIDATION PENDING
**Branch:** `fix/antinuke-hostile-reentry-race`
**Base:** `fd7315ed7a0f5288c0484443c5f981aa8f64da45`

## Live acceptance failure

The Sep 13 live GANG-Nuker retest showed Dank Shield banning the known hostile identity, but the identity still completed destructive actions before removal and the same installer repeatedly recreated the bot/integration pair.

Confirmed contributing paths:

- Guild `1476736723953385514` initially booted with AntiNuke OFF; destructive events at 16:57/16:58 were explicitly observed while disabled.
- Once containment was active, known-hostile re-entry still performed a `refresh=True` reputation lookup before the ban, leaving a post-admission race window.
- Discord bot/integration admission is observed after the action; there is no pre-join veto callback.
- Physical guild owners cannot be contained by a Discord bot, so owner-originated hostile re-adds must block the hostile target and correlated integration rather than punish the owner.
- `integration_create` was covered for detection/containment but did not have creation rollback equivalent to channels/webhooks.

## Implemented

- Added `anti_nuke_reentry_race_runtime.py` as the final AntiNuke runtime layer.
- Uses already-cached/local hostile reputation before any authoritative reputation refresh for known identities.
- Adds a fast member-join block for already-known hostile IDs.
- Adds a fast known-hostile bot-add path that bans the target before integration enumeration or further reconciliation.
- Correlates `integration_create` and `bot_add` in both event orderings for the same installer.
- Removes integrations correlated with a known-hostile re-add even when the installer is the physical guild owner.
- Rolls back untrusted integration creation immediately in Contain while preserving explicitly trusted normal staff behavior.
- Preserves the owner platform boundary and existing Alert/disabled semantics.
- Installs after the final Contain/Strict Lockdown product policy so it operates on the canonical final policy stack.
- Added focused regression coverage for hot reputation, integration-before-bot, bot-before-integration, untrusted integration rollback, trusted Contain behavior, fast member join, and startup order.

## Validation required

- Focused race/integration regression suite green.
- Full exact-head Dank Shield CI green.
- Diff review shows only this live-acceptance repair.
- After merge/deploy, repeat the same GANG-Nuker re-entry test and confirm no destructive action lands before the hostile identity/integration is removed.

## Platform boundary

Discord can still admit an owner-authorized bot before Dank Shield receives a gateway/audit event. This repair removes avoidable DB latency and correlated integration persistence, but no Discord bot can mathematically pre-veto an action performed by the physical guild owner or an already-authorized actor before Discord emits the event.
