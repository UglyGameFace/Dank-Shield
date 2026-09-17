# ACTIVE TASK

## DS-INVITE-INTERACTION-RESPONSE-FALSE-POSITIVE — Stop Invite Shield deleting utility app responses

**Status:** FINAL EXACT-HEAD VALIDATION

**Branch:** `hotfix/invite-shield-interaction-response`
**Base main:** `bc0a14cf59bcddb7c7ac0a45498125921cae4023`
**Validated implementation head:** `ddefe9b9c5579bfd43a46b9f307064b472cd982c`

## Why PR #254 was insufficient

PR #254 fixed the first confirmed false-positive class: Discord-generated URL previews on ordinary messages. Production then proved a second path remained. At 18:05:01 the live enforcer detected an external invite in the video-downloader application response and deleted the response. The user-visible downloader result includes a **Support Server** Discord link/button.

Discord interaction response messages carry `interaction_metadata` (with legacy `interaction` compatibility). PR #254 still treated bot interaction responses like ordinary bot-authored rich messages, so its support-server embed/button became Invite Shield evidence.

## Root cause

Invite Shield had one message-surface distinction too few:

- human messages were content-only
- ordinary bot/webhook messages scanned authored rich embeds/components
- **interaction responses were incorrectly in the ordinary bot bucket**

That allowed a legitimate slash-command utility response to be deleted solely because the third-party app included a support/community invite in response chrome.

## Correct behavior and implementation

- human messages evaluate actual message content only
- interaction/app-command responses evaluate actual message content only
- Discord `interaction_metadata` is the primary interaction-response marker
- legacy `interaction` remains supported
- explicit Discord invites written directly in response content remain detectable
- support/community links that exist only in an app response embed/button do not delete the utility response
- ordinary unsolicited bot/webhook messages still scan custom rich embeds/components
- generated link/article/video previews remain ignored
- same-server/external classification, exemptions, Link Shield interaction, statistics, and central delete ownership remain unchanged
- no additional startup layer or second invite policy was introduced

## Files changed

Exactly four task files:

- `ACTIVE_TASK.md`
- `stoney_verify/invite_policy_message_surface_runtime.py`
- `tests/test_invite_policy_message_surface_runtime.py`
- `tools/audit_invite_link_safety.py`

## Regression coverage

Focused tests and the standalone invite-link safety audit cover:

- downloader-style interaction response + Support Server button => no invite code
- interaction follow-up + support embed/button => no invite code
- interaction response with explicit invite in content => detected
- legacy interaction marker => content-only
- ordinary bot custom-rich invite => detected
- bot generated-link preview => no false invite
- human generated-link preview => no false invite
- human explicit invite => detected

## Implementation-head validation

On exact implementation head `ddefe9b9c5579bfd43a46b9f307064b472cd982c`:

- Dank Shield CI #2277: **success**
  - Python compile check: **success**
  - full unit test suite: **success**
  - standalone tool checks: **success**
  - public setup/isolation audit: **success**
  - canonical public command audit: **success**
  - public command/startup friction audit: **success**
  - public invite permissions audit: **success**
  - setup safety audit: **success**
  - Dank Design Smart Auto-Detect audit: **success**
  - role truth ownership audit: **success**
  - event boundary ownership audit: **success**
  - Claim-first ticket security: **success**
  - Managed category SQL smoke test: **success**
- Application Command Size Diagnostics #1262: **success**
- Dank Design Regression CI #504: **success**
- Ticket Owner Emergency Override #848: **success**
- Profile Runtime Diagnostics #1011: **success**

The implementation head was 0 commits behind `main`, the diff contained only the four task files, and PR #255 had no unresolved review threads or code-review blockers.

## Cleanup / conflicts / risks

- No unrelated files are present.
- No new invite listener, startup owner, or duplicate delete path was added.
- AntiNuke consolidation remains isolated on `refactor/antinuke-runtime-bootstrap-consolidation`.
- The remaining risk is production behavior of third-party Discord apps; the exact failure class now has direct regression coverage using Discord interaction-response markers rather than bot-name/domain allowlists.

## Final validation note

This bookkeeping update changes the PR head SHA. Therefore the new final head must pass the complete required and companion workflow set again before merge. Do not merge based only on the successful implementation-head run above.

## Next step

Revalidate this final bookkeeping head exactly, confirm it remains 0 behind `main` with only the four task files, mark PR #255 ready, merge using the exact validated SHA, verify the resulting `main` merge parent, and require Discloud deployment success before releasing the task lock.
