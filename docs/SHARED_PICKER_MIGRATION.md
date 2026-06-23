# Dank Shield Shared Picker Migration

Dank Shield now has one reusable picker contract:

```py
from stoney_verify.ui import DankPickerView, make_choice
```

## Why this exists

The bot had dropdowns and picker-like flows scattered across setup, protection, design, tickets, spam, members, welcome, and startup guard modules. Each one handled labels, owner checks, cancel buttons, back buttons, empty states, and Discord limits differently. That made the product feel inconsistent and made fixes drift into startup guards.

## Rule going forward

New picker/dropdown surfaces must use `DankPickerView` unless Discord requires a native entity selector such as `RoleSelect`, `ChannelSelect`, `UserSelect`, or `MentionableSelect`.

When native Discord entity selectors are required, wrap them in the same UX rules:

- owner-only unless explicitly public
- clear placeholder
- Close/Back controls when the flow is not terminal
- no silent failures
- no business logic inside startup guards
- all saves must go through the native owner module/service

## Migration order

Move one feature surface at a time. Do not rewrite the whole bot in one PR.

1. `/dank setup` choice screens
2. `/dank protection` invite/link/spam pickers
3. `/dank design` style/layout/font/separator pickers
4. ticket panel/category pickers
5. members cleanup/review pickers
6. self-role/profile pickers
7. welcome/modlog setup pickers
8. remaining startup guard pickers must be deleted or moved native

## Required behavior for every picker

- The picker must clearly say what the choice changes.
- It must not show raw IDs unless the choice is explicitly advanced.
- It must have a safe empty state.
- It must never save cross-guild/global state by accident.
- It must use `interaction.guild.id` for guild-scoped saves.
- It must give the user an answer instead of letting Discord show `Interaction failed`.
- It must stay usable on mobile.

## Example

```py
async def on_pick(interaction, value):
    await save_choice(interaction.guild.id, value)
    await interaction.response.edit_message(content=f"Saved `{value}`.", view=None)

view = DankPickerView(
    author_id=interaction.user.id,
    custom_id="dank:setup:example_picker",
    placeholder="Choose what to set up…",
    choices=[
        make_choice("Tickets", "tickets", description="Create support ticket channels", emoji="🎫"),
        make_choice("Verification", "verify", description="Set up join verification", emoji="✅"),
    ],
    on_pick=on_pick,
)
```

## Anti-patterns to remove

- `discord.ui.Select` classes copied per feature with different owner checks.
- Startup guards that monkey-patch select callbacks.
- Long option labels that truncate important details.
- Pickers with no Close button.
- Pickers that only explain choices instead of showing example previews where previews matter.
