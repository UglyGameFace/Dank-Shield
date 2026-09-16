# ACTIVE TASK

## DS-AUD-SETUP-ROLE-CHANNEL-PICKERS — Replace live /dank setup native entity pickers with the Dank resource browser

**Outcome target:** Every normal public `/dank setup` path that asks an owner to choose an existing Discord role, text/voice channel, or category uses a Dank Shield-owned, cache-backed resource browser with paging, search, owner locking, safe empty states, and explicit interaction-error handling. Normal setup must not fall back to Discord's generic `RoleSelect` / `ChannelSelect` UX for these mappings.

**Status:** INVESTIGATION / IMPLEMENTATION

**Branch:** `audit/setup-role-channel-pickers`
**Base main:** `a82bffa58effa1fec841c4a68f71f5caa613f7a4`
**Previous integrated task:** PR #240 merged at `a82bffa58effa1fec841c4a68f71f5caa613f7a4`

## Scope

- public `/dank setup` role/channel/category selection paths that are loaded by the normal public command profile
- shared Dank Shield resource-browser ownership needed to avoid duplicating the PR #240 browser pattern
- `public_setup_solid`
- `public_setup_recommend` guided one-item existing-resource choices
- `public_setup_full_customization`
- preservation of existing role hierarchy/channel permission checks, config aliases, save sources, navigation, and stale-screen protection
- focused regression/static acceptance coverage
- picker adoption documentation and task/PR bookkeeping

Out of scope unless production-path tracing proves otherwise:
- admin-only legacy fallback commands (`public_setup_start`, `public_setup_picker`)
- protection/design/ticket/member/welcome picker migrations
- guild-config persistence semantics
- creation/default builders
- permission-repair mutation semantics
- startup-guard redesign unrelated to setup picker ownership

## Findings / root cause

1. PR #240 fixed the user-reported **Fix Access** target selector by replacing its native `ChannelSelect` with a Dank-owned cache browser, and that task is now merged.
2. The public command profile explicitly loads `public_setup_solid`, `public_setup_recommend`, and `public_setup_full_customization` as normal setup owners.
3. `public_setup_solid` still defines raw `SaveRoleSelect(discord.ui.RoleSelect)` and `SaveChannelSelect(discord.ui.ChannelSelect)` and uses them throughout Ticket Basics, Access Roles, Verification Channels, and Logs + Status.
4. `public_setup_full_customization` independently defines another raw `SaveRoleSelect` and `SaveChannelSelect` pair and uses them for roles, Discord categories, feature channels, and logs/status.
5. `public_setup_recommend` has raw `GuidedExistingRoleSelect` and `GuidedExistingChannelSelect` for the guided one-item setup path.
6. The repository's shared picker wrappers `DankRoleSelect` / `DankChannelSelect` are intentionally thin wrappers over Discord-native entity selectors. Replacing raw selectors with those wrappers would improve ownership checks but would **not** solve the generic-picker discovery problem the user reported.
7. The existing shared-picker migration docs say `/dank setup` role/channel mapping is the next migration stage, require mobile-safe/no-silent-failure behavior, and prohibit one-off raw entity selectors without a documented limitation.
8. `public_setup_start` remains an admin-profile fallback. It is imported by the public dashboard for naming modals, but its raw role/channel mapping screens are not part of the normal public profile unless tracing proves a live handoff.

## Implementation direction

- Promote the PR #240 cache/search/paging pattern into a reusable shared guild-resource browser instead of copying feature-specific picker code again.
- Support role and channel/category resource types with deterministic guild-cache discovery, 25-item pages, name/ID/mention search, owner locking, Back/Close, and explicit `on_error` behavior.
- Keep feature-specific validation and persistence in the setup owner modules. The shared browser chooses an object; it does not own setup business logic.
- Replace normal public setup native entity selectors with buttons/flows that open the shared resource browser, then feed the selected live guild object into the existing validation/save path.
- Preserve current config aliases, role-manage requirements, channel permission/file requirements, guided stale-screen checks, and post-save navigation.

## Validation plan

- prove normal public `/dank setup` owner modules contain no raw `discord.ui.RoleSelect` / `discord.ui.ChannelSelect` selection path after migration
- prove the shared browser pages more than 25 resources and searches by name + Discord ID/mention
- prove wrong-owner interactions are rejected safely
- prove role/channel filters select only allowed resource types
- prove existing role hierarchy and channel permission rejection behavior still runs before save
- prove guided one-item existing-resource selection still saves and advances only when the guided step is current
- prove setup remains inside the existing navigation tree and every callback has a safe response path
- run all PR workflows on the exact final head before merge-readiness is claimed

## Cleanup / compatibility

- Do not add a startup guard, monkey patch, or parallel config writer.
- Do not change role/channel creation behavior in this task.
- Keep admin-only legacy pickers separate unless a real public execution path reaches them.
- If a startup guard mutates one of the live setup picker classes, record it as an ownership conflict and remove or migrate only the conflicting behavior needed for this task.

## Backlog

- `/dank protection` invite/link/spam picker migration
- `/dank design` style/layout/font/separator picker migration
- ticket/member/self-role/welcome/modlog picker migrations in the documented order
- admin-only legacy setup picker cleanup after the public production path is clean

## Next step

Finish tracing the three live public setup selector owners and their existing validation/save contracts, implement one shared resource browser, migrate those live paths without changing business rules, add focused regressions, then open a draft PR and run exact-head CI.
