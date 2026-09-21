# ACTIVE TASK

## Active task / desired outcome

**No active Dank Shield implementation task.**

The previous task, **P0-GUARD-EMBED-001 — Retire dormant global Embed newline patcher**, is complete.

## Completed task

### Outcome

The obsolete `startup_guards/embed_literal_newline_guard.py` monkey patch was retired after ownership inspection proved it had no production importer and native Dank Design cleanup already owned the useful newline behavior.

### Root cause / ownership finding

The retired module was dormant production code with dangerous import-time behavior:

- no production module imported it;
- normal startup did not iterate the historical guard inventory;
- its only executable consumer was `tools/test_embed_literal_newline_guard.py`;
- importing it immediately called `apply()`;
- `apply()` globally replaced six `discord.Embed` methods.

Native visible-newline cleanup remains owned by:

`stoney_verify/services/server_design_majority_layout.clean_design_text()`

with regression coverage in:

`tests/test_server_design_majority_layout.py::test_clean_design_text_replaces_literal_newline_artifacts`

### Changes

- removed historical `embed_literal_newline_guard` metadata;
- deleted the dormant global Embed monkey patch;
- deleted its implementation-preservation tool test;
- strengthened loader-retirement coverage so the patcher stays absent;
- preserved native Dank Design newline sanitizer/regression ownership;
- corrected the startup ownership audit record.

### Validation

Exact implementation head:

`ee5020269ded0a40b541697eb7f4cb421969b347`

Passed on that exact SHA:

- `git diff --check`;
- Python compile;
- startup guard retirement static check;
- native Dank Design newline regression: **10 passed**;
- startup/ownership continuation;
- full Python suite: **1794 passed, 79 warnings, 0 failures**.

PR #290 merged to `main` as:

`efef78fa9ed3f5be744d2585b1e9ed93f4209566`

Post-merge verification confirmed the merged commit is the current `main` head and the retired startup-guard file is absent from the production guard inventory.

### Cleanup / conflicts

- no production importer was introduced;
- no native Dank Design behavior changed;
- no unrelated startup guard was modified;
- no unresolved review threads remained on the validated implementation head;
- the only remaining stale item was this task ledger, corrected by the closeout change.

### Blockers / risks

None known for P0-GUARD-EMBED-001.

### Backlog

Existing unrelated audit/backlog items remain unchanged and must be handled under their own task locks.

## Next step

Start the next explicitly selected implementation task from a clean branch or repository with its own active-task record.
