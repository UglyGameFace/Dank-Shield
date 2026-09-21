# ACTIVE TASK

## Active task / desired outcome

**DS-INVITE-REG-001 — Restore live Invite Blocker toggle ownership**

Restore the Protection Center contract so pressing **Invite Blocker** actually toggles live Discord invite blocking, while advanced Invite Shield targeting/cleanup remains available through a separate settings action.

## Status

**IMPLEMENTED — focused validation and exact-head CI inspection pending**

## Root cause

The Sep. 16 native Invite Shield UI migration captured the original `_toggle_invite_shield` function and then replaced `center._toggle_invite_shield` with `open_invite_shield`.

The public Protection Center button still retained the label **Invite Blocker** and still called `_toggle_invite_shield(interaction)`. Because the global function had been rebound, pressing the button opened the editor instead of toggling `automod_block_invites`.

That created a control-plane regression: a server owner could press **Invite Blocker**, configure watched bots/channels, and reasonably believe protection was enabled while the authoritative live blocker flag remained OFF.

## Execution path

`ProtectionCenterView.block_invites_button`
→ `_toggle_invite_shield`
→ guild config + Spam Guard persistence
→ invite policy cache invalidation
→ `globals.py` `on_message` listener
→ `invite_policy_engine.enforce_live_invite_message`
→ `decide_invite_message`
→ `delete_message_if_allowed`

Advanced configuration now has its own path:

`ProtectionCenterView.invite_settings_button`
→ `public_protection_invite_ui.open_invite_shield`

## Scope

In scope:

- preserve native `_toggle_invite_shield` ownership;
- add a distinct **Invite Settings** action;
- show authoritative live ON/OFF state in the Invite Shield editor;
- expose one canonical/testable live enforcement boundary in `invite_policy_engine`;
- route the guaranteed globals listener through that boundary;
- add regressions for toggle ownership and real live human-invite deletion;
- preserve same-server invite allowance.

Out of scope:

- Quiet Server Notice PR #285;
- startup Discord REST/rate-limit PR #286;
- changing invite regex semantics;
- changing Spam Guard burst policy;
- changing allowed-user/role/channel exceptions;
- changing same-server invite policy;
- retiring other startup guards.

## Changes

- `public_protection_invite_ui.py`
  - no longer rebinds `center._toggle_invite_shield`;
  - still captures the original toggle for the editor's own ON/OFF action;
  - displays live blocker state as ON/OFF/UNKNOWN.
- `public_protection_center.py`
  - **Invite Blocker** remains the actual toggle;
  - new **Invite Settings** button opens the advanced editor.
- `invite_policy_engine.py`
  - adds `enforce_live_invite_message` as the canonical testable live boundary.
- `globals.py`
  - guaranteed `on_message` listener delegates to the canonical live boundary.
- tests
  - lock separate toggle/editor ownership;
  - prove a human-posted external `discord.gg` invite is deleted when Invite Shield is enabled;
  - prove a same-server invite remains allowed;
  - require the globals listener to use the canonical enforcement boundary.

## Compatibility / cleanup review

- no startup guard added;
- no Discord.py monkey patch added;
- no duplicate delete authority added;
- central `invite_policy_engine` remains the only delete-decision authority;
- live listener remains installed from `globals.py`;
- same-server invites remain allowed by default;
- normal non-invite links remain outside Invite Shield deletion policy;
- PR #285 and PR #286 are untouched.

## Validation

Pending on exact final head:

- Python syntax/compile;
- `tests/test_protection_invite_native_ui.py`;
- `tests/test_invite_live_enforcement.py`;
- existing invite policy/message-surface regressions;
- invite safety audit;
- relevant full-suite/CI lanes if runners execute;
- final diff and review-thread inspection.

## Blockers / risks

GitHub Actions has recently produced runnerless failures on neighboring PRs. A red workflow with no executed steps must not be represented as a code/test failure or as successful validation.

Live production verification still requires deployment after merge; repository tests can prove the intended runtime path but cannot prove Discord permissions/config on a deployed guild.

## Backlog

- PR #285 Quiet Server Notice auto-clear remains separate.
- PR #286 startup Discord REST burst shutdown remains separate.

## Next step

Inspect the exact branch diff, open a focused draft PR, run/inspect all available exact-head validation, resolve any task-owned failures, then mark merge-ready only with executed evidence.
