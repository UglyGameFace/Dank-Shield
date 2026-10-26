# Community Hub: grow servers with opt-in public gaming recruitment

**Scope:** This is a public-facing discovery and invitation feature for Dank Shield's **existing** Community Hub. It is not a separate bot, not a Discord global-user search, and not a provider posting automation.

## What outsiders actually get

- `https://<your-Dank-Shield-Site>/lfg` is an HTTPS, responsive, read-only game directory. The matching JSON projection is `/lfg.json`. All eligible Dank Shield guilds can appear, but **none is enrolled automatically**. Results are deliberately capped at 40 and ordered by current activity. Guests search games by name and follow the guild's validated Discord invite.
- **Community Hub → Public Gaming Network** lets members of *any* guild running Dank Shield search these same independently approved gaming groups in Discord, then open the outside guild's invitation. This is intentionally an outbound link, not a cross-server Quick Match or silent enrollment.
- External web visitors have to discover the URL through sharing, search engines, partner sites or event promotions. A URL alone does not automatically attract traffic and does not search arbitrary Discord users.
- The100.io is an **optional, separate** provider. **Its API token is normally restricted to paid supporters** (confirmed in its official API docs, October 26, 2026). Community-tool developers may request complimentary lifetime supporter status from the named contacts in The100.io's Discord, but approval is not guaranteed. Neither Public Gaming Network nor this recruitment board requires that token or a subscription. The100.io **cannot** automatically publish Dank Shield sessions or recruit The100.io players into your server without explicit authorization. No auto-posting/bumping to Discord directories.

## Setup: all server owners, including age-restricted/custom servers

1. **Run database migration** `supabase/migrations/20261026083000_community_hub_public_lfg_recruitment.sql` using the dedicated guarded Supabase migration process. It is additive; no existing LFG session is exposed. Fail closed if it has not been applied.
2. Configure the public Dank Shield Site's HTTPS origin with `DANK_LFG_PUBLIC_BASE_URL=https://your-site.example` (optional when `DANK_SHARE_VIDEO_PUBLIC_BASE_URL` already matches that origin). Both `/lfg` and `/lfg.json` are handled by the existing Share Router Site listener. No Cinema code or services are reintroduced.
3. In your own guild, **/dank community → Staff Dashboard → Settings → Public Recruiting → Set Invite & Enable**. Staff enters that guild's valid Discord invite and types **PUBLISH**. Dank Shield verifies Discord reports the invite targets the *same* guild, then stores the HTTPS invitation. This is **server-level permission to publish**, not blanket publication of member groups.
4. A session host/co-host or authorized staff opens a current **public** group → **Manage Session → Share Outside Server**. The group now appears on the public board and cross-server network, until revoked, ended, paused, locked/private, expired or the guild's public recruitment is disabled. Private notes, host/member IDs, Discord channel IDs and personally identifying member information are never displayed.
5. Share the approved `/lfg` link on your own website, social channels and approved partner communities. Consider using Discord Scheduled Event invite links as a second *manual* recruitment surface. Honor other sites' promotion/spam policies.

## Stop/rollback

- **Immediately hide an individual group:** Host/co-host/staff presses **Share Outside Server** again (toggle off). The DB query requires current group opt-in each request, and does not cache public records.
- **Hide every group:** Staff presses **Stop Recruiting**, which clears the guild's publication gate. Session flags remain but cannot be read from the directory; a future deliberate re-enable would republish still-active opted-in sessions, so review before re-enabling.
- Invite links already shared elsewhere are **not automatically invalidated** by hiding a group. To invalidate those, revoke the invitation in Discord. This feature does not override server verification or age restrictions.
- Database or migration outage: directory returns HTTP 503, not a fabricated empty successful list. A public listing never auto-enrolls a Discord account.

## Safeguards

- Double consent: explicit staff per-guild enablement + explicit session host/cohost/staff opt-in. All existing/new guilds and sessions default OFF.
- Sessions are queried from the **existing** authoritative Community Hub database and must still be currently active, public, not idle/hard-expired, and outside guild's enabled/non-maintenance policy. No new independent session truth or external player database.
- The stored invite must be a strict HTTPS `discord.gg` or `discord.com/invite` URL, verified with Discord against the invoking guild at setup. The public page also validates destination format, escapes titles and uses a strict CSP, bounded results, no-cache headers, request pacing and a 5-second DB budget.
- No sensitive user/profile fields, raw notes, server IDs, analytics or access tokens in public response. Outbound link buttons are explicitly separate from native Join/Quick Match.
- Per-guild permissions and stale group state are rechecked on Discord button actions. The public page itself has no POST/DM/ping/Discord join action.

## Go-live checks (not implied by CI)

- Full Dank Shield CI, focused Community Hub regression CI, PostgreSQL schema smoke and command-size check must pass on the exact PR head.
- Apply migration **before** using the new controls. Check `/lfg` and `/lfg.json` initially return no rows on a clean database; a guild without consent is never exposed.
- Canaries: configure one approved guild invite, explicitly share one public gaming group, verify listing from a logged-out mobile browser and another guild, test the invite, then revoke group/guild consent and confirm it disappears immediately. Repeat for private/locked/ended/expired sessions and DB unavailability.
- Confirm age-restricted server verification and staff policies still operate. A public browser page can link to an age-restricted Discord server, but does not attest visitors' ages.
- Use the protected Discloud deployment workflow. A green PR is not proof of applied SQL or a running production page.

## Next possible reach improvements

Link the page from your owned website (e.g., the420lobby.com) with a visible gaming section, and use tagged/referral invites to measure recruitment with consent. Evaluate other LFG providers only with their documented APIs and account authorization. Do not scrape member directories, send unsolicited DMs, automate DISBOARD bumps or bypass platform rules.
