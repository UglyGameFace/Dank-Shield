# Invite Shield × OneBump: Trusted Advertising Bots

## Why the integration exists

Dank Shield's Invite Shield normally removes unauthorized external Discord invitations. OneBump deliberately publishes other servers' adverts, frequently as rich cards with Join buttons. Turning Invite Shield off or whitelisting the whole advert channel gives ordinary spammers an opening.

This implementation adds an *exact bot Discord User ID + exact channel ID* allow decision within the **existing central Invite Shield policy**. It does **not** whitelist the channel for humans or grant full bot exemption elsewhere.

## Actual free OneBump policy verified 26 October 2026

- Official [setup](https://bump.ltd/guides/set-up-onebump) says OneBump advert deliveries appear in a channel chosen by server managers. Bot uses invites, embeds and buttons.
- Official [fair-play rules](https://bump.ltd/guides/fair-play) and [received advert guide](https://bump.ltd/guides/adverts-in-your-server) require leaving adverts visible **at least 24 hours** and warn against deleting/clearing adverts with other bots. OneBump can penalize bulk deletion, even after the minimum. This program does **not** claim that scheduled cleanup is approved by OneBump.
- No token, premium account, API key or automated bump system is required. Dank Shield **never** triggers or automates OneBump's `/bump` command.
- We do not hardcode a vendor username or unknown application ID. Owners explicitly pick the installed bot account's **Discord User ID** and a particular channel. Names, nicknames or spoofed app/webhook messages grant no trust.

## Setup on Discord Android, per server

1. Choose/confirm the visible advertising channel in OneBump's own `/settings`, such as `#partner-ads`. OneBump says the channel must be viewable by everyone participating in its fair-play arrangement.
2. In Dank Shield open **/dank → Protection → Invite Shield → Trusted Advertising Bots**.
3. Tap **Use This Channel** when opened inside the intended channel, or **Choose Channel** using the existing searchable guild-resource picker. No Discord native channel picker needed.
4. Tap **Find Bot** to see possible bot IDs (suggestions **only**), or go to the actual OneBump bot profile in Discord Developer Mode and **Copy User ID**. Tap **Approve Bot** and paste the ID. Dank Shield fetches and validates that exact account is a *real bot currently in this server*; type `TRUST` to confirm.
5. Trusted ads are now allowed only from that exact bot in that exact channel. Invite Shield remains ON and its existing block settings, unknown-target rules, log/audit and manual rescans remain intact elsewhere. No cleanup is enabled by default.
6. **Optional compact adverts:** Tap **Collapse Previews** for the exact approved bot and channel. The bot ID is prefilled when there is just one approved bot. Type `HIDE` to confirm. `ON+RECENT` turns on new-message suppression **and queues Smart Bulk Collapse** of older messages; `ON` suppresses future posts only; `RECENT` queues one-time bulk processing without changing the automatic setting; `OFF` stops new-message suppression and cancels any running bulk scan, but does not restore existing previews. `STATUS` and `CANCEL` are also available in the modal. The normal **Bulk Progress** and **Cancel Bulk** buttons work immediately when there is one selected bot. It does not delete posts or change 24/48/72-hour expiry settings. The entire large preview / Join card may disappear, so **OneBump fair-play visibility warnings still apply**.
7. **Optional deletion:** Tap **Enable Cleanup** after approving the bot. Choose 24, 48 or 72 hours and type `DELETE` acknowledging the provider-policy warning. The deletion worker uses the **Discord message timestamp**, plus a **10-minute safety buffer** beyond the chosen interval. Each tracked message expires independently. This option is **at your own risk** under OneBump's fair-play rules.
8. **Manage / Remove → PAUSE** disables future automated deletion but keeps the bot allowed. **REMOVE** revokes the pair entirely, restoring normal protection. Other servers' settings are separate.

## Enforcement and deletion details

- The existing `invite_policy_engine.decide_invite_message()` authorizes exact bot+channel matches *before* any generic protected-poster, link, or external-invite delete rule; it exposes rule `trusted_advertiser_exact_bot_channel` in invite decision traces.
- A normal member in the advert channel with an unauthorized Discord invite is still blocked. Same-name impersonations, messages in another channel, webhooks or mismatched app IDs are not trusted.
- When the **optional** `suppress_embeds` flag is enabled for the exact bot/channel pair, Dank Shield uses `message.edit(suppress=True)` on eligible bot-authored rich embeds with `Manage Messages`. It never changes the message content, author, native components, or invite moderation decision. The same check runs on original messages and cached edits when Discord attaches the embed slightly later. A global per-process 12-per-minute limit avoids bursts; over-limit posts stay visible rather than retrying aggressively. No extra database schema or provider token required.
- **Smart Bulk Collapse** is staff-initiated and background-only. Every round reads at most 25 older messages and changes at most 6 verified embeds; all suppression writes share the existing 12/min process-wide budget with the automatic listener. There is no three-per-button artificial total cap. A private, service-role-only `dank_trusted_advert_collapse_jobs` record saves the exact history cursor and scanned/collapsed/skipped counters across Discloud restarts. The worker polls at most every 20 seconds, uses Postgres atomic claims with leases, and checks fresh guild bot/channel policy and Discord permissions every chunk. **Bulk Progress** returns the saved counters. **Cancel Bulk** stops future work; an already-in-flight API request may finish. Pausing/revoking trust also prevents continuation. The job never purges, reposts or deletes advertisements.
- Only bot-authored, non-interaction, ad-like messages with links, embeds or buttons in an explicitly opted-in pair are recorded. Human messages are short-circuited without DB reads. This feature never auto-deletes pinned/other member chat or nonad bot receipts.
- Optional cleanup writes a **private service-role-only ledger** `dank_trusted_advert_messages` to Supabase, one row per (guild, message) with original Discord posting timestamp, due time, state and attempt count. A single bounded worker polls due work every 120 seconds, claims **at most eight** rows with SQL `FOR UPDATE SKIP LOCKED` and a five-minute lease.
- Before deletion, the worker fetches current guild settings, rechecks bot+channel trust and cleanup consent, Discord **View Channel, Read Message History and Manage Messages** permissions, message author and ad shape, and the strict minimum age. Revoked pairs are cancelled, missing messages are marked done, temporary errors are retried with a 30-minute backoff, at most eight attempts. Offline/restarting bots resume already-tracked due rows automatically.
- When Dank Shield was **offline at the moment an ad was posted**, it could not register that unseen message. This implementation intentionally does **not** backfill or purge arbitrary channel history; a historical cleanup scan without an exact stored provenance record would risk deleting unrelated posts. Only new, observed ad messages can be scheduled.
- **Do not apply the database migration or enable optional cleanup until its CI and guarded production rollout are approved.** Missing migration/DB causes cleanup to fail closed; trusted Invite Shield authorization still works independently.

## Release gates and acceptance

- Apply additive migration `supabase/migrations/20261026170500_trusted_advert_retention.sql` through the guarded source database migration process. It does not delete or modify any existing messages or Invite Shield targets.
- Exact-head Invite Shield CI, real PostgreSQL 16 default/privacy/atomic claim checks, complete Dank Shield CI, public command contract, existing spam-guard and Invite Shield regressions.
- Canary: with protection enabled, approved bot advert in approved channel stays; exact bot in other channel gets ordinary policy; a regular member's unauthorized invite in advert channel deletes; a content-redacted trusted bot message avoids wrongful protected-poster deletion; webhook spoof fails.
- Validate optional expiry by using a controlled test message and an isolated test database clock, then live after the strict due interval, **only if provider-policy risk is accepted**. Verify service-role ledger, denial when cleanup paused, restart recovery and deletion errors. Do not manually purge the advert channel or automate OneBump bumping.
- Observe OneBump `/info server` fair-play status during any canary. If it declines, turn off cleanup immediately while leaving the invite exemption enabled.

This release is a **configurable multi-server integration**, not a server-specific hardcode of The 420 Lobby's IDs.


### Smart Bulk Collapse deployment (separate from 24-hour retention)

- Deploy `supabase/migrations/20261026193000_trusted_advert_bulk_collapse.sql` through the existing authorized, safeguarded migration process **before** enabling the new bulk UI. The previous trusted-ad expiry migration and its private ledger remain unchanged.
- The new private job record persists only exact guild/channel/bot/user IDs, a Discord history cursor, progress counters and state. No message content, member profiles or invite URLs are collected.
- Repeated starts for a currently running bot/channel pair do **not** rewind the cursor. An owner can stop and then explicitly restart a completed/cancelled/failed job. A crashed worker's three-minute lease expires and its next claim resumes the saved position.
- Each scan begins at the channel's latest known Discord message ID, covering existing history. New arrivals are handled by the separately enabled automatic listener. An unavailable channel, revoked trust, missing permissions, or unreadable guild configuration pauses or cancels scanning instead of bypassing moderation.
- No bot can guarantee that OneBump will consider suppressed previews fair-play compliant. Monitor `/info server`, and use **Cancel Bulk** and `OFF` if the server's standing changes. <https://bump.ltd/guides/fair-play>
- Canary: start with one verified advertising bot in a test channel; inspect 50+ older adverts, verify progress and rate pacing, cancel after a few batches, restart, and confirm content, other bots, members, 24-hour cleanup and invites are untouched. Test Discord Android controls and a worker restart with an in-progress cursor.
