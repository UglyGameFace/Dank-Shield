# ACTIVE TASK

## Active task / desired outcome

**P0-SERVER-STATS-001 — repair and expand Server Stats from `/dank home`**

Make Server Stats reliable, first-class in the compact UI, substantially more
customizable, and safe at public-bot scale without creating a second
persistence/runtime owner.

## Status

**IMPLEMENTED ON PR #289 — synchronized with current main; exact-head validation pending**

Branch: `fix/server-stats-home-customization`

Base after sync: `main@3c21431e7c4c8920b76ad671cf857b232f300c6d`

## Previous task closed

PR #304 — Restore single-owner Basic Verify interaction runtime

- merged to `main` as `3c21431e7c4c8920b76ad671cf857b232f300c6d`;
- exact PR head passed **1865 tests, 9 warnings** and all repository workflow/audit gates;
- post-merge ownership check confirmed the persistent Basic Verify view is
  authoritative and the duplicate compatibility dispatcher is absent.

## Root causes / required behavior

The existing Server Stats runtime was real but product access and lifecycle
ownership were incomplete:

- compact `/dank home` had no direct Server Stats destination;
- Protection exposed only a one-click creation path rather than a management surface;
- missing tracked categories were not self-healed reliably;
- Spam Guard counter changes could persist without promptly refreshing visible channels;
- names, visible counters, number formatting, and placement were hardcoded;
- periodic all-guild config reads would not scale;
- fully customized displays could not be safely rediscovered after restart;
- name-only recovery could adopt unrelated categories;
- failed hide/remove work could lose ownership IDs;
- disabling category ownership requires canonical config-key removal rather than an empty-ID write.

## Canonical ownership

`stoney_verify/security_stats.py` remains the single runtime owner for preference
normalization, category/channel ownership, counter computation, create/repair/
refresh/disable behavior, coalesced event refresh, bounded restart discovery, and
periodic refresh.

`commands_ext/public_server_stats.py` is UI only and delegates mutations to that
runtime owner.

`guild_config` remains the existing per-guild persistence authority. No schema
or migration is added.

## Main-sync conflict review

The original PR base was 142 commits behind current main.

Nine of the ten original PR paths were unchanged on main. The only overlapping
production file was `public_protection_center.py`, which has since gained
settings-registry work, native Import Pack behavior, and AntiNuke trust-role
controls.

The synchronized branch preserves current main's Protection Center and reapplies
only PR #289's Server Stats integration:

- rename the existing Live Stats presentation to Server Stats;
- route its existing custom ID into the dedicated Server Stats center;
- remove the now-unused one-click `ensure_security_stats_display` import;
- preserve all newer Protection Center behavior.

## Scope

In scope:

- first-class Server Stats entry from `/dank home`;
- route Protection's existing stats control to the same center;
- per-guild category name;
- visible-counter selection;
- custom labels/emoji;
- compact/exact number formatting;
- top/keep/bottom placement;
- reset, repair/refresh, disable/remove;
- missing-display self-heal;
- coalesced protection-event refresh;
- bounded active-display periodic work;
- batched persisted-display discovery;
- safe ownership recovery;
- cleanup retry ownership retention;
- single-panel modal behavior;
- focused scaling and behavior coverage.

Out of scope:

- new database schema;
- unrelated Protection/AntiNuke/Spam policy changes;
- Quiet Notice;
- Exit Card Unicode rendering;
- changing the compact public slash-root budget.

## Compatibility / cleanup

- The compact public command roots remain unchanged.
- Existing Server Stats config remains authoritative and backward compatible.
- Protection keeps its historical `dank_protection:live_stats` component custom ID.
- No duplicate stats persistence/runtime service is introduced.
- Current main's newer Protection Center features are preserved.

## Validation required

- exact-head branch/base comparison;
- diff/conflict/whitespace inspection;
- Python 3.11 compile;
- Server Stats focused behavior tests;
- command-surface regressions;
- startup recovery/scaling regressions;
- Protection Center regressions;
- full `pytest tests/`;
- standalone `tools/test_*.py` checks;
- all repository audits;
- GitHub workflow gates;
- review-thread inspection;
- final mergeability check.

## Blockers / risks

No known code blocker after synchronization. Validation may expose stale tests or
main-era compatibility changes and those must be resolved before merge readiness.

## Backlog

- PR #302: cross-guild Exit Card Unicode rendering remains the next task after PR #289.

## Next step

Run exact-head validation on the synchronized branch, repair only Server
Stats-related failures, perform final cleanup/review inspection, then mark PR
#289 ready and merge with an expected-head guard.
