# ACTIVE TASK

## DS-PROFILE-CARDS-001 — Private profile controls and non-repetitive live cards

**Status:** EXECUTION-PATH AUDIT AND IMPLEMENTATION
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

## Architecture Rules

- No startup guard.
- No monkey patch.
- No duplicate `/dank profile` group or command owner.
- No duplicate card renderer or message listener.
- Reuse the current profile role/card functions and interaction-locking path.
- Server configuration remains in canonical guild config.
- User identities/privacy use a dedicated user-scoped persistence table with no public client policies.
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

## User Privacy and Platforms

- External identities are hidden until the user explicitly shares them.
- Users can disable live cards for themselves.
- Users can independently control public profile roles, account dates, and platform identities.
- Server managers may restrict fields further but cannot reveal anything the user hid.
- Supported identities: Steam, Epic, Xbox, PlayStation, Nintendo, Riot, Battle.net, Roblox, Twitch, YouTube, Kick, and a limited custom entry.
- Store visible usernames separately from optional profile URLs.
- Use clickable Discord link buttons only when a validated official URL exists.
- Username-only platforms remain visible without a fabricated link.

## Validation Required

- Runtime behavior tests for debounce, coalescing, cooldown, same-speaker suppression, bot/webhook ignoring, one-card ownership, failed-send safety, and restart reconciliation.
- Privacy tests proving hidden-by-default identities and stricter-user-choice precedence.
- URL normalization tests covering official links and phishing lookalikes.
- Card tests proving clean labels, omission of hidden/missing fields, and pagination only when necessary.
- Command-tree uniqueness and normal startup registration.
- Event-boundary ownership and setup-safety audits.
- Python compilation, full pytest, every standalone tool check, complete production audits, cleanup inspection, review-thread inspection, and conflict comparison against `main`.

## Definition of Done

- [ ] Actual command, persistence, event, and test paths inspected.
- [ ] Dedicated user-scoped persistence added and migration validated.
- [ ] Existing `/dank profile` panel gains private settings/privacy/platform controls.
- [ ] Server setup can enable selected live-card channels without copying IDs.
- [ ] Live cards are restart-safe and non-repetitive.
- [ ] Privacy and official-link validation are enforced in the service layer.
- [ ] Focused behavioral tests pass.
- [ ] Full CI and repository audits pass on the exact final head.
- [ ] Temporary helpers/debug files are removed.
- [ ] Final branch is conflict-free with `main`.
- [ ] Merge requires explicit owner approval.
