# ACTIVE TASK

## DS-INVITE-INTERACTION-RESPONSE-FALSE-POSITIVE — Stop Invite Shield deleting utility app responses

**Status:** EMERGENCY PRODUCTION HOTFIX

**Branch:** `hotfix/invite-shield-interaction-response`
**Base main:** `bc0a14cf59bcddb7c7ac0a45498125921cae4023`

## Why PR #254 was insufficient

PR #254 fixed the first confirmed false-positive class: Discord-generated URL previews on ordinary messages. The new production logs prove the remaining delete is a different path. At 18:05:01 the live enforcer still detected an external invite in the application response and deleted it. The screenshot shows the video-downloader result itself contains a **Support Server** button.

Discord interaction response messages carry `interaction_metadata` (and legacy `interaction` compatibility). The current PR #254 runtime still treats bot interaction responses like ordinary bot-authored rich messages, so its support-server embed/button is scanned and the entire utility response is removed.

## Correct behavior

- human messages: evaluate actual message content only
- interaction/app-command responses: evaluate actual message content only
- explicit Discord invites written directly in an interaction response's content remain detectable
- support/community links that exist only in an app response embed/button do not delete an otherwise legitimate utility response
- ordinary unsolicited bot/webhook messages still scan custom rich embeds/components
- generated link/article/video previews remain ignored
- same-server/external invite classification and central delete ownership remain unchanged

## Scope

- refine the already-installed `invite_policy_message_surface_runtime.py`
- extend focused regression tests
- extend the standalone invite-link safety audit
- update this task record

No additional startup layer or second invite policy is introduced.

## Validation gate

- slash/app response with downloader embed + Support Server button => no invite code
- app follow-up with invite only in support button/embed => no invite code
- app response with explicit invite in message content => detected
- ordinary bot custom-rich invite => detected
- human normal-link and explicit-invite regressions remain green
- full compile/unit/standalone audits and every required/companion workflow pass on exact head
- merge exact validated head and verify current `main` plus Discloud success

## Paused work

The AntiNuke consolidation branch `refactor/antinuke-runtime-bootstrap-consolidation` remains preserved and paused until this live moderation regression is merged and deployed.
