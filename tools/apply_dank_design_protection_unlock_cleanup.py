from __future__ import annotations

"""Expose Dank Design protected-name unlock controls.

The design engine already falls back per unsupported glyph, so protected items
should not be treated like a hidden font-failure bucket. This patch makes the
protection controls obvious and adds manager-level buttons for default protected
names:
- Allow Font on Defaults: default ticket/log/system names can receive font styling.
- Restore Default Protection: remove those default overrides.

Run from repo root:
    python tools/apply_dank_design_protection_unlock_cleanup.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "stoney_verify/commands_ext/public_design_studio.py"

INSERT_AFTER = '''async def _save_protection_rule(interaction: discord.Interaction, *, base_name: str, mode: str | None) -> dict[str, Any]:
    guild = interaction.guild
    assert guild is not None

    options = await _load_design_options(int(guild.id))
    rules = _protection_rules(options)
    base = studio.normalize_base_name(base_name)

    if mode is None:
        rules.pop(base, None)
    else:
        clean = _safe_str(mode).lower().replace("-", "_")
        if clean not in PROTECTION_LABELS:
            clean = "never"
        rules[base] = clean

    options["protection_rules"] = rules
    await _save_options(interaction, options) if "_save_options" in globals() else await _save_design_options(int(guild.id), options)
    return options


'''

HELPERS = '''async def _set_default_protection_rules(interaction: discord.Interaction, *, mode: str | None) -> tuple[dict[str, Any], int]:
    guild = interaction.guild
    assert guild is not None

    options = await _load_design_options(int(guild.id))
    rules = _protection_rules(options)
    changed = 0
    clean_mode = _safe_str(mode or "").lower().replace("-", "_")

    for name in sorted(studio.DEFAULT_PROTECTED_NAMES):
        base = studio.normalize_base_name(name)
        if not base:
            continue
        if mode is None:
            if base in rules:
                rules.pop(base, None)
                changed += 1
            continue
        if clean_mode not in PROTECTION_LABELS:
            clean_mode = "font_only"
        if rules.get(base) != clean_mode:
            rules[base] = clean_mode
            changed += 1

    options["protection_rules"] = rules
    await _save_options(interaction, options) if "_save_options" in globals() else await _save_design_options(int(guild.id), options)
    return options, changed


'''

REPLACEMENTS = (
    ('label="Rename Protection"', 'label="Protected Names / Unlock"'),
    ('custom_id="dank_design:protection_manager"', 'custom_id="dank_design:protection_manager"'),
    ('label="Rename Protection", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="dank_design:submenu_protection", row=2)',
     'label="Protected Names / Unlock", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="dank_design:submenu_protection", row=2)'),
    ('"**Rename Protection** = decide what the bot may rename.\n"', '"**Protected Names / Unlock** = decide what the bot may style or skip.\n"'),
    ('"Control which ticket/log/system items are protected and which ones may be styled.\n\n"\n            "Use the Category/Channel Editor to pick an exact item, then set its protection mode."',
     '"Control which ticket/log/system items are protected and which ones may be styled.\n\n"\n            "Use **Allow Font on Defaults** when you want protected names included in font changes without allowing full layout/frame changes."'),
    ('embed.set_footer(text="Protected items do not block Apply. They are safe skips unless overridden.")',
     'embed.set_footer(text="Unsupported font glyphs fall back per character; protection is a rename policy, not a font failure.")'),
)

OLD_VIEW = '''class ProtectionManagerView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Pick Item with Editor", emoji="#️⃣", style=discord.ButtonStyle.primary, custom_id="dank_design:protection_pick_item", row=0)
    async def pick_item(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        if "ChannelEditorPickerView" not in globals():
            return await interaction.response.send_message("Channel Editor is not installed yet.", ephemeral=True)
        await interaction.response.edit_message(
            embed=_channel_editor_embed(guild, page=0),
            view=ChannelEditorPickerView(guild, page=0),
        )

    @discord.ui.button(label="Back to Design Studio", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="dank_design:protection_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=_home_embed(interaction.guild, options), view=DesignHomeView(options))


'''

NEW_VIEW = '''class ProtectionManagerView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Allow Font on Defaults", emoji="🔤", style=discord.ButtonStyle.primary, custom_id="dank_design:protection_allow_font_defaults", row=0)
    async def allow_font_defaults(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options, changed = await _set_default_protection_rules(interaction, mode="font_only")
        embed = _protection_manager_embed(guild, options)
        embed.title = "🔤 Default Protected Names Allow Font"
        embed.add_field(
            name="Updated",
            value=f"**{changed}** default protected name rule(s) now allow font styling while still blocking full layout/frame changes.",
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=ProtectionManagerView())

    @discord.ui.button(label="Restore Default Protection", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_design:protection_restore_defaults", row=0)
    async def restore_defaults(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options, changed = await _set_default_protection_rules(interaction, mode=None)
        embed = _protection_manager_embed(guild, options)
        embed.title = "↩️ Default Protection Restored"
        embed.add_field(name="Updated", value=f"Removed **{changed}** default protected-name override(s).", inline=False)
        await interaction.response.edit_message(embed=embed, view=ProtectionManagerView())

    @discord.ui.button(label="Pick Exact Item", emoji="#️⃣", style=discord.ButtonStyle.primary, custom_id="dank_design:protection_pick_item", row=1)
    async def pick_item(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        if "ChannelEditorPickerView" not in globals():
            return await interaction.response.send_message("Channel Editor is not installed yet.", ephemeral=True)
        await interaction.response.edit_message(
            embed=_channel_editor_embed(guild, page=0),
            view=ChannelEditorPickerView(guild, page=0),
        )

    @discord.ui.button(label="Back to Design Studio", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="dank_design:protection_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=_home_embed(interaction.guild, options), view=DesignHomeView(options))


'''


def main() -> None:
    text = PUBLIC.read_text(encoding="utf-8")

    if INSERT_AFTER in text and "_set_default_protection_rules" not in text:
        text = text.replace(INSERT_AFTER, INSERT_AFTER + HELPERS)
        print("✅ added default protection rule helper")
    elif "_set_default_protection_rules" in text:
        print("✅ default protection rule helper already present")
    else:
        raise SystemExit("Could not find protection save helper insertion point")

    for old, new in REPLACEMENTS:
        text = text.replace(old, new)

    if OLD_VIEW in text:
        text = text.replace(OLD_VIEW, NEW_VIEW)
        print("✅ patched ProtectionManagerView")
    elif NEW_VIEW in text:
        print("✅ ProtectionManagerView already patched")
    else:
        raise SystemExit("Could not find ProtectionManagerView block")

    required = (
        "Protected Names / Unlock",
        "Allow Font on Defaults",
        "Restore Default Protection",
        "_set_default_protection_rules",
        "font styling while still blocking full layout/frame changes",
        "Unsupported font glyphs fall back per character",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit("Missing protection unlock tokens: " + ", ".join(missing))

    PUBLIC.write_text(text, encoding="utf-8")
    print("✅ Dank Design protection unlock controls exposed")


if __name__ == "__main__":
    main()
