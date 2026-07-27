# ACTIVE TASK

## DS-PROFILE-STUDIO-LIVE-011 — Per-member live signature ownership

**Status:** IMPLEMENTING / DATABASE MIGRATION AND VALIDATION PENDING
**Branch:** `fix/live-signature-per-member-ownership`
**Base:** current `main` after merged PR #141

## Single Active Task Lock

Do not switch to unrelated implementation work until deployed Discord smoke proves that different members keep independent visible signatures, one member's new message replaces only that member's prior signature, rapid same-member messages still coalesce, and bot-authored signature traffic is ignored by moderation/activity listeners.

## Confirmed production bug

- Durable ownership is keyed by `(guild_id, channel_id)`.
- Warm runtime ownership, scheduler state, and deletion are also keyed by `(guild_id, channel_id)`.
- The newest speaker therefore overwrites the channel row and deletes the previous speaker's signature.
- PR #141 described per-member ownership but changed only the profile diagnostics workflow; it did not change runtime or database ownership.

## Required behavior

- Key live signature state by `(guild_id, channel_id, user_id)`.
- Keep one visible signature per member in each configured channel.
- Replace only the same member's prior signature.
- Preserve other members' cards during rapid or concurrent traffic.
- Serialize bot sends per channel without sharing ownership or cancellation state between members.
- Reconcile and clean duplicates per member, never across different members.
- Member opt-out/departure removes only that member's rows and cards.
- Channel disable/delete removes all member rows and verified bot-owned cards in that channel.
- SpamGuard, cleanup sweeps, AutoMod, member activity tracking, and RaidGuard must not treat Dank Shield's signature messages as human traffic.

## Validation gates

- [ ] Per-member Supabase primary-key migration is applied.
- [ ] Focused live-signature delivery, cleanup, migration, and moderation-isolation tests pass.
- [ ] Full repository suite, standalone checks, compilation, whitespace, and audits pass on exact head.
- [ ] Branch is conflict-free and reviewed for stale channel-scoped ownership assumptions.
- [ ] Deployed smoke: member A card remains after member B posts.
- [ ] Deployed smoke: member A posts again; only old A card is replaced and B remains.
- [ ] Deployed smoke: no SpamGuard warning/card, raid alert/card, or activity record is caused by bot-authored signature output.
