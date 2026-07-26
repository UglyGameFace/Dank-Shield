# ACTIVE TASK

## DS-PROFILE-CARDS-001 — Private profile controls and non-repetitive live cards

**Status:** FINAL DOCUMENTATION-HEAD VALIDATION
**Branch:** `feature/live-profile-cards`
**PR:** #126
**Base:** `main` at `6cd8333dea9af10f753cb2692d3fbcadc37bf102`

## Single Active Task Lock

Do not switch to unrelated implementation work until this task reaches Definition of Done or the owner explicitly uses the force-switch format.

## Scope

Build a separate Dank Shield member-profile signature system without changing join-only welcome-card behavior.

- Existing welcome cards remain server-admin controlled and appear only when a member joins.
- `/dank profile` remains the one canonical member-profile command family.
- A server may optionally configure live profile cards in selected channels.
- Live cards must not repetitively post after every message.
- User privacy always wins over server display preferences.
- Platform identities are user-supplied and unverified unless a future OAuth flow verifies them.
- Setup uses Discord channel pickers and can include the saved welcome/start-here channel.
- Static welcome/start-here configuration remains separate from join/leave announcements.

## Root Causes Found

- The feature existed in draft PR #126 but had not completed exact-head validation.
- Temporary CI artifact plumbing and migration-copy experiments had polluted the branch.
- A committed root file literally named `\` contained an obsolete ANSI-colored patch dump and prevented Supabase from cloning the repository.
- Two migration pairs reused the same Supabase version prefixes (`20260426` and `20260611`). Supabase stores only the numeric prefix, so fresh replay could not record both files.
- Historical optional migrations assumed legacy tables (`tickets`, `guild_members`) always existed.
- Both guild-config migrations used `CREATE TABLE IF NOT EXISTS` but then indexed, triggered, or commented columns that an existing compatible table might not contain.
- `commands.py` had been accidentally shortened during earlier work, dropping passive channel/thread observers and exports.
- Profile setup preview/open/add-welcome callbacks could perform database or rendering work before acknowledging Discord interactions.

## Delivered Implementation

### Live signature behavior

- Disabled by default.
- Explicit manager-selected text channels.
- One bot-owned live profile card per enabled channel.
- DMs, bots, webhooks, system messages, and unsupported message types are ignored.
- Message bursts are debounced and coalesced to the latest eligible human speaker.
- Repeated cards for the same speaker are cooldown-suppressed.
- A new card is posted and ownership is persisted before the prior card is removed.
- Failed replacements leave the existing valid card intact.
- Restart/reconnect reconciliation validates stored ownership and removes duplicate bot-owned cards.
- User messages are never edited, deleted, copied, or reposted.
- Every send uses `AllowedMentions.none()`.

### Privacy and platforms

- Dedicated service-role-only storage for global privacy defaults, per-guild deny-only overrides, platform identities, and live-card ownership.
- RLS enabled with no anon/authenticated policies.
- Users can disable live cards and independently hide profile roles, account dates, or platforms.
- Server managers can restrict fields further but cannot reveal anything a member hid.
- External identities remain private until explicitly shared.
- Supported identities: Steam, Epic, Xbox, PlayStation, Nintendo, Riot, Battle.net, Roblox, Twitch, YouTube, Kick, and a limited custom entry.
- Visible usernames are stored separately from optional URLs.
- Clickable link buttons are emitted only for validated HTTPS URLs on official platform hosts.
- Username-only platforms never receive fabricated profile links.

### Setup and command ownership

- Reuses the existing `/dank profile` group; no duplicate group or command replacement.
- One additive runtime listener owner is attached idempotently.
- Profile runtime registration has its own failure boundary and cannot be skipped by an unrelated command-module failure.
- The complete pre-existing `commands.py` passive lifecycle path and exports are preserved.
- Canonical setup path: `/dank setup` → All Features & Settings → Member Profiles & Live Cards.
- Multi-channel Discord text-channel picker; no copied channel IDs.
- Add Welcome Channel stages the saved welcome/start-here channel before saving.
- Enabling refuses channels missing View Channel, Send Messages, Embed Links, or Read Message History.
- Slow setup actions acknowledge Discord before storage/render work.
- `/dank profile live-cards` remains a one-channel manager fallback.
- `/dank welcome` remains the static welcome/start-here and join-only image-card owner.
- `/dank welcome join-leave` owns separate member join/leave announcements.

## Migration and Repository Cleanup

- Restored the canonical CI workflow; temporary pytest/tool artifact plumbing removed.
- Removed the temporary repository-portability audit.
- Removed the root `\` patch-dump artifact that broke Supabase cloning.
- Kept the original `20260426_create_guild_configs.sql` version for existing history and moved the later reconciliation migration to unique version `202604260001`.
- Kept `20260611_member_activity_notices.sql` on the original version and moved the later ticket-automation migration to unique version `202606110001`.
- Added a global regression requiring every committed Supabase migration version to be unique.
- Made the TicketTool parity migration skip safely when the legacy `tickets` table is absent; it never fabricates a partial ticket table.
- Made the guild-member role-state migration require both the legacy table and `role_state` column before altering constraints.
- Made both guild-config migrations reconcile their required columns before indexes, triggers, comments, or seed updates use them.
- Supabase fresh preview now replays the complete migration chain successfully, including the new live-profile-card migration.

## Conflict Inspection

- `commands.py` is additive against `main`: profile registration added with zero existing lifecycle deletions.
- Branch comparison after implementation: ahead of `main`, behind by zero, mergeable.
- No inline review threads and no submitted reviews are unresolved.
- No unrelated implementation task was started.

## Validation

Implementation head `7dffef6153582c0e90871ad62060c8867c737eec`:

- ✅ Dank Shield CI run #801
- ✅ committed diff whitespace
- ✅ Python compilation
- ✅ full unit test suite
- ✅ every standalone tool check
- ✅ public setup text/isolation audit
- ✅ canonical public command-surface audit
- ✅ startup-friction audit
- ✅ public invite-permission audit
- ✅ setup-safety audit
- ✅ Dank Design Smart Auto-Detect audit
- ✅ role-truth ownership audit
- ✅ event-boundary ownership audit
- ✅ Setup Check Inference Sanity runs #323 and #324
- ✅ Supabase preview database, services, APIs, configuration, migrations, seeding, and edge-function checks

## Remaining Gates

- This documentation-only task-record commit must pass exact-head CI and setup inference.
- Reconfirm branch comparison and review-thread state on the final head.
- Keep PR #126 draft and unmerged until a live Discord smoke-through and explicit owner approval.

## Backlog

None.

## Definition of Done

- [x] Actual command, persistence, event, caller, and test paths inspected.
- [x] Dedicated user-scoped persistence and migration added.
- [x] Existing `/dank profile` panel gains private settings/privacy/platform controls.
- [x] Server setup can enable selected live-card channels without copied IDs.
- [x] Saved welcome/start-here channel can be added through the profile setup picker.
- [x] Join/leave announcements are separated from static welcome and join-only card commands.
- [x] Live cards are restart-safe and non-repetitive.
- [x] Privacy and official-link validation are enforced in the service layer.
- [x] Existing command lifecycle observers and exports are preserved.
- [x] Slow profile setup interactions are acknowledged before storage/render work.
- [x] Focused behavioral tests pass.
- [x] Implementation head passed full CI and repository audits.
- [x] Complete Supabase migration replay passed.
- [x] Temporary helpers/debug artifacts are removed.
- [x] Branch is conflict-free with `main` before the documentation commit.
- [ ] Exact final documentation head passes CI and setup inference.
- [ ] Live Discord smoke-through completed.
- [ ] Merge requires explicit owner approval.
