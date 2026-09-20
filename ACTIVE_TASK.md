# ACTIVE TASK

## Active task / desired outcome

**DS-DESIGNER-MORE-CATEGORY-FRAMES**

Expand Dank Design category-frame choices substantially while keeping every choice reachable on Discord mobile, keeping preview/apply behavior consistent, preserving existing themes, and avoiding silent truncation at Discord's 25-option select limit.

## Scope

- canonical category-frame catalog;
- server-wide Design Entire Server frame picker;
- exact category custom-format frame picker;
- category-frame parsing / majority detection needed so new canonical frames round-trip correctly;
- regression tests and validation for this task.

Out of scope: themes, fonts, channel separators, permissions, tickets, verification, role behavior, channel ordering, topics, and unrelated server-design behavior.

## Status

**IMPLEMENTED — exact-head executable validation pending**

## Findings / root cause

The canonical catalog only contained 10 frames. The server-wide frame picker previously built one Discord string select and truncated it with `choices[:25]`, so merely adding many more frames would silently hide everything past the platform limit.

The exact category editor had the same structural ceiling through `studio.CATEGORY_FRAMES[:25]`.

Category-frame cleanup and majority detection also used hard-coded knowledge of the original small frame set. Expanding only the UI/catalog would therefore make newer frames render correctly but fail round-trip parsing or be misdetected as plain during live-layout analysis.

## Changes

- expanded the canonical frame catalog from **10 to 40** frames;
- organized the catalog into five groups:
  - Core
  - Boxes & Brackets
  - Premium & Decorative
  - Gothic & Celestial
  - Tech & Minimal
- server-wide Frame now opens a grouped, paginated picker instead of truncating the catalog;
- the frame browser opens on the group containing the current custom selection;
- Theme Default remains available on every server-wide frame page;
- exact-category custom format keeps a compact primary select and adds **Browse all 40 frames…** for the full grouped catalog;
- exact-category browser returns to the normal custom-format editor after a choice;
- added canonical `category_frame_affixes()` so generated frame parsing is data-driven;
- base-name normalization now strips any canonical frame without another hard-coded character list;
- majority detection now detects the complete canonical catalog instead of six hand-maintained frame forms.

## Execution path

Server-wide:
`/dank` → Design Entire Server → Frame → grouped frame browser → save draft → Preview Server → reviewed Apply.

Exact category:
Edit One Category / Channel → category → Custom Format → Category Header Style → Browse all frames → select → Save Rule & Preview → reviewed Apply.

Both paths continue to use the existing canonical `category_frame_id`, design-plan construction, protection checks, preview, and apply pipeline.

## Regression coverage

- canonical catalog contains exactly 40 frames;
- every frame appears exactly once in grouping metadata;
- all 40 frames are reachable through server-wide pages while every select remains under 25 options;
- Theme Default remains singular and correct;
- a frame in the final group reopens on the correct page and remains selected;
- every canonical frame round-trips through base-name normalization;
- every canonical frame is recognized by majority detection;
- exact category editor exposes a full-catalog browse path;
- late-catalog exact selections remain visible/defaulted without exceeding the select limit.

## Cleanup / compatibility

Existing frame IDs and theme defaults are preserved. No migrations or schema changes are required. Existing saved `category_frame_id` values remain valid.

The browser is additive and uses Discord component rows within platform limits. No legacy frame IDs were renamed or removed.

## Blockers / risks

Executable exact-head validation has not run yet. Do not mark this task complete or merge-ready until Python 3.11 compile, focused Dank Design tests/audits, and relevant package validation pass on the final head.

## Backlog

None for this task.

## Next step

Run exact-head validation on the finalized branch. If green, record results on the PR, mark ready, merge, and verify the merged implementation on `main`.
