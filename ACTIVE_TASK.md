# ACTIVE TASK

## DS-PROFILE-CARDS-012 — Premium live profile banners and separated role controls

**Status:** IMPLEMENTED / FULL EXACT-HEAD CI AND DEPLOYED DISCORD SMOKE PENDING
**Branch:** `fix/live-profile-channel-spam`
**PR:** #145
**Base:** current `main` (`0` commits behind at the latest comparison)

## Single Active Task Lock

Do not switch to unrelated implementation work until the profile-card runtime, wide banner renderer, platform controls, privacy defaults, and the separate **Server Roles** / **Profile Tags & Cosmetics** workflows pass exact-head validation and deployed Discord smoke.

## Scope

- Stop live profile cards from creating walls of bot messages in active chat.
- Use the supplied wide 420-lobby card direction for every live profile signature while keeping all text and branding dynamic.
- Keep link, copyable-username, and logo-only platform modes fast and privacy-safe.
- Separate ordinary server-role visibility from member-selected profile tags/cosmetics in both settings and navigation.
- Preserve mobile, tablet, and desktop usability.

## Root causes confirmed

- Per-member visible-card ownership created one persistent bot message for every speaker in the channel.
- Slow renders could finish after a newer speaker and place a stale card under the wrong conversation position.
- The startup compatibility guard injected a second Profile Tags manager route even though the native builder already owned one.
- Character-count truncation did not guarantee that long role names, platform usernames, or server names stayed inside their reserved pixel regions.
- Temporary materializer workflows competed over the same profile files during development; those workflows and scripts are no longer present in the final branch tree.

## Implemented behavior

### Live delivery

- One verified visible Dank Shield signature per configured channel.
- A short quiet window coalesces message bursts and lets the latest eligible speaker win.
- Stale work is rejected before send, after send, and around durable-state persistence.
- Existing stacked cards are collapsed safely on first activity.
- Disabling live signatures removes the member's current card and prevents reposting.
- Bot-authored signature output remains isolated from moderation and activity systems.

### Banner design and spacing

- Wide `1400 × 340` premium banner layout inspired by the supplied 420 Lobby examples.
- Dynamic member avatar, display name, server name, server icon, roles, profile tags, dates, and platform identities.
- Theme/accent-aware background, frame, glow, motifs, and typography.
- Bundled real platform artwork; no generic Unicode substitutes in the rendered card.
- Pixel-width fitting with ellipsis for server-role badges, platform usernames, profile-tag/role chips, and server labels.
- Reserved right-side platform and server-branding regions cannot be crossed by long dynamic text.

### Platforms

- **Link:** opens a validated official profile URL.
- **Username:** pressing the username returns a private copy-ready text box in the same Discord client.
- **Logo only:** renders the platform mark without requiring a username or creating a dead button.
- Preview preserves link and copyable-username controls.
- Saving account details does not make them public automatically.

### Role separation

- **Server Roles** is a dedicated visibility control for safe roles already assigned by the server.
- Server-role display defaults to hidden for privacy.
- **Profile Tags & Cosmetics** separately owns pronouns, identity, interests, community labels, and harmless cosmetics.
- Profile-tag display defaults to shown, subject to server policy and the member's privacy choice.
- The native `builder:cosmetics` route is the only builder manager route.
- The obsolete guard-added `builder:role_editor` route and its handler were removed.
- Missing profile-tag suggestions are review-only and never create or assign roles automatically.

## Validation status

- [x] Focused Profile Runtime Diagnostics passed on source head `7c49cf3`.
- [x] Application Command Size Diagnostics passed on source head `7c49cf3`.
- [x] Profile implementation compilation passed.
- [x] Focused live-delivery, lifecycle, cleanup, migration, privacy, platform-mode, visual-link, role-separation, and spacing regressions passed.
- [x] Static Profile Tags manager and compatibility-guard checks passed.
- [x] Bundled platform assets and attribution checks passed.
- [x] `git diff --check` passed.
- [x] Temporary patch scripts, payload directories, and competing workflows are absent.
- [x] Branch comparison reports `0` commits behind `main`.
- [ ] Full repository unit suite, standalone tools, and all safety/ownership audits finish successfully on the latest exact head.
- [ ] Final PR diff and GitHub mergeability are rechecked after all workflows settle.

## Deployed Discord smoke gates

- [ ] Three users speak rapidly; after the quiet window exactly one card remains for the latest speaker.
- [ ] A new message during rendering leaves no stale card behind.
- [ ] Existing stacked cards collapse on first activity.
- [ ] Long usernames, server names, platform handles, and role names remain inside the banner boundaries.
- [ ] Username-mode platform controls return a fast private copy-ready value on mobile, tablet, and desktop.
- [ ] Link-mode controls open the validated official profile; logo-only mode creates no dead control.
- [ ] **Server Roles** opens only role visibility and never redirects into pronouns/identity/interests.
- [ ] **Profile Tags & Cosmetics** opens only the self-selected tag manager and appears once in the builder.
- [ ] Default privacy shows profile tags but hides ordinary server roles until the member opts in.
- [ ] Turning Live Signature off removes the current card and prevents reposting.
- [ ] Bot-authored cards cause no SpamGuard, RaidGuard, AutoMod, cleanup, or member-activity event.

## Cleanup status

- Temporary source-transfer payloads: removed.
- Temporary patch/materializer scripts: removed.
- Competing profile patch workflow: removed.
- Canonical profile diagnostics workflow: read-only and retained.
- Duplicate Profile Tags manager route: removed.
- Obsolete mixed **Server Roles / Cosmetics** wording: removed from the active user flow.

## Blockers

- Full exact-head CI is still running.
- Deployed Discord visual and interaction smoke has not yet been performed.

## Backlog

- None. Any newly reported unrelated feature or bug remains deferred until this task satisfies its Definition of Done.
