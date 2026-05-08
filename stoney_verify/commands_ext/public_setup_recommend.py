from __future__ import annotations

"""Plain-language public /dank setup home.

This module patches the hardened setup flow from public_setup_solid.py into a
simple first-run screen. It deliberately avoids developer/product terms.

Public language rules:
- Say Dank Shield, not Stoney/Stoney Verify.
- Use plain labels: Basic server, Help desk, ID check, Voice check,
  ID + voice check, Custom setup.
- No forced forms by default.
"""

from typing import Any, Optional

import discord

from ..globals import now_utc
from ..guild_config import get_guild_config
from ..setup_new import (
    build_setup_template_embed,
    build_setup_template_select_options,
    get_setup_template,
    setup_template_payload,
)
from . import public_setup_solid as solid

_PATCHED = False


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = solid._cfg_value(cfg, key)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    return default


def _attr_id(cfg: Any, name: str) -> int:
    try:
        return int(_cfg_value(cfg, name, 0) or 0)
    except Exception:
        return 0


def _saved_choice_text(cfg: Any) -> str:
    label = str(_cfg_value(cfg, "setup_choice_label", "") or "").strip()
    key = str(_cfg_value(cfg, "setup_choice", "") or "").strip()
    if label:
        return f"✅ Saved setup choice: **{label}**"
    if key:
        choice = get_setup_template(key)
        if choice is not None:
            return f"✅ Saved setup choice: **{choice.label}**"
    return "⚠️ No setup choice saved yet. Press **Choose Setup Type** first."


async def _setup_progress(guild: discord.Guild) -> tuple[str, int, int, str, Any]:
    done = 0
    total = 0
    lines: list[str] = []
    next_step = "Choose the setup type that best matches this server."

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return (
            f"🚫 Saved setup could not load: `{type(e).__name__}: {str(e)[:180]}`",
            0,
            1,
            "Fix Supabase/config loading first.",
            None,
        )

    def check(label: str, ok: bool, fail_hint: str) -> None:
        nonlocal done, total, next_step
        total += 1
        if ok:
            done += 1
            lines.append(f"✅ {label}")
        else:
            lines.append(f"⚠️ {label}: {fail_hint}")
            if next_step == "Choose the setup type that best matches this server.":
                next_step = fail_hint

    choice_saved = bool(str(_cfg_value(cfg, "setup_choice", "") or "").strip())
    check("Setup type", choice_saved, "Press Choose Setup Type.")

    bot_member = getattr(guild, "me", None)
    bot_perms = getattr(bot_member, "guild_permissions", None)
    check(
        "Bot permissions",
        bool(bot_perms and bot_perms.manage_channels and bot_perms.manage_roles and bot_perms.send_messages),
        "Give the bot Manage Channels, Manage Roles, Send Messages, Embed Links, and Attach Files.",
    )

    check("Ticket staff role", guild.get_role(_attr_id(cfg, "staff_role_id")) is not None, "Use My Existing Server → Ticket Basics → Ticket staff role.")
    check("Open ticket folder", guild.get_channel(_attr_id(cfg, "ticket_category_id")) is not None, "Use My Existing Server → Ticket Basics → Open ticket folder.")
    check("Closed ticket folder", guild.get_channel(_attr_id(cfg, "ticket_archive_category_id")) is not None, "Use My Existing Server → Ticket Basics → Closed ticket folder.")
    check("Transcript channel", guild.get_channel(_attr_id(cfg, "transcripts_channel_id")) is not None, "Use My Existing Server → Ticket Basics → Transcript text channel.")

    style = str(_cfg_value(cfg, "verification_panel_style", "") or "").strip()
    needs_verify = style in {"id_check", "voice_check", "id_voice_check", "custom"}
    if needs_verify:
        check("Verify text channel", guild.get_channel(_attr_id(cfg, "verify_channel_id")) is not None, "Use My Existing Server → Verification Channels → Verify text channel.")
        check("Approved role", guild.get_role(_attr_id(cfg, "verified_role_id")) is not None, "Use My Existing Server → Access Roles → Approved role.")
        check("New/waiting role", guild.get_role(_attr_id(cfg, "unverified_role_id")) is not None, "Use My Existing Server → Access Roles → New/waiting role.")

    check("Modlog channel", guild.get_channel(_attr_id(cfg, "modlog_channel_id")) is not None, "Use My Existing Server → Logs + Status → Modlog channel.")

    try:
        category_load = await solid._category_load(guild)
        total += 1
        if category_load.error:
            lines.append(f"🚫 Ticket menu: database error — {category_load.error[:120]}")
            next_step = "Fix the ticket_categories table/Supabase connection, then press Health Check."
        elif category_load.rows:
            done += 1
            lines.append(f"✅ Ticket menu: {len(category_load.rows)} option(s) configured")
        else:
            lines.append("⚠️ Ticket menu: no options yet")
            if next_step == "Choose the setup type that best matches this server.":
                next_step = "Press Ticket Menu Options → Create Recommended Ticket Menu."
    except Exception:
        lines.append("⚠️ Ticket menu: could not check ticket options")

    if done == total:
        next_step = "Post your ticket panel, then open a test ticket."

    return "\n".join(lines)[:1024], done, total, next_step, cfg


