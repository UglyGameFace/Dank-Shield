# ACTIVE TASK

## DS-PROFILE-CARDS-012 — Premium live profile banners

**Status:** IMPLEMENTED — FINAL CI AND DEPLOYED DISCORD SMOKE PENDING
**Branch:** `fix/live-profile-channel-spam`
**PR:** #145

## Single Active Task Lock

Do not switch to unrelated work until this PR passes final CI and deployed Discord testing.

## Scope

- One live profile signature per configured channel with stale-render protection and cleanup.
- Reference-faithful compact profile cards using real member/server data.
- Normal Unicode emoji in names, interests, labels, and profile-tag pills.
- Hard bounds for names, roles, tags, dates, server labels, and gamertags.
- Link, raw-username, and logo-only platform modes.
- Separate Server Roles and Profile Tags & Cosmetics controls.
- Six base themes plus Steam, Xbox, PlayStation, Epic, and multi-platform styles.
- Working Theme, Font, Colors, Mix Colors, Background, Layout, Avatar Frame, and Preview controls.
- Four visual color slots with instant real-card previews and Advanced Hex as fallback.
- Mobile, tablet, desktop, and web support.

## Confirmed root causes

- Per-member visible ownership created walls of bot cards.
- Slow renders could land after newer messages.
- The first wide renderer used the wrong composition and stale welcome-card colors.
- Several Appearance values were saved but ignored.
- Pills truncated before wrapping.
- Emoji reached Pillow as unsupported text glyphs.
- Username copy used fenced Markdown and included extra characters.
- Styled names were not clipped to the final content region.
- Custom colors supported only two raw hex fields.
- A compatibility copy guard duplicated native behavior.

## Implemented

### Delivery

- One verified visible signature per channel.
- Message bursts coalesce so the latest eligible speaker wins.
- Stale work is rejected around rendering, sending, and persistence.
- Existing stacked cards collapse on first activity.
- Turning live signatures off removes the current card.
- Signature output remains isolated from moderation/activity listeners.

### Styles and renderer

- Compact `1400 × 300` card with dynamic avatar, name, dates, roles, tags, platforms, server name, and server icon.
- Classic, Minimal, and Spotlight layouts.
- Glow, Clean Ring, and No Frame avatar treatments.
- Base families: 420 Lobby, Forest, Cyber Neon, Galaxy, Premium Gold, Community Glow, Esports Ember, and Minimal Glass.
- Platform families: Steam Command, Xbox Arena, PlayStation Pulse, Epic Vault, and Multi-Platform Grid.
- Platform families use real bundled logos and distinct motifs rather than simple recolors.
- Theme, font, color, background, layout, and frame settings reach the active renderer.

### Four-color system

- Primary, Secondary, Accent 3, and Highlight slots.
- Legacy one- and two-color profiles derive missing accents automatically.
- Named visual color choices for normal use; Advanced Hex is the final fallback.
- Color select/replace/rotate/remove/reset actions save and render the real card immediately.
- Member overrides and server defaults persist all four colors.

### Emoji and overflow

- Complete emoji-sequence parsing for keycaps, flags, variation selectors, skin tones, tag sequences, and ZWJ families.
- Redundant Twemoji sources, caching, inline raster rendering, and a drawn non-tofu fallback.
- Interests/tags use safe slash separators.
- Pills wrap before truncation.
- Styled display names are fitted and clipped before compositing.
- Reserved platform and server-brand zones cannot be crossed by dynamic text.

### Platforms and roles

- Link mode opens validated official URLs.
- Username mode returns exactly the private raw username with no wrapper or helper text.
- Logo-only mode needs no username and creates no dead control.
- Server Roles defaults hidden.
- Profile Tags & Cosmetics remains separate and defaults shown subject to policy/privacy.
- The native profile-tag manager is the only manager route.

## Validation

- [x] Native source committed.
- [x] Four-color/emoji/copy/style correction compiled.
- [x] Materializer validation suite: 38 passed.
- [x] Independent targeted profile regression suite passed.
- [x] Temporary scripts, payloads, failure captures, and one-time workflow removed.
- [x] Obsolete username-copy guard and references removed.
- [x] Permanent diagnostics reject temporary/competing profile patches.
- [ ] Final exact-head Profile Runtime Diagnostics.
- [ ] Final exact-head Dank Shield CI.
- [ ] Final exact-head command-size and ticket-menu checks.
- [ ] Deployed Discord smoke.

## Deployed Discord smoke gates

- [x] Server Roles and Profile Tags are separate.
- [x] Server roles default hidden; profile tags default shown.
- [ ] Base themes display the intended green/purple/gold/teal/red/blue families.
- [ ] Platform-focused themes are selectable and visibly distinct.
- [ ] Every Appearance control visibly alters the real card.
- [ ] Every color change immediately previews the real card.
- [ ] Regular emoji render correctly in names and pills.
- [ ] Long names and pills remain inside reserved zones.
- [ ] Username mode returns only the raw username on mobile, tablet, desktop, and web.
- [ ] Rapid speakers leave one card for the latest eligible speaker.
- [ ] No stale card appears when a message arrives during rendering.
- [ ] Existing stacked cards collapse on activity.
- [ ] Link and logo-only modes behave correctly.
- [ ] View Member Profile uses the same generated banner.
- [ ] Turning live signatures off removes the card and prevents reposting.
- [ ] Signature messages trigger no moderation, cleanup, or activity event.

## Blockers

Final exact-head CI and deployed Discord smoke must pass before PR #145 leaves draft status or is merged.

## Backlog

None. All profile-card corrections remain in this active task.
