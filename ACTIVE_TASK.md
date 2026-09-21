# ACTIVE TASK

## Active task / desired outcome

**P0-GUARD-EMBED-001 — Retire dormant global Embed newline patcher**

Remove the obsolete `startup_guards/embed_literal_newline_guard.py` monkey patch now that production ownership inspection proves it has no runtime importer and native Dank Design text cleanup owns the useful newline behavior.

## Status

**IMPLEMENTED — exact-head validation pending**

Branch: `audit/retire-dormant-embed-newline-guard-20260921`

Base: current `main` after PR #288.

## Previous task closed

**QUIET-NOTICE-001** is complete.

PR #288 merged as `195a2d5bd37ae17fdb4dcb587e646172bac91f16`.

Exact implementation head `732ea825af78b8f7d185cab1878fd939e1fab08f` passed:

- diff integrity;
- Python compile;
- 49 focused Community Tools tests;
- Community Tools static ownership checks;
- PostgreSQL migrations applied twice;
- Quiet Notice atomic SQL behavior/security smoke;
- full suite: **1794 passed, 79 warnings, 0 failures**.

All six implementation/test blobs were verified byte-for-byte identical on merged `main`.

## Root cause / ownership finding

`embed_literal_newline_guard.py` is dormant production code with dangerous import-time behavior:

- no production module imports it;
- normal startup does not iterate the historical guard inventory;
- its only executable consumer is `tools/test_embed_literal_newline_guard.py`;
- importing it immediately calls `apply()`;
- `apply()` globally replaces six `discord.Embed` methods.

That means the file provides no production behavior today but remains a resurrection hazard if accidentally imported later.

Useful newline normalization already has a native owner:

`stoney_verify/services/server_design_majority_layout.clean_design_text()`

with regression coverage in:

`tests/test_server_design_majority_layout.py::test_clean_design_text_replaces_literal_newline_artifacts`

## Scope

In scope:

- delete `startup_guards/embed_literal_newline_guard.py`;
- remove its inert historical startup-inventory entry;
- delete the obsolete tool test that imports/activates the patcher;
- strengthen startup-loader retirement coverage so the patcher stays absent;
- preserve native Dank Design newline sanitizer/regression ownership;
- correct the startup-ownership audit record.

Out of scope:

- changing native Dank Design formatting behavior;
- changing any live startup guard;
- `slash_command_cleanup` retirement;
- ticket/setup/verification/member/invite compatibility families;
- command registry redesign;
- central settings registry work.

## Changes

- removed historical `embed_literal_newline_guard` metadata;
- deleted the dormant global Embed monkey patch;
- deleted its implementation-preservation tool test;
- loader-retirement regression now requires:
  - retired guard file absent;
  - retired guard metadata absent;
  - obsolete guard test absent;
  - native `clean_design_text` owner present;
  - native newline regression coverage present;
- startup ownership document now records both the already-retired panel retry path and the retired Embed patcher accurately.

## Risk / compatibility

Expected production runtime behavior change: **none**.

The deleted module had no production importer. Native user-facing newline cleanup remains untouched.

The intended risk reduction is removal of an accidental-import path capable of mutating global `discord.Embed` behavior process-wide.

## Validation required

- exact-head `git diff --check`;
- Python compile;
- `tools/test_startup_guard_literal_newline_registered.py`;
- `tests/test_server_design_majority_layout.py`;
- startup/ownership-focused tests;
- full Python suite;
- final changed-file/review-thread inspection;
- merge with expected-head guard;
- post-merge absence verification on `main`.

## Next step

Open a focused draft PR, inspect exact-head CI, run Termux validation if GitHub runners remain unavailable, then merge and verify the retired patcher stays absent on `main`.
