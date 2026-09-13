# ACTIVE TASK

## DS-SEC-042 — AntiNuke zero-damage + compromised Dank Shield identity hardening

**Status:** IMPLEMENTATION COMPLETE / FINAL EXACT-HEAD VALIDATION PENDING
**Branch:** `fix/antinuke-self-token-compromise-containment`
**Base:** `83ba250abffc621d398f9b2f8efc728e06b0c1d3` (`main`, merge of PR #208)
**PR:** #209 — `Fail closed on unverified Dank Shield self-actions`

## Outcome

Close the remaining AntiNuke attack-surface gaps found after #208. The final design combines durable hostile identity, structural rollback/containment, one-time proof for legitimate Dank Shield self-actions, durable fail-closed quarantine for suspected Dank Shield credential misuse, broader current Discord audit coverage, and a preventive readiness gate that refuses contain mode while delegated identities still retain native AntiNuke-risk authority.

The product promise is intentionally precise: Dank Shield can remove native destructive authority from the *allowed configuration* by refusing to arm strict contain mode until delegated dangerous permissions are removed. Discord's physical guild owner and an attacker already holding Dank Shield's own credential remain platform boundaries because Discord accepts those actors' API requests before creating the audit entry.

## Ten audit findings addressed

1. **Self-ejection retry suppression:** suspected self-compromise no longer marks a guild handled before `guild.leave()` succeeds. Ejection retries three times, a failed attempt remains retryable, and owner warning is attempted even when ejection fails.
2. **Direct webhook HTTP gap:** direct `PATCH/DELETE /webhooks/{id}` requests are included in local self-action proof in addition to the high-level `discord.Webhook` wrappers.
3. **Security-state fail-open:** unmatched self-attributed protected actions use the durable AntiNuke security snapshot when live config lookup fails and fail closed when neither live nor durable state can be read.
4. **Single-message delete gap:** `message_delete` joins bulk deletion in the guarded audit surface and is first-strike in contain mode.
5. **Post-action audit limitation:** strict contain mode now has a preventive readiness gate. It cannot be newly armed while any non-owner/non-Dank role or channel overwrite still grants permissions already classified by AntiNuke as dangerous. Existing unsafe contain-mode guilds are reconciled at ready/join and receive a critical warning until owners remove the blockers.
6. **Incomplete Discord audit surface:** guardian coverage now includes current integration, expression, scheduled-event, thread, stage, soundboard, onboarding, Server Guide/Home, member voice move/disconnect, and related create/update/delete surfaces in addition to the pre-existing structural set. Raw voice-channel-status audit values 192/193 are compatibility-mapped because discord.py 2.7.1 does not yet name them.
7. **Narrow guild-update filter:** security-sensitive `GUILD_UPDATE` classification now includes notification/system/rules/public-update/safety-alert channels, locale, features, AFK settings, system flags and other durable guild settings in addition to the original identity/security fields.
8. **Readiness truth:** contain-mode health now verifies the exact self-action proof hooks and tested discord.py route contract. Least privilege is preserved: Dank Shield is not granted extra Manage Messages/Threads/Events/Expressions permissions merely because those actions are audited.
9. **In-memory-only compromise state:** suspected Dank Shield identity compromise is persisted locally immediately and mirrored to guild config. `on_ready` and `on_guild_join` re-enforce active quarantine after restart/re-add. Default quarantine is 30 minutes and can be explicitly configured.
10. **Broad discord.py compatibility range:** production is pinned to `discord.py==2.7.1`, the route/audit behavior validated by this runtime and its regressions.

## Implementation

- `stoney_verify/anti_nuke_self_action_runtime.py`
  - one-time `[DSA:<nonce>]` local audit authorization;
  - action/guild/target scoped;
  - one-time and short-lived;
  - protects the self-bot trust boundary without reading/exposing the Discord credential.
- `stoney_verify/anti_nuke_zero_damage_runtime.py`
  - direct webhook route proof;
  - settings fail-closed path;
  - retryable self-ejection;
  - durable compromise quarantine;
  - expanded guardian surface and guild-update fields;
  - exact discord.py/self-proof health contract.
- `stoney_verify/anti_nuke_audit_compat_runtime.py`
  - raw Discord audit action 192/193 compatibility for voice-channel status create/delete;
  - matching local request proof route.
- `stoney_verify/anti_nuke_readiness_strict.py`
  - read-only scan for delegated dangerous role/overwrite authority.
- `stoney_verify/anti_nuke_readiness_gate_runtime.py`
  - blocks new contain-mode enablement while delegated dangerous authority remains;
  - appends blockers to AntiNuke permission health;
  - warns once per process for already-enabled unsafe guilds on ready/join.
- `requirements.txt`
  - pins `discord.py==2.7.1`.
- `main.py`
  - installs self-action proof, zero-damage hardening, audit compatibility and strict readiness gate in final order before app import.

## Important policy choices

- **No silent staff-role surgery.** The bot does not automatically rewrite/remove server staff permissions as a side effect of enabling AntiNuke. Instead, strict contain mode refuses to arm until the owner intentionally removes conflicting delegated authority.
- **No broad first-strike false positives.** First-strike remains scoped to destructive/security-root actions in the guardian map. Benign create/update actions stay observable/weighted without treating every legitimate admin action as a nuke.
- **Least privilege for Dank Shield.** Expanded audit coverage does not justify requesting additional destructive Discord permissions the bot does not need for existing rollback/containment.
- **Physical owner boundary remains explicit.** #208 detects owner-originated destruction on first strike but Discord does not permit a bot to kick/ban/role-strip the physical guild owner.
- **Stolen Dank Shield credential boundary remains explicit.** The first externally accepted request can occur before Discord emits its audit record; DS-SEC-042 minimizes follow-on damage with self-origin proof, immediate retrying self-ejection and durable quarantine.

## Regression coverage

- legitimate one-time self-action proof and replay/stale/mismatch rejection;
- direct webhook HTTP route classification;
- config-outage durable snapshot and fail-closed fallback;
- retryable ejection and no failure suppression;
- durable quarantine across in-memory reset/restart path;
- single-message deletion and expanded administrative action classification;
- broad guild-update field classification;
- raw Discord 192/193 voice-status audit compatibility and local proof route;
- discord.py exact-version contract;
- delegated dangerous role/overwrite readiness blockers;
- owner-only dangerous authority exclusion;
- save-time refusal to arm unsafe contain mode;
- warning for pre-existing unsafe contain mode;
- startup-order regression through all AntiNuke layers.

## Cleanup / scope review

- No offensive tooling, token-grabbing code, credential extraction, destructive external harness, or nuker recreation added.
- No direct changes to `main` branch.
- Existing #206 durable hostile identity and #208 structural/configuration lockdown remain authoritative.
- Automatic delegated-role permission stripping was intentionally rejected in favor of an explicit read-only readiness gate.
- An over-broad experimental final threshold wrapper was removed before final validation; first-strike policy remains action-scoped.
- Extra bot permission requirements that would increase stolen-token blast radius were removed before final validation.

## Production risks / acceptance

- Discord audit entries are post-action evidence. The physical owner and Dank Shield's own credential cannot be pre-vetoed by the bot itself.
- Strict contain mode therefore requires the owner to remove dangerous native permissions from all delegated users/bots/roles/overwrites before enablement.
- Already-enabled servers with delegated dangerous authority remain protected by reactive rollback/containment but are explicitly reported as not satisfying strict preventive readiness until corrected.
- Live adversarial testing must happen only after the exact PR head is fully green and merged/deployed.

## Definition of done

- all ten findings above have focused regression coverage;
- compile/static/standalone audits and the full exact-head Dank Shield CI pass;
- exact-head auxiliary workflows pass;
- PR diff contains only DS-SEC-042-owned changes with no conflict markers, secrets, debug bypasses or unrelated edits;
- PR body records exact-head validation and platform boundaries;
- PR is marked ready only after the final exact head is green.

## Next step

Run final exact-head CI on the bookkeeping-complete branch. If any job fails, inspect the exact failure and repair it on the same branch. If all workflows pass, perform the final diff/security review, update PR metadata without moving the head, and mark #209 ready for review. Do not merge without explicit user approval.
