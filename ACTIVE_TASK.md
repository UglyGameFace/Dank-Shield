# ACTIVE TASK

## ID
DS-SHARE-ROUTER-265 — Restore native Share Router runtime and protect proxy infrastructure

## Status
IMPLEMENTATION IN PROGRESS — exact-head validation pending

## Single active-task lock
Only the Share Router production-runtime restoration is active. Do not admit
unrelated cleanup, redesign, or feature work until this task is investigated,
implemented, tested, validated on the exact final head, cleaned up, merged, and
verified on main.

## Previous task closed
PR #264 (lifecycle-card long-tail Unicode fallback) merged as
`758145be9e20d6d26240908980a6d1a4948616eb`. Its exact final head
`f0f216ed5ea780b4edd0355d237b4a297f6fd321` passed the required validation,
and the resulting main commit reports `discloud/commit: success`.

## User-visible problem
The server still contains the private `SHARE ROUTES` proxy category used to
share links from mobile into channels that Discord may omit from the native share
sheet, especially age-restricted destinations. The feature implementation still
exists in the repository, but current production boot does not activate it, so
the visible proxy channels can remain while routing is dead.

The same proxy resources were also eligible for Dank Design renaming, which can
turn the plain share-sheet names into decorative names and make the old exact-name
hub discovery create or miss duplicate infrastructure.

## Root cause
- Share Router lives only in the historical
  `startup_guards/share_router_guard.py` module.
- Normal production boot intentionally does not bulk-import historical startup
  guards, and no current owner imports this module.
- The compact public command surface would prune the old direct
  `/dank share-router` child even if the historical module were imported.
- Dank Design scans all editable categories/channels and has no structural
  exclusion for Share Router infrastructure.
- The historical hub repair looks up `🔗 SHARE ROUTES` and child names by exact
  string, so a previously styled hub is not reliably recognized.

## Execution path
Current broken path:
Discord mobile share → `share-*` source → no active Share Router listener.

Historical path:
`startup_guards/share_router_guard.py` import side effect
→ `install()`
→ `bot.add_listener(_route_message, "on_message")`
→ local `data/share_routes.json`
→ target channel.

Dank Design conflict:
`public_design_studio._editable_channels()`
→ full-plan / Smart Auto-Detect / separator-only plan
→ Share Router category and source channels treated as normal design resources.

## Scoped implementation
- promote Share Router into a normal production-owned runtime with explicit,
  idempotent listener installation and no import side effect;
- keep the existing per-guild route file format/path readable so deployed saved
  routes remain compatible;
- expose Share Router from the existing menu-first Community Tools UI instead of
  adding a new public slash-command child;
- use Dank Shield's searchable guild resource browser for target selection;
- create/repair the canonical private proxy hub without deleting unrelated
  channels or overwriting unrelated explicit permission entries;
- recognize and repair previously decorated Share Router names rather than
  creating a second hub;
- require proxy sources to remain non-age-restricted and private from
  `@everyone`;
- require the sender as well as the bot to have normal Discord view/send
  permissions in the destination before routing;
- exclude the reserved Share Router hub and canonical proxy children from every
  Dank Design path that uses the shared editable-resource scan;
- retire the historical startup-guard implementation to a side-effect-free
  compatibility wrapper;
- add focused regression coverage for ownership, resource recognition, UI
  reachability, Designer exclusion, route safety, and legacy persistence
  compatibility.

## Compatibility / safety
- no route IDs are hardcoded;
- no existing saved route file is renamed or discarded;
- no destination age restriction is changed;
- Share Router does not grant destination viewing access;
- no unrelated guild permissions are widened;
- no automatic deletion of duplicate/legacy channels;
- the compact public command surface remains unchanged.

## Validation
Pending implementation head:
- committed-diff whitespace check
- Python compile
- focused Share Router tests
- full unit suite
- Dank Shield CI
- Dank Design Regression CI
- application-command size diagnostics
- changed-file and duplicate-implementation cleanup inspection
- branch currentness against main

## Backlog
None admitted from this task.

## Next step
Implement the native runtime/UI/resource identity boundary and focused
regressions, then run the exact-head validation gate.
