# ACTIVE TASK

## DS-PROFILE-CARDS-012 — Premium live profile banners and separated role controls

**Status:** DEPLOYED SMOKE FOUND VISUAL/COPY REGRESSIONS / CORRECTION IN PROGRESS
**Branch:** `fix/live-profile-channel-spam`
**PR:** #145
**Base:** current `main` (`0` commits behind at the latest comparison)

## Single Active Task Lock

Do not switch to unrelated implementation work until the profile-card runtime, reference-faithful banner renderer, platform controls, privacy defaults, appearance studio, and separate **Server Roles** / **Profile Tags & Cosmetics** workflows pass deployed Discord smoke.

## Scope

- Stop live profile cards from creating walls of bot messages in active chat.
- Follow the supplied six-card 420 Lobby reference for every live profile signature while keeping all text and branding dynamic.
- Support real regular Unicode emoji in names, labels, interests, and profile-tag bubbles without tofu/replacement boxes.
- Keep every long display name, role, tag, server label, and gamertag inside its reserved card area.
- Keep link, exact clean-copy username, and logo-only platform modes fast and privacy-safe.
- Add additional genuinely distinct card families, including Steam-, Xbox-, PlayStation-, Epic-, and multi-platform-focused designs using the bundled real platform artwork.
- Make every Appearance control functional: Theme, Font, Colors, Mix Colors, Background, Layout, Avatar Frame, and Preview.
- Provide a visual custom color mixer with up to four semantic accents, immediate real-card preview after each selection, reordering/removal controls, and manual hex only as an advanced fallback.
- Separate ordinary server-role visibility from member-selected profile tags/cosmetics in both settings and navigation.
- Preserve mobile, tablet, desktop, and web usability.

## Root causes confirmed

### Delivery and role ownership

- Per-member visible-card ownership created one persistent bot message for every speaker in the channel.
- Slow renders could finish after a newer speaker and place a stale card under the wrong conversation position.
- The startup compatibility guard injected a second Profile Tags manager route even though the native builder already owned one.
- The member-profile viewer reused the generated-image embed without attaching the generated PNG.

### Deployed visual regression found during smoke

- The first wide renderer followed a different orange/red mockup instead of the supplied stacked green, purple, gold, blue, red, and teal 420 Lobby cards.
- Profile themes reused welcome-card colors and allowed stale avatar/custom accents to dominate the selected theme family.
- The live renderer hard-coded the classic layout and glow frame, so several appearance settings did not affect the new card.
- Pills were fitted into the remaining row width before wrapping, causing interests and other sections to be cut off unnecessarily.
- Pillow rendered unsupported regular emoji as tofu/replacement boxes because no reliable emoji raster path reached every bubble/tag render path.
- Username-mode responses used fenced `text` Markdown, causing mobile copy actions to include backticks and the `text` language marker.
- Styled display-name output was measured too loosely and could visually extend into the platform or brand zones.
- The existing custom-color path only exposed two raw hex fields and did not provide visual selection, four-color mixing, or automatic previews.

## Implemented behavior

### Live delivery

- One verified visible Dank Shield signature per configured channel.
- A short quiet window coalesces message bursts and lets the latest eligible speaker win.
- Stale work is rejected before send, after send, and around durable-state persistence.
- Existing stacked cards are collapsed safely on first activity.
- Disabling live signatures removes the member's current card and prevents reposting.
- Bot-authored signature output remains isolated from moderation and activity systems.

### Reference-faithful banner design

- Compact `1400 × 300` horizontal card based directly on the supplied 420 Lobby reference.
- Circular avatar with working **Glow**, **Clean Ring**, and **No Frame** choices.
- Large member name, readable account dates, separate server-role badge, wrapped profile-tag pills, glass platform-logo tiles, and integrated server-brand panel.
- Six distinct old-name-compatible visual families:
  - 420 Lobby Neon → green leaf/smoke
  - Cyber Neon → purple smoke
  - Premium Gold → black/gold flow
  - Community Glow → teal grower-style treatment
  - Esports → red ember treatment
  - Minimal Glass → blue/ice treatment
- Theme, custom, and avatar-derived accents are normalized to stay bright and readable against the dark card.
- Selected display font affects the large username; small metadata remains in a clean readable font.
- **Classic**, **Minimal**, and **Spotlight** use different real geometry rather than one hard-coded layout.
- Theme, profile, and server custom background modes remain wired to the new renderer.
- Dynamic avatar, display name, server name, server icon, roles, profile tags, dates, and platform identities remain real data—no fake level or membership claims.

### Emoji and overflow safety

- Regular Unicode emoji are parsed as full sequences, including ZWJ families and flags.
- Emoji raster support exists, but deployed bubble/tag coverage is not accepted until ordinary emoji visibly render without replacement boxes.
- Pills wrap to the next reserved row before truncation.
- Pixel-width fitting remains enforced for names, dates, badges, handles, pills, and server labels.
- Platform and server-brand zones are reserved and cannot be crossed by long dynamic text once final styled-output bounds are enforced and smoke-tested.

### Platforms

- **Link:** opens a validated official profile URL.
- **Username:** target behavior is one private raw username value with no Markdown wrappers, labels, code-fence language, or extra characters.
- **Logo only:** renders the platform mark without requiring a username or creating a dead button.
- Preview preserves link and username controls.
- Saving account details does not make them public automatically.

