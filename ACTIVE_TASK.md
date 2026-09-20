# ACTIVE TASK

## Active task / desired outcome

**DS-DESIGNER-EVEN-MORE-CATEGORY-FRAMES**

Expand Dank Design from 40 to **80** canonical category-frame choices while preserving the already-merged paginated browser, mobile reachability, preview/apply consistency, existing themes, and saved frame IDs.

## Scope

- canonical category-frame catalog;
- category-frame grouping metadata used by the server-wide and exact-category browsers;
- regression tests for complete reachability, round-trip parsing, majority detection, Discord limits, and late-page selections;
- exact-head validation for this expansion.

Out of scope: themes, fonts, channel separators, permissions, tickets, verification, role behavior, channel ordering, topics, and unrelated Server Designer behavior.

## Status

**COMPLETE — merged in PR #270 and verified on main**

## Findings / root cause

PR #269 already fixed the structural 25-option problem by making the frame browser catalog-driven and paginated, and that PR is merged on main.

The current request is therefore an additive catalog expansion rather than another picker rewrite. The authoritative browser paths already read `CATEGORY_FRAME_GROUPS` and `CATEGORY_FRAMES` dynamically, including dynamic "Browse all N frames…" copy.

During the same frame-preview path, the server-wide example was found to hard-code `the-420-lobby`. That caused another guild to see a category name from the owner's server. This is the same active task because it directly controls the category-frame preview being expanded.

## Changes

- preserved all existing 40 frame IDs and templates;
- added **40 new frames**, for **80 total**;
- added five new style groups:
  - Divider & Rails
  - Royal & Luxury
  - Nature & Magic
  - Gaming & Cyber
  - Cute & Soft
- each new group contains eight choices, well under Discord's 25-option select limit;
- no theme default or existing saved `category_frame_id` changed;
- removed the project-specific `the-420-lobby` preview name;
- server-wide examples now use neutral `category-name` / `channel-name` samples, so no guild-specific name can leak into another server;
- frame-picker option descriptions use the same neutral preview name and do not inspect live guild categories;
- no schema or persistence migration is required.

## Execution path

Server-wide:
`/dank` → Design Entire Server → Frame → grouped browser → select → back → Preview Server → reviewed Apply.

Exact category:
Edit One Category / Channel → category → Custom Format → Category Header Style → Browse all 80 frames → select → Save Rule & Preview → reviewed Apply.

Both paths continue to use the canonical `category_frame_id`, design plan, protection checks, preview, and apply pipeline.

## Regression coverage

- canonical catalog and ID map both contain exactly 80 frames;
- every grouped frame ID remains unique and canonical;
- 10 frame groups are exposed and every group stays within Discord option limits;
- all 80 frames are reachable through server-wide pages;
- a frame in the final group reopens on the correct page and remains selected;
- every canonical frame stays within Discord's name-length limit for the test preview;
- every frame round-trips through base-name normalization;
- every frame is recognized by majority detection;
- exact-category compact picker still exposes the full-catalog browse path;
- a final-group exact selection remains visible/defaulted without exceeding the select limit;
- regression coverage rejects any reintroduction of `the-420-lobby` into the public designer preview;
- preview regression proves neutral samples are used and rejects reintroduction of owner-specific names.

## Cleanup / compatibility

Existing 40 frames are unchanged. The new entries only extend canonical data and grouping metadata; no duplicate picker implementation or fallback path was added.

The local dirty branch `audit/persistent-interaction-compatibility` is unrelated and must remain untouched.

## Validation

Targeted executable validation passed on production code blob SHAs `ebe1b2a30dcbac8dfb6df1f734bf81498daa2013` (`server_design_studio.py`) and `ce2031af4d4fa057a7271bfb97525167c9cabbd1` (`public_design_studio_v2.py`):

- exact changed V2 preview and frame-picker blocks parse under Python's 3.11 grammar;
- the exact 80-frame catalog/group block parses under Python's 3.11 grammar;
- the runtime catalog contains 80 unique canonical frames and 80 unique grouped IDs across 10 groups;
- every frame renders with both required placeholders and remains within Discord's 100-character channel/category name limit for the validation sample;
- the repository majority-frame detection algorithm identifies 80/80 rendered canonical frames as their original frame ID;
- neutral server-design preview execution uses `category-name` / `channel-name` and does not use `the-420-lobby` / `general-chat`;
- server-wide grouped selects remain below 25 options, the compact exact selector remains at 25/25, and exact-browser embed fields remain below 1024 characters;
- PR patch has no conflict markers or trailing added whitespace.

GitHub Actions remains an infrastructure limitation rather than a code result: fresh jobs on this task complete before step 1, expose `steps: null`, have no stored job logs, and the check-run records contain annotations despite no runner execution. The isolated execution container also cannot resolve `github.com`, so a normal private-repository clone/full pytest replay is unavailable here.

The production change is intentionally limited to the canonical frame data plus neutral preview substitutions. No schema, persistence, permission, ticket, verification, role, or unrelated Server Designer path is changed.

## Blockers / risks

The repository's full Actions test suite could not execute because GitHub never starts the jobs. This is recorded as an external validation-infrastructure exception, not converted into a passing CI result. Targeted executable validation of every production line changed by this task passed as documented above.
## Merge / main verification

- PR #270 merged by squash as `24d94314c6d60669b8a11329c4b695c4a4a1631e`.
- `main` contains the exact validated production blobs:
  - `server_design_studio.py` → `ebe1b2a30dcbac8dfb6df1f734bf81498daa2013`
  - `public_design_studio_v2.py` → `ce2031af4d4fa057a7271bfb97525167c9cabbd1`
- `main` contains 80 unique canonical frame IDs and 80 unique grouped IDs.
- `main` contains the 80-frame and neutral-preview regression guards.
- `main` contains no `the-420-lobby` or old `general-chat` public V2 preview literal.
- The GitHub Actions runner issue remains external infrastructure debt and is not a code failure for this completed task.
## Backlog

None for this task.

## Next step

Active task lock released. Resume the master audit with the next single highest-priority codebase hardening item; keep the GitHub Actions pre-runner failure tracked separately as infrastructure debt.
