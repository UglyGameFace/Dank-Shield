# ACTIVE TASK

## ID
DS-DESIGNER-CATEGORY-FRAME — Expose server-wide category-frame editing

## Status
IMPLEMENTATION COMPLETE — exact-head executable validation blocked by GitHub Actions infrastructure

## Single active-task lock
Only the Server Designer category-frame editing gap is active in this conversation
and PR. Do not start unrelated work until this task is tested, validated, cleaned
up, merged, and verified on main.

## User-visible problem
The **Design Entire Server** screen shows the active Category frame, for example
**Top Box**, and already uses that value in previews, but it had no control for
changing the frame. Theme, Font, Strength, and Separator were editable while
Category frame was display-only.

## Root cause
The naming engine already had first-class category-frame support:

- `server_design_studio.CATEGORY_FRAMES` is the canonical frame catalog;
- `category_frame_id` already flows into `build_styled_name()`, preview planning,
  saved rules, and persisted server-design options;
- the exact-item editor already exposes the same frame catalog.

Two directly related gaps were found in the server-wide path.

First, the consolidated V2 `DesignServerView` already occupied Discord's five
component rows:

- row 0: Theme
- row 1: Font
- row 2: Strength
- row 3: Separator
- row 4: Preview Server / Preview Separator / Clean Redesign / Back

That left Category frame visible in the embed but without an interactive entry
point.

Second, every server-wide selector synchronizes an enabled global format lock
through `_sync_enabled_global_lock()`, but `_current_format_lock()` always copied
the theme's category frame and ignored an explicit server-wide
`category_frame_id`. Adding a picker without correcting that path would make a
custom frame appear to save and then silently lose it whenever the global format
lock was enabled.

## Execution path
`/dank home`
→ **Server Design**
→ **Design Entire Server**
→ `DesignServerView`
→ server-wide draft options
→ `build_saved_design_plan()`
→ `build_design_plan()`
→ `_effective_format_options()`
→ `build_styled_name(..., category_frame_id=...)`
→ reviewed preview
→ Apply.

Saved-rule precedence remains:
channel rule → category rule → enabled global rule → server-wide draft.

## Changes
- added a compact **Frame** action as the fifth button on the existing row-4 action
  row; no sixth Discord row is introduced;
- added an in-place `DesignServerCategoryFrameView` and
  `DesignServerCategoryFrameSelect`;
- the picker uses the canonical `CATEGORY_FRAMES` catalog and exposes all current
  frames plus **Theme Default**;
- Theme Default clears the explicit override, while an explicit frame remains
  selected when Theme changes;
- frame selections acknowledge the Discord interaction before config I/O, save
  through the existing draft path, synchronize an enabled global format lock, and
  return to **Design Entire Server**;
- added canonical `theme_default_category_frame_id()` and
  `effective_server_category_frame_id()` resolution beside the existing
  separator resolver;
- V2 display/preview logic and legacy global-lock construction consume that same
  canonical resolver;
- the summary now distinguishes **theme default** from **custom override**;
- Strength below 4 explains that the selected category frame is saved but does not
  become active until Strength 4+;
- Server Design and Clean Redesign guidance now includes Category Frame.

## Regression coverage added
- consolidated server selectors now include the category-frame save/sync path;
- explicit frame selection and Theme Default clearing are regression-locked;
- canonical resolution prefers a valid explicit frame and safely falls back to the
  selected theme for an invalid/missing override;
- enabled global-lock construction preserves an explicit server-wide frame;
- the main server-design view is locked to five row-4 buttons;
- the picker exposes the complete canonical frame catalog and remains below
  Discord's 25-option limit;
- Night Gothic Theme Default resolves to Top Box;
- explicit Lenticular and Top Box overrides are represented correctly;
- the server-design summary identifies theme-owned versus custom frame state.

## Compatibility
- Existing valid saved `category_frame_id` values remain authoritative.
- No schema or persistence format changes.
- Theme-default behavior is represented by the absence of an explicit override.
- Category frames still require Strength 4+ to affect category names.
- Narrow category/channel/exact rules retain their existing precedence.
- Theme, Font, Strength, Separator, Preview Server, Preview Separator, Clean
  Redesign, and Back remain available.
- No permissions, tickets, roles, verification, channel order, topics, or other
  non-design behavior changes.

## Validation / evidence
Implementation source and final task diff were inspected against main.

Current implementation evidence before this bookkeeping update:
- implementation head: `f31f4ec5b7b29fabc828804c651d6a6bbf4b4be5`;
- base/current main: `33b17fae0bbbd631c8942ddeec5215f3a2394ad3`;
- branch currentness: **0 behind main**;
- PR #268: open, draft, mergeable;
- unresolved review threads: **0**;
- changed files were limited to the active task:
  `ACTIVE_TASK.md`,
  `public_design_studio.py`,
  `public_design_studio_v2.py`,
  `server_design_plan_service.py`,
  `test_dank_design_consistency_030.py`, and
  `test_design_studio_consolidation_032.py`;
- final diff inspection found no unrelated runtime/schema/permission changes and no
  second category-frame catalog.

GitHub scheduled all normal workflows on the exact implementation head, but they
failed before executing any step. Confirmed examples:
- **Dank Design Regression CI** → Consolidated Design Studio regressions:
  `steps=null`, `logs_url=null`;
- **Dank Shield CI** → Python compile check:
  `steps=null`, `logs_url=null`;
- Application Command Size Diagnostics likewise has no executed steps.

The same account-level pre-run Actions failure was already present on preceding
heads. Therefore these red checks do **not** constitute test failures, but they
also do **not** satisfy executable validation.

The current ChatGPT execution container cannot clone the private repository
because outbound GitHub DNS/network access is unavailable, so it cannot honestly
substitute a local pytest/compile run for the unavailable hosted runner.

## Cleanup
- reused the existing canonical `CATEGORY_FRAMES` catalog;
- centralized server category-frame default/override resolution in the plan
  service rather than leaving V2 and legacy lock code with competing policy;
- no temporary guards, monkey patches, alternate frame catalogs, debug code, or
  unrelated cleanup were added;
- the row-4 frame button is intentionally compact for the five-button mobile
  action row.

## Conflicts
None currently known. The branch is current with main and PR #268 reports
mergeable.

## Blockers / risks
- Exact-head Python compile, focused Dank Design pytest/audits, and broader
  repository validation still need to execute on a working Python 3.11 runner.
- A live Discord smoke test has not yet been performed.
- Until executable validation passes, this PR must remain draft and must not be
  described as fixed, complete, production-ready, or ready to merge.

## Backlog
None.

## Next step
Run the repository's existing Python 3.11 Dank Design workflow-equivalent checks
on the exact final head as soon as a runner/local repository environment is
available. Then perform final currentness/diff review, update this record with the
results, mark PR #268 ready only if green, merge the validated head, and verify
main/deployment behavior.
