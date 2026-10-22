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
