# Community Hub: free external player discovery

## Problem and user-facing experience

Existing Community Hub sessions and the public opt-in network do not, by themselves, enumerate players outside participating Dank Shield guilds. This milestone adds clearly distinct **player profiles** and **manual external communities**, without creating a second internal matchmaking service.

**/dank → Community Hub → External Groups / Find Players → External Groups** now opens the **Find Players Outside Your Server** menu.

1. **Minecraft Players:** Lists opted-in public player profiles from Minecraft LFG. It is a source of players looking for Minecraft partners, **not** a current online-presence feed or a confirmation that they joined your Discord. Filter by Java, Bedrock or Cross-play. Up to five pages of 20 records, with short cached reads. The Open Player Directory link takes members to the independent site to make contact there under its rules.
2. **More Free Communities:** Human-browsed outbound links to independently moderated Reddit communities: Minecraft Buddies, Minecraft Servers, Gamer Pals, Xbox Gamers, and PlayStation Friends. These are *not* automatically scraped posts. The bot does not create submissions, copy player data, DM strangers or infer age/availability.
3. **The100.io (optional):** Preserves the existing consent-based, token-gated read-only provider. It is **supporter/developer authorization-gated**, not a free source by default. It is not needed for Minecraft or the public gaming network.
4. **Public Gaming Network & Public Recruiting:** The separate, merged PR #736 feature allows Discord guilds and session hosts to explicitly publish their own public games and authorized server invites to the outside web. Only this consented outward path is designed as an acquisition funnel.

## Research and provider qualification (October 26, 2026)

| Source | Discovery mode | Auth/pricing | Verification and decision |
|---|---|---|---|
| [Minecraft LFG developer API](https://minecraftlfg.com/developers) | Opted-in public Minecraft **player profiles**, `GET /api/v1/players` | Public docs say keyless, read-only, 60 calls/min/IP | **Integrated with bounded on-demand reads**; documented protocol verified, production endpoint availability must be canary-tested after deployment |
| [LFG Atlas](https://lfgatlas.pages.dev/) | Claimed multisource gaming API | Unconfirmed | **Not integrated:** the purported hostname/API, documented endpoints and permissions could not be independently verified. Never manufacture counts, tokens or results |
| [The100.io](https://www.the100.io/api-docs/index.html) | Read-only session discovery | API token restricted to supporters or approved developer exceptions; separate authorization for bot writes | Existing optional provider retained; no default-on paid dependency |
| [r/MinecraftBuddies](https://www.reddit.com/r/MinecraftBuddies/) | Community discussions | Manual public browsing | **Links only**, follow each community's rules, not a scraping API |
| [r/mcservers](https://www.reddit.com/r/mcservers/) | Public server recruitment posts | Manual public browsing | **Links only** |
| [r/GamerPals](https://www.reddit.com/r/GamerPals/) | Multiple games and platforms | Manual public browsing | **Links only** |
| [r/XboxGamers](https://www.reddit.com/r/XboxGamers/) | Xbox social gaming groups | Manual public browsing | **Links only** |
| [r/PSNFriends](https://www.reddit.com/r/PSNFriends/) | PlayStation social gaming groups | Manual public browsing | **Links only** |
| Reddit API | Programmatic social posts | API access policies and OAuth/developer approval | **Not used**; no automatic republishing, public user enumeration or unsolicited outreach |
| Discord global discovery / arbitrary users | Server directories and app discovery | No authorized general-purpose user-contact/search API for this task | **Not implemented**, no invented public-user search |

Do not imply Minecraft LFG provides an outbound Discord invitation, automatic match acceptance, user presence status or provider-profile links if these are not part of the documented API contract. The **Open Player Directory** button is a fixed provider homepage URL rather than a guessed profile URL.

## Abuse protection and host cost

- One fixed, hardcoded HTTPS read endpoint, strict shape and size checks. No provider response can add arbitrary Discord link URLs, disclose private IDs or create actions. No redirects, retries or unsafe user-supplied endpoints.
- Provider's published allowance is 60 requests/minute/IP. Dank Shield deliberately enforces a global **3-second** minimum gap between uncached requests per process (maximum 20/minute) and a **180-second** cache per filter/page, with a bounded 24-entry cache. Burst requests fail with a helpful cooldown notice rather than hammering the provider. It avoids background polling entirely.
- Discord edition filter values: all, JAVA, BEDROCK, CROSSPLAY. The API may return fewer profiles under a filter.
- Source records are intentionally rendered as **public profiles with unverified current availability**; never auto-enrolled in Discord Quick Match, /toke, member lists or notifications.
- Browser links leave Discord to independent sites. External games may not meet the member's age or the host guild's age policies. Host guild rules and age verification remain intact.
- On provider API 429, outage, unfamiliar JSON or network issue: show explicit source-unavailable status and the official website link; never fabricate players or silently fall back to a different source.

## Validation and release

Run focused Python tests with mocked provider HTTP, Community Hub CI, full Dank Shield CI and application command diagnostics. Verify live provider response from Discloud **after** the merge and approved deploy. No new tables, migrations or secrets are required; do not apply schema changes to enable this provider.

Manual canary:
1. Open Community Hub → External Groups → Minecraft Players on Discord Android.
2. Check public player profiles (if upstream currently has data), edition filters, prev/next and any cooldown notices.
3. Check every manual community outbound link for correct destination and moderation rules.
4. Confirm The100.io still shows its separate authorization message with no token and that Public Gaming Network still honors host/server consent.
5. Confirm Share Router and Cinema separation tests remain passing; no restarts or service changes should be required beyond normal guarded Discloud deployment.
