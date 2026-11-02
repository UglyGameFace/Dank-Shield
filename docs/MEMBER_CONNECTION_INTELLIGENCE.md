# Member Connection Intelligence: ban-related review and trusted channel access

## Where staff find it

Open `/dank members` → **Intelligence** → **Connection Controls**. The two automatic review switches and restricted-channel manager are on one compact private panel. Select **Recent Ban Connections** to browse up to ten recently banned accounts by name, or inspect a current member. **Connections by ID** remains available only for older records. Existing member history and review tools are unchanged.

## Three server controls

1. **Monitor** (on by default): after a member is banned, check the existing guild's retained evidence for related accounts. Send a private moderator lead only if relationships exist. No role, timeout, ban, kick or public post results from a connection.
2. **Review suspicious accounts** (on by default): after the normal join safety and verification process, check only an explicit recorded invite/identity link against Discord's current ban status. Alert staff privately if verified. Similar usernames, age buckets, weak clusters, unconfirmed invites and friendship guesses do not trigger a banned-account claim.
3. **Protect restricted channels** (opt-in): an authorized admin picks a specific trusted role and text channel using Dank Shield's Search-Safe role/channel browser and explicitly confirms. For that channel, Dank Shield denies `@everyone` visibility and allows the chosen role. The server's existing Discord Administrator permission always bypasses channel overwrites. A friend, inviter or possible alt is **never** automatically punished or restricted because of a connection.

Protection supports up to ten specific text channels per guild. This is **not** a server-wide role rewrite. It refuses a channel with conflicting explicit access grants to another role/member. No other role/member overwrite is modified. Channel/category permission synchronization may become unsynced when a channel is protected. Administrators must check any inherited/category or elevated access. Users with the trusted role can read the channel, regardless of their recorded connections. The protection applies to channel visibility, not copy/export/screenshot prevention.

**Review or remove** shows live status (including permission drift, deleted channel/role or interrupted setup). **Restore previous visibility** checks the current exact overwrites before restoring only the two visibility bits changed by the bot. Other changes are not silently overwritten. A pending/partial operation has a durable recovery record; use the review/restore menu, or manually resolve deleted targets in Discord. No global permission rollback or retroactive member action is performed.

Only a member with Administrator/Manage Server can change the review switches. Protecting/restoring a channel additionally requires Manage Roles permissions for the user and bot, including at the selected channel. Every action re-checks identity, guild, role/channel existence and permissions when confirmed; changing settings requires an authoritative persistent config compare-and-swap. Write failures are not reported as success.

## Evidence and privacy

The read-only, bounded analyzer consumes only existing guild-scoped `guild_members`, `member_joins` and `manual_alt_links` records. No new personal-data table, identity matching service, OAuth permission or private DM collection is added. Four distinct evidence classes remain:

- Staff-reviewed duplicate account relationship.
- Staff-marked possible duplicate identity.
- Confirmed server invitation or vouch relationship.
- Weak unconfirmed invitation attribution.

An explicit `not_linked` staff decision suppresses same-person claims without erasing actual history. Missing data, insufficient coverage and backend errors are not interpreted as a clean bill of health. Relationships never prove leaking information.

Connection leads go through the **existing moderator-log pipeline with 15-minute semantic deduplication**. These sensitive alerts are now suppressed when the configured log channel is public or its staff visibility cannot be reasonably verified. The modlog must be non-public, and only elevated staff or configured staff roles may have explicit channel view allows. Other ordinary modlog events retain their existing routing policy. There is no automatic public alert.

Discord bots cannot discover ordinary friendships, other servers, private DMs, off-platform exchanges or screenshots. No bot can prevent trusted users from privately sharing screenshots. Restricted-channel trust and human review are therefore the correct boundary, rather than punishing people for social association.

## Rollout / production acceptance