async def _product_main_setup_payload(guild: discord.Guild) -> tuple[discord.Embed, discord.ui.View]:
    progress_text, done, total, next_step, cfg = await _setup_progress(guild)
    saved_choice = _saved_choice_text(cfg) if cfg is not None else "⚠️ Saved setup could not be read."

    embed = discord.Embed(
        title="🚀 Dank Shield Setup",
        description=(
            "Pick what this server needs. You can preview first and change it later.\n\n"
            "Start with **Choose Setup Type**. Then pick your existing roles/channels or let Dank Shield create missing basics.\n\n"
            "No setup choice forces long forms on members by default."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Saved Choice", value=saved_choice, inline=False)
    embed.add_field(name=f"Setup Progress: {done}/{total} complete", value=progress_text or "No setup checks ran.", inline=False)
    embed.add_field(name="Recommended Next Step", value=next_step[:1024], inline=False)
    embed.add_field(
        name="What the buttons mean",
        value=(
            "🧭 **Choose Setup Type** — pick Basic server, Help desk, ID check, Voice check, ID + voice check, or Custom setup.\n"
            "🧩 **Use My Existing Server** — choose the roles/channels/folders you already have.\n"
            "✨ **Create Missing Items** — creates only missing default items. It does not delete anything.\n"
            "🧾 **Ticket Menu Options** — edit the choices users see when opening a ticket."
        )[:1024],
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • /dank setup")
    return embed, ProductSetupHomeView()


class SetupChoiceSelect(discord.ui.Select):
    def __init__(self, selected_key: Optional[str] = None) -> None:
        super().__init__(
            placeholder="Choose what this server needs…",
            min_values=1,
            max_values=1,
            options=build_setup_template_select_options(),
            row=0,
        )
        if selected_key:
            for option in self.options:
                option.default = option.value == selected_key

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        selected = str(self.values[0]) if self.values else ""
        view = self.view
        if isinstance(view, SetupChoiceView):
            view.selected_key = selected
        guild_name = getattr(getattr(interaction, "guild", None), "name", "this server")
        embed = build_setup_template_embed(selected_key=selected, guild_name=str(guild_name or "this server"))
        embed.add_field(
            name="Next",
            value="Press **Use This Setup** to save this choice, or pick another option from the menu.",
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=view)


class SetupChoiceView(solid.BackToSetupView):
    def __init__(self, *, selected_key: Optional[str] = None) -> None:
        super().__init__()
        self.selected_key = selected_key
        self.add_item(SetupChoiceSelect(selected_key=selected_key))

    @discord.ui.button(label="Use This Setup", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank_setup_choice:publish", row=1)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        selected = str(self.selected_key or "").strip()
        choice = get_setup_template(selected)
        if choice is None:
            return await interaction.response.send_message("Pick a setup type from the menu first.", ephemeral=True)

        await solid._safe_defer_update(interaction)
        payload = setup_template_payload(selected)
        payload.update(
            {
                "setup_choice_selected_at": solid._utc_iso(),
                "setup_choice_selected_by_id": str(interaction.user.id),
                "setup_choice_selected_by_name": str(interaction.user),
            }
        )
        await solid._save_config(interaction, payload)

        embed = build_setup_template_embed(selected_key=selected, guild_name=str(guild.name))
        embed.title = "✅ Setup Choice Saved"
        embed.description = (
            f"Saved **{choice.label}** for this server.\n\n"
            "Next, choose your existing roles/channels or create missing basics."
        )
        embed.add_field(
            name="Next step",
            value=(
                "• Press **Use My Existing Server** if your roles/channels already exist.\n"
                "• Press **Create Missing Items** if you want Dank Shield to create missing basics.\n"
                "• Press **Health Check** when you think setup is ready."
            ),
            inline=False,
        )
        await solid._edit_or_followup(interaction, embed=embed, view=ProductSetupHomeView())

    @discord.ui.button(label="Preview Only", emoji="👀", style=discord.ButtonStyle.secondary, custom_id="dank_setup_choice:preview", row=1)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        selected = str(self.selected_key or "").strip()
        guild_name = getattr(getattr(interaction, "guild", None), "name", "this server")
        embed = build_setup_template_embed(selected_key=selected, guild_name=str(guild_name or "this server"))
        embed.add_field(name="Preview only", value="Nothing has been saved yet.", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)


class ProductSetupHomeView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Choose Setup Type", emoji="🧭", style=discord.ButtonStyle.primary, custom_id="dank_setup:choose_type", row=0)
    async def choose_type(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        guild_name = str(getattr(guild, "name", "this server") or "this server")
        selected_key: Optional[str] = None
        try:
            if guild is not None:
                cfg = await get_guild_config(guild.id, refresh=True)
                selected_key = str(_cfg_value(cfg, "setup_choice", "") or "") or None
        except Exception:
            selected_key = None
        embed = build_setup_template_embed(selected_key=selected_key, guild_name=guild_name)
        embed.add_field(
            name="Simple choices",
            value=(
                "Pick the closest option. You can change it later.\n"
                "If you are unsure, choose **Basic server** or **Help desk**."
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=SetupChoiceView(selected_key=selected_key))

    @discord.ui.button(label="Use My Existing Server", emoji="🧩", style=discord.ButtonStyle.secondary, custom_id="dank_setup:existing", row=0)
    async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🧩 Use My Existing Server",
            description=(
                "Use this when you already have roles/channels and want Dank Shield to use them.\n\n"
                "Names do not matter. Dank Shield saves Discord IDs."
            ),
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(
            name="Recommended order",
            value=(
                "1. **Ticket Basics**\n"
                "2. **Access Roles**\n"
                "3. **Verification Channels**\n"
                "4. **Logs + Status**\n"
                "5. Back → Health Check"
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=solid.ChooseExistingView())

    @discord.ui.button(label="Create Missing Items", emoji="✨", style=discord.ButtonStyle.success, custom_id="dank_setup:create_missing", row=1)
    async def create_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        try:
            from . import public_setup_defaults

            await public_setup_defaults._setup_defaults_callback(interaction)
            if interaction.guild is not None:
                created, skipped, error = await solid._seed_recommended_categories(interaction.guild)
                msg = "✅ Missing defaults were handled.\n\n**Next:** run `/dank setup`, press **Health Check**, then post a test ticket panel."
                if error:
                    msg += f"\n\n⚠️ Ticket menu options could not be checked: `{error}`"
                elif created:
                    msg += f"\n\nCreated ticket menu options: {', '.join(f'`{x}`' for x in created)}"
                elif skipped:
                    msg += "\n\nTicket menu options already existed."
                await interaction.followup.send(msg, ephemeral=True)
        except Exception as e:
            msg = f"❌ Create Missing Items failed: `{type(e).__name__}: {str(e)[:250]}`"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="Ticket Menu Options", emoji="🧾", style=discord.ButtonStyle.primary, custom_id="dank_setup:ticket_menu", row=1)
    async def ticket_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed, view = await solid._build_category_manager_payload(guild)
        await solid._edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="Health Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="dank_setup:health", row=2)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed = await solid._build_health_embed(guild)
        embed.add_field(name="What happens next", value="Fix the first blocker listed, then press Back to Setup and run Health Check again.", inline=False)
        await solid._edit_or_followup(interaction, embed=embed, view=solid.BackToSetupView())


def _patch() -> None:
    global _PATCHED
    solid._build_main_setup_payload = _product_main_setup_payload
    _PATCHED = True


_patch()


def register_public_setup_recommend_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _patch()
    print("✅ public_setup_recommend: plain-language /dank setup choices active")


__all__ = ["register_public_setup_recommend_commands"]
