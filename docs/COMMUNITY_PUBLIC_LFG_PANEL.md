# Community Hub: always-available public LFG panel

## Purpose

One long-lived, shared Discord message makes LFG easy to find. A member enters the designated channel, sees the current eligible public gaming groups and presses a button. **Their personal browsing and account controls open privately**, so twenty players can use the same public panel simultaneously without overwriting each other's interactions.

This is **not** a fake always-online claim or a raw Discord presence tracker. The summary is from Community Hub's stored, owner-managed joinable sessions. No private groups, personal game histories, member IDs, contact credentials or arbitrary invite URLs are published.

## Staff setup on Android

1. Open `/dank → Community Hub → Staff Dashboard → Public LFG Panel` with Manage Server permission.
2. Tap **Use This Channel** to post where you ran the command, or **Choose Channel** for the existing shared Search-Safe text channel picker. It recognizes styled names, emojis and IDs.
3. Confirm Dank Shield can View Channel, Send Messages, Embed Links and Read Message History. A single shared panel message is posted, and its channel/message IDs are persisted under `community_hub_public_lfg_panel_v1` using guild-config compare-and-swap.
4. Staff can **Refresh Live Panel**, **Remove Panel**, or move it with Use This Channel/Choose Channel. Moving installs the new message first, saves its ID, and only then attempts to delete the old bot-authored panel. A concurrent second install cannot create a second accepted public panel.
5. The same view is registered on boot with `timeout=None` and stable custom IDs. Restarts do not strand the panel's buttons. When staff removes/moves a panel, the old message's interaction guard no longer authorizes its buttons.

## Buttons members see

- **Find Players:** Opens a **private** live local-group browser with existing paging, filters and real join controls.
- **Create Group:** Opens the existing Start Gaming Session form; all current per-server creation permissions and limits apply.
- **My Groups:** Opens a member's private current group list.
- **Outside Players:** Opens the already integrated Atlas Gaming and Minecraft player discovery navigator.
- **Public Network:** Opens the existing opt-in cross-server public directory, when the required public recruitment migration is installed.
- **Refresh:** Refreshes the same shared panel in place, with a 30-second per-guild spam guard. No public channel response or new message.

All public-board buttons avoid adding new slash commands. No custom voice/presence capture or unsolicited recruitment DMs.

## Near-live status, scaling and reliability

- The worker wakes every 15 seconds, samples up to four local guilds per tick, and avoids rechecking a guild more often than once a minute. Existing Community Hub lifecycle events also mark a guild for earlier refresh. Each board shows at most five eligible public groups, and all join actions query the authoritative database when a user opens Find Players.
- A stable content fingerprint prevents unnecessary Discord edits. It only updates its existing message when the public group summary genuinely changes. The worker never scans Discord channel history, repeatedly reposts panels or bulk-pings members.
- Session data is filtered server-side for `privacy=public`, joinable `open/forming/ready/active` states, and configured idle/lifetime windows. The panel displays only sanitized game names, status and capacity.
- On database failure, the board shows unavailable instead of made-up players. On Discord permission/missing-message errors, it warns in logs; staff must repair or repost. It doesn't silently duplicate public messages.
- **Important limit:** 'Near-live' means bounded refresh, not instant across 10,000 guilds. Manual refresh and authoritative private browsers remain available. A future high-scale event-bus would be needed for guaranteed sub-minute refresh fleet-wide.

## Database deployment dependency (verified October 26, 2026)

The connected DankShield Supabase database has `dank_community_sessions`, but does **not** yet have the `public_lfg_opt_in` column, the `community_hub_public_lfg_directory` function, or the `dank_community_atlas_publications` table. These belong to already merged PRs #736 and #741. **Do not pretend outside publishing works until migrations are installed in the correct active database and the Discloud runtime is on the matching main commit.**

This public-panel PR adds **no new SQL schema**. Panel pointer configuration uses existing guild-config persistence, so it can be merged independently. Public Network and external advertising still depend on the earlier migrations.

## Canary

1. Install one panel in a public channel and verify a **different nonstaff member** can click Find Players and Create Group without changing anyone else's public message.
2. Create, join, pause, lock, and end a session; ensure only eligible public groups appear after refresh, never private notes/host data.
3. Try from Discord Android, then restart the bot on Discloud; all six panel buttons still respond.
4. Move the panel using Search-Safe picker; old post is inactive, no duplicate live panel, and staff Remove Panel disables it.
5. Confirm a maintenance-mode outage shows a friendly unavailable banner. Inspect REST pacing before opening this for thousands of guilds.
6. Separately apply the missing public recruiting and Atlas migrations under the authorized production rollout before testing external ad POST/DELETE.