### Role separation

- **Server Roles** is a dedicated visibility control for safe roles already assigned by the server.
- Server-role display defaults to hidden for privacy.
- **Profile Tags & Cosmetics** separately owns pronouns, identity, interests, community labels, and harmless cosmetics.
- Profile-tag display defaults to shown, subject to server policy and the member's privacy choice.
- The native `builder:cosmetics` route is the only builder manager route.
- Missing profile-tag suggestions are review-only and never create or assign roles automatically.

## Correction work still active

- Finish the native four-color style model and renderer mapping for **Primary**, **Secondary**, **Accent 3**, and **Highlight**.
- Make each selected color immediately save and regenerate the real card preview; no extra Preview/Save step after a color choice.
- Keep legacy one- and two-color profiles compatible by deriving missing Accent 3 and Highlight values until the user chooses them.
- Add reorder, replace, remove, and reset controls for the four-color mix.
- Keep **Advanced Hex** as the final fallback, with up to four exact color values.
- Route every tag/bubble string through the same full Unicode emoji raster path.
- Enforce actual composited/styled display-name bounds, not only logical text-width bounds.
- Fix the source gamertag callback so its private response contains only the raw username.
- Add Steam-, Xbox-, PlayStation-, Epic-, and multi-platform-focused card families using bundled real logos and genuinely different composition—not simple recolors.
- Verify every Appearance button saves, invalidates cached renders, and produces a visibly fresh card.

## Validation status

### Previously passed exact-head validation

- [x] Focused Profile Runtime Diagnostics.
- [x] Application Command Size Diagnostics.
- [x] Full profile and repository compilation.
- [x] Full repository unit suite (`783 passed` on the previous exact head).
- [x] Every standalone `tools/test_*.py` contract.
- [x] Public setup/isolation, canonical command surface, command friction, invite permissions, setup safety, Dank Design, role-truth, and event-boundary audits.
- [x] Bundled platform asset and role-separation checks.
- [x] Branch comparison reported `0` commits behind `main`.

### Current correction validation

- [x] New renderer compiles locally.
- [x] Six reference theme families rendered and visually inspected.
- [x] Classic, Minimal, and Spotlight rendered with visibly different geometry.
- [x] Glow, Ring, and None avatar-frame paths rendered.
- [x] Long interest content wraps to row two instead of being prematurely cut off.
- [x] Plain-copy response guard compiles.
- [x] Regression coverage added for reference palettes, settings geometry, emoji tokenization/raster placement, compact bounds, and copy sanitation.
- [ ] Native four-color implementation committed without temporary materializers or competing workflow code.
- [ ] Targeted four-color, appearance, emoji, overflow, platform-style, and exact-copy tests pass.
- [ ] Exact-head full GitHub CI passes after all corrections.
- [ ] Redeployed Discord smoke passes after exact-head CI.

## Deployed Discord smoke gates

- [x] Profile studio presents separate **Server Roles** and **Profile Tags** controls.
- [x] Default privacy shown in the studio: server roles hidden, profile tags shown.
- [ ] Corrected card visually follows the supplied 420 Lobby reference on Discord.
- [ ] Every base theme shows its intended green/purple/gold/teal/red/blue family.
- [ ] New Steam, Xbox, PlayStation, Epic, and multi-platform card families are selectable and visibly distinct.
- [ ] Theme, font, colors, four-color mix, background, layout, and avatar-frame settings all alter the new card correctly.
- [ ] Every color selection immediately produces the real updated card preview without an extra step.
- [ ] Regular emoji render correctly in names, labels, interests, and bubble tags without tofu/replacement boxes.
- [ ] Long display names never cross the platform or server-brand boundary.
- [ ] Long profile-tag pills wrap cleanly without clipping or crossing reserved zones.
- [ ] Username-mode response contains only the raw username on mobile, tablet, desktop, and web.
- [ ] Three users speak rapidly; after the quiet window exactly one card remains for the latest speaker.
- [ ] A new message during rendering leaves no stale card behind.
- [ ] Existing stacked cards collapse on first activity.
- [ ] Link mode opens the validated official profile; logo-only mode creates no dead control.
- [ ] View Member Profile displays the same generated banner.
- [ ] Turning Live Signature off removes the current card and prevents reposting.
- [ ] Bot-authored cards cause no SpamGuard, RaidGuard, AutoMod, cleanup, or member-activity event.

## Cleanup status

- Duplicate Profile Tags manager route remains removed.
- Obsolete mixed **Server Roles / Cosmetics** wording remains removed from the active user flow.
- Current temporary four-color materializer/workflow must be removed after it produces and validates the native implementation.
- Final branch must contain no source-transfer payloads, one-time patch scripts, diagnostic captures, competing renderers, or competing workflows.

## Blockers

- Finish and cleanly commit the complete correction scope above, obtain exact-head green CI, redeploy the branch, and repeat the failed visual/interaction smoke before PR #145 can leave draft status.

## Backlog

- None. The emoji, overflow, exact-copy, additional platform-focused styles, four-color mixer, and Appearance-control verification are all part of this single active PR #145 task—not deferred work.
