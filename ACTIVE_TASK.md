# ACTIVE TASK

## DS-PROFILE-STUDIO-LIVE-007 — Restore live signatures and make every editor control real

**Status:** CLEAN IMPLEMENTATION / EXACT-HEAD CI REQUIRED
**Branch:** `fix/profile-signature-runtime-editor`
**PR:** `#136`
**Base:** current `main`

## Single Active Task Lock

Do not switch to unrelated implementation work until configured live-signature channels post successfully and Theme, Font, Colors, Background, Layout, and Avatar Frame each produce a visibly distinct saved preview.

## Live findings

- A normal member message in the configured text channel produced no Dank Shield signature.
- The runtime always supplied `view=None` when the member had no clickable platform links. Discord rejects explicit null view payloads on this send path.
- The send exception was swallowed, so the channel looked ignored and the logs did not identify the failure.
- A card was suppressed entirely when every optional field was hidden, despite the renderer already supporting a basic avatar/name signature.
- Compact font rendering used only the broad font family and ignored the advertised effect, tracking, shear, uppercase, outline, chrome, pixel, and glow settings.
- The fallback channel command did not verify Attach Files even though compact signatures are image attachments.

## Scope

- Omit absent Discord payload fields instead of passing explicit `None` values.
- Log live-card send/state failures with guild, channel, and user context.
- Always allow a basic avatar/name signature when live cards are enabled; privacy only removes optional details.
- Render the real typography effects used by every advertised font style.
- Make avatar/profile/custom backgrounds visibly distinguishable behind the content panel.
- Validate Attach Files on every setup path and improve setup guidance.
- Add dynamic regression coverage for live posting and every appearance control.

## Validation

- [x] Strict null-view live-send regression passes.
- [x] Basic private signature renders with zero optional fields.
- [x] All 16 built-in font styles produce distinct compact output.
- [x] Theme, colors, background, layout, and frame output tests pass.
- [x] Changed Python modules compile in the focused gate.
- [ ] Full unit suite and repository audits pass on exact clean head.
- [x] Branch is conflict-free with current `main` before exact-head CI.
- [ ] Deployed Discord smoke posts a live card and visibly changes at least two fonts plus two other appearance controls.

## Cleanup

- [x] Temporary materialization workflow/script removed before final validation.
- [x] No monkey patch, compatibility fork, duplicate renderer, or temporary runtime path remains.

## Backlog

- Fix departed-member reconciliation consuming `Guild.fetch_members()` as a normal iterable instead of an async iterator.
- Review contradictory worker startup log wording.
- Enable automatic sharding before scaling toward the configured 100+ public guild expectation.
