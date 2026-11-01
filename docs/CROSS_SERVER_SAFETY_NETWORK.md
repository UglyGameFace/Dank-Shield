# Cross-server Safety Network (issue #782)

## What this does

When a Discord account joins an opted-in **receiving** server and has owner-attested, still-active reports from other **publishing** servers that currently run Dank Shield, send an advisory to the receiving server's physical owner's DM. Show source server, category, date and reason. New incidents are additional immutable records, never an overwrite of `last_reason`. Corrections and withdrawals are also immutable events, with the current view derived from event history.

The feature is **off by default for every guild**. The owner must enable Receive and separately confirm Publish with `SHARE`. Owners can add and privately review their own reports even when Publish is off; no stored incident is made network-visible until Publish is explicitly enabled.

No legacy `guild_security_actor_reputation`, guild bans, `manual_alt_links`, social affiliation or older moderation history is automatically imported into network reports. A report is expressly labeled **owner-attested**, not independently verified. The same exact Discord user ID must match; friendships, alts, associates and matching display names never count as the same account. Reports only advise review; they never call moderation or verification enforcement.

## Owner UI

`/dank safety-network` opens a private owner-only panel:

- **Receive alerts**: toggle private owner DMs for future joins (other guilds must publish).
- **Share incidents**: confirm explicit publishing of your server's name and written reasons, or disable sharing again. This is separate from receiving.
- **Review account**: select a current member or enter an exact Discord ID for a departed/banned account; submit an incident category and specific reason, review current shared reports, or correct/withdraw your own original incidents.

Every UI callback rechecks the actual guild owner. Notes do not permit raw private conversations or personal/victim information. Unrelated servers' owner notes are not editable.

## Database and activation

**Do not deploy by merely merging this PR.** Apply `supabase_fresh/migrations/202611010001_network_safety_advisories.sql` only to the dedicated DankShield project after a reviewed migration/rollback and canary. It creates three new tables: owner consent, append-only owner events, and per-destination receipt cooldowns, plus an atomic claim RPC. All have RLS, with no anon/authenticated access and service-role backend-only privileges. This migration does not change or delete source tables or old reputation.

The bot's existing global service-role client performs bounded lookups; missing consent, disconnected guilds, owner changes, missing schema, report retrieval errors and too-large history fail closed. Joins do **not** await report lookup before the existing verification/security systems act. Bot runs under existing Discord REST rates; nonblocking database lookup is bounded and no per-join scanning of 10,000 guilds is performed.

If a receiving server owner's DMs are disabled, the private alert fails and the event is logged without revealing the report in a public channel. A private-channel fallback needs separate owner authorization; public/modlog fallback is deliberately absent to prevent leaking other guilds' reports.

On same digest, one advisory per account per receiving guild per 24 hours. A changed report digest may prompt another alert after an hour; atomic claims prevent multi-worker duplicate storms.

## Live acceptance before calling this deployed

1. Apply SQL on isolated database and run grants, constraints, RLS and concurrent atomic-claim tests with service_role, anon and authenticated.
2. Confirm three disposable guilds: no alert with no consent; source Publish plus destination Receive must both be on; disabling either immediately stops later alerts.
3. Confirm exact-id join DM shows source and appended reasons; only owner sees it; no modlog/public message; duplicate/rejoin suppressed.
4. Amend and withdraw incidents, change source owner, leave source guild, block owner DMs and simulate database outage. Verify fail-closed privacy and unchanged local AntiNuke/verification.
5. Check post-merge GitHub tests, then deploy the exact main SHA to the existing Discloud app; verify the running commit and database source before declaring the production feature ready.
