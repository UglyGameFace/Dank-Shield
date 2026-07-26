# ACTIVE TASK

## DS-PROFILE-CARDS-001 — Private profile controls and non-repetitive live cards

**Status:** FINAL EXACT-HEAD VALIDATION
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

## Findings

- The feature existed in draft PR #126 but had not completed exact-head validation.
- The branch had temporary CI logging changes and historical migration renames that did not belong in the final feature diff.
- A committed root file literally named `\` contained an old ANSI-colored patch dump. It prevented Supabase from cloning the repository and had no runtime purpose.
- Two historical `20260611` migration files share one legacy version. A new test incorrectly tried to solve that by renaming deployed migrations, which made remote migration history disappear locally.
- Supabase fresh replay exposed a separate historical defect: `20260426_guild_configs.sql` indexed `enabled` and `public_beta_enabled` after an earlier same-day migration had created a smaller table without those columns.
- `commands.py` had been accidentally shortened during earlier work, dropping existing passive channel/thread lifecycle observers and exports.
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

## Cleanup and Conflict Inspection

- Restored the canonical CI workflow; temporary pytest/tool artifact plumbing removed.
- Restored historical migration filenames; no deployed migration version is missing locally.
- Removed the duplicate copied ticket-automation migration.
- Removed the temporary repository-portability audit.
- Removed the root `\` patch-dump artifact that broke Supabase cloning.
- Historical guild-config migration keeps its original filename/version and now reconciles missing columns idempotently before indexing them.
- `commands.py` is additive against `main`: profile registration added with zero existing lifecycle deletions.
- Current branch comparison: ahead of `main`, behind by zero, mergeable.
- No inline review threads and no submitted reviews are unresolved.

## Validation

Implementation head `9ef425a697ce2eb3da1c4f990aa179566c3d53f9`:

- ✅ Dank Shield CI run #785
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
- ✅ Setup Check Inference Sanity run #307

## Remaining Gates

- This task-record commit must pass the same exact-head CI and setup-inference gates.
- Close and reopen draft PR #126 once so Supabase recreates its preview branch and replays the corrected existing migration plus the new profile migration.
- Confirm Supabase migration/database health on the recreated preview.
- Reconfirm branch comparison and review-thread state.
- Keep the PR unmerged until explicit owner approval and a live Discord smoke-through.

## Backlog

None. No unrelated implementation task was started.

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
- [x] Temporary helpers/debug artifacts are removed.
- [x] Branch is conflict-free with `main` before the task-record commit.
- [ ] Exact final task-record head passes CI and setup inference.
- [ ] Recreated Supabase preview successfully applies migrations.
- [ ] Live Discord smoke-through completed.
- [ ] Merge requires explicit owner approval.
