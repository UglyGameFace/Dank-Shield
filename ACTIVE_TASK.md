# ACTIVE TASK

## ID
DS-SHARE-ROUTER-265 — Restore native Share Router runtime and protect proxy infrastructure

## Status
IMPLEMENTATION COMPLETE — exact-head CI passed; merge authorized

## Single active-task lock
Only the Share Router production-runtime restoration is active until PR #265 is
merged and the resulting main commit is verified. No unrelated implementation
work is admitted before that verification.

## Previous task closed
PR #264 (lifecycle-card long-tail Unicode fallback) merged as
`758145be9e20d6d26240908980a6d1a4948616eb`. Its exact final head
`f0f216ed5ea780b4edd0355d237b4a297f6fd321` passed the required validation,
and the resulting main commit reports `discloud/commit: success`.

## User-visible problem
The server still contains the private `SHARE ROUTES` proxy category used to
share links from mobile into channels that Discord may omit from the native share
sheet, especially age-restricted destinations. The implementation still existed
in the repository, but production boot no longer activated it, so the visible
proxy channels could remain while routing was dead.

The same proxy resources were eligible for Dank Design renaming, which could
turn the plain share-sheet names into decorative names and make the old exact-name
hub discovery create or miss duplicate infrastructure.

## Root cause
- Share Router lived only in the historical
  `startup_guards/share_router_guard.py` module.
- Normal production boot intentionally does not bulk-import historical startup
  guards, and no current owner imported that module.
- The compact public command surface would not retain the old direct
  `/dank share-router` child as the supported configuration surface.
- Dank Design scanned editable categories/channels without a structural
  exclusion for Share Router infrastructure.
- Historical hub repair looked up `🔗 SHARE ROUTES` and child names by exact
  string, so a previously styled hub was not reliably recognized.

## Final execution path
Production runtime:
`commands_ext.register_all_commands()`
→ `public_share_router.register_public_share_router()`
→ `share_router_runtime.ensure_share_router_runtime(bot)`
→ one idempotent `on_message` listener
→ existing per-guild Share Router persistence
→ configured target channel.

Configuration UI:
Community Tools
→ Share Router
→ Create / Repair Hub
→ Add / Change Route
→ Dank Shield searchable guild resource browser.

Dank Design boundary:
all shared editable scans and exact editor paths reject reserved Share Router
resources, and the transactional apply service performs a final reserved-resource
check before renaming.

## Implemented changes
- promoted Share Router to a normal production-owned runtime with explicit,
  idempotent listener installation and no import side effect;
- preserved the existing per-guild route file format/path so deployed saved
  routes remain compatible;
- exposed Share Router from the existing menu-first Community Tools UI without
  adding a new public slash-command child;
- used Dank Shield's searchable guild resource browser for target selection;
- added canonical private proxy hub create/repair without deleting unrelated
  channels or discarding unrelated explicit permission overwrite fields;
- recognized and repaired previously decorated Share Router names;
- required proxy sources to remain non-age-restricted and private from
  `@everyone`, failing closed at runtime if privacy drifts;
- required normal destination permissions for the sender and required bot
  source/destination permissions, including source cleanup permission when
  delete-source is enabled;
- rejected self-routes and kept private proxy channel mentions out of forwarded
  public text;
- structurally excluded Share Router resources from Dank Design batch planning,
  Smart Auto-Detect inputs, exact editors, format/protection rules, direct
  rename paths, preflight, apply, and undo;
- retired the historical startup-guard implementation to a side-effect-free
  compatibility shim;
- documented the production ownership correction;
- added focused regression coverage for ownership, identity, menu reachability,
  Designer isolation, route safety, overwrite preservation, and legacy
  persistence compatibility.

## Compatibility / safety
- no route IDs are hardcoded;
- no existing saved route file is renamed or discarded;
- no destination age restriction is changed;
- Share Router does not grant destination viewing access;
- no unrelated guild permission overwrite fields are discarded;
- no automatic deletion of duplicate/legacy channels;
- the compact public command surface remains unchanged.

## Validation
Exact implementation head before this bookkeeping-only status commit:
`62aedea48515a6b49df87823a2eaf13d15975e5e`

All required workflows completed successfully on that head:
- Dank Shield CI
- Dank Design Regression CI
- Application Command Size Diagnostics
- Profile Runtime Diagnostics
- Schema Authority SQL
- Smart Stickies 029 / Community Tools 031
- Ticket Owner Emergency Override

The branch was mergeable and current with main when validated. This status-only
commit must receive the same required exact-head CI before merge.

## Cleanup
- no unresolved review threads;
- no duplicate production Share Router owner remains;
- historical startup-guard path is compatibility-only and side-effect free;
- no unrelated feature work included.

## Backlog
None admitted from this task.

## Next step
Run exact-head CI on this bookkeeping-only commit, then mark PR #265 ready,
merge it, and verify the resulting main commit.