- This feature extends merged PR #767 and reuses the one `/dank members` Intelligence surface; no duplicate slash command, background scanner, relationship database or automatic punishment engine is introduced.
- Run full exact-head CI and focused config-CAS, owner authorization, Discord overwrite, false-positive, recovery and privacy tests.
- Merge only after relevant checks pass and reconcile the concurrent whole-bot UI overhaul PR #773. Do not claim a production feature from a branch alone.
- After the approved Discloud deployment, test from Android **and desktop**: owner/staff/member access, default switches, switching settings off and on, ban/join deduplication in a private modlog, bad/modlog-permission paths, selected channel and role, conflicting overwrite refusal, permission change, visible access as trusted and untrusted members, remove/restore, missing role/channel, pending recovery and restart.
- Verify the actual running bot's commit and the configured guild database schema/permissions. GitHub CI cannot prove live Discord channel permissions or delivery.

## Dank Shield native Safety Reports

No external moderation bot is required. From a selected live member's
**Intelligence** or **Connections** panel, the physical server owner can choose
**Safety Reports** to open the existing `/dank safety-network` report view for
that exact Discord account. The existing database and opt-in publishing /
receiving controls still apply: the source server must currently be served by
Dank Shield and have owner-authorized publication, while the receiving server
must have owner-authorized receiving enabled.

Only the physical owner may read the private cross-server view. Any staff
member can continue using guild-local connections without gaining permission
to see unrelated servers' reports. Incident reports remain **owner-attested**,
not independently verified. They do not prove alternate identities,
friendships, wrongdoing, or common membership. Inaccessible data is reported
as unavailable rather than innocent; zero shared reports is not a clearance.
No external-bot installation, arbitrary-user guild enumeration, OAuth token,
automatic ban, or moderation restriction is involved.

History counts shown in **Stored History** are explicitly labeled as bounded
recent readbacks or recorded minimums, not complete lifetimes. Live membership
is still checked through Discord REST; unknown stored status cannot replace it.

## External safety sources (no third-party bots)

The owner-only **External Sources** button on the current-member and
Connections review views performs **explicit, on-demand**, bounded lookups.
They are never a global guild scan, automatic action, training set, relationship
profile or proof that anyone committed misconduct. Provider errors, missing
credentials and mismatched identities remain **unavailable**, never safe.

- **Protector**: `GET /api/v1/blacklist/{user_id}`, requires
  `DANK_PROTECTOR_API_KEY`. Free rate: 10/minute, 500/month, provider enforced.
  Returns an account blacklist flag without underlying evidence.
- **Discord Reviews**: user reputation endpoint by *currently verified*
  username; requires `DANK_DISCORDREVIEWS_API_KEY`. Responses must carry
  exactly the requested Discord ID or they are rejected. Free: 100/hour.
  Negative public reviews are opinions, not verified incidents.
- **CheaterStats**: only its documented game **ban-record count** endpoint,
  not external Discord server memberships, role tracking or social affiliations.
  Paid, 10 provider tokens per lookup. Requires both
  `DANK_CHEATERSTATS_API_KEY` and explicit
  `DANK_CHEATERSTATS_PAID_LOOKUPS=1` before any billable check.
- **Trustify / Project Redacted**: direct official public search links and
  `manual_review` only; public API routes could not be verified and must not
  be scraped or invented. No keys, bots or page scraping.
- **Discord AntiScam**: separate **Check Scam Domain** button. Loads a bounded,
  cached domain TXT list from its documented fixed GitHub URL. This source was
  archived in October 2026; findings are historical, potentially stale. The bot
  never opens user-submitted URLs and only checks hostnames against the cached
  list; no match is **not** proof a link is safe.

Provider credentials are environment secrets on Discloud, never committed to
GitHub or shown in the UI. Results are ephemeral, never added to the permanent
member ledger or automatically published. A 15-minute per-process cache avoids
repeat provider requests but is **not** a durable global quota ledger. Providers
enforce limits; scale-out or unattended lookups would require persistent
budget/receipt accounting before enabling them. No background sampling,
automatic bans, paid calls by default or inferred identity/relationship links.

## Original investigation affordances

Select current member → **Connections**, **Recent Ban Connections**, **Connections by ID** and **Stored History** remain available. Ban status is verified against Discord and a failed lookup is never treated as banned. Existing staff-verdict, moderated-action permissions, role hierarchy, verification and join safeguards are preserved.
