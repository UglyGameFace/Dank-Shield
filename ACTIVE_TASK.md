# ACTIVE TASK

## ID
DS-DESIGNER-CATEGORY-FRAME — Expose server-wide category-frame editing

## Status
IN PROGRESS — root cause confirmed; implementation and regression validation underway

## Single active-task lock
Only the Server Designer category-frame editing gap is active in this conversation
and PR. Do not start unrelated work until this task is implemented, tested,
validated, cleaned up, merged, and verified on main.

## User-visible problem
The **Design Entire Server** screen shows the active Category frame (for example
**Top Box**) in its summary and uses it in previews, but there is no control on
that screen to change it. Theme, Font, Strength, and Separator are editable while
Category frame is display-only.

## Root cause
The naming engine already has first-class category-frame support:

- `server_design_studio.CATEGORY_FRAMES` owns the supported frame catalog;
- `category_frame_id` is already persisted, normalized, previewed, and applied;
- the exact-item editor already exposes the same frame catalog.

The omission is in the consolidated V2 server-wide UI. `DesignServerView`
allocates Discord's five available component rows to four select menus plus one
button row:

- row 0: Theme
- row 1: Font
- row 2: Strength
- row 3: Separator
- row 4: Preview / separator preview / clean redesign / back

So the server-wide frame value is rendered in the embed but never given an
interactive entry point.

## Execution path
`/dank home`
→ **Server Design**
→ **Design Entire Server**
→ `DesignServerView`
→ server-wide draft options
→ `build_saved_design_plan()`
→ reviewed preview
→ Apply.

Category frames already flow through the planner as `category_frame_id`; this
task only needs to expose and safely persist that existing dimension from the
consolidated server-design workflow.

## Intended solution
Keep the existing four server selectors and the existing row-4 actions intact.
Add a fifth row-4 action, **Category Frame**, which opens an in-place frame picker
backed by the canonical `CATEGORY_FRAMES` catalog. The picker will support both
**Theme Default** and explicit frame overrides, save through the same draft path,
sync any enabled global format lock, and return to the Design Entire Server
screen after a selection.

This avoids inventing a sixth Discord action row, avoids a second frame catalog,
and does not remove or degrade Theme, Font, Strength, Separator, Preview,
Separator Preview, Clean Redesign, or Back.

## Compatibility
- Existing saved `category_frame_id` values remain authoritative.
- No schema or persistence format changes.
- Theme-default behavior remains available by clearing the explicit override.
- Category frames still require Strength 4+ to become active in styled category
  names; the UI will make that dependency visible.
- Narrow category/channel/exact rules continue to override the server-wide draft.
- No permissions, tickets, roles, verification, channel order, or non-design
  behavior changes.

## Validation
Pending implementation.

Required before completion:
- focused Server Designer/category-frame regressions;
- affected-module compile;
- repository compile/static checks used by the existing project;
- Dank Design focused regression/audit coverage;
- final diff and duplicate-ownership inspection;
- branch currentness and exact-head validation.

Base main when branch was created:
`33b17fae0bbbd631c8942ddeec5215f3a2394ad3`.

## Cleanup
No unrelated cleanup planned. Reuse the canonical category-frame catalog and the
existing server-design save/sync path rather than creating parallel logic.

## Conflicts
None known at task start.

## Blockers / risks
Discord message components allow only five action rows. The implementation must
fit within that limit without removing existing server-design capabilities.

## Backlog
None.

## Next step
Implement the in-place Category Frame picker and regression coverage, then run the
focused validation suite on the exact branch head.
