# Shared Picker Adoption Status

This tracks migration from one-off Discord dropdowns/selects to the shared Dank Shield picker kit.

## Available primitives

- `DankPickerView` — normal option/dropdown menu.
- `DankGuildResourceBrowserView` — Dank Shield-owned role/channel/category browser with guild-cache discovery, 25-item paging, Search-Safe current/styled-name and saved-previous-name lookup, ID/mention search, owner locking, Back/Close, and explicit error handling.
- `DankRoleSelect` — Discord-native role picker with shared owner-lock behavior. Keep this only where native Discord entity discovery is intentionally acceptable.
- `DankChannelSelect` — Discord-native channel/category picker with shared owner-lock behavior. Keep this only where native Discord entity discovery is intentionally acceptable.
- `DankUserSelect` — Discord-native user/member picker with shared owner-lock behavior.
- `DankMentionableSelect` — Discord-native role/user picker with shared owner-lock behavior.

## Current status

- Shared picker foundation was added and merged through PR #82.
- Fix Access moved off Discord's generic channel picker in PR #240 after the native selector failed to surface expected resources reliably. The Search-Safe continuation now consolidates its discovery/search/paging onto `DankGuildResourceBrowserView` so it does not maintain a second resource-browser search engine.
- `/dank setup` role/channel/category mapping is being migrated to `DankGuildResourceBrowserView` in PR #241.
- The normal public setup path keeps validation and persistence inside its feature owners; the shared browser owns discovery/search/paging only.
- Member Setup Manager role/channel/category mapping now follows the same ownership rule: `DankGuildResourceBrowserView` owns Search-Safe discovery/search/paging/Back/Close while Member Setup retains validation, Strict Gate rules, and persistence.
- Other feature modules still need one-by-one migration.

## Current migration targets

### `/dank setup`

Normal public setup role/channel/category choices use the dedicated resource browser rather than `DankRoleSelect` / `DankChannelSelect`, because those wrappers still delegate discovery/rendering to Discord's native entity picker.

Covered by the setup resource-browser migration:

- `public_setup_solid` Ticket Basics, Access Roles, Verification Channels, core/advanced Logs + Status
- `public_setup_recommend` guided **Choose one I already have** role/channel/category steps
- `public_setup_full_customization` role, category, channel, and logs/status choices

Admin-only legacy fallback setup pickers stay outside the public-path task until their own cleanup pass.

### `/dank protection`

- Invite/link/spam scope dropdowns should move out of startup guards and into native protection modules using the shared picker kit.

### `/dank design`

- Font/layout/separator/exact-format menus should use `DankPickerView` and show previews where visual choice matters.

## Search-Safe lookup boundary

The shared resource browser owns semantic current/styled-name and saved-previous-name lookup. Discord-native `RoleSelect` / `ChannelSelect` components cannot receive hidden aliases from Dank Shield because Discord owns their entity discovery. Feature-owned native selectors therefore must not be described as alias-aware. Migrating those surfaces requires a feature-by-feature component-contract review, especially for multi-select workflows.

Member Setup Manager is now migrated for its four admin resource choices: setup channel, automatic Member Access role, optional Eligibility Prerequisite role, and protected category. Its public member-review controls remain feature-owned and are not resource-discovery pickers.

## Migration rule

Each migration PR should include:

1. exact runtime picker path identified
2. feature validation/save ownership preserved
3. no startup guard patch added
4. owner-safe mobile behavior and explicit error response
5. focused regression coverage plus exact-head CI
6. rollback/integration risk recorded before merge
