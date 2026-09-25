# Active Task

## Active task / desired outcome

**P0-SERVER-STATS-DESIGN-INTEGRATION-008 — make Server Stats inherit Server Design safely and make every selectable counter provider-backed and section-customizable**

Desired outcome: Server Stats remains the sole owner of its live category/counter
channels, while optionally inheriting the server's saved Dank Design visual
language. Server owners can customize each counter's icon/prefix, label,
label-to-value separator, and value wrapper independently, e.g.
`[🎫] Open Tickets: [0]` or `[🎫] Open Tickets: 「0」`, without breaking the
live value source. Every counter exposed in the UI must have a real implemented
provider and clear capability/source metadata.

## Scope / single active task lock

Only Server Stats ↔ Server Design integration is active:

- preserve Server Stats ownership of dynamic channel names;
- keep Server Design from directly renaming live stats resources;
- add opt-in design inheritance for existing installations and design inheritance
  by default for newly enabled stats displays;
- inherit stable visual pieces only: category frame, safe label font, and the
  server design's channel separator between icon and label;
- never transform the dynamic numeric/status value;
- add per-counter structured overrides for icon/prefix, label, value separator,
  and value template/wrapper;
- validate fully rendered Discord channel names at 1–100 characters;
- expose only provider-backed metrics and document their source/requirements;
- keep refreshes coalesced/rate-limit-aware;
- preserve existing per-server category name, visibility, labels, number style,
  placement, and ownership IDs.

Do not broaden into unrelated Discord setup, permissions repair, AntiNuke,
tickets UX, or general Server Design redesign.

## Discord documentation findings

Checked current official Discord Developer Documentation on 2026-09-25.

1. Channel names are **1–100 characters**.
   Source: https://docs.discord.com/developers/resources/channel
2. Modifying a guild channel requires **MANAGE_CHANNELS**. Permission-overwrite
   mutation has additional MANAGE_ROLES rules, but Server Stats formatting only
   renames/repositions its owned channels.
   Source: https://docs.discord.com/developers/resources/channel
3. Discord rate limits are per-route and global, are subject to change, and
   **must not be hardcoded**. Applications should honor Discord's returned
   rate-limit headers/retry behavior.
   Source: https://docs.discord.com/developers/topics/rate-limits
4. `GUILD_MEMBERS`, `GUILD_PRESENCES`, and `MESSAGE_CONTENT` are privileged
   Gateway intents. Stats depending on them must explicitly declare that
   requirement rather than silently showing zero.
   Source: https://docs.discord.com/developers/events/gateway
5. `GUILD_CREATE.member_count` is the total number of guild members, so the
   existing Member Count metric does not need a presence-derived estimate.
   Source: https://docs.discord.com/developers/events/gateway-events#guild-create
6. Discord exposes a Get Guild Role Member Counts endpoint for future
   role-backed counters, which is preferable to inventing counts from an
   incomplete member cache.
   Source: https://docs.discord.com/developers/resources/guild

## Current architecture findings

- Server Stats already stores per-guild category ID, owned channel IDs, visible
  counters, labels, number style, category placement, and category name.
- Server Design already excludes the saved Server Stats category/counter IDs
  from normal design detection/apply to avoid two owners renaming the same
  resources.
- Server Design options live under `server_design_studio_options` and include
  theme/font/separator/category-frame settings.
- Server Stats currently renders every counter as a hardcoded
  `label: value` string. That is the missing integration seam.
- Current counter keys are already finite and auditable, but the registry does
  not explain source/capability requirements to the owner.

## Planned implementation

1. Add a canonical metric registry containing title, semantic icon/label,
   provider/source description, capability requirements, and value kind.
2. Refactor live value collection so every registry entry must map to a real
   provider result; unknown/unimplemented keys cannot be exposed by the UI.
3. Add saved per-counter format overrides:
   - icon/prefix section;
   - label;
   - label/value separator;
   - value template containing exactly one `{value}` token.
