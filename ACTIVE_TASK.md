# ACTIVE TASK

## DS-INVITE-HUMAN-EMBED-FALSE-POSITIVE — Stop Invite Shield deleting normal human-posted links

**Status:** EMERGENCY PRODUCTION HOTFIX

**Branch:** `hotfix/invite-shield-human-embed-false-positive`
**Base main:** `e895e875c1661d32424c968babca5ef098786fc1`

## Emergency task switch

The AntiNuke runtime-consolidation branch is preserved unchanged at `refactor/antinuke-runtime-bootstrap-consolidation` and is temporarily paused. A live moderation regression takes priority over architecture cleanup.

## User-visible regression

A normal web link posted by a human can be deleted by Invite Shield even when the human did not post a Discord invite. This was reproduced by the current execution path rather than inferred from the warning text alone.

## Root cause

`invite_policy_engine.message_text()` scans message content plus Discord embeds, embed descriptions/URLs/fields, components, and attachment metadata. For a normal human-posted URL, Discord may generate an unfurled preview embed whose remote metadata contains a Discord invite. The central extractor then sees that remote page metadata as if the human authored the invite and permits Invite Shield to delete the whole message.

That violates the engine's own documented contract that normal links are not touched.

## Correct behavior

- human-authored messages: Invite Shield evaluates the actual message content only
- explicit Discord invites in human message content remain detectable and blockable
- Discord-generated unfurl/embed metadata must never turn an unrelated human link into an invite violation
- bot/webhook-authored rich messages keep embed/component invite scanning because those surfaces are actually authored by the automated sender
- same-server invite classification, external invite classification, exemptions, allowed channels/roles/codes, Link Shield interaction, reconciliation, and central delete ownership remain unchanged

## Implementation plan

Install a narrowly scoped runtime correction before invite reconciliation starts. It replaces only the invite-policy message-text surface selection, leaves the central policy decision/deletion engine intact, and is covered by focused regression tests. After the production hotfix is merged and deployed, fold this rule into the native invite-policy engine during the subsequent cleanup instead of leaving duplicate ownership.

## Validation gate

- human normal URL + Discord invite inside generated embed metadata => no invite code
- human explicit Discord invite in content => detected
- bot/webhook embed-only Discord invite => detected
- existing text extractor redirect/path false-positive cases remain clean
- focused invite-policy tests pass
- invite-link safety audit passes
- full unit/compile and required CI pass on exact head
- diff contains only the hotfix surface, tests, bootstrap hook, and task record
- merge exact validated head and verify `main` + Discloud

## Next step

Implement the message-surface correction and regression tests, run exact-head validation, merge/deploy the hotfix, then resume the preserved AntiNuke runtime-consolidation branch from the new main.
