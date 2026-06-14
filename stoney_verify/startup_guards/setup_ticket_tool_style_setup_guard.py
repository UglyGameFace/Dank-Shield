from __future__ import annotations

"""Make /dank setup match the canonical public setup policy.

This keeps the first setup screen Ticket Tool-style:
- normal public guilds only see choices they can actually use
- ID/web upload verification is hidden unless the guild ID is allowlisted
- choosing an ID-only option in a non-allowlisted guild is blocked
- setup help text no longer advertises Stoney-style ID verification everywhere
"""

from typing import Any, Optional

import discord

_PATCHED = False


def _id_allowed(guild: Optional[discord.Guild]) -> bool:
    try:
        from stoney_verify.setup_engine.verification_modes import id_verify_allowed_for_guild

        return bool(guild and id_verify_allowed_for_guild(guild))
    except Exception:
        return False


def _choices_for(mod: Any, guild: Optional[discord.Guild]) -> tuple[Any, ...]:
    choices = tuple(getattr(mod, "SETUP_CHOICES", ()) or ())
    if _id_allowed(guild):
        return choices
    return tuple(choice for choice in choices if not bool(getattr(choice, "needs_id", False)))


def _choice_by_key(mod: Any, key: str) -> Optional[Any]:
    for choice in tuple(getattr(mod, "SETUP_CHOICES", ()) or ()):
        if str(getattr(choice, "key", "")) == str(key):
            return choice
    return None


