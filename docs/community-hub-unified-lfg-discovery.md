# Community Hub: source-aware gaming discovery (issue #638)

## Source boundaries

Find Players keeps its original, authoritative **local** session list, native join/details and Quick Match. Members may switch to **Partner Groups**, which queries only currently-approved public HubLink sessions, or **External Groups** (The100.io), which only renders sanitized, ID-derived outbound links. No external session is copied into the Dank Shield database. External listings are not joinable using native Quick Match.

The external service is default-off per guild, requires the existing `external_the100_enabled` migration and `DANK_COMMUNITY_THE100_API_TOKEN` on the bot host, and must be explicitly enabled by staff. Never put that token in Discord messages or configuration. On missing token, denied API access, or provider failure, the UI reports unavailable and does not fabricate sessions.

## Find Players game search

The local and partner views have source-switch buttons and **Search Game** / **Clear Filter** controls. Search opens a mobile-friendly single-input modal; filters match a normalized game title (case, punctuation and a conservative list of aliases such as MC/Minecraft and GTA V/GTA 5). Search never inspects user-written descriptions, notes, membership, or private group names. The query is preserved when switching sources or refreshing, but not persisted to a guild or member profile.

Local and partner sources support a separate mobile-native **session preference** selector: exact-match **play style** (casual, competitive, beginner or the session's explicit any) and **microphone policy** (optional, preferred, required or no mic). Both selectors filter only stored metadata, exclude unknown/legacy values when active, retain preferences when switching local/partner sources or searching, and can be cleared together. External The100.io is intentionally excluded because its adapter does not verify these fields. Platform/region preferences are not fabricated.

Local and partner modes re-read up to **100** recent authorized sessions from their *separate* existing service methods and page through the matching records, **25 per page**, using **More groups / Previous groups** options in the existing mobile filter menu. Each page action re-reads the current authorized source; source switching and changed filters reset to page one. A stale page after sessions expire/revoke returns to page one rather than exposing old records. This is a bounded recent-results browser, **not an exhaustive history search**, and it never expands partner consent, session visibility, native joining, or the Discord 25-option select limit. Native details are rechecked in the current guild on click; partner detail clicks recheck current HubLink consent/public discovery before showing the partner card.

The100.io mode filters the currently retrieved **25** source rows, then pages up to five links per embed. The100.io's backend is not queried for a game-specific dataset, so the UI describes its search as a filter of the current provider snapshot, not global provider search. No unverified platform/region/mic fields are displayed.

## Manual, game-specific notification roles

Staff configure up to **12 game-role mappings** in **Community Hub → Staff Dashboard → Game Role Pings**. Roles must first be created/enabled as **Notification** options in Community & Pings. Legacy, disabled, managed, /toke, CHEERS and Movie Night notification roles are not eligible. Configuration uses the canonical guild config CAS RPC; no new SQL migration is introduced.

Group hosts, co-hosts, or authorized staff can press **Call Game Crew** from **Manage Session**. No ping occurs on searches, page views, session creation, external discoveries, or automatic notification dispatch. The selected session must still be a public and joinable *local guild* Community Hub group with a current Discord card. Role/channel permissions, current role opt-in option, and notification settings are re-evaluated at click time. Every allowed send reserves its durable guild state *before* calling Discord. Reservations are shared across processes: **one call per session**, per-game **15–240 minute** configurable cooldown (default 30), a **five-minute guild-wide gap**, and **four role calls/hour per guild**. An uncertain Discord send consumes the reservation rather than risking another ping. Explicit per-game opt-in DMs and /toke/CHEERS flows remain separate.

No quiet-hours policy owner currently exists for Community Hub role mentions, so this release adds no automatic role delivery and does not claim quiet-hours enforcement. Later scope may add an explicit staff-configured do-not-disturb window with proven timezone handling.

## Acceptance, still required

- GitHub full CI, Community Hub regression tests, app command payload checks and diff review at the exact feature-branch head.
- In Discord mobile and desktop, switch among sources, search and clear, refresh while filtered, verify the source-specific join/link behavior and expired/revoked partner links.
- Verify actual API token authorization, production SQL migration, staff opt-in and disabled/degraded screens, plus Discloud redeploy/restart recovery.
- Later issue #638 phases: additional verified providers, richer game/platform filters with available provider data, consented per-game notifications and optional website presentation. None is claimed here.
