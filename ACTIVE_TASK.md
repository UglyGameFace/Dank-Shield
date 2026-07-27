# ACTIVE TASK

## DS-PROFILE-STUDIO-LIVE-008 — Restore live posting and simplify member controls

**Status:** CLEAN IMPLEMENTATION / FINAL EXACT-HEAD CI RUNNING
**Branch:** `fix/profile-platform-privacy-preview-ux`
**PR:** #137
**Base:** current `main`

## Single Active Task Lock

Do not switch to unrelated implementation work until the deployed Profile Signature flow posts live cards, clearly allows every saved platform to become Public or Private, and every preview/style save completes without a stuck loading state.

## Confirmed findings

- Profile Privacy displayed saved Steam/Xbox accounts but offered no button to manage their individual visibility.
- The actual visibility control was hidden in the separate Platforms screen under the ambiguous label **Share / Hide**.
- Privacy exposed eight similarly styled global/server buttons with no Back or Manage Accounts action, making the mobile panel difficult to understand.
- Preview and style-save callbacks deferred a loading response, then sent follow-up messages instead of completing the deferred response.
- Platform manager/detail navigation stacked new ephemeral messages rather than replacing one mobile-friendly panel.
- The live worker discarded the authoritative `message.author` member and relied on `guild.get_member()`. A member-cache miss therefore produced a silent no-card result even for a valid message.
- Members need one obvious ON/OFF switch instead of a generic Every Server Live inheritance button.

## Scope

- Use the authoritative message member before any guild-cache fallback in live posting.
- Add actionable runtime diagnostics for member, permission, privacy, send, and state failures.
- Add one obvious **Turn On/Off Live Signature** member control that reflects the effective state in this server.
- Replace inheritance jargon with plain action labels and plain-language status summaries.
- Add an obvious **Manage Accounts** action to Profile Privacy.
- Replace **Share / Hide** with state-aware **Make Public** / **Make Private** for every platform.
- Mark saved identities as `🌐 Public` or `🔒 Private` in every summary.
- Add Back navigation between Privacy, Platforms, Preview, and the Signature home.
- Complete deferred preview/style-save responses by editing the original ephemeral panel.
- Keep account saves private by default and require a username before Public can be enabled.

## Validation

- [x] A valid message still posts when `guild.get_member()` misses but `message.author` is a member.
- [x] Profile Privacy exposes exactly one obvious Turn On/Off Live Signature switch.
- [x] Profile home displays Live Signature: ON/OFF and toggles it in place.
- [x] Live switches reflect an old hidden-here override and clear it when turning back on.
- [x] Privacy buttons use plain actions without Every Server/Inherit jargon.
- [x] Privacy panel exposes Manage Accounts, Preview Signature, and Back to Profile.
- [x] Every platform detail screen shows Make Public or Make Private based on saved state.
- [x] Unsaved identities cannot be made Public.
- [x] Privacy summaries clearly mark Steam and all other platforms Public/Private.
- [x] Preview and style-save callbacks complete the original deferred response.
- [x] Focused tests and changed-module compilation pass.
- [ ] Full unit suite and repository audits pass on exact clean head.
- [x] Branch is conflict-free with current `main`.
- [ ] Deployed Discord smoke confirms a designated-channel message posts, Steam can be made Public, the member toggle works, and Preview returns without hanging.

## Cleanup

- [x] Temporary materialization workflow/scripts removed before final validation.
- [x] No duplicate privacy panel, compatibility fork, or temporary runtime path remains.

## Backlog

- Fix departed-member reconciliation consuming `Guild.fetch_members()` as a normal iterable instead of an async iterator.
- Review contradictory worker startup log wording.
- Enable automatic sharding before scaling toward the configured 100+ public guild expectation.