4. Add safe renderer with final 1–100-character validation and readable fallback.
5. Add Server Design inheritance:
   - full category-frame/font treatment for the owned stats category;
   - safe font transformation of stable counter labels;
   - design channel separator between icon and label;
   - dynamic values remain plain and machine-readable.
6. Add a Server Stats **Design Sync** toggle. Existing enabled installs remain
   visually unchanged until opted in; newly enabled displays inherit design by
   default.
7. Upgrade the counter picker descriptions so owners see the source and any
   Discord intent/permission requirement before enabling a metric.
8. Add regression coverage to existing Server Stats / Server Design test files;
   do not create another one-off test file.

## Status

**IMPLEMENTATION COMPLETE FOR INITIAL VALIDATION — focused/full repository validation pending**

Branch: `feat/server-stats-design-format-registry-20260925`

Base: current `main` after merged PR #321 and successful live access-repair
acceptance.

## Validation required

Before merge:

- compile all changed modules;
- focused Server Stats tests;
- focused Dank Design functional-resource tests;
- full `pytest tests/`;
- existing repository audits/workflows;
- normal existing stats formatting remains backward-compatible when Design Sync
  is off;
- new displays enable Design Sync without mutating Server Design resources;
- per-counter examples render exactly as configured;
- final names never exceed Discord's 100-character limit;
- no dynamic value is font-transformed;
- hidden counters are removed only through existing owned-ID logic;
- no counter can appear in the picker without a provider/capability registry row;
- no privileged-intent-dependent counter silently reports zero;
- event refresh coalescing/cooldowns remain intact;
- Server Design continues excluding live stats resources from its own rename
  plans.

## Implementation completed

- Added one canonical metric registry. The public counter picker is derived from
  it and every row names its real provider/source and capability requirements.
- Added provider parity enforcement so a registry metric without a real value
  provider fails validation instead of silently becoming a decorative zero.
- Added structured per-counter format overrides:
  - icon/prefix;
  - label;
  - label/value separator;
  - value template with exactly one `{value}` token.
- Added examples supported by the renderer such as
  `[🎫] Open Tickets: [0]` and `[🎫] Open Tickets: 「0」`.
- Added final Discord name validation/fallback at 1–100 characters while
  preserving the live value.
- Added stable-prefix recovery so wrapped values do not break owned-channel
  rediscovery when a saved channel ID is stale.
- Added optional Server Design inheritance:
  - the Stats category can inherit category frame/font treatment;
  - stable metric labels can inherit the selected font;
  - the saved design channel separator can sit between default icon and label;
  - dynamic numeric/status values are never font-transformed.
- Structured per-counter formatting is section-aware: changing only the value
  wrapper keeps untouched design sections inherited, while a custom icon/prefix
  becomes authoritative for that layout section.
- Existing enabled installations default Design Sync to Off unless explicitly
  saved; newly enabled displays save Design Sync On by default.
- Added a **Design Sync** control to the Server Stats center.
- Design Sync stays visually neutral until the server has actually saved a
  Server Design; it does not impose fallback styling on untouched servers.
- Saving Server Design requests a Server Stats refresh through the existing
  coalesced/cooldown queue when Design Sync is active.
- Upgraded the old label modal in place to a four-section format editor so old
  call sites remain compatible.
- Picker descriptions now explain each metric's source/requirements, and future
  unavailable metrics are rejected on selection instead of silently enabled.
- Dank Design functional-resource recovery now recognizes Server Stats by saved
  category ID, saved counter parent, or canonical/custom/design-synced category
  name, while still excluding the live resources from normal Design renames.
- Added regression coverage to the existing Server Stats and Dank Design test
  modules; no new test file was created.

## Next step

PR #322 is open as a draft. Freeze feature behavior and run all exact-head
repository workflows. Inspect focused/full-suite failures and correct only
same-task regressions. Then review the final diff for configuration
compatibility, UI limits, ownership conflicts, and Discord
1–100-character/rate-limit constraints before marking ready.
