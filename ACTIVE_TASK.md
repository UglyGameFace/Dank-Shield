# ACTIVE TASK

## DS-INVITE-035 — Invite reconciliation production hardening

**Status:** PRODUCTION CORE ACCEPTANCE PASSED / HARDENING IMPLEMENTED / VALIDATION PENDING
**Branch:** `fix/invite-reconcile-production-hardening-195`
**Base:** `9b56271b175e0acdb7013835d2f4ed146adf5a1b` (`main`, squash merge of PR #194)
**Started:** 2026-09-10

## Outcome required

Dank Shield must keep one central invite-delete policy, delete blocked invites live, recover missed blocked invites automatically, preserve allowed/same-server invites, remain fail-safe during transient persistence failures, and perform recovery without unnecessary database/disk/Discord-API amplification or misleading runtime-health telemetry.

## Production acceptance from PR #194

The first deployed build after PR #194 proved the previously missing recovery path is active:

- guild `1357215261001912320`: 37 eligible channels, 3,491 messages checked, 48 invite messages matched, 48 deleted, 0 failed;
- guild `1514374173517152418`: 42 eligible channels, 2,315 messages checked, 51 invite messages matched, 51 deleted, 0 failed;
- **99 historical blocked invite messages were removed automatically on ready without staff cleanup**;
- a later live external invite was still deleted by the guaranteed `invite_live_enforcer`, proving live ownership remained intact after recovery;
- guilds with no enabled invite-delete path were skipped rather than scanned needlessly.

Core DS-INVITE-035 production behavior therefore passed. Remaining work is hardening and stronger acceptance telemetry, not restoration of the original missing feature.

## Production findings driving this hardening pass

### 1. Process-health RSS telemetry was mislabeled

`stoney_verify/startup_guards/process_health.py` reported `resource.getrusage(...).ru_maxrss` as `rss≈...`. On Linux that value is the process lifetime **peak** RSS, not current resident memory. The production screenshot therefore could not prove a live memory leak: tasks fell from roughly 58 to 13 while the displayed RSS stayed near 313 MB because the metric itself cannot fall.

### 2. Bulk invite recovery amplified persistence and display work

Each successful historical delete went through the correct durable event ledger, but `record_deleted_invite_decision()` also:

- reread the legacy compatibility invite counter before every event;
- mirrored the durable total back into guild config after every event;
- rewrote the retry-outbox file after every normal successful event even when nothing had been pending.

The compatibility mirror also schedules a forced security-stats display refresh. That display updates changed stats voice-channel names with `channel.edit(name=...)`. During the 99-message startup recovery, repeated coalesced refreshes therefore formed a plausible source of the observed long Discord `PATCH /channels/...` 429. The hardening must reduce that amplification without changing one-event-per-delete durability.

### 3. Central invite classification duplicated Discord lookups

`invite_policy_engine._guild_invite_codes()` called Spam Guard's five-minute cached `_fetch_guild_invite_codes()` and then repeated `guild.invites()`. A completed empty cached snapshot also caused the central policy to repeat the same REST call, defeating the cache for servers with no ordinary invite codes or an already-observed fetch failure.

The same helper also called `guild.vanity_invite()` for every matched invite classification. During a historical cleanup, that could create another per-message REST stream even though vanity identity is stable and per-code target resolution already exists.

For individual invite classification, `_invite_code_belongs_to_guild()` called the shared `invite_shield_sanitize_shared` resolver, which already uses and caches `bot.fetch_invite()` by invite code, and after a non-local result called `fetch_invite()` a second time to rediscover the target guild.

### 4. Recovery summaries could hide channel warnings

`scan_channel_invites()` can return a warning string for a channel-level scan problem without necessarily incrementing the numeric `failed` counter. Aggregating only `failed=0` could therefore overstate a clean recovery. Event recovery also omitted the existing `allowed` count from its log, making safe allow-path acceptance harder to observe.

## Hardening implemented

### Runtime memory truth

- Linux current RSS is read from `/proc/self/status` (`VmRSS`) with `/proc/self/statm` fallback.
- `ru_maxrss` remains available only as the explicitly labeled lifetime peak.
- heartbeats, ready logs, signal logs, and process-exit logs now emit `rss_current≈... rss_peak≈...` instead of presenting peak as current memory.

### Durable invite-stat efficiency

- Added a **pass-local per-guild bulk recovery seed** used only after the first successful `auto-reconcile:*` durable event in that recovery pass.
- The first bulk event still reads the legacy compatibility seed, preserving the previous compatibility floor.
- Later events in the same recovery pass reuse the authoritative durable total returned by the previous successful write, eliminating repeated legacy seed reads without carrying a generic process-wide count cache.
- The pass-local seed is cleared when the one final bulk reconciliation finishes, including failure cleanup.
- Every deleted message still records its own replay-safe durable event. Deduplication and the SQL event ledger are unchanged.
- Bulk startup/resume recovery defers the legacy compatibility-counter mirror until the guild scan finishes, then performs one authoritative durable-count reconciliation.
- Normal `globals_live_enforcer` writes retain immediate compatibility sync behavior.
- Retry-outbox persistence is no longer rewritten after a normal successful event unless an actual pending entry was removed.
- Retry-persisted events still rewrite the outbox and still mirror their successful durable result.

### Discord invite lookup efficiency

- A successfully completed Spam Guard own-code snapshot is trusted even when it is empty, so the central policy does not immediately repeat the exact same `guild.invites()` call.
- Direct `guild.invites()` remains as a compatibility fallback when the shared getter itself cannot run or raises out to the caller.
- `guild.vanity_url_code` is used immediately when Discord already supplied it.
- The `guild.vanity_invite()` fallback is cached per guild for five minutes instead of being requested for every matched historical message.
- Individual invite target classification reuses `invite_shield_sanitize_shared.fetch_invite_guild_id()`, which already performs and caches the public `fetch_invite()` lookup.
- A resolved target ID equal to the current guild remains internal; a different nonzero target remains external.
- Target names are recovered from the bot's existing guild cache when available, without another network request.
- If the shared resolver cannot prove a target, the existing low-level HTTP `get_invite` fallback remains in place.
- No allow/block policy semantics were changed to achieve the request reduction.

### Reconciliation observability

- Guild summaries now include `warnings=<channel count>` in addition to `failed`.
- Channel scan warnings are logged with guild/reason context.
- Event recovery logs now include `allowed=<count>` so a deliberately posted same-server/allowed invite can be verified as surviving the policy path without introducing another decision engine.
- A completed bulk recovery with deletes emits one `stats_flush` line containing the final durable invite total.

## Safety invariants

- `invite_policy_engine` remains the only authority allowed to approve an invite deletion.
- `globals` remains the single live invite-enforcement owner.
- `invite_reconciliation_runtime` remains the single missed-message recovery owner.
- Same-server invites, explicit allowed codes/channels/roles/users, exemptions, Link Shield, Invite Shield, protected-poster rules, and Spam Guard burst semantics are unchanged.
- The regular own-invite lookup still runs through Spam Guard's cached `guild.invites()` path, and direct list fallback remains if that shared getter cannot execute.
- Vanity codes remain covered by the gateway-provided vanity code, a bounded REST fallback cache, per-code public resolver, and low-level per-code fallback.
- The low-level per-code target lookup still exists when the shared cached target resolver cannot prove a guild.
- Every successful delete still creates one durable event identity based on guild/channel/message and still uses the existing database ledger.
- No new Supabase table, RPC, or migration is required.
- The durable counter remains monotonic exactly as the existing SQL RPC already enforces with `greatest(existing, seed)` plus `on conflict (event_hash) do nothing`.
- Failed durable writes still enter the retry outbox; no moderation delete is rolled back because a statistics write failed.
- Welcome-card rendering and unrelated ticket/server-design code remain out of scope.

## Regression coverage

- `tests/test_process_health_memory_195.py` checks that current Linux RSS and peak RSS are distinct and clearly labeled.
- `tests/test_durable_invite_stats_recovery_195.py` checks pass-local bulk seed reuse, the first-event legacy floor, deferred bulk compatibility mirroring, unchanged immediate live mirroring, no pointless outbox rewrite on ordinary success, required outbox rewrite when a pending entry is removed, one final bulk flush, and pass-local seed cleanup on success/failure.
- `tests/test_invite_policy_lookup_efficiency_195.py` checks populated and empty shared own-code snapshots do not trigger duplicate list fetches, direct list fallback remains when the shared getter cannot run, vanity REST fallback is cached, gateway vanity avoids REST entirely, shared target-ID resolution classifies external and same-server invites without a second client `fetch_invite`, and unresolved shared lookup still uses the low-level same-server fallback.
- `tests/test_invite_runtime_reconcile_194.py` checks one final bulk stats flush, warning accounting, and event-recovery allowed telemetry while preserving all PR #194 ownership/permission/concurrency/retry coverage.

## Validation required

Before merge:

- focused DS-INVITE-035 regressions;
- existing invite policy/link safety regressions;
- durable invite-stat existing regressions;
- process-health telemetry regression;
- Python compileall;
- committed diff whitespace;
- invite/link safety audit;
- full repository pytest suite;
- all PR workflows green on one exact head;
- final base-drift/scope/diff/review-thread check.

## Production acceptance after hardening deploy

Require:

- `rss_current` and `rss_peak` both visible on Linux heartbeats so post-startup memory can actually be judged;
- `stats_flush` once after a bulk recovery that deleted historical invites, rather than a compatibility/display mirror for every event;
- recovery summary `warnings=0` on healthy channels;
- a same-server or explicitly allowed invite produces event-recovery telemetry with `allowed>0` and remains present;
- a blocked external invite still produces `invite_live_enforcer ... deleted=True`;
- transient persistence failure continues to defer/retry rather than converting a known configured guild into a fresh authoritative unconfigured state;
- no repeated startup burst of security-stats channel edits attributable to historical invite-count increments;
- no per-message repetition of guild invite-list, vanity invite, or public invite-target requests when the bounded caches already cover them.

## Suspended / backlog

- **DS-TICKET-034 production acceptance:** historical Ticket Choices restore/cross-guild live acceptance remains suspended.
- **Join-context Supabase schema mismatch:** production previously reported missing `entry_confidence` in `guild_members` and `member_joins`.
- **Generic memory optimization:** do not optimize allocations based on the old `ru_maxrss` high-water mark; first observe corrected current RSS after this hardening deploy.
- **Server Design setup regression/full audit:** preserve for after current invite task closure.

## Next step

Freeze one exact code-and-record head, require the complete PR workflow gate plus final scope/diff/review checks, squash-merge PR #195 only when that exact head is green, then perform the production checks above.
