# ACTIVE TASK

## DS-FIX-AUTHORIZED-BOT-BAN — Stop legitimate bot and invite false-positive containment

**Outcome:** Dank Shield must not remove a legitimate bot merely because the physical guild owner installed it, and an ordinary member creating a Discord invite must not be promoted into durable hostile identity state and repeatedly banned. Genuine known-hostile identities and non-owner unauthorized bot additions must remain protected by AntiNuke.

**Status:** IMPLEMENTED AND VALIDATED ON CODE HEAD — PR #235 pending final record-head CI and merge

**Branch:** `fix/authorized-bot-ban-false-positive`
**PR:** #235 — `Fix authorized bot and invite false-positive bans`
**Base main:** `d3853509b90a57769cd2c5a5a3d565cad28c9d87`
**Validated implementation head:** `0f664bbf71495b4d4b178b753c1df2341f72850b`

## Scope

- AntiNuke bot-add authorization when the physical guild owner installs a bot
- AntiNuke treatment of ordinary Discord invite creation/mutation audit events
- durable hostile-reputation creation caused by that invite false positive
- canonical and fast/local hostile re-entry enforcement consuming that false reputation
- focused behavioral regressions for legitimate and genuinely hostile cases

No unrelated ticket, setup, lifecycle-card, permission-repair, schema, or general moderation redesign is included.

## Findings / root cause

### Authorized bot-add path

The canonical bot-add handlers already exempt the physical guild owner, but the later lockdown wrapper reinterpreted `antinuke_trusted_user_ids` as a required allowlist for the **target bot ID**. That meant an owner-authorized bot could still be removed simply because its ID was not pre-populated in a hidden target allowlist. This contradicted the canonical owner-authorization contract.

The exact historical Discadia audit entry is not available through the repository, so the repository evidence proves the conflicting owner-add removal path rather than claiming an unavailable Discord audit record. Other inspected bot paths already fail safely for this incident class: SpamGuard downgrades bot invite escalation to alert-only, verification/join-removal safety skips bot accounts, and fresh-join role recovery treats bots as skipped.

### Ordinary invite creation -> durable hostile identity

`anti_nuke_zero_damage_runtime` expanded the guardian surface to include `invite_create` and `invite_update`, even though the canonical gateway contract classifies those member-facing creation actions as benign/non-first-strike. The guardian then fed those events into the canonical destructive processor.

For an ordinary member who was neither owner/Dank Shield nor explicitly delegated AntiNuke trust, the canonical destructive processor used first-strike containment. The hostile-actor runtime wraps containment durably: it wrote an active `confirmed_destructive_actor` reputation row first, then banned the member. The exact false-positive reasons were:

- `Dank Shield AntiNuke containment: Invite creation`
- `Dank Shield AntiNuke containment: Invite mutation`

Both the canonical hostile member-join path and the fast hot/local re-entry path could consume that persisted reputation and ban the same member again after an owner unbanned/reinvited them.

The supplied production logs match this execution order: the returning member disappeared before verification could assign the Unverified role, verification then received Discord `Unknown Member`, and a recent ban audit entry existed. The verification failure was therefore downstream of the ban rather than its cause.

## Execution path

### Bot-add false positive

Discord `bot_add` audit event -> guardian bot-add handling -> lockdown bot-add wrapper -> hidden target-ID allowlist check -> owner-authorized bot removal.

### Invite/re-ban false positive

Discord `invite_create` / `invite_update` audit event -> zero-damage guardian expansion -> guardian `_process` -> canonical `_process_claimed_destructive_event` -> first-strike containment -> hostile runtime durable containment -> `mark_confirmed_hostile` -> ban -> later member join -> canonical/fast reputation lookup -> re-entry ban.

## Changes

- `stoney_verify/anti_nuke_lockdown_runtime.py`
  - physical guild-owner bot installation is explicit authorization when the target has no exact active hostile reputation
  - `antinuke_trusted_user_ids` is no longer a hidden required target-bot allowlist
  - non-owner bot additions still delegate to canonical AntiNuke security handling
  - exact active hostile-bot reputation still outranks owner authorization

