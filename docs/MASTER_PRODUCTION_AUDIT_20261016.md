# Dank Shield production master audit: baseline and migration order

**Master tracking:** [issue #602](https://github.com/UglyGameFace/Dank-Shield/issues/602).  
**Historical branch debt and Discloud main cutover:** [issue #597](https://github.com/UglyGameFace/Dank-Shield/issues/597).  
**Source baseline:** `main@8850e470563d14c4a10806a58a64d13af9060f93`, October 16, 2026.  
**Scope of this first slice:** repair a demonstrable legacy production-audit command-contract mismatch and establish the remaining audit plan. **Not a completed whole-repository sign-off.**

## Fresh Git-tree measurements

GitHub's full, nontruncated recursive tree contained 1,239 tracked file blobs, with these overlapping counts:

| Area | Tracked files |
| --- | ---: |
| `stoney_verify/` files | 594 |
| `stoney_verify/**/*.py` | 576 |
| `stoney_verify/startup_guards/` | 122 |
| `stoney_verify/commands_ext/` | 133 |
| `tests/*.py` | 395 |
| `.github/workflows/` YAML | 39 |
| `docs/` | 48 |
| Live Git branches before this audit PR | 69 |

These counts indicate **audit surface, not runtime activation or proof of defects**. Do not infer that all 122 startup guards run; `startup_guards/__init__.py` expressly marks its historical registry inert.

## Confirmed production-identity problem and fix #1

`stoney_verify/commands_ext/public_production_audit.py` requires `/stoney`, `/ticket-intake`, `/ticket-category` and `/ticket-panel`, despite `stoney_verify/command_surface_contract.py` defining the final public command surface as:

`/dank`, `/captions`, `/cinema`, `/mod`, `/movie`, `/role`, `/ticket`, `/tickets`, `/toke`, `/verify` and the **View Dank Profile** *user context-menu command*.

The old audit's required ticket-intake/category subcommands are also invisible at the final top level. An audit invoked after public compaction could report the intentional removal of those legacy commands as a failure. The first PR updates **only the read-only audit's expectations**:

- derive slash roots from the canonical contract, excluding the context menu;
- check the context menu with the proper Discord **user context command type**;
- check approved direct `/dank home`, `/dank purge`, `/dank setup` and `/dank upload` doorways;
- preserve real blocking diagnostics when any expected command or context menu is missing;
- leave all registration, compaction, permissions, persistent views, storage and Discloud deployment untouched.

Focused tests execute the extracted audit logic with a simulated final command tree, including one intentionally missing slash command, missing `/dank` child and missing context-menu check. This is a test of the diagnostic contract, **not** proof that a particular production guild has been tested.

## Product brand vs executable identity: inventory

| Identity | Current behavior | Migration rule |
| --- | --- | --- |
| Displayed brand | README and public command contract say **Dank Shield** | Keep product copy consistent with canonical UI |
| Python package | `stoney_verify/` is imported by `main.py`, commands, bot runtime and test/tool modules | **Do not rename in this slice.** Map every import + loader/static-data path and only perform a reviewed, atomic source migration with full CI and startup acceptance. No alias/shim or import monkey patch |
| Discloud app identity | `discloud.config`: `ID=stoneyverify`, `TYPE=site`, `MAIN=main.py`, `NAME=Dank Shield Helper` | Existing-app ID is operational state, **not cosmetic text**. Do not delete/recreate/rebind app while auditing |
| Persistent Discord identifiers | Examples include `stoney_verify:verify_ui:v9` footer marker and `sv:verify:*` component IDs | Preserve old posted panels/messages and their handlers during any future naming release. Never global-replace persisted IDs |
| Legacy rebrand tool | `scripts/rebrand_dank_shield.py` deliberately preserved the package name, `/stoney`, and old IDs | Treat this as historical migration tooling, not a safe current production script. Review or retire in its own slice |
| Compatibility startup | Both `sitecustomize.py` and `usercustomize.py` touch verify command registration; `stoney_verify.app` depends on deliberate import order | Verify whether each hook is actually needed, then move owned logic to its native module under tests before removing hooks |
| Historical root monkey patches | Several root `runtime_*_patch.py` modules still exist and other code contains `builtins.__import__` interception | Presence alone does not establish execution. Trace explicit import reachability and compare canonical behavior, then retire one owner at a time |
| Recovery branches | 69 remaining before audit PR, including changed-head/unmerged and backup refs | Continue guarded #597 diff/ancestry review; keep linked Discloud and recovery refs, no blanket deletion |

## Sequenced acceptance gates

1. **Master-audit source of truth:** issue #602 owns one implementation slice at a time, with exact `main` SHA, source evidence, rollback and test/soak status. #597 keeps branch/Discloud cutover history.
2. **Canonical public contract:** first PR green on exact-head CI; verify that correct final commands no longer produce false audit failures and that missing commands still fail closed. This is a read-only audit function fix.
3. **Startup + patch ownership:** enumerate imports from `main.py`, `sitecustomize.py`, `usercustomize.py`, `stoney_verify.app`, commands and feature loaders. Prove individual listeners/workers/guards active or dormant. Never start a bulk historical guard loader.
4. **Stoney Verify package migration:** inventory all `from stoney_verify`, `import stoney_verify`, import-string, scripts, workflows, package asset/resource paths and old external IDs. Design an atomic native migration only after path-boundary tests and rollback plan. Preserve durable config keys and Discord component IDs, avoid introducing dual app singleton or alias wrapper.
5. **Behavioral parity:** incrementally audit ticket intake, verification, member lifecycle and purge, AntiNuke, Share Router, Cinema/media cache, modlog, moderation, roles, `/toke`, captions/voice. Merge **only** proved current-main fixes from historical branches, never stale snapshots.
6. **Persistence and release:** check Supabase/PostgreSQL ownership, migration safety, backups/restores, growth and retention. Keep operations read-only until off-host recovery and Discloud exact-code/DB state are verified. Audit stale CI/workflows before retiring them.
7. **Production acceptance:** full exact-head test matrix plus Android and real Discloud interactions, live bot SHA, no command regression, ticket/verify/moderation canaries, backup/restore evidence, guarded rollback. Green CI alone cannot attest this.

## Change-control constraints

Do not modify `stoneyverify` Discloud ID, linked `aaa-cinema-pre-pregateway-bind` branch, production database, secrets, persistent Discord custom IDs or shard/startup ownership as part of this first slice. No app deletion, broad text replacement, introduction of new global import hooks, or cross-feature fixes without their own evidence. Do not claim the package rename, complete master audit, or main cutover is finished.
