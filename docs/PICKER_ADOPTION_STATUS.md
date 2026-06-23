# Shared Picker Adoption Status

This file tracks the migration from one-off Discord dropdowns/selects to the shared Dank Shield picker kit.

## Available shared picker primitives

- `DankPickerView` — normal option/dropdown menu.
- `DankRoleSelect` — Discord-native role picker with shared owner-lock behavior.
- `DankChannelSelect` — Discord-native channel/category picker with shared owner-lock behavior.
- `DankUserSelect` — Discord-native user/member picker with shared owner-lock behavior.
- `DankMentionableSelect` — Discord-native role/user picker with shared owner-lock behavior.

## Current status

- Foundation added and merged through PR #82.
- Follow-up work expands the kit to cover Discord entity selectors.
- Existing feature modules still need one-by-one migration.

## First migration targets

### `/dank setup`

Current native owners to migrate first:

- `public_setup_solid.SaveRoleSelect` → `DankRoleSelect`
- `public_setup_solid.SaveChannelSelect` → `DankChannelSelect`
- `public_setup_fresh_choice.CustomServicePresetSelect` → `DankPickerView`

Do this carefully because `/dank setup` is the customer entry point.

### `/dank protection`

- Invite/link/spam scope dropdowns should move out of startup guards and into native protection modules using `DankPickerView`.

### `/dank design`

- Font/layout/separator/exact-format menus should use `DankPickerView` and show previews where visual choice matters.

## Migration rule

Do not migrate every picker in one PR. Each PR should migrate one owner surface and include:

1. exact old picker removed/replaced
2. native owner file touched
3. no startup guard patch added
4. manual test path
5. rollback risk
