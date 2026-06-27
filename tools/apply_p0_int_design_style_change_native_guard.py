from __future__ import annotations

"""Compact native-guard migration for Dank Design Style Change issue-review flows.

Run from repo root:
    python tools/apply_p0_int_design_style_change_native_guard.py
"""

from pathlib import Path

TARGET = Path("stoney_verify/commands_ext/public_design_studio.py")

ACTION_NAMES = (
    "design.style_change.missing_icons_submit",
    "design.style_change.apply_safe_only",
    "design.style_change.fix_missing_icons_modal",
)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Refusing to patch {label}: expected 1 match, found {count}.")
    return text.replace(old, new, 1)


def main() -> None:
    path = TARGET
    text = path.read_text(encoding="utf-8")

    if all(name in text for name in ACTION_NAMES):
        print("Already applied: Dank Design style-change native guard slice is present.")
        return

    if "async def _guard_design_action(" not in text or "safe_send_interaction" not in text:
        raise SystemExit("Missing native design interaction helpers. Apply exact-format guard migration first.")

    # Keep this slice intentionally narrow: wrap the three issue-review entrypoints
    # and route their short error replies through safe_send_interaction.
    text = replace_once(
        text,
        '''    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_design_permission(interaction):
            return

        guild = interaction.guild
        assert guild is not None
''',
        '''    async def on_submit(self, interaction: discord.Interaction) -> None:
        async def action() -> None:
            if not await _require_design_permission(interaction):
                return

            guild = interaction.guild
            assert guild is not None
''',
        "style-change missing emoji modal start",
    )

    text = replace_once(
        text,
        '''        key = _key(int(guild.id), int(interaction.user.id))
        pending = _PENDING.get(key)
        if not pending:
            return await interaction.response.send_message("This preview expired. Run Style Change again.", ephemeral=True)
''',
        '''            key = _key(int(guild.id), int(interaction.user.id))
            pending = _PENDING.get(key)
            if not pending:
                await safe_send_interaction(
                    interaction,
                    content="This preview expired. Run Style Change again.",
                    ephemeral=True,
                    action_name="design.style_change.missing_icons.expired",
                )
                return
''',
        "style-change modal expired send",
    )

    text = replace_once(
        text,
        '''        await interaction.response.edit_message(embed=embed, view=view)


class StyleChangeApplySafeOnlyButton(discord.ui.Button):
''',
        '''            await interaction.response.edit_message(embed=embed, view=view)

        await _guard_design_action(interaction, "design.style_change.missing_icons_submit", action, defer=False)


class StyleChangeApplySafeOnlyButton(discord.ui.Button):
''',
        "style-change modal guard footer",
    )

    text = replace_once(
        text,
        '''    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return

        guild = interaction.guild
        assert guild is not None
''',
        '''    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        async def action() -> None:
            if not await _require_design_permission(interaction):
                return

            guild = interaction.guild
            assert guild is not None
''',
        "style-change apply-safe callback start",
    )

    text = replace_once(
        text,
        '''        key = _key(int(guild.id), int(interaction.user.id))
        pending = _PENDING.get(key)
        if not pending:
            return await interaction.response.send_message("This preview expired. Run Style Change again.", ephemeral=True)
''',
        '''            key = _key(int(guild.id), int(interaction.user.id))
            pending = _PENDING.get(key)
            if not pending:
                await safe_send_interaction(
                    interaction,
                    content="This preview expired. Run Style Change again.",
                    ephemeral=True,
                    action_name="design.style_change.apply_safe.expired",
                )
                return
''',
        "style-change apply-safe expired send",
    )

    text = replace_once(
        text,
        '''        await interaction.response.edit_message(
            embed=embed,
            view=StyleChangePreviewView(can_apply=True, has_blockers=False),
        )


class StyleChangeFixMissingEmojiButton(discord.ui.Button):
''',
        '''            await interaction.response.edit_message(
                embed=embed,
                view=StyleChangePreviewView(can_apply=True, has_blockers=False),
            )

        await _guard_design_action(interaction, "design.style_change.apply_safe_only", action, defer=False)


class StyleChangeFixMissingEmojiButton(discord.ui.Button):
''',
        "style-change apply-safe guard footer",
    )

    text = replace_once(
        text,
        '''    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return

        guild = interaction.guild
        assert guild is not None
''',
        '''    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        async def action() -> None:
            if not await _require_design_permission(interaction):
                return

            guild = interaction.guild
            assert guild is not None
''',
        "style-change fix-missing callback start",
    )

    text = replace_once(
        text,
        '''        key = _key(int(guild.id), int(interaction.user.id))
        pending = _PENDING.get(key)
        if not pending:
            return await interaction.response.send_message("This preview expired. Run Style Change again.", ephemeral=True)
''',
        '''            key = _key(int(guild.id), int(interaction.user.id))
            pending = _PENDING.get(key)
            if not pending:
                await safe_send_interaction(
                    interaction,
                    content="This preview expired. Run Style Change again.",
                    ephemeral=True,
                    action_name="design.style_change.fix_missing.expired",
                )
                return
''',
        "style-change fix-missing expired send",
    )

    text = replace_once(
        text,
        '''        if not missing:
            return await interaction.response.send_message("No missing-emoji rows found in this preview.", ephemeral=True)

        if len(missing) > 5:
            return await interaction.response.send_message(
                "Too many missing-emoji rows for one modal. Use **Apply Safe Ones Only** to apply safe rows first, then fix the rest from Channel Editor.",
                ephemeral=True,
            )
''',
        '''            if not missing:
                await safe_send_interaction(
                    interaction,
                    content="No missing-emoji rows found in this preview.",
                    ephemeral=True,
                    action_name="design.style_change.fix_missing.none",
                )
                return

            if len(missing) > 5:
                await safe_send_interaction(
                    interaction,
                    content="Too many missing-emoji rows for one modal. Use **Apply Safe Ones Only** to apply safe rows first, then fix the rest from Channel Editor.",
                    ephemeral=True,
                    action_name="design.style_change.fix_missing.too_many",
                )
                return
''',
        "style-change fix-missing validation sends",
    )

    text = replace_once(
        text,
        '''        separator_id = _safe_str(pending.get("separator_id"), "none")
        await interaction.response.send_modal(
            StyleChangeFixMissingEmojiModal(items=missing, separator_id=separator_id)
        )


class StyleChangePreviewView(DesignPreviewView):
''',
        '''            separator_id = _safe_str(pending.get("separator_id"), "none")
            await interaction.response.send_modal(
                StyleChangeFixMissingEmojiModal(items=missing, separator_id=separator_id)
            )

        await _guard_design_action(interaction, "design.style_change.fix_missing_icons_modal", action, defer=False)


class StyleChangePreviewView(DesignPreviewView):
''',
        "style-change fix-missing guard footer",
    )

    path.write_text(text, encoding="utf-8")
    print("Applied: Dank Design style-change native guard slice.")


if __name__ == "__main__":
    main()
