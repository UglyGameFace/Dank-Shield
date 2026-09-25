# Active Task

## Active task / desired outcome

**P0-INVITE-V2-PROTECTION-012 — make Invite Shield reliably enforce external Discord invites across live, edited, modern-app, and missed-history surfaces**

Desired outcome: when Invite Shield is enabled, directly authored external Discord invites from people, bots, webhooks, rich messages, and Components V2 all reach the same canonical invite policy. Messages missed during restart/cache gaps are recovered without coupling Invite Shield to unrelated activity-history checkpoints.

## Scope / single active task lock

Only Invite Shield enforcement, its recovery path, its Protection Center controls, and directly related regression coverage are active. Do not broaden into AntiNuke, verification, tickets, Community Hub, or unrelated startup work.

## Evidence / root cause

Production logs prove the canonical live policy/delete path works when invite evidence reaches it: messages in guild `1357215261001912320` were classified as `invite_shield_external_or_blocked` and deleted successfully.

The affected messages surviving elsewhere exposed separate ingress/recovery gaps:

1. **Missed history was keyed to the wrong durable clock.** Invite reconciliation borrowed the member/activity heartbeat. Activity recovery could advance that checkpoint even though Invite Shield had not inspected the same messages, permanently placing missed invite posts outside the next Invite Shield startup window.
2. **Cache-dependent edits could be missed.** The normal `on_message_edit` path only covers cached messages. A modern application card updated after creation can require raw message edit recovery.
3. **The previous content-redacted fallback was vendor-specific.** It hardcoded one advertising application identity. That is not acceptable for a public bot because the same Discord delivery shape can come from another bot/application/webhook.
4. **When Discord supplies the authored invite text, the existing generalized extractor is correct.** It already covers human message content, bot-authored rich surfaces, Components V2 Text Display content, nested children/accessories, and directly authored component URLs subject to the existing human/app-response false-positive boundaries.

## Correct execution path

Live authored invite:

`on_message -> globals live enforcer -> invite_policy_engine.enforce_live_invite_message -> generalized extraction -> same-server/external/unknown classification -> canonical policy -> delete_message_if_allowed -> durable stats/modlog`

Uncached edit:

`on_raw_message_edit -> fetch exact changed message -> canonical enforce_live_invite_message -> canonical policy/delete`

Restart recovery:

`Invite Shield durable checkpoint -> bounded channel history window -> scan_channel_invites -> canonical policy/delete -> advance Invite Shield checkpoint only after completed scan`

Content-redacted bot/app/webhook:

`stable sender shape + no MESSAGE_CONTENT surfaces -> generic protected-poster candidate -> delete only if explicit protected bot/channel rule matches; broad all-bots alone cannot authorize blind deletion`

Humans are never guess-deleted when authored content is unavailable.

## Changes

Branch: `fix/invite-shield-generalized-recovery-20260925`

Implemented:

- removed the OneBump identity table and vendor-specific content-redacted policy;
- generic content-redacted candidate now covers bot/application/webhook sender shapes without using display names or vendor IDs;
- content-redacted automatic deletion still requires the existing explicit protected bot/channel rule;
- interaction responses remain excluded from contentless automatic deletion;
- human explicit invite URLs remain handled by the normal generalized extractor;
- allowed users/roles/channels and same-server invite behavior remain intact;
- introduced Invite Shield's own durable `invite_reconcile_checkpoint_at`;
- first checkpoint bootstrap performs a bounded 24-hour recovery window; stale gaps are capped at seven days;
- checkpoint is not advanced when policy is unavailable or Invite Shield recovery is disabled;
- added uncached `on_raw_message_edit` recovery that fetches only content-related edits and delegates to the canonical live policy;
- live raw-edit recovery uses discord.py's route limiter and is not stalled behind startup activity-history pacing;
- existing startup/resume scans remain under the shared recovery REST budget;
- Protection Center wording and historical cleanup are vendor-neutral;
- removed the unused OneBump/Discadus-specific bump receipt classifier;
- updated safety audits and focused regressions so vendor hardcoding cannot silently return.

## Validation / results

Implementation is present on the branch. Exact-head validation is still required before merge:

- compile changed modules;
- run invite live-enforcement tests;
- run invite message-surface tests;
- run invite reconciliation tests;
- run startup recovery scaling tests;
- run Protection Center tests;
- run `tools/audit_invite_link_safety.py`;
- inspect branch diff for accidental scope;
- open PR and require exact-head CI before merge.

## Cleanup / conflicts

No second invite policy or direct delete authority was added. Canonical ownership remains:

- live ingress: `globals.py`;
- decision/delete authority: `invite_policy_engine.py`;
- sender-surface filtering: `invite_policy_message_surface_runtime.py`;
- missed-event/history recovery: `invite_reconciliation_runtime.py`;
- persisted targeting: `invite_scope_settings.py`;
- controls: `public_protection_invite_ui.py`.

## Blockers / risks

Live production acceptance is still required after a validated deployment. Content-redacted human messages remain fail-safe because there is no trustworthy invite evidence to classify. Content-redacted bot/app/webhook messages are only removable automatically when the server explicitly protected that bot/channel.

## Next step

Run exact-head validation, repair any failures, open the PR, inspect all workflow evidence, and merge only from the tested head.