async def _save_choice_or_block(mod: Any, interaction: discord.Interaction, choice: Any) -> None:
    if not await mod.solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    if bool(getattr(choice, "needs_id", False)) and not _id_allowed(guild):
        return await interaction.response.send_message(
            "🔒 This server uses Basic Button Verification. ID/web verification is only available for allowlisted guild IDs.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    await mod.solid._safe_defer_update(interaction)
    await mod._save_choice(interaction, choice)
    await mod.solid._edit_or_followup(interaction, embed=mod._choice_preview_embed(guild, choice), view=mod.AfterChoiceView())


def _choice_button(mod: Any, choice: Any, *, row: int) -> discord.ui.Button:
    style = discord.ButtonStyle.secondary if str(getattr(choice, "key", "")) == "custom_setup" else discord.ButtonStyle.primary
    button = discord.ui.Button(
        label=str(getattr(choice, "label", "Setup"))[:80],
        emoji=getattr(choice, "emoji", None),
        style=style,
        custom_id=f"dank_setup_choice:scoped:{getattr(choice, 'key', row)}",
        row=row,
    )

    async def callback(interaction: discord.Interaction, selected: Any = choice) -> None:
        await _save_choice_or_block(mod, interaction, selected)

    button.callback = callback  # type: ignore[assignment]
    return button


def _choice_view(mod: Any, guild: Optional[discord.Guild]) -> discord.ui.View:
    view = mod.solid.BackToSetupView()
    for index, choice in enumerate(_choices_for(mod, guild)):
        view.add_item(_choice_button(mod, choice, row=min(3, index // 2)))
    return view


def _setup_help_embed(mod: Any, guild: Optional[discord.Guild] = None) -> discord.Embed:
    embed = discord.Embed(
        title="❓ Dank Shield Setup Help",
        description="Simple answers for the setup screen.",
        color=discord.Color.blurple(),
        timestamp=mod.now_utc(),
    )
    embed.add_field(name="What should I press first?", value="Press **Choose Setup Type**, then **Use My Existing Server** if your roles/channels already exist.", inline=False)
    embed.add_field(name="What if my server already has roles/channels?", value="Use the Discord pickers. Names do not matter; Dank Shield saves IDs per server.", inline=False)
    embed.add_field(name="Do members have to fill out forms?", value="No. Tickets open fast by default. Forms stay off unless you turn them on later.", inline=False)
    if _id_allowed(guild):
        embed.add_field(name="Can this server use ID/web verification?", value="Yes. This guild ID is allowlisted, so ID/web upload choices can appear in setup.", inline=False)
    else:
        embed.add_field(name="Where is ID/web verification?", value="Hidden. This guild uses Basic Button Verification. ID/web upload verification only appears for allowlisted guild IDs.", inline=False)
    embed.add_field(name="Will this affect other servers?", value="No. Every setup choice is stored per guild. No cross-server channel or role IDs are reused.", inline=False)
    return embed


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_fresh_choice as mod
    except Exception:
        return False

    original_main_payload = getattr(mod, "_plain_choice_main_payload", None)
    original_existing = getattr(mod, "_open_existing_server_setup", None)
    original_create_missing = getattr(mod, "_open_create_missing_items", None)
    original_ticket_menu = getattr(mod, "_open_ticket_menu_options", None)
    original_health = getattr(mod, "_open_plain_health", None)
    if not callable(original_main_payload):
        return False

    async def scoped_main_payload(guild: discord.Guild):
        embed, _old_view = await original_main_payload(guild)
        try:
            lines = "\n".join(f"{c.emoji} **{c.label}** — {c.short}" for c in _choices_for(mod, guild))
            if not _id_allowed(guild):
                lines += "\n\n🔒 **ID/web verification hidden** — this guild uses Basic Button Verification."
            embed.set_field_at(0, name="Setup Choices", value=lines[:1024], inline=False)
        except Exception:
            pass
        return embed, ScopedHomeView()

    class ScopedHomeView(discord.ui.View):
        def __init__(self) -> None:
            super().__init__(timeout=900)

        @discord.ui.button(label="Choose Setup Type", emoji="🧭", style=discord.ButtonStyle.primary, custom_id="dank_setup_plain_scoped:choose", row=0)
        async def choose(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            if not await mod.solid._require_setup_permission(interaction):
                return
            guild = interaction.guild
            if guild is None:
                return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
            embed = discord.Embed(title="🧭 Choose Setup Type", description="Pick what this server actually needs. Only available choices are shown.", color=discord.Color.blurple(), timestamp=mod.now_utc())
            for choice in _choices_for(mod, guild):
                embed.add_field(name=f"{choice.emoji} {choice.label}", value=f"{choice.short}\nMembers see: {choice.member_sees}", inline=False)
            if not _id_allowed(guild):
                embed.add_field(name="🔒 ID/web verification hidden", value="This guild uses Basic Button Verification. ID/web upload choices are only available for allowlisted guild IDs.", inline=False)
            await interaction.response.edit_message(embed=embed, view=_choice_view(mod, guild))

        @discord.ui.button(label="Use My Existing Server", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_setup_plain_scoped:existing", row=0)
        async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            if callable(original_existing):
                await original_existing(interaction)

        @discord.ui.button(label="Create Missing Items", emoji="✨", style=discord.ButtonStyle.success, custom_id="dank_setup_plain_scoped:create_missing", row=0)
        async def create_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            if callable(original_create_missing):
                await original_create_missing(interaction)

        @discord.ui.button(label="Ticket Menu Options", emoji="🧾", style=discord.ButtonStyle.secondary, custom_id="dank_setup_plain_scoped:ticket_menu", row=1)
        async def ticket_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            if callable(original_ticket_menu):
                await original_ticket_menu(interaction)

        @discord.ui.button(label="Setup Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="dank_setup_plain_scoped:health", row=1)
        async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            if callable(original_health):
                await original_health(interaction)

        @discord.ui.button(label="Help / FAQ", emoji="❓", style=discord.ButtonStyle.secondary, custom_id="dank_setup_plain_scoped:help", row=1)
        async def help_faq(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            if not await mod.solid._require_setup_permission(interaction):
                return
            await interaction.response.edit_message(embed=_setup_help_embed(mod, interaction.guild), view=mod.solid.BackToSetupView())

    def scoped_help_embed() -> discord.Embed:
        return _setup_help_embed(mod, None)

    mod._plain_choice_main_payload = scoped_main_payload  # type: ignore[assignment]
    mod._build_setup_help_embed = scoped_help_embed  # type: ignore[assignment]
    _PATCHED = True
    try:
        print("✅ setup_ticket_tool_style_setup_guard active; setup choices are scoped and ID verify is allowlist-only")
    except Exception:
        pass
    return True


apply()

__all__ = ["apply"]
