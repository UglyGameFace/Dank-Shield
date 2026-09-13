# ACTIVE TASK

## DS-SEC-039-HF1 — Fix AntiNuke managed-integration readiness

**Status:** IN PROGRESS / VALIDATION PENDING  
**Branch:** `fix/antinuke-managed-role-readiness`  
**Base:** `a70a82144e52d1057025ba2723bf87ce18638699` (`main`, merge of PR #202)  
**Started:** 2026-09-12

## Objective

Make AntiNuke actually usable after the DS-SEC-039 hardening by removing false readiness blockers without weakening real containment requirements.

## Production failure

A server owner attempted to enable AntiNuke and received:

`Role hierarchy: move Dank Shield above managed @DISBOARD.org cannot be stripped`

The server role hierarchy had already been checked and Dank Shield was above the managed integration role. AntiNuke therefore remained disabled and a subsequent nuker test had no AntiNuke protection active.

## Root cause

`_dangerous_hierarchy_blockers()` treated every managed role carrying dangerous permissions as an unconditional containment blocker because the role itself cannot be stripped. It did not distinguish between:

- a managed integration/bot role whose non-owner holders are below Dank Shield and can be removed from the guild; and
- a managed dangerous role whose holder is at/above Dank Shield and therefore genuinely cannot be contained.

The channel-overwrite readiness path contained the same conceptual false-positive for managed roles granted dangerous overwrite permissions.

## Runtime path

`/dank protection` → AntiNuke toggle → candidate config with `antinuke_enabled=True` → `antinuke_permission_health()` → hierarchy/overwrite readiness → save only when readiness is clean.

The save layer preserves previous cached truth when the database is unavailable, so it does not falsely report AntiNuke enabled after a failed authoritative write.

## Changes made

- Added managed-role readiness finalization that removes the legacy `managed @Role cannot be stripped` blocker only when every non-owner holder is actually manageable by Dank Shield.
- Added equivalent managed-role logic for dangerous channel-overwrite readiness.
- Real blockers remain intact when any managed dangerous holder is at/above Dank Shield.
- Empty managed roles and managed roles held only by safely removable actors no longer disable AntiNuke.
- Kept View Audit Log, Manage Roles, Kick Members, Manage Channels, Manage Webhooks, Manage Server, hierarchy, owner, and Discord platform limits intact.

## Tests

`tests/test_antinuke_managed_integration_readiness.py` covers:

- DISBOARD-style managed dangerous integration below Dank Shield does not block enablement;
- dangerous managed integration above Dank Shield still blocks;
- dangerous managed-role channel overwrite below Dank Shield does not block;
- dangerous managed-role channel overwrite above Dank Shield still blocks.

## CI state

Pending exact-head PR validation.

## Cleanup / risks

- This is a production hotfix using the already-existing AntiNuke finalizer ownership point. It does not introduce another listener or punishment policy.
- The canonical readiness implementation can absorb these invariants during later runtime-patch consolidation; behavior must remain identical when that cleanup happens.
- Structural backup/restore remains outside AntiNuke scope.

## Next step

Open the hotfix PR, run full exact-head CI, review the final diff, merge only when green, then retest enabling AntiNuke before another destructive test.
