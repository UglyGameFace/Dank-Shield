# ACTIVE TASK

## DS-DESIGNER-261 — Uncross Server Designer state and interaction flow

**Status:** IMPLEMENTED, VALIDATION PENDING

**Branch:** `fix/server-designer-v2-ownership`
**Base main:** `2420f9dc8dccd8283d03acdc74982a0bbc76554d`

## Active task lock

The Single Active Task Lock is the Server Designer workflow shown in the Sep 18
Discord screenshots: server-wide theme/strength/separator editing, its preview
handoffs, and the interaction acknowledgement path that was timing out. No
unrelated Dank Shield cleanup or redesign is admitted.

## User-visible failure

The server-wide design screen was visually and behaviorally inconsistent:

- **Design Entire Server** was owned by V2, but **Change Separators Only**
  handed the user into the legacy `StyleChangeView` surface.
- legacy separator selection rebuilt live-style analysis before acknowledging
  the Discord interaction, so a normal selection could sit disabled and produce
  **"Dank Shield didn't respond in time"**.
- theme/strength selection also performed config I/O before acknowledging the
  interaction, exposing the same timeout class under slow DB/runtime conditions.
- the server-wide page hid the current separator and did not make it obvious
  that saved category/channel/exact-name exceptions still outrank the server
  draft, which made a clean redesign look like unrelated designer settings were
  crossing.

## Execution path

`/dank home`
→ **Server Design**
→ `public_design_studio_v2.DesignServerView`
→ theme / strength / separator draft settings
→ either full saved-design planner or separator-only planner
→ one reviewed V2 Apply owner
→ `server_design_apply_service`

Legacy `public_design_studio.py` remains a backend/compatibility source for
mature planner/editor primitives, but the server-wide separator workflow no
longer navigates into the legacy `StyleChangeView`.

## Root cause

The consolidated V2 shell still had a user-facing ownership hole: its
server-wide separator button explicitly constructed the legacy separator embed
and legacy view. That legacy view performed expensive live analysis before
responding to the component interaction. The V2 theme/strength callbacks also
waited for config reads/writes before acknowledging Discord.

This was both a state-authority problem and a Discord interaction-timing
problem, not merely a cosmetic layout issue.

## Implemented changes

- Server Designer now keeps **theme, strength, and channel separator** together
  on the same V2 screen.
- Added a V2-owned separator selector.
- Replaced the legacy handoff with two explicit preview scopes:
  **Preview Entire Server** and **Preview Separator Only**.
- Separator-only preview now builds the existing safe planner directly and
  enters the shared reviewed-Apply flow without opening `legacy.StyleChangeView`.
- Theme, strength, and separator selectors acknowledge the Discord interaction
  before config I/O.
- Back-to-home navigation now acknowledges before reloading config.
- The screen shows the current separator and warns when saved narrow overrides
  will intentionally outrank the server draft.
- Existing transactional apply, stale-preview protection, protection rules,
  rollback snapshots, and separator persistence are preserved.

## Validation added

- regression coverage verifies the separator path no longer constructs
  `legacy.StyleChangeView`
- regression coverage verifies V2 owns separator selection and preview
- regression coverage verifies all three server-design selectors defer before
  config I/O
- the static UX contract now requires both preview scopes and the V2 separator
  selector

## Validation status

Not yet complete. Repository CI and the relevant Dank Design regression suite
must run on the exact final head. A live Discord acceptance pass is also still
needed to confirm the original timeout/wonky-screen reproduction is gone.

## Cleanup / conflict inspection

The task deliberately does not delete the legacy separator classes because
mature exact-item compatibility code still references the legacy module. The
public server-wide path no longer enters that legacy view. Broader removal of
legacy compatibility code would be a separate task unless validation proves it
is required for correctness here.

## Blockers / risks

- exact-head CI has not run yet
- live Discord interaction timing has not yet been acceptance-tested
- saved narrow rules still outrank the server draft by design; the UI now makes
  that explicit instead of silently looking like crossed state

## Backlog

- Consider a later dedicated migration that removes the remaining late
  presentation monkey-patches from `public_runtime_ux_repairs.py` after their
  channel-editor/protection behavior is moved natively. This is not required for
  the current server-wide flow fix.

## Next step

Open a focused PR, run exact-head CI and Dank Design regressions, inspect the
final diff for unrelated changes, then perform the live Discord acceptance pass
before merging.
