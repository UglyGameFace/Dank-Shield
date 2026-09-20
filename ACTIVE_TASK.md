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

**IMPLEMENTED — exact-head validation pending**

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
- server-wide examples now derive a safe normalized category/channel example from the guild currently being edited;
- frame-picker option descriptions use that guild's category example, with neutral `category-name` fallback when no category exists;
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
- preview-source regression proves current-guild category/channel names are used.

## Cleanup / compatibility

Existing 40 frames are unchanged. The new entries only extend canonical data and grouping metadata; no duplicate picker implementation or fallback path was added.

The local dirty branch `audit/persistent-interaction-compatibility` is unrelated and must remain untouched.

## Blockers / risks

Exact-head Python 3.11 validation has not run yet. Do not call this complete or merge-ready until focused Dank Design tests, compile checks, relevant audits, and final diff/branch validation pass on the final head.

## Backlog

None for this task.

## Next step

Run exact-head validation on `improve/server-designer-80-category-frames`. If green, open a focused PR, verify CI/review state, then merge and verify the merged result on `main`.
