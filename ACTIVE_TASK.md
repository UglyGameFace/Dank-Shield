# ACTIVE TASK

## ID
DS-BASIC-VERIFY-TICKET-ROUTING — Stop Basic Verify users being forced into verification tickets

## Status
IMPLEMENTATION COMPLETE — latest-main integration complete; final exact-head validation pending

## Single active-task lock
Only the Basic Verify / verification-ticket routing bug is active in this conversation
and PR. Do not start unrelated work until this task is validated, cleaned up, merged,
and verified on main.

## User-visible problem
A member using the green Basic Verify flow could still end up in a private
verification ticket instead of receiving the normal one-click access-role update
and retaining ordinary support-ticket access.

## Root cause
Dank Shield had two different sources of truth for verification-mode precedence:

- `setup_engine.verification_modes.effective_verification_mode()` correctly treats
  an explicitly enabled Simple/Basic Verify flow as authoritative, including
  intentional Simple + Voice configurations.
- `startup_guards/unverified_ticket_panel_flow.py` reimplemented mode detection
  from raw legacy flags and checked ID/Voice before Basic Verify.

That duplicated policy meant a guild could correctly show and authorize Basic
Verify while the ticket interception layer still considered the same member an
advanced-verification user and hijacked public support-ticket creation into a
verification ticket.

## Execution path
Basic Verify:
`BasicVerifyButton`
→ `apply_basic_verification()`
→ canonical Basic Verify authorization
→ add access roles / remove Unverified.

Public ticket panel:
`PublicCreateTicketPanelView.create_ticket()`
→ `_handle_unverified_panel_click()`
→ `_should_auto_route_unverified_ticket(guild, cfg)`
→ canonical `effective_verification_mode(guild, cfg)`.

Only canonical primary `id_verify` or `voice_verify` modes may auto-route to a
verification ticket. `basic_button` and `disabled` remain on the normal support
ticket path.

## Changes
- removed the duplicated raw verification-mode parser from
  `unverified_ticket_panel_flow.py`;
- made ticket auto-routing consume the canonical verification-mode resolver;
- fail open to normal support routing if mode resolution itself errors, rather than
  guessing from stale legacy flags;
- updated the related compatibility caller to pass the guild into the canonical
  routing decision;
- added behavioral regression coverage for Basic-only, Simple + Voice, Voice-only,
  allowlisted ID, and unavailable/non-allowlisted ID configurations.

## Compatibility
- Basic Verify role mutation behavior is unchanged.
- Voice-only verification still auto-routes to the verification-ticket flow.
- Allowlisted ID verification still auto-routes.
- Non-allowlisted/unavailable ID verification no longer forces a broken ticket.
- Normal support tickets remain normal support when Basic Verify is authoritative.
- No ticket permissions, role hierarchy, setup UI, Discord resources, or database
  schema are changed.

## Validation
Pre-integration head `7756c5aa3003221436faaef52f2fe2cbd5b46d98`:
- focused verification + authorization: 17 passed;
- affected-module compile: passed;
- repository compileall: passed;
- diff check: passed;
- all standalone tools: passed;
- all eight primary CI audits: passed;
- Ubuntu/Python 3.11.7 full suite: 1706 passed, 8 warnings, 0 failures.

The earlier Android/Termux Python 3.13 run had two font-rendering failures, and both
reproduced unchanged on the then-current main baseline, so they were not regressions.

While validation was running, PR #266 merged and advanced main to
`2be39743c9fdf2f6728179665a4f1166e7af36a3`. Its changed files overlap this PR only at `ACTIVE_TASK.md`;
the verification runtime and regression-test files do not overlap. This branch
has now integrated that main commit. Exact-head validation must be repeated on
the resulting integration commit before merge.

GitHub-hosted Actions remain unavailable because account Actions usage is maxed,
so equivalent checks are being run locally in Ubuntu/Python 3.11.

## Cleanup
The duplicated ticket-routing policy helpers were removed from the affected module.
No unrelated cleanup is included. PR #266's Server Designer changes are inherited
from main, not duplicated into this PR diff.

## Conflicts
PR #266 overlapped only in `ACTIVE_TASK.md`; current-task bookkeeping is intentionally
kept as this PR's active record. No production-code conflict exists.

## Blockers / risks
GitHub-hosted CI cannot execute until Actions usage resets or billing changes.
Final merge requires the local exact-head replacement validation to pass.

## Backlog
None.

## Next step
Run the final exact-head Ubuntu/Python 3.11 validation on the latest-main integration
commit, then perform final diff/currentness review and merge verification.
