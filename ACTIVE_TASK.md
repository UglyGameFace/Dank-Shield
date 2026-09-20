# ACTIVE TASK

## ID
DS-DESIGN-SEPARATORS-265 — First-class mixed separator coverage

## Status
INVESTIGATED — root cause confirmed; implementation in progress

## Single active-task lock
Only the Server Designer separator-coverage task is active. Do not start unrelated
feature, security, cleanup, or redesign work until this task is implemented,
validated on the exact final head, cleaned up, merged, and verified on main.

## Previous task closed
PR #264 (deterministic long-tail lifecycle-card Unicode fallback) merged as
`758145be9e20d6d26240908980a6d1a4948616eb`. Its exact validated PR head was
`f0f216ed5ea780b4edd0355d237b4a297f6fd321`, the merged main commit reports
`discloud/commit: success`, and superseded draft PR #263 has been closed.

## User-visible problem
A real server uses intentional mixed channel layouts such as:
- `👋--welcome`
- `📣-announcements`
- category/header layouts using a readable spaced pipe, e.g. `🏁 | start-here-verify`

Dank Design can parse many separator styles internally, but double hyphen is not a
first-class separator and the primary Server Designer / exact-item pickers expose
only hard-coded subsets of the larger separator catalog. The result is that a user
can create a valid style manually in Discord that the bot cannot faithfully select,
preserve, or reproduce from its own UI.

## Root cause
1. `server_design_studio.SEPARATOR_LIBRARY` contains single hyphen but not
   `--`, so Smart Auto-Detect sees `--` as a repeated single-hyphen separator
   instead of an intentional two-character separator.
2. `server_design_majority_layout.detect_channel_separator()` deliberately marks
   repeated known tokens as `doubled`; this is correct for accidental duplicates,
   but without a longer `--` token in the library it misclassifies the intentional
   layout.
3. Server-wide and exact-item UIs maintain separate hard-coded separator subsets,
   so supported catalog entries can exist but remain unavailable from a given flow.
4. The Gothic spaced ASCII pipe is currently synthesized dynamically by
   `ensure_separator_spec()` instead of existing as a stable first-class catalog
   entry.

## Execution path
Server-wide:
`public_design_studio_v2.DesignServerSeparatorSelect`
→ `public_design_studio._style_change_separator_options`
→ `server_design_studio.SEPARATORS_BY_ID`
→ `server_design_plan_service`
→ `server_design_studio.build_styled_name`

Exact-item:
`public_design_studio.ExactSeparatorSelect`
→ saved format lock
→ `server_design_plan_service`
→ `server_design_studio.build_styled_name`

Smart Repair / Auto-Detect:
`server_design_majority_layout.detect_channel_separator`
→ `infer_*_layout`
→ local/global repair options
→ Server Design plan

## Required behavior
- Treat `--` as an intentional supported separator, not an accidental duplicate.
- Make compact pipe `|` and spaced pipe ` | ` stable first-class catalog entries.
- Add a few common repeated ASCII variants that are safe and useful for mixed real
  servers, without turning arbitrary punctuation inside names into separators.
- Preserve every separator currently exposed in the server-wide and exact-item UI.
- Keep Discord select menus within the 25-option hard limit.
- Keep the full separator example gallery available.
- Keep accidental repeats such as `----` detectable as doubled `--` when the
  configured token itself is `--`.
- Do not alter permissions, roles, topics, channel order, ticket behavior, protection,
  Unicode font behavior, or unrelated Server Designer ownership.

## Planned implementation
- add first-class `double_dash`, `pipe_compact`, `pipe_spaced`,
  `double_pipe`, and `double_colon` specs to the canonical separator library
- centralize the server-wide and exact-item curated separator ID lists in
  `server_design_studio.py`
- wire both Discord pickers to those canonical lists
- make Gothic Clean use the stable `pipe_spaced` catalog entry directly
- add regressions for parsing, auto-detect, UI exposure, select-size limits, and
  intentional-vs-accidental doubled separator behavior

## Validation required
- targeted separator / Server Designer tests
- full Python compile
- full unit suite
- Dank Design regression suite and static audits
- all required PR workflows on the exact final head
- final changed-file/diff inspection and main-currentness check
- merge only the exact validated SHA
- verify merged main and `discloud/commit: success`

## Cleanup / conflicts
- superseded Unicode draft PR #263 was closed before this task started
- no unrelated code changes are authorized

## Backlog
None added from this task.

## Next step
Implement the canonical separator catalog and picker ownership changes, then add the
mixed-layout regressions before running exact-head validation.
