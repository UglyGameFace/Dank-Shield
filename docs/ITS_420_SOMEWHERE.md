# It's 4:20 Somewhere

A voluntary, mobile-friendly world clock and community activity in **/dank home → Community & Engagement → It's 4:20 Somewhere**. Available to members in any guild. No top-level command, no automatic worker, no role spam.

## Member flow

- Shows where it is currently **4:20 PM** in 27 cities (with accurate IANA time zone and daylight saving rules).
- If none of the representative cities is at 4:20 now, shows the next worldwide waves as Discord-relative timestamps in **each viewer's local time**.
- **Refresh World Clock** recalculates against the current UTC instant. **Switch AM / PM** shows 04:20 instead of 16:20.
- **Start /toke** calls the already authoritative `open_toke_command` handler with a 4:20-themed message. It still checks starter and notification roles, destination and permissions, media-composer requirements, cool-downs and live session lifecycle; it **does not silently create a session or ping a role**. If no media is attached, the existing 90-second GIF composer and **Post Without Media** button remain the normal experience.
- Buttons operate only in the originating member's private view and expire in 10 minutes. Reload through navigation to open a fresh clock.

## Server owner flow

- **Share Here (staff)** publishes a static worldwide board to the *current text channel* (no custom channel picker because the manager deliberately chooses where to invoke it). It has no role mention and no background scheduling.
- Requires server owner, Administrator or Manage Server authority **at click time** and the bot's live View, Send and Embed Links permissions.
- Enforces an **atomic, per-guild one-hour share cooldown** in canonical `guild_config` under `somewhere_420_share_v1`. Two managers racing can only win one CAS claim. Failure to read/write the database fails closed; a failed Discord send attempts a compare-and-swap rollback without overwriting concurrent changes.
- Members may always browse without needing an enabled announcement or persistence setting.

## Technical boundaries

- `somewhere_420_clock.py` is pure date math, no networking, regional offset guesses or access to personal location.
- `commands_ext/public_somewhere_420.py` owns UI and guarded manual posting; existing `commands_ext/public_toke.py` owns sessions, not this feature.
- Canonical `navigation_registry.py` and `public_command_surface_v2.py` expose it without adding another global slash command, startup guard, polling service or direct `/dank` child.
- **No automated announcements or custom ping scheduling in this version.** These would need a separately approved, scale-safe durable scheduler and an opt-in UI integrated with Community & Pings, quiet hours and consent; they must not be enabled by implication from this on-demand feature.

## Acceptance checks

- `python -m pytest tests/test_somewhere_420.py tests/test_navigation_registry_runtime.py -q`
- Existing public command-surface, guild-config CAS, /toke role/cooldown and regression suites, plus compilation and CI.
- Mobile canary: browse world clock as ordinary member; test AM/PM and daylight saving examples; try Start /toke with/without roles, test safe GIF capture, verify no unwanted ping; manager share and immediate second-share denial, permissions failures; test concurrent staff sharing and DB unavailable.
- Production deployment is outside this PR; check actual runtime before claiming live support.


## Version 2: Worldwide celebrations and personal preferences

Feature expansion after merged PR #688; see the separate follow-up PR. The original on-demand clock, staff non-pinging manual share, and canonical /toke session ownership remain.

### Richer world board
- **Next destination** hero and accurate viewer-local relative countdown, with a highlighted 4:20 minute.
- Favorite city appears on your private world board with its actual local clock.
- **Start /toke** keeps original GIF/photo composer. **Quick Text-Only Sesh** is a deliberately selected shortcut through the SAME role, cooldown and outbound send handler; it never adds a public slash argument. **Join Active Sesh** finds the latest visible, live session in that guild without bypassing channel permissions.
- **My 4:20 Preferences** allows individual timezone/favorite-city setup and an explicit, private daily DM opt-in. Defaults OFF.
- **Daily Server Settings** is manager-only. Role selection is NOT a new separate picker: it uses the existing /toke notify capability in Community & Pings. A Search-Safe server text-channel browser selects the delivery channel.

### Scheduled celebrations
- New private Supabase schedule table `public.dank_somewhere_420_schedules` and narrow service-only `dank_420_claim_due` RPC. Both production-source and fresh-schema migrations are present; they need to be reviewed/applied to the active bot data project **before** activating schedules. No new environment secret is necessary if the bot's existing service-role connection is working.
- A single bot-owned scheduled worker is started from the existing guarded background workers startup path. It polls **one indexed due schedule queue**, not every guild or user, with bounded batch/concurrency and no per-server timers. Before send, an atomic DB claim reserves and advances the *next local calendar day* for DST-aware recurrence.
- Notifications are **at most once per local day**: a network send failure consumes that delivery attempt rather than risking duplicate pings across restarts/shards. Subscriptions default disabled. A stale delivery more than 3 minutes late is skipped automatically.
- Guild managers choose: timezone (IANA name), a live text destination (Search-Safe picker), whether to mention ONLY the configured opt-in /toke notification role, and local quiet hours (default 22:00–08:00, same hours = none). Then they deliberately select **Enable Daily 4:20**. Managers may disable anytime. No @everyone/@here.
- Members may set a cross-server personal time zone, favorite city, and explicit daily DM preference, independent of guild announcements. **Stop My DMs** is always accessible in their panel. DM failures are safely consumed, not repeatedly attempted the same day.
- Staff and member schedules always use **4:20 PM local time**; the AM switch remains a viewing-only feature. A daily announcement is a world-board snapshot, never an automatic /toke session.

### Capacity and rollout gates
- Cap delivery to 16 due records per 20-second sweep and 4 parallel requests. Avoid spiking Discord REST requests or the gateway loop. This is conservative for current guild counts, **not proof of simultaneous 10,000-server punctual delivery**. If large populations share one timezone, capacity must be tested and sharding/rate-limit queues evolved before broad rollout.
- Fail closed when the new schedule table/function or service credentials are unavailable; on-demand clock still works.
- CI workflow `.github/workflows/somewhere-420-ci.yml` runs focused Python and PostgreSQL migration/idempotency/atomic-claim smoke, in addition to existing full CI.
- Before production enable: apply and verify migrations on the correct configured Supabase project, test role permission changes and quiet-hour skip, private DM off/on and blocked DM, concurrency/restarts, member leaving guild, mobile modal and Search-Safe browsing. **No production migration or Discloud change occurs just by merging code.**
