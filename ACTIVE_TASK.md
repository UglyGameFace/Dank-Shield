# ACTIVE TASK

## ID
DS-DESIGN-SEPARATORS-265 — First-class mixed separator coverage

## Status
IMPLEMENTATION COMPLETE — validation in progress

## Single active-task lock
Only the Server Designer separator-coverage task is active. Do not start unrelated
feature, security, cleanup, or redesign work until this task is implemented,
validated on the exact final head, cleaned up, merged, and verified on main.

## Previous task closed
PR #265 (Share Router production-runtime restoration) merged as
`179bf2a1b30dc7b7160c14a19d5788417553c9c0`. Its validated implementation
restores the native Share Router runtime and structurally excludes that reserved
infrastructure from Dank Design. The merged main commit reports
`discloud/commit: success`.

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

## Implementation
- added first-class `double_dash`, `pipe_compact`, `pipe_spaced`,
  `double_pipe`, and `double_colon` specs to the canonical separator library
- centralized server-wide and exact-item curated separator IDs in
  `server_design_studio.py`
- server-wide design now exposes 24 explicit separator choices plus Theme Default,
  exactly fitting Discord's 25-option select limit
- exact-item design exposes 25 choices while preserving every option it already had
- both Discord picker flows now consume the canonical lists instead of owning
  divergent hard-coded subsets
- Gothic Clean now returns the stable first-class `pipe_spaced` catalog entry
  directly instead of mutating the separator catalog at runtime
- the full paginated separator example gallery still consumes the complete
  `SEPARATOR_LIBRARY`, so the broader catalog remains browsable

## Regression coverage added
- `👋--welcome` parses and round-trips as intentional `double_dash`
- `👋----welcome` is still classified as an accidental doubled `--`
- `🏁 | start-here-verify` is reproducible through first-class `pipe_spaced`
- compact/spaced pipe lookups do not mutate the runtime separator catalog
- server-wide picker exposes the new mixed-layout ASCII options and remains at 25
  total choices including Theme Default
- exact-item picker remains at Discord's 25-option limit
- every previously exposed server-wide and exact-item separator remains available

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
- main advanced through PR #265 while this task was in progress
- the conflict was resolved by preserving PR #265 Share Router ownership/isolation
  and re-applying only this task's separator changes on top
- no unrelated code changes are authorized

## Backlog
None added from this task.

## Next step
Open the scoped draft PR and run targeted/full exact-head validation. If any same-root
regression fails, repair it on this branch, then re-run the complete gate before the
final bookkeeping commit and merge.
