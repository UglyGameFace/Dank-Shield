# ACTIVE TASK

## DS-SEC-042 — Compromised Dank Shield token containment

**Status:** IN PROGRESS
**Branch:** `fix/antinuke-self-token-compromise-containment`
**Base:** `83ba250abffc621d398f9b2f8efc728e06b0c1d3` (`main`, merge of PR #208)

## Outcome

Close the remaining AntiNuke self-identity trust gap. When AntiNuke is enabled in contain mode, an audit event attributed to the Dank Shield bot account must be accepted as legitimate only when the running process can prove it issued the corresponding Discord API mutation. A self-attributed destructive action without matching local authorization is treated as probable bot-token compromise and must terminate that guild's exposure immediately.

## Confirmed findings

- PR #208 merged successfully and its zero-destruction structural/configuration hardening is on `main`.
- Canonical AntiNuke currently treats the guild owner and Dank Shield's own bot ID as absolute roots through `_actor_is_owner_or_bot()`.
- The self-bot exemption is necessary for legitimate setup/repair/rollback operations, but it also means destructive requests made elsewhere with a stolen Dank Shield token are attributed to the same bot identity and can bypass ordinary containment.
- Discord audit events are post-action evidence, so the first externally issued mutation cannot be pre-vetoed by an audit listener.
- Removing the bot from the affected guild is a practical fail-closed response: it immediately removes the compromised bot identity's authority in that guild, whereas local process locks alone cannot stop an attacker using the same token from another machine.

## Implementation scope

1. Add a dedicated self-action authorization runtime after the existing AntiNuke lockdown layer.
2. Wrap the running bot's Discord HTTP request path and record short-lived, high-confidence authorizations only for audited security-sensitive guild mutations actually issued by this process.
3. Match bot-attributed audit events against the local authorization ledger using guild/action/target evidence and consume authorizations once.
4. Treat unmatched bot-attributed protected actions as suspected token compromise while AntiNuke contain mode is active.
5. On confirmed mismatch, emit best-effort security telemetry/owner warning and make Dank Shield leave the affected guild immediately so the stolen bot identity cannot continue destructive actions there.
6. Keep alert mode non-destructive and preserve the physical guild-owner platform boundary.
7. Add focused regression coverage for legitimate self-actions, replay prevention, stale authorization rejection, mismatched target/action rejection, alert mode, and fail-closed guild self-ejection.
8. Run focused tests, compile/static checks, full exact-head CI, diff review, and cleanup before readiness.

## Safety / scope

- Defensive only. No destructive external test tooling, token-grabbing code, or bypass instructions are added.
- No direct changes to `main`.
- Existing #206 durable hostile identity and #208 lockdown behavior remain authoritative and must not be weakened.
- Authorization evidence is process-local and short-lived; old reasons, copied strings, or stale entries must not authorize later actions.
- Ordinary legitimate Dank Shield maintenance must continue to function when its outbound API action matches the resulting audit event.

## Definition of done

- bot-attributed protected audit actions cannot pass solely because the actor ID equals Dank Shield's bot ID;
- locally issued matching protected actions are recognized without false compromise containment;
- authorizations are one-time, target/action scoped, guild scoped, and expire quickly;
- unmatched protected self-actions in contain mode cause fail-closed guild self-ejection;
- alert mode reports without leaving;
- focused tests, compile/static checks, and full exact-head Dank Shield CI pass;
- PR diff contains no unrelated changes and final validation evidence is recorded without weakening prior AntiNuke protections.

## Next step

Inspect the Discord HTTP route/audit mapping used by the current discord.py range, implement the process-local authorization ledger and bot-attributed audit guard, then open a draft PR and validate the exact head.
