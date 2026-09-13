# ACTIVE TASK

## DS-SEC-041 — AntiNuke zero-destruction hardening

**Status:** IN PROGRESS
**Branch:** `fix/antinuke-zero-destruction-hardening`
**Base:** `33d0b735f4eb7004682d74f4ca789f5f9eb5068c` (`main`, merge of PR #207)

## Outcome

Close the remaining AntiNuke control-plane and destructive-surface bypasses found after the hostile-actor re-entry repair. With AntiNuke enabled in contain mode, delegated actors must not receive destructive-action grace, generic configuration restore must not be able to disarm or retarget AntiNuke/security-control settings, newly added bots must follow an explicit allowlist even when the guild owner performs the add, and the audit gateway must classify the remaining high-impact Discord administrative surfaces.

## Confirmed findings

- PR #207 is merged and the production Supabase migration deployment completed successfully.
- Generic Configuration History restore can currently restore `antinuke_*` values even though direct AntiNuke mutation is guild-owner only.
- Configured trusted AntiNuke users/roles currently receive destructive thresholds and rollback exemptions in contain mode.
- The broad audit guardian does not currently classify message delete/bulk-delete and several integration/expression/event/thread/stage authority events.
- Server-control role IDs are not automatically treated as AntiNuke-sensitive role grants.
- A first-time unknown bot added by the physical guild owner is currently exempt from the normal untrusted-bot removal path.
- Discord's physical guild owner remains an unavoidable platform authority boundary; Dank Shield can detect owner-originated destruction immediately but cannot ban/kick/role-strip the owner.

## Implementation scope

1. Add a final AntiNuke lockdown runtime after hostile-identity installation.
2. Preserve live AntiNuke and server-control security keys across generic full/selective Configuration History restores.
3. Remove configured-trust destructive grace while AntiNuke is enabled in contain mode.
4. Automatically treat configured server-control roles as protected AntiNuke role IDs at runtime.
5. Expand dangerous-permission and audit-action coverage, including message purge and remaining high-impact administrative surfaces.
6. Require explicit bot-ID trust for owner-added bots while contain mode is active; known-hostile reputation still takes precedence over trust.
7. Make guild-owner compromise detection first-strike so the platform boundary is surfaced immediately.
8. Add focused regression tests plus full exact-head CI and diff cleanup before readiness.

## Safety / scope

- No offensive tooling or destructive test code is added.
- No direct changes to `main`.
- Existing durable hostile-identity enforcement remains authoritative and must not be weakened.
- Generic backup creation remains allowed; only restoring security-root values through the generic history path is blocked/preserved.
- Legitimate trusted bot IDs remain explicitly configurable, but a known-hostile ID cannot become safe merely by being trusted.

## Definition of done

- generic full and selective restores cannot change protected AntiNuke/security-control values;
- contain mode gives no destructive threshold/rollback exemption to configured trusted actors;
- owner-added unapproved bots are removed while explicitly trusted non-hostile bots are allowed;
- message purge plus the audited high-impact administrative surfaces are classified by the guardian;
- control-role grants are treated as security-sensitive;
- owner destructive activity alerts on the first attributed action;
- focused tests, compile/static checks, and the full exact-head Dank Shield CI pass;
- PR diff contains no unrelated changes and `ACTIVE_TASK.md` records final validation evidence.

## Next step

Implement the lockdown runtime and focused tests on this branch, open a draft PR, then validate and clean the exact head before marking it ready.
