# ACTIVE TASK

## Active task / desired outcome

**P0-PROTECTION-IMPORT-001 — Native Protection Center Import Pack + retire dormant Protection patch guards**

Preserve the Protection Center starter filter-pack feature while moving its UI, normalization, persistence, and interaction behavior into the real `public_protection_center.py` owner. Retire the dormant startup-guard files that could mutate command/UI behavior if accidentally imported.

## Status

**IMPLEMENTED — exact-head validation pending**

Branch: `audit/native-protection-import-pack-20260921`

Base: current `main` after PR #291.

## Previous task closed

**P0-CMD-CLEANUP-001** is complete.

PR #291 merged as:

`b5962860f2411b9398d3203756201af26447e59d`

Exact implementation head:

`30f93f1f3b95e265f89a159e02dd5d370b51950a`

Termux validation passed:

- diff integrity;
- Python compile;
- command cleanup retirement/native owner tests;
- ticket doctor audit;
- startup/ownership tests;
- full suite: **1796 passed, 79 warnings, 0 failures**.

Post-merge verification confirmed:

- `slash_command_cleanup.py` absent;
- `ticket_panel_command_epoch_guard.py` absent;
- historical startup metadata clean;
- ticket doctor detached from the epoch shim;
- native `DankCommandTree` owner and unchanged-sync regression present.

## Root cause / ownership finding

Three Protection Center startup-guard files had no production importer but still contained import-time mutation behavior:

### `protection_center_command_guard.py`

- changed `commands_ext._ALLOWED_DANK_CHILDREN`;
- changed `commands_ext._CONFUSING_DANK_CHILDREN`;
- removed `automod` / `spam` children from `dank_group` at import time.

The canonical command registrar already owns this shape:
- `/dank protection` is registered natively;
- `automod` and `spam` are already in the confusing/legacy child metadata;
- production runtime pruning is disabled by default.

### `protection_import_button_patch.py`

- replaced `ProtectionCenterView.__init__` to inject an Import Pack button;
- duplicated a fallback path to the manual-import guard.

### `protection_pack_manual_import_guard.py`

- dynamically attached the Import Pack button;
- duplicated filter normalization;
- duplicated guild-config persistence;
- owned a modal outside the canonical Protection Center module.

The Import Pack feature itself is useful and must be preserved, so deletion without migration would be incorrect.

## Native owner after migration

`stoney_verify/commands_ext/public_protection_center.py` now owns:

- native `Import Pack` button on `ProtectionCenterView`;
- native `StarterPackImportModal`;
- canonical filter normalization through existing `_clean_filter_item` / `_csv_items`;
- bounded merge behavior through `_merge_imported_filter_terms`;
- existing 700 imported-term limit;
- existing 22,000-character Automod filter budget;
- canonical guild-config persistence through `_save_automod`;
- native permission checking;
- native `run_guarded_interaction` protection for button and modal actions.

## Scope

In scope:

- migrate Import Pack into `public_protection_center.py`;
- preserve old limits and saved metadata fields;
- add focused behavior/ownership regressions;
- delete:
  - `startup_guards/protection_center_command_guard.py`;
  - `startup_guards/protection_import_button_patch.py`;
  - `startup_guards/protection_pack_manual_import_guard.py`;
- remove their inert startup inventory entries;
- update startup/protection ownership ledgers.

Out of scope:

- redesigning Automod;
- redesigning Spam Guard;
- changing Invite Shield policy;
- changing AntiNuke;
- changing the public Protection Center command path;
- invite enforcement compatibility cleanup;
- setup/design/ticket/VC guard families.

## Changes

- added native Import Pack button to Protection Center row 2;
- added native starter-pack modal;
- added pure bounded filter-merge helper;
- preserved normalization, dedupe, invalid-term skipping, filter-size limit, and import-count metadata;
- used existing `_save_automod` persistence owner;
- used native interaction guards for open/submit actions;
- updated Protection Center help text;
- added focused native Import Pack regressions;
- deleted all three dormant Protection patch files;
- removed their startup metadata;
- updated ownership ledgers.

## Expected production behavior

The previously intended Import Pack feature becomes reliably available through the real Protection Center owner instead of depending on dormant patch files.

No other Protection Center behavior should change.

## Validation required

- exact-head `git diff --check`;
- Python compile;
- `tests/test_protection_import_pack_native.py`;
- `tests/test_public_protection_center_native_interaction_static.py`;
- `tests/test_protection_invite_native_ui.py`;
- Protection Center/import-related regressions;
- startup ownership tests;
- full Python suite;
- final changed-file/review-thread inspection;
- merge with expected-head guard;
- post-merge verification that all three patch files remain absent and native Import Pack remains present.

## Next step

Inspect exact branch diff, open a focused draft PR, validate exact head in GitHub CI or Termux, then merge and verify on `main`.
