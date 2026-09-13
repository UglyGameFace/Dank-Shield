# ACTIVE TASK

## DS-SEC-041 — AntiNuke zero-destruction hardening

**Status:** IN PROGRESS — SELF-PROVENANCE FOLLOW-UP AFTER PR #208
**Branch:** `fix/antinuke-self-provenance-guard`
**Base:** `83ba250abffc621d398f9b2f8efc728e06b0c1d3` (`main`, merge of PR #208)

## Outcome

Finish the remaining AntiNuke trust-boundary hardening after PR #208. The structural/configuration bypasses found in the first audit are now merged. The remaining high-value gap is Dank Shield's own Discord identity: Discord attributes requests made with the bot token to Dank Shield itself, while the canonical AntiNuke treats that identity as trusted. The runtime must be able to distinguish administrative mutations initiated by this running process from an externally issued request using a stolen bot token, without causing Dank Shield to attack its own legitimate setup/repair work.

## Completed in PR #208

- generic full/selective Configuration History restore can no longer roll back protected AntiNuke/server-control security values;
- contain mode removes configured-trust grace for structural/security destruction while preserving bounded ordinary moderation;
- server-control roles are security-sensitive without becoming implicit AntiNuke trust exemptions;
- dangerous permission coverage includes message/thread/event/expression authority;
- bulk message purge is classified as a severe first-strike action;
- owner-added bots require explicit bot-ID trust, with durable hostile reputation taking precedence;
- owner-originated destructive activity is surfaced on first strike;
- exact-head compile, focused regressions, full unit suite, and repository audit gates passed before merge.

## Remaining confirmed gap

- `_actor_is_owner_or_bot()` treats Dank Shield's own bot identity as an absolute trust root.
- That is correct for normal self-generated setup/repair mutations, but Discord uses the same identity when a stolen bot token is used externally.
- Audit actor identity alone therefore cannot distinguish legitimate local bot work from token-compromise activity.
- Discord does not provide a bot API that lets a running bot rotate/revoke its own token, so token compromise remains a platform-root boundary. The defensive goal is immediate attribution, rollback where Discord exposes reversible state, owner/out-of-band warning, and elimination of silent self-exemption.

## Implementation scope

1. Add a self-action provenance runtime after the existing lockdown layer.
2. Mark locally initiated high-impact Discord REST mutations with one-time in-process provenance IDs in the audit-log reason.
3. Keep a short-lived one-time ledger binding provenance IDs to the expected audit action/target so copied or stale reasons cannot simply be replayed.
4. Replace/wrap the canonical audit listener so a Dank Shield-authored audit entry is trusted only when its provenance is valid.
5. Treat missing, invalid, mismatched, or replayed provenance on a protected self-attributed action as suspected bot-token compromise.
6. Reuse existing AntiNuke rollback paths where safe and available, including created channel/webhook removal, overwrite rollback, AutoMod rollback, and sensitive role-grant/permission rollback.
7. Alert the guild owner directly as well as the normal incident/modlog path when suspected bot-token compromise is detected.
8. Preserve ordinary non-self AntiNuke behavior and sparse-attribution recovery.
9. Add focused tests for valid provenance, replay/mismatch rejection, self-attributed compromise handling, disabled-mode behavior, sparse recovery dispatch, and startup ordering.
10. Run full exact-head CI and diff cleanup before readiness.

## Safety / scope

- No offensive tooling, destructive test payloads, token extraction, or bypass instructions are added.
- No direct changes to `main`.
- Existing hostile-identity, owner-compromise, configuration-lockdown, and structural first-strike protections remain authoritative.
- Provenance IDs are random, one-time, short-lived, and kept only in process memory. They are not a replacement for Discord token rotation after an actual compromise.
- A compromised physical guild owner and a stolen Dank Shield bot token remain Discord platform-root boundaries; this work removes silent trust and maximizes detection/rollback but does not claim an impossible pre-action veto over those roots.

## Definition of done

- locally initiated protected bot mutations are recognized without false AntiNuke incidents;
- an externally issued protected mutation attributed to Dank Shield without valid live provenance is never silently trusted;
- provenance cannot be reused after consumption and mismatched action/target use is rejected;
- reversible self-attributed destructive actions use the existing rollback paths where safe;
- suspected self-token compromise produces a critical AntiNuke incident plus owner warning;
- sparse audit recovery still reaches the same provenance decision;
- existing AntiNuke tests remain green and new focused regressions cover the trust boundary;
- full exact-head Dank Shield CI and repository audit gates pass;
- PR diff contains no unrelated changes and final validation evidence is recorded without creating another self-invalidating code commit.

## Next step

Implement the self-action provenance runtime and focused regressions on this branch, then open a draft PR and validate the exact head before marking it ready.
