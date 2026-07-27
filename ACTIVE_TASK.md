# ACTIVE TASK

## DS-PROFILE-STUDIO-LIVE-011 — Per-member live signature ownership

**Status:** IMPLEMENTED / DATABASE MIGRATION AND EXACT-HEAD VALIDATION PENDING
**Branch:** `fix/live-signature-per-member-ownership`
**PR:** #142
**Base:** current `main` after merged PR #141

## Single Active Task Lock

Do not switch to unrelated implementation work until deployed Discord smoke proves that different members keep independent visible signatures, one member's new message replaces only that member's prior signature, rapid same-member messages still coalesce, and bot-authored signature traffic is ignored by moderation/activity listeners.

## Confirmed production bug

- Durable ownership was keyed by `(guild_id, channel_id)`.
- Warm runtime ownership, scheduler state, and deletion were also keyed by `(guild_id, channel_id)`.
- The newest speaker therefore overwrote the channel row and deleted the previous speaker's signature.
- PR #141 described per-member ownership but changed only the profile diagnostics workflow; it did not change runtime or database ownership.

## Implemented behavior

- Key live signature state by `(guild_id, channel_id, user_id)`.
- Keep one visible signature per member in each configured channel.
- Replace only the same member's prior signature.
- Preserve other members' cards during rapid or concurrent traffic.
- Serialize bot sends per channel without sharing ownership or cancellation state between members.
- Reconcile and clean duplicates per member, never across different members.
- Member opt-out/departure removes only that member's rows and cards.
- Channel disable/delete removes all member rows and verified bot-owned cards in that channel.
- SpamGuard, cleanup sweeps, AutoMod, member activity tracking, and RaidGuard remain isolated from bot-authored signature traffic, with regression coverage.
- Added an idempotent Supabase migration changing live-card ownership to `(guild_id, channel_id, user_id)`.
- Removed all temporary transfer payloads and temporary transfer workflows from the final branch tree.

## Validation gates

- [ ] Per-member Supabase primary-key migration is applied before runtime deployment.
- [x] Local compilation passes.
- [x] Local migration-safety and moderation-isolation tests pass.
- [x] The first focused CI run exposed a test-fixture-only omission; the state-write regression now explicitly requests replacement so it reaches the intended failure path.
- [ ] Focused live-signature delivery, cleanup, migration, and moderation-isolation tests pass on the corrected exact head.
- [ ] Full repository suite, standalone checks, compilation, whitespace, and audits pass on exact head.
- [x] Branch is zero commits behind `main`, conflict-free, and temporary transfer files are absent.
- [ ] Diff is reviewed for any stale channel-scoped ownership assumption.
- [ ] Deployed smoke: member A card remains after member B posts.
- [ ] Deployed smoke: member A posts again; only old A card is replaced and B remains.
- [ ] Deployed smoke: no SpamGuard warning/card, raid alert/card, AutoMod action, or activity record is caused by bot-authored signature output.
