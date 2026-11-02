# Dank Shield: external safety-source setup

This feature belongs to **Dank Shield itself**. No third-party Discord bot
needs to be invited. The current visible path on Discord is:

**`/mod` → Member Command Center → Intelligence → select a member →
External Sources.** Alternative: **`/dank home` → Members & Moderation →
Member Command Center → Intelligence**.

The old `/dank members` alias is a *hidden legacy command* and may not appear
in Discord slash-command suggestions. It is not the advertised entry point.
For departed or banned accounts, use **Stored History by ID** from the
Intelligence menu to reach a review with the same Safety Reports and
External Sources buttons. **Connections → External Sources** also works.
The account ID and external review status are shown privately to the owner.

GitHub `main` does not automatically deploy to Discloud. After merging,
follow the existing manual production deployment/backup checks in
`docs/DISCLOUD_MAIN_QUICK_RELEASE.md`. Until the exact running SHA is
verified, a merged feature might still be missing from the live bot.

## Create the API keys

| Provider | Official registration | Discloud environment name | Quota / cost |
| --- | --- | --- | --- |
| Protector | https://protector-bot.com/developers | `DANK_PROTECTOR_API_KEY` | Free 10/minute, 500/month |
| Discord Reviews | https://discordreviews.com/settings | `DANK_DISCORDREVIEWS_API_KEY` | Free 100/hour |
| CheaterStats | https://cheaterstats.cc/v2/api | `DANK_CHEATERSTATS_API_KEY` | Paid: 10 tokens per `/v2/csbans` request |

**Official documentation:**
- https://protector-bot.com/docs/api
- https://discordreviews.com/docs
- https://docs.cheaterstats.cc/docs

Sign in with Discord to each **official** account settings/dashboard. Follow
its instructions to create a personal API key. Protector keys begin with
`pk_` and are only shown once. Store secrets in the existing Discloud app's
private environment/secrets configuration. **Do not paste actual keys into
GitHub, an issue, a Discord channel or this document.** The bot reads these
variables at request time, so no key literals belong in the repository.

Example *names only* for the Discloud configuration (replace placeholder
values in Discloud, never commit your keys):

```dotenv
DANK_PROTECTOR_API_KEY=your_protector_key
DANK_DISCORDREVIEWS_API_KEY=your_discord_reviews_key
DANK_CHEATERSTATS_API_KEY=your_cheaterstats_key
DANK_CHEATERSTATS_PAID_LOOKUPS=0
```

After changing Discloud settings, restart the existing app deployed from
`main`. Do not create an alternative host or branch-linked bot. Verify the
running commit matches the deployed merge commit before claiming the change
is live. If keys are missing, expired, or quota-limited, the owner sees
**unavailable**, never a fictitious clean result.

CheaterStats is **not free**. Paid requests are disabled by default even if a
key exists. To opt into its 10-token per-request `/v2/csbans` checks, the owner
must explicitly set `DANK_CHEATERSTATS_PAID_LOOKUPS=1` in Discloud. For free
operation, keep it `0` or omit it. This integration never uses
CheaterStats's Discord-server affiliation, social membership, messages, or
individual tracking endpoints.

## Use immediately without API keys

**Trustify:** On the External Sources panel, tap **Open Trustify**, enter the
exact Discord ID displayed by Dank Shield in its official public search:
https://trustify.fun/scammers .

**Project Redacted:** Tap **Open Redacted**, enter the same Discord ID in the
official public global-ban search: https://project-redacted.org/ .

The public sites provide human-search pages. Their stable public account
lookup API endpoints were **not verifiable**, so Dank Shield does not
pretend that an API query occurred, auto-submit accounts, or scrape their HTML.
The result of a human lookup is **not** automatically added to Dank Shield's
ledger, enforcement, or member-relationship graph.

**Discord AntiScam** requires **no key**: tap **Check Scam Domain**, paste an
http(s) link or hostname. Dank Shield checks the host against a cached copy
of https://github.com/Discord-AntiScam/scam-links 's published list.txt.
The source was archived on October 15, 2026, so a miss is never proof a
link is safe. The bot does not open the submitted URL.

## Security and data limits

- No external results automatically ban, kick, remove roles, block
  verification, generate affiliation claims or prove wrongdoing.
- Protector blacklist entries have no published case evidence; Discord
  Reviews are user-submitted opinions; ban labels require context.
- The Discord Reviews user endpoint is username-based. The bot accepts a
  result only if its returned Discord ID **exactly matches** the requested ID.
- Requests are owner-triggered, timeout-bounded and cached per process.
  Provider limits remain authoritative; this is **not** a distributed
  daily/monthly quota ledger. Do **not** enable automatic guild-wide
  collection or unrestricted batch scans.
- Read-only searches do not grant external providers any Discord bot token,
  privileged guild permission or guild-membership access.
- The code is not production validated until exact-head CI is green, the
  merge to `main` is recorded, and Discloud network/readbacks are checked.