- `stoney_verify/anti_nuke_product_policy_runtime.py`
  - removes `invite_create` and `invite_update` from punitive guardian processing and panic scoring in the final product policy
  - leaves invite-message enforcement with Invite Shield / SpamGuard configuration rather than destructive AntiNuke identity containment
  - recognizes only the exact historical invite false-positive reputation shape/reasons
  - masks that row inactive in memory immediately and schedules a durable `clear_hostile_reputation` write
  - genuine destructive reputation remains active

- `stoney_verify/anti_nuke_reentry_race_runtime.py`
  - routes hot in-memory and local-mirror reputation through the same legacy false-positive sanitizer before the fast re-entry path can ban

- focused regression coverage
  - owner-authorized non-hostile bot remains allowed
  - non-owner bot addition still reaches canonical security handling
  - active known-hostile bot still reaches hostile handling even when owner-added
  - final guardian policy removes invite create/mutation from destructive/panic surfaces
  - exact legacy invite false-positive is masked and durably cleared
  - fast re-entry cannot re-ban the legacy invite false positive
  - a real destructive hostile identity is still blocked on fast re-entry

## Validation / results

Validated implementation head: `0f664bbf71495b4d4b178b753c1df2341f72850b`

- Dank Shield CI #2154 — **PASS**
  - Python compile — PASS
  - full unit suite — **1487 passed, 9 warnings**
  - standalone tool checks — PASS
  - public setup audit — PASS
  - public command-surface/friction audits — PASS
  - public invite/permissions audit — PASS
  - setup-safety audit — PASS
  - Dank Design Smart Auto-Detect audit — PASS
  - role-truth audit — PASS
  - event-boundary audit — PASS
  - Claim-first ticket security — PASS
  - Managed category SQL smoke test — PASS
- Ticket Owner Emergency Override #725 — **PASS**
- Dank Design Regression CI #405 — **PASS**
- Application Command Size Diagnostics #1158 — **PASS**
- Profile Runtime Diagnostics #912 — **PASS**
- committed diff whitespace check — PASS

This record update changes the branch head after the validated implementation head, so the final record-only head must also complete required CI before merge.

## Cleanup / conflicts

- PR #235 contains only the active task record, the three affected AntiNuke runtime files, two existing focused test files, and one new focused regression test file.
- No ticket, setup, permission-repair, lifecycle-card, schema, or unrelated feature code was changed.
- No PR review thread requires a code response at the time of this record update.
- `main` remained at the task base during implementation and the PR was mergeable during validation.

## Blockers / risks

- The exact historical Discord audit entry for the Discadia incident is not repository-accessible. The conflicting owner-add code path is proven and corrected, but the unavailable incident record is not fabricated.
- This change deliberately does **not** auto-unban existing Discord bans. Automatically reversing arbitrary bans would be unsafe because some may be legitimate. After deployment, affected false-positive users/bots must be manually unbanned if they remain on Discord's ban list.
- GitHub validation cannot prove which revision is currently running on Discloud. Production must be restarted/deployed from the merged `main` revision before the fix is live.

## Backlog discovered during incident logs

These are real but separate tasks and are intentionally not mixed into PR #235:

1. configured modlog channel repeatedly returns Discord `403 Missing Access`
2. configured welcome/exit card channel lacks View Channel, Send Messages, Embed Links, and Read Message History for Dank Shield
3. source-reputation query references missing `member_joins.evidence_tier` column
4. verify the merged revision is the revision actually running on Discloud

## Next step

Wait for required CI on this record-only final head, mark PR #235 ready, merge it, verify `main`, then deploy/restart Dank Shield from the merged `main` revision. After the fixed revision is live, manually unban/re-add only the accounts affected by these false positives. The next repository task should then address the logging/channel permission failures before the schema mismatch, one task at a time.
