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

This continuation branches from merged `main` commit
`0b93a5289fc4e0232cd219df0394b523b9beb726` after the first Components V2 fix proved insufficient in live production.

## Evidence / root cause

A new live production screenshot at 17:23 on 2026-09-25 proves the first
Components V2 extraction fix was not sufficient: OneBump posted another
external-server advertisement at 17:13 in the bump-board and older OneBump ads
were still present.

The earlier V2 traversal itself is valid for discord.py 2.7.1: Text Display
objects expose `content`, while Container and Section components expose nested
children/accessories. The remaining failure is one layer earlier.

Discord's current official Gateway documentation states that `MESSAGE_CONTENT`
is privileged and controls message-content data across the APIs. Without access,
Discord returns empty values for `content`, `embeds`, `attachments`,
`components`, and `poll` except documented exceptions. Therefore a user can
visibly see OneBump's server card in Discord while Dank Shield receives stable
sender/application metadata but none of the fields from which the invite URL
can be parsed.

The exact OneBump application/bot identity is
`1028956609382199346`. The new fallback is intentionally identity-bound and
does not trust display names.

A second gap explains why old posts remained: automatic recovery is deliberately
bounded to recent restart/live windows, while the Protection Center historical
cleanup previously inspected only 200 recent messages. A content-redacted
OneBump post also could not trigger the delayed recovery sweep because the
trigger itself first required invite text extraction.

The corrected design keeps `invite_policy_engine` as the only delete authority.
It does not invent invite text. Instead, when all Discord message-content
surfaces are empty, it may recognize the exact known advertising application
from stable metadata. Live deletion still requires the Protected Poster rule
plus an explicit bot ID or channel ID target. The broad "all bots" flag alone is
not enough to authorize contentless deletion. Slash-command interaction
responses are excluded so a bump success receipt is not mistaken for an
unsolicited ad.

Staff-selected historical cleanup is a separate explicit authorization: after a
server manager chooses a channel to clean, the central scanner may remove
content-redacted posts from the exact known advertiser even if that channel was
not already a protected live target. Existing allowed-channel/user/role
exemptions still win.

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

Branch: `fix/invite-shield-onebump-contentless-20260925`

Implemented in this continuation:

- added exact OneBump application identity matching using author/application IDs,
  never the display name;
- added a narrow content-unavailable detector covering Discord's gated
  `content`, `embeds`, `attachments`, `components`, and `poll` surfaces;
- contentless live deletion requires the Protected Poster rule plus an explicit
  bot ID or channel ID target; the server-wide "all bots" flag by itself cannot
  authorize blind deletion;
- excluded interaction responses from the contentless advertising fallback so
  slash-command bump receipts are preserved;
- preserved existing allowed-user, allowed-role, and allowed-channel exemptions
  before the fallback can delete;
- wired the same contentless candidate into delayed live reconciliation so a
  newly posted OneBump ad can trigger recent-channel cleanup even when no invite
  text was delivered;
- deepened staff-selected "Clean Existing Invites" from 200 to 1,000 recent
  messages, with a 2,000-message hard scanner ceiling and a clear re-run notice
  when the bounded pass is full;
- gave staff-selected cleanup explicit authority to remove exact known
  content-redacted advertiser posts through the canonical policy engine;
- added contentless candidate/deletion counts to historical cleanup diagnostics;
- updated trusted bump-receipt V2 traversal to include Text Display content and
  nested accessories, and bound OneBump trust to its exact application identity;
- added focused live, spoof-resistance, interaction-receipt, allowlist,
  historical-cleanup, and reconciliation-trigger regressions;
- expanded the invite safety audit and Protection Center text so this fallback
  cannot silently broaden later.

## Validation / results

Implementation is present on the continuation branch. Exact-head validation is still pending.

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

No new invite listener, startup guard, retry loop, or hardcoded Discord rate-limit workaround has been added. The new contentless fallback lives inside the existing canonical invite policy and cannot delete arbitrary contentless bot messages.

The existing canonical ownership remains:

- live ingress: `globals.py`;
- decision/delete authority: `invite_policy_engine.py`;
- sender-surface filtering: `invite_policy_message_surface_runtime.py`;
- persisted invite targeting: `invite_scope_settings.py`;
- public controls: `commands_ext/public_protection_center.py` and
  `public_protection_invite_ui.py`.

## Blockers / risks

Live Discord acceptance is still required after deployment. The new path no longer depends on reconstructing OneBump invite text when Discord withholds all message-content fields, but general invite parsing still requires approved MESSAGE_CONTENT access. The Protection Center now explains that distinction instead of pretending code-level intent configuration proves Discord granted the data.

## Backlog

Preserve unrelated existing follow-ups, including AntiNuke permission repair,
startup activity-history pacing, trusted-role UX, Community Hub work, and other
control-center redesigns.

## Next step

Inspect the exact branch diff, open a draft PR, run exact-head CI and focused invite/protection tests, inspect any failing job logs, then merge only if the exact tested head is clean.
