# Shared Picker Adoption Status

This tracks migration from one-off Discord dropdowns/selects to the shared Dank Shield picker kit.

## Available primitives

- `DankPickerView` — normal option/dropdown menu.
- `DankRoleSelect` — Discord-native role picker with shared owner-lock behavior.
- `DankChannelSelect` — Discord-native channel/category picker with shared owner-lock behavior.
- `DankUserSelect` — Discord-native user/member picker with shared owner-lock behavior.
- `DankMentionableSelect` — Discord-native role/user picker with shared owner-lock behavior.

## Current status

- Foundation added and merged through PR #82.
- Entity selector wrappers are added in the follow-up picker work.
- Existing feature modules still need one-by-one migration.

## First migration targets

### `/dank setup`

- `public_setup_solid.SaveRoleSelect` -> `DankRoleSelect`
- `public_setup_solid.SaveChannelSelect` -> `DankChannelSelect`
- `public_setup_fresh_choice.CustomServicePresetSelect` -> `DankPickerView`

### `/dank protection`

- Invite/link/spam scope dropdowns should move out of startup guards and into native protection modules using `DankPickerView`.

### `/dank design`

- Font/layout/separator/exact-format menus should use `DankPickerView` and show previews where visual choice matters.

## Migration rule

Each migration PR should include:

1. exact old picker replaced
2. native owner file touched
3. no startup guard patch added
4. manual test path
5. rollback risk
