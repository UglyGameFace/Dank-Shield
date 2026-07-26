# ACTIVE TASK

## DS-PROFILE-CARDS-001 — Private profile controls and non-repetitive live cards

**Status:** FINAL VALIDATION
**Branch:** `feature/live-profile-cards`
**Base:** current `main` after merged welcome-card shuffle PR #125

## Single Active Task Lock

Do not switch to unrelated implementation work until this task reaches Definition of Done or the owner explicitly uses the force-switch format.

## Owner Request

Build a separate Dank Shield member-profile card system without changing join-only welcome-card behavior.

- Existing welcome cards remain server-admin controlled and appear only when a member joins.
- `/dank profile` remains the one canonical member-profile command family.
- A server may optionally configure live profile cards in selected channels.
- Live cards must not repetitively post after every message.
- User privacy always wins over server display preferences.
- Platform identities are user-supplied and unverified unless a future OAuth flow verifies them.
- Setup must expose a real Discord channel picker and make it easy to include the saved welcome/start-here channel.
- Static welcome/start-here configuration must remain separate from join and leave event announcements.

## Architecture Rules

- No startup guard.
- No monkey patch.
- No duplicate `/dank profile` group or command owner.
- No duplicate card renderer or message listener.
- Reuse the current profile role/card functions and interaction-locking path.
- Server configuration remains in canonical guild config.
- User identities/privacy use dedicated service-role-only persistence with no public client policies.
- Live-card message state is durable so restart reconciliation cannot create duplicates.
- Never delete, edit, or repost a user's message.
- Delete or replace only a message authored by Dank Shield and recorded as its owned live card.
- External links must pass a platform-specific official-host allowlist; never invent profile URLs.
- Missing or private fields are omitted rather than displayed as `N/A`.

## Live Card Behavior

- Disabled by default.
- Explicit text-channel selection by a server manager.
- One bot-owned live profile card per enabled channel.
- Ignore DMs, bots, webhooks, system messages, and unsupported message types.
- Debounce message bursts and coalesce to the latest eligible human speaker.
- Suppress repeated cards for the same speaker during the configured cooldown.
- A failed replacement leaves the existing valid card intact.
- A successful replacement posts the new card before removing the previous owned card.
- Restart reconciliation validates the stored message and safely clears stale state.
- All sends use `AllowedMentions.none()`.

## Setup and Welcome Separation

- Canonical path: `/dank setup` → Manage Setup → All Features & Settings → Member Profiles & Live Cards.
- The profile center uses a multi-channel Discord text-channel picker; no copied IDs are required.
- **Add Welcome Channel** stages the server's saved welcome/start-here channel in the same picker before saving.
- Enabling refuses channels missing View Channel, Send Messages, Embed Links, or Read Message History.
- `/dank profile live-cards` remains a one-channel manager fallback that points to the full setup picker.
- `/dank welcome` owns the static welcome/start-here message and join-only image card.
- `/dank welcome join-leave` owns the separate member join and leave announcements.
- The old public `/dank welcome events` command is removed; only an internal compatibility function alias remains.

## User Privacy and Platforms

- External identities are hidden until the user explicitly shares them.
- Users can disable live cards for themselves.
- Users can independently control public profile roles, account dates, and platform identities.
- Server managers may restrict fields further but cannot reveal anything the user hid.
- Supported identities: Steam, Epic, Xbox, PlayStation, Nintendo, Riot, Battle.net, Roblox, Twitch, YouTube, Kick, and a limited custom entry.
- Store visible usernames separately from optional profile URLs.
- Use clickable Discord link buttons only when a validated official URL exists.
- Username-only platforms remain visible without a fabricated link.

## Validation Completed

- Patch application, changed-module compilation, focused profile/setup tests, full pytest, every standalone tool, all production audits, cleanup, and source push passed in the integration gate.
- Setup-safety audit now permanently requires the Member Profiles & Live Cards feature-center button.
- No inline review threads or submitted reviews are unresolved on PR #126.

## Validation Still Required

- Permanent read-only CI must pass on the exact cleaned final head.
- Supabase preview/migration status must be confirmed after the final migration-bearing head is available.
- Final changed-file cleanup and conflict comparison against current `main`.

## Definition of Done

- [x] Actual command, persistence, event, and test paths inspected.
- [ ] Dedicated user-scoped persistence added and migration validated on the final head.
- [x] Existing `/dank profile` panel gains private settings/privacy/platform controls.
- [x] Server setup can enable selected live-card channels without copying IDs.
- [x] Saved welcome/start-here channel can be added through the profile setup picker.
- [x] Join/leave announcements are separated from static welcome and join-only card commands.
- [x] Live cards are restart-safe and non-repetitive.
- [x] Privacy and official-link validation are enforced in the service layer.
- [x] Focused behavioral tests pass.
- [ ] Full CI and repository audits pass on the exact final head.
- [x] Temporary helpers/debug files are removed.
- [ ] Final branch is conflict-free with `main`.
- [ ] Merge requires explicit owner approval.
