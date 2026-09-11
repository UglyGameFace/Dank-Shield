# ACTIVE TASK

## DS-SETUP-038 — Restore public `/dank setup` onboarding entrypoint

**Status:** IMPLEMENTED / VALIDATION PENDING
**Branch:** `fix/public-dank-setup-entrypoint-199`
**Base:** `9dafb8b8371dfb7959f0bd0abf1b985464865fb2` (`main`, squash merge of PR #198)
**Started:** 2026-09-11

## User-visible failure

New server owners have no visible `/dank setup` command even though Dank Shield has a complete canonical setup hub. The setup command is registered correctly, then the final public command compactor removes every `/dank` child and only restores Home and Upload. The final Exit layer later restores Purge, leaving the deployed surface as `home`, `purge`, `upload`.

## Authoritative root cause

`stoney_verify/commands_ext/public_setup_group.py` owns the existing unified setup flow. `stoney_verify/commands_ext/public_command_surface_v2.py` was deleting that command object during final compaction, while `stoney_verify/command_surface_contract.py` explicitly classified `setup` as hidden. This was command-surface policy drift, not missing setup functionality.

## Repair

- Capture the already-registered canonical `setup` command before compact-v2 removes children.
- Re-add that exact command object after Home, preserving its existing callback, permissions, and setup ownership.
- Keep the final public `/dank` surface limited to `home`, `purge`, `setup`, `upload`.
- Remove only the unified `setup` command from the hidden-child contract.
- Keep advanced/internal aliases such as `setup-tickets`, `setup-verify`, `setup-logs`, `setup-status`, and repair/migration setup commands hidden.
- Keep the existing `/dank home` Setup & Settings button and all underlying setup modules unchanged.

## Validation required

- focused command-surface/setup regressions and audits;
- Python compile and diff check;
- full repository pytest/static audit gate on one exact head;
- final scope/review/base-drift check;
- squash merge only after exact-head green;
- post-merge main CI and Discloud exact-commit deployment success;
- production acceptance: `/dank setup` appears and opens the canonical setup hub for an authorized server owner without exposing retired setup aliases.
