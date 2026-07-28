# ACTIVE TASK

## DS-PROFILE-CARDS-012 — Premium live profile banners

**Status:** IMPLEMENTED — DEPLOYED DISCORD SMOKE PENDING
**Branch:** `fix/live-profile-channel-spam`
**PR:** #145

## Single Active Task Lock

Do not switch to unrelated work until this PR passes the deployed Discord smoke and is ready to merge.

## Scope

- One live profile signature per configured channel with stale-render protection and cleanup.
- Live signatures are member opt-in and default off when no explicit preference exists.
- Reference-faithful compact cards using live member/server data.
- Normal Unicode emoji in names, interests, labels, and profile-tag pills.
- Hard bounds for names, roles, tags, dates, server labels, and gamertags.
- Link, raw-username, and logo-only platform modes.
- Server-owner truth plus complete real-role labels without partial names or ellipses.
- Separate Server Roles, Profile Tags & Cosmetics, and Server Branding visibility controls.
- Optional server branding that can be hidden without abandoning the selected style.
- Six base color directions plus Steam, Xbox, PlayStation, Epic, and multi-platform families.
- Theme, Font, Colors, Mix Colors, Background, Layout, Avatar Frame, and Preview controls.
- Four visual color slots with instant real-card previews and Advanced Hex as fallback.
- Theme changes preserve independently selected custom colors and custom artwork.
- Personal and server-default custom background artwork with strict validation and safe-zone guidance.
- Mobile, tablet, desktop, and web support.

## Implemented behavior

### Delivery and defaults

- One verified visible signature per configured channel.
- Message bursts coalesce so the latest eligible speaker wins.
- Stale work is rejected around rendering, sending, and persistence.
- Existing stacked cards collapse on first activity.
- New or missing preferences resolve Live Signature to off.
- Explicit existing `live_cards_enabled: true` remains on.
- Turning Live Signature off removes the current card and prevents reposting.
- Signature output remains isolated from moderation, cleanup, and member-activity listeners.

### Roles, ownership, and branding

- Discord `guild.owner_id` determines the truthful `Server Owner` badge.
- Non-owners use a complete real server role when role sharing is enabled; otherwise the safe fallback is `Member`.
- Role names shrink/wrap as complete values and are skipped when they cannot fit; they are never cut into misleading partial labels.
- Server Roles defaults hidden.
- Profile Tags & Cosmetics remains separate and defaults shown subject to policy/privacy.
- Server Branding is independently toggleable and controls the server icon/name panel.
- Hiding Server Branding releases its card area to the platform section.

### Platforms and copy behavior

- Platform handles render on separate adaptive lines instead of one joined ellipsis line.
- Complete handles shrink/wrap within the reserved platform zone; an unfit value is omitted rather than partially displayed.
- Link mode opens validated official URLs.
- Username mode returns exactly the private raw username with no label, Markdown wrapper, language marker, or helper text.
- Logo-only mode requires no username and creates no dead control.

### Styles and four-color customization

- Compact `1400 × 300` card with dynamic avatar, name, dates, roles, tags, platforms, and optional server branding.
- Classic, Minimal, and Spotlight layouts.
- Glow, Clean Ring, and No Frame avatar treatments.
- Base families cover green, purple, gold, teal, red, and blue treatments.
- Steam Command, Xbox Arena, PlayStation Pulse, Epic Vault, and Multi-Platform Grid use bundled real logos and distinct compositions.
- Primary, Secondary, Accent 3, and Highlight slots persist independently.
- Legacy one- and two-color profiles derive missing accents automatically.
- Named visual choices are the normal flow; Advanced Hex is the exact-color fallback.
- Color select, replace, rotate, remove, and reset actions save and render immediately.
- Choosing a different theme changes the visual family only; custom colors and custom artwork remain active.
- Theme colors and Theme Artwork remain explicit independent reset choices.

### Custom background artwork

- Personal uploads and manager-controlled server defaults are separate from Welcome/Join cards.
- Accepted files: PNG, JPG/JPEG, and WebP.
- Upload maximum: 8 MB; decoded source maximum: 20 megapixels.
- Recommended/minimum canvas: `1400 × 300`; accepted ratio: 4.29:1 through 5.04:1.
- Valid artwork is center-cropped and normalized to exactly `1400 × 300`.
- The studio provides a generated safe-zone guide for avatar, member text, roles/platforms, and optional server branding.
- Artwork remains a background layer; live names, roles, platforms, ownership, and privacy are rendered by Dank Shield.

### Emoji and bounds

- Complete emoji-sequence parsing covers keycaps, flags, variation selectors, skin tones, tag sequences, and ZWJ families.
- Redundant Twemoji sources, caching, inline raster rendering, and a drawn non-tofu fallback are active.
- Interests/tags use safe separators.
- Styled display names are fitted and hard-clipped before compositing.
- Dynamic text cannot cross the reserved profile, platform, or branding zones.

## Automated validation

- [x] Native source committed.
- [x] Python compilation passed.
- [x] Focused profile regression suite passed.
- [x] Full repository unit suite passed.
- [x] Application Command Size Diagnostics passed.
- [x] Profile Runtime Diagnostics passed.
- [x] Dank Shield CI passed.
- [x] Standalone role-menu compatibility checks passed.
- [x] Public setup, command-surface, permission, setup-safety, Dank Design, role-truth, and event-boundary audits passed.
- [x] `git diff --check` passed.
- [x] Temporary materializers, payloads, failure captures, and write-enabled workflow logic removed.
- [x] Obsolete username-copy guard and references removed.
- [x] Permanent diagnostics reject temporary or competing profile patches.
- [x] Branch is 0 commits behind `main` and conflict-free.

## Deployed Discord smoke gates

- [ ] New/missing profiles start with Live Signature off; explicit enabled profiles remain enabled.
- [ ] Server owners display `Server Owner` automatically.
- [ ] Shared real roles display complete names with no overflow or ellipses.
- [ ] Platform handles display complete values with no overflow or ellipses.
- [ ] Base and platform-focused themes are selectable and visibly distinct.
- [ ] Theme changes preserve custom color mixes and custom artwork.
- [ ] Every color change immediately previews the real card.
- [ ] Personal custom-background upload enforces the documented file, size, ratio, and canvas rules.
- [ ] Server-default custom artwork remains manager-only.
- [ ] Server Branding can be hidden and restored independently.
- [ ] Regular emoji render correctly in names, labels, interests, and pills.
- [ ] Long display names and profile tags remain inside reserved zones.
- [ ] Username mode returns only the raw username on mobile, tablet, desktop, and web.
- [ ] Rapid speakers leave one card for the latest eligible speaker.
- [ ] No stale card appears when a message arrives during rendering.
- [ ] Existing stacked cards collapse on activity.
- [ ] Link and logo-only modes behave correctly.
- [ ] View Member Profile uses the same generated banner.
- [ ] Turning Live Signature off removes the card and prevents reposting.
- [ ] Signature messages trigger no moderation, cleanup, or activity event.

## Blocker

Deploy the final exact branch head to Discloud and complete the Discord smoke gates before PR #145 leaves draft status or is merged.

## Backlog

None. All profile-card corrections remain in this active task.
