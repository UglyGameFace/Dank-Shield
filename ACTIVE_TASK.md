# ACTIVE TASK

## ID
DS-BASIC-VERIFY-TICKET-ROUTING — Stop Basic Verify users being forced into verification tickets

## Status
IMPLEMENTATION COMPLETE — validation pending

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
- added regression coverage for Basic-only, Simple + Voice, Voice-only,
  allowlisted ID, and unavailable/non-allowlisted ID configurations.

## Compatibility
- Basic Verify role mutation behavior is unchanged.
- Voice-only verification still auto-routes to the verification-ticket flow.
- Allowlisted ID verification still auto-routes.
- Non-allowlisted/unavailable ID verification no longer forces a broken ticket.
- Normal support tickets remain normal support when Basic Verify is authoritative.
- No ticket permissions, role hierarchy, setup UI, Discord resources, or database
  schema are changed.

## Validation required
- targeted verification routing regression tests;
- verification-mode authorization tests;
- Python compile/static checks;
- relevant full repository CI;
- exact-head workflow verification;
- final diff/currentness review;
- merge exact validated SHA and verify resulting main deployment status.

## Cleanup
The duplicated ticket-routing policy helpers were removed from the affected module.
No unrelated cleanup is included.

## Blockers / risks
Validation may be blocked if GitHub-hosted runners are still unavailable for this
private repository. Do not interpret pre-run Actions failures as code failures.

## Backlog
None.

## Next step
Open the scoped draft PR and run the complete validation gate on its exact head.
