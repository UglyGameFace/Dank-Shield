# Phase 7: Dedicated Dank Shield profile privacy and live card contracts

**Dedicated offline candidate only:** `viamriafkbqhquvrsnua`. Original shared Supabase remains live; Discloud has not been reconfigured.

The original private profile migration `supabase/migrations/20260725_live_profile_cards.sql` created `dank_profile_users`, `dank_profile_guild_settings` and `dank_live_profile_cards`. The later **production** migration `supabase/migrations/202607270001_live_profile_cards_per_member.sql` changed the live-card primary key from `(guild_id,channel_id)` to `(guild_id,channel_id,user_id)`, permitting multiple members' records in a channel. The live `stoney_verify/profile_card_service.py` upserts precisely on the three-column key. Both source migrations must be applied, **in order**, or live-card persistence is incompatible.

New migration `202610200018_dankshield_profile_cards.sql` follows those source migrations, enables RLS on all three tables and explicitly restricts table reads/writes to the backend `service_role`. No Discord code or frontend/browser clients receive profile credentials. There are no anonymous/authenticated profile policies. No defaults are written for any existing member, so consent/opt-in history is not fabricated.

The rollback-only smoke tests three table security boundaries, the three-column PK, two members' card records in one channel, idempotent per-member upsert, correct scoped delete, server privacy overrides and profile platform preference storage using **SET LOCAL ROLE service_role**. Synthetic values are always rolled back.

## Historical source data still needs verified migration

A fresh table without historical rows does not represent user consent, opt-out, saved platform identity preferences, live-caption preferences or a bot-owned Discord message's durable provenance. Before cutover, obtain the **authoritative old database schema and current row digest** for all three profile tables. Copy current rows only after validating per-member keys and privacy scopes. Existing live-card messages should be recovered only if a verified current Discord message was bot-authored; never invent a card by copying a dead message reference. Fail closed when original state is uncertain. User preferences and privacy opt-outs must never silently reset to permissive defaults.

Other pending dependencies: `warns` table/source moderation history, broader Cinema tables/RPCs, Hub settings and active sessions (Hub schema installed but zero data), staff metric history, final Invite Shield delta and cross-feature source parity. Postgres capacity, egress, backpressure, rollback/restore and Discord small-guild canary remain unfinished. High-guild target is architectural groundwork only, not a 500K verified certification.

## Branch policy

Only one active `migration/dankshield-profile-cards-20261020` branch is used; on merge, the guarded nightly retention workflow should retire its verified exact-head branch. Protected backup and Discloud refs remain untouched.
