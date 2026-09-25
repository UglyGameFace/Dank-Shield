# Active Task

## Active task / desired outcome

**P0-INVITE-V2-PROTECTION-012 — enforce Invite Shield on modern Discord app cards and make Protection Center report live delete health truthfully**

Desired outcome: when Invite Shield is ON, an external Discord invite that is
visibly posted in a normal message, bot/webhook rich surface, or Discord
Components V2 Text Display must reach the same canonical invite-policy decision
and be deleted when policy and Discord permissions allow it. `/dank protection`
must also distinguish policy state from the bot's actual delete permission in
the current channel, and closing the panel must remove the controls instead of
leaving a disabled-looking panel behind.

## Scope / single active task lock

Only the invite-enforcement and directly related Protection Center failures
reported on 2026-09-25 are active:

- preserve `invite_policy_engine` as the sole authority that decides whether a
  Discord invite message may be deleted;
- recognize sender-authored Components V2 Text Display content, including nested
  component containers/accessories;
- preserve the existing false-positive boundary that ignores Discord-generated
  preview metadata for humans;
- preserve the existing application-response rule that ignores rich embed
  metadata and support/link-button destinations unless the invite is directly
  visible as message or Components V2 text;
- preserve same-server invite allowances, explicit invite allowlists/exemptions,
  and existing Link Shield / Spam Guard semantics;
- expose current-channel View Channel / Manage Messages health in
  `/dank protection` so “Invite Blocker ON” is not confused with Discord
  permission readiness;
- make Protection Center Close remove its embed/components cleanly;
- add regression coverage for modern app-card invite text and the close/health
  behavior.

Do not broaden into AntiNuke behavior, startup activity-recovery pacing,
verification/ticket interactions, general Discord UI redesign, or unrelated
permission repair.

## Prior task closure

PR #325, **Restore owner authority and self-heal stale Verify panels**, was
merged on 2026-09-25. Its exact PR head
`1fc726b853b1f02ff6d5f22feb88b06d54ab8d92` completed all six observed
workflow groups successfully: Dank Shield CI, Dank Design Regression CI, Ticket
Owner Emergency Override, DS Backlog 027 Validation, Application Command Size
Diagnostics, and Profile Runtime Diagnostics.

This task branches from the merged `main` head
`8f3aea4d1f9a9dcda23854dfb93da13bcb44f61a`.

## Evidence / root cause

Production logs prove the live enforcer itself is running and can delete invite
messages. For example, guild `1357215261001912320` logged successful
`invite_shield_external_or_blocked` deletes at 13:28 and 14:44.

The reported bump-board messages are different: their visible external
`discord.gg` URLs remain on modern app-style cards, while no corresponding
Invite Shield decision is emitted for those posts.

The execution trace explains the gap:

1. `globals._dank_globals_live_invite_enforcer` sends every non-self guild
   message to `invite_policy_engine.enforce_live_invite_message`.
2. `main.py` installs `invite_policy_message_surface_runtime` before login.
3. That runtime replaces `invite_policy_engine.message_text`.
4. For interaction responses it previously returned legacy
   `Message.content` only.
5. For ordinary bot/webhook messages it delegated component inspection to
   `_component_text`, which previously inspected component URLs/children but
   not Text Display `content`.
6. Discord Components V2 puts directly visible message text in Text Display
   components rather than legacy message content/embeds.
7. Therefore a modern app card could visibly contain
   `https://discord.gg/<external>` while Invite Shield extracted no invite code
   and never reached its delete decision.

This is an extraction-boundary bug, not evidence that the canonical policy or
delete routine is offline.

A second product-truth issue was visible in Protection Center: the UI could say
Invite Blocker is ON without showing whether Dank Shield actually has
View Channel + Manage Messages in the channel where the panel was opened.
The existing “Permission health” block is AntiNuke-specific and does not answer
that question.

## Execution path

Before fix:

`Discord on_message -> globals live enforcer -> enforce_live_invite_message ->
extract_invite_codes_from_message -> patched message_text -> Components V2
Text Display omitted -> codes=[] -> no policy decision/delete`

Correct path:

`Discord on_message -> globals live enforcer -> enforce_live_invite_message ->
patched message_text -> direct visible Components V2 text included ->
external invite classification -> canonical Invite Shield delete decision ->
message.delete -> durable stats/modlog`

Interaction-response safety path:

`application response -> legacy content + directly visible V2 text only ->
ignore rich embed metadata and button URLs -> canonical policy`

Protection Center health path:

`/dank protection -> current interaction channel -> channel.permissions_for(
guild.me) -> report View Channel/Manage Messages readiness separately from
Invite Shield ON/OFF policy state`

## Changes

Branch: `fix/invite-shield-components-v2-20260925`

Implemented so far:

- extended canonical component extraction to read Components V2 Text Display
  `content` and nested `accessory`/children;
- kept component link-button URLs available for ordinary bot/webhook authored
  surfaces;
- added an application-response-specific visible-component extractor that reads
  Text Display text but intentionally ignores support/link-button URLs and rich
  embed metadata;
- preserved human message content-only extraction so generated preview embeds
  cannot become false invite evidence;
- added regression tests for ordinary bot Components V2 invite cards and
  interaction-response Components V2 invite cards;
- added current-channel Invite Shield delete-access health to Protection Center;
- documented modern-app-card coverage in Protection Center;
- changed Protection Center Close to clear the embed and view rather than
  disabling controls in place;
- added static regressions for Protection Center health and close semantics.

## Validation / results

Implementation is present on the task branch. Exact-head validation is still
pending.

Required before completion:

- inspect branch diff against `main` for scope and accidental changes;
- compile changed Python modules;
- run Components V2 invite-surface tests;
- run live invite-enforcement and invite-policy engine tests;
- run Protection Center tests;
- run existing invite safety/audit tools;
- run the full repository test suite through the repository's normal CI;
- confirm all GitHub workflow groups are green on the exact PR head;
- confirm no duplicate invite listener/policy/delete implementation was added;
- confirm existing support-button/generated-preview false-positive regressions
  remain green;
- confirm current-channel permission health does not claim policy OFF when only
  Discord permissions are missing.

## Cleanup / conflicts

No new invite listener, fallback delete policy, startup guard, retry loop, or
hardcoded Discord rate-limit workaround has been added.

The existing canonical ownership remains:

- live ingress: `globals.py`;
- decision/delete authority: `invite_policy_engine.py`;
- sender-surface filtering: `invite_policy_message_surface_runtime.py`;
- persisted invite targeting: `invite_scope_settings.py`;
- public controls: `commands_ext/public_protection_center.py` and
  `public_protection_invite_ui.py`.

## Blockers / risks

Live Discord acceptance is still required after deployment for the exact OneBump
Components V2 message shape shown in the production screenshot.

If Discord does not deliver message/component content because the application
lacks the privileged Message Content intent at the Developer Portal level, code
cannot reconstruct text Discord did not provide. The bot requests the intent in
code, and production already proves some invite content is being received; this
remains a deployment check rather than the identified code root cause.

## Backlog

Preserve unrelated existing follow-ups, including AntiNuke permission repair,
startup activity-history pacing, trusted-role UX, Community Hub work, and other
control-center redesigns.

## Next step

Inspect the exact branch diff, open a draft PR, run exact-head CI and focused
invite/protection tests, fix only same-root regressions, then mark ready only
after the Definition of Done has evidence.
