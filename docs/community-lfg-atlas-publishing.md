# LFG Atlas: consent-only public game advertisement

## What this feature does

This is the **outside acquisition path** missing from the earlier Community Hub: a host or co-host can deliberately advertise a public, joinable Dank Shield gaming session on a **third-party multi-game directory**, rather than only showing other members' external posts. It does not automatically recruit, scrape player lists, invite strangers, or repeatedly bump posts.

Reference: [LFG Atlas developer docs](https://lfgatlas.pages.dev/developers/), verified 26 October 2026. Atlas publishes a free, keyless POST `/api/posts` and DELETE `/api/posts/{id}`; consent is mandatory and the author must publish their own authorized contact. Reply 201 contains public URL and a **private edit_token** that must be retained to remove the post. Exact live API response from Discloud was **not** verifiable at development time. Post publishing is therefore opt-in, one at a time and requires a live release canary before being considered production proven.

## Android use

1. Staff enables Community Hub **Public Recruiting**, verifying the Discord invite and public community name. This is a server-wide precondition. The provider is free: no subscription, no API key.
2. A host/co-host opens an active, public group and chooses **Share Outside Server**. This is separate explicit consent to appear in the existing public board.
3. The same host/co-host selects **Advertise Externally**, enters a region (e.g. NA or EU West), chooses 1, 2, 4, 12 or 24 hours, confirms whether the audience is **18+ only**, and types **POST**. Discord guilds explicitly marked age-restricted always publish as adult-only even if the host selects NO. This is a separate affirmative user request for a third-party publication and disclosure of the staff-approved guild invite.
4. Dank Shield rechecks session consent, current host/co-host membership, availability, official game support and stored approved invite, then queues the job. It sends the request only if still authorized.
5. **External Ad Status** shows queued, publishing, posted (with public URL), removing, removed or failed. It never shows the private edit token, member IDs, or hidden Discord notes.
6. **Remove External Ad** requests provider deletion using the privately retained edit token. It can also prevent a still-queued publish. Server-wide public recruiting disable, individual **Share Outside Server** opt-out, the session ending/locking/expiring, maintenance mode or invite changes trigger the same deletion through the scheduled worker. A removed public post's private token is cleared after confirmed removal.

**Important:** The game must be one of Atlas's currently documented 28 supported games. Unknown custom game names are refused instead of advertised under a false game label. This does not guarantee that outsiders will join The 420 Lobby, but creates an actual listing on a third-party discovery platform.

## Server safety and provider compliance

- No auto-bumping, auto-posting for all public groups, harvesting player handles, DM outreach, spoofed user consent or post creation based solely on guild settings.
- Region and post duration require explicit user input. The SQL enqueue RPC additionally requires an active `host`/`cohost` membership, individually opted-in session, guild public recruiting enabled, a valid trusted invite, public privacy and nonexpired session. Even a staff moderator who is not a group host cannot publish as another host.
- Provider request exposes only consent flag, app source `dankshield`, game slug, safe default title, region, **adult-only audience flag** and **the already approved Discord server invite as the host's authorized contact**. Does not expose session notes, Discord user IDs, role lists, extra descriptions or group voice/thread IDs.
- Provider allows 100 posts/10m, 300/hour, 3000/day per IP and 1 per 15s per contact. Our worker handles at most two queued jobs per minute with an extra delay, and database caps to eight active posts per server. It never automatically retries a POST after a timeout because the external provider may have accepted it.
- Private service-role-only Supabase table `dank_community_atlas_publications` stores the single post ID, external public URL and **private edit_token** per session. Row level security disallows anon/authenticated access; user-facing status strips all credentials and IDs. Never log or expose the token through Discord.
- Successful provider POST followed by local credential persistence failure causes immediate best-effort compensation DELETE; if the process crashes in between, the job becomes `unknown` and stops rather than risk creating a duplicate. Unknown posts may require manual reconciliation with LFG Atlas because a deletion token could have been lost.
- Provider deletion retries are bounded to eight. A nonremovable post remains in a failed state with its private token for staff investigation; false removal success is never reported.
- All external requests use a fixed official HTTPS host, small bounded JSON responses, 9-second timeout, no redirects, validated public URLs and immutable API path derived from an alphanumeric post ID.

## Deployment and canary

1. Merge PR only after exact-head focused Community Hub Python tests, real PostgreSQL 16 migration/privacy/consent/revocation tests, full Dank Shield CI and cross-feature regressions pass.
2. Apply additive `supabase/migrations/20261026194500_lfg_atlas_optin_publishing.sql` to the **correct production database** through the approved migration process before guarded Discloud deployment. This is **not** the same as the OneBump database migration.
3. With staff permission, use a throwaway **real** Discord test session from the bot in Discloud and separately confirm its public opt-in. Host enters POST once, checks queued→posted with a verified provider URL, then uses Remove External Ad. Confirm removal on the external site and that its private token is cleared.
4. Test server public recruiting turned off, session unshared, session ended, local status, provider timeout/429, bad game, no remaining slots and whether LFG Atlas's actual 201 shape matches the public documentation.
5. No external post must be sent in CI, preview, or demo mode; CI uses mocked HTTP and isolated PostgreSQL fixtures.

## Follow-on functionality, not silently enabled here

Atlas also offers game metadata, feeds and embed widgets. Future optional improvements include game slug discovery and user-controlled external post analytics. Multi-provider posting is **not** implied. Independent subreddits are outbound links only and their posting rules must be respected.
