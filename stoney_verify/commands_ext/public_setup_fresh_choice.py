from __future__ import annotations

"""Fresh-server setup choice polish.

A brand-new server does not always mean the owner wants Dank Shield's default
layout. This module replaces the vague first setup screen with clear paths:

1. Services
   - Pick Tickets-only, Verification-only, SpamGuard-only, or combinations.
   - Health Check focuses only on selected services.
   - SpamGuard setup shows selected-service state separately from actual guard state.

2. Fresh Server
   - Dank Shield creates a specific starter layout.
   - The screen shows exactly what may be created before the owner confirms.

3. Existing Server
   - Dank Shield creates nothing.
   - The owner maps their current Discord structure with dropdowns.

The patch is loaded after the main setup/recovery modules and updates the
builder used by the Recovery button wrapper so the first setup screen keeps all
recovery/start-over controls.
"""

from typing import Any

import discord

from ..globals import now_utc
from . import public_setup_recommend as recommend
from . import public_setup_recovery as recovery
from . import public_setup_solid as solid

_PATCHED = False

AUTO_BUILD_ROLES = ("Bot Manager", "Support Team", "Unverified", "Verified", "Member")
AUTO_BUILD_CATEGORIES = ("👋 START HERE", "🎫 ACTIVE TICKETS", "📦 TICKET ARCHIVE", "🛠️ STAFF TOOLS")
AUTO_BUILD_TEXT_CHANNELS = (
    "👋・welcome",
    "✅・verify",
    "🎫・support",
    "🎙️・vc-verify-queue",
    "📑・transcripts",
    "🛡️・mod-log",
    "🚪・join-leave-log",
    "📡・bot-status",
)
AUTO_BUILD_VOICE_CHANNELS = ("🎙️ Voice Verification",)
AUTO_BUILD_TICKET_MENU = ("Support", "Verification Help", "Appeal", "Report User", "Question", "Bug Report", "Other")


def _list_lines(items: tuple[str, ...]) -> str:
    return "\n".join(f"• `{item}`" for item in items)


def _exact_auto_build_list() -> str:
    return (
        "**Roles**\n"
        f"{_list_lines(AUTO_BUILD_ROLES)}\n\n"
        "**Discord category folders**\n"
        f"{_list_lines(AUTO_BUILD_CATEGORIES)}\n\n"
        "**Text channels**\n"
        f"{_list_lines(AUTO_BUILD_TEXT_CHANNELS)}\n\n"
        "**Voice channels**\n"
        f"{_list_lines(AUTO_BUILD_VOICE_CHANNELS)}\n\n"
        "**Ticket menu options**\n"
        f"{_list_lines(AUTO_BUILD_TICKET_MENU)}"
    )


async def _progress_for_home(guild: discord.Guild) -> tuple[str, int, int, str]:
    try:
        return await recommend._setup_progress(guild)
    except Exception as e:
        return f"🚫 Setup progress failed: `{type(e).__name__}: {str(e)[:180]}`", 0, 1, "Run Health Check or check boot logs."


async def _service_state_for_home(guild: discord.Guild) -> tuple[str, str, bool]:
    try:
        from stoney_verify.startup_guards import setup_service_modes

        state = await setup_service_modes.load_service_state(guild.id)
        summary = setup_service_modes._service_summary_text(state)  # type: ignore[attr-defined]
        hint = setup_service_modes._service_mode_hint(state)  # type: ignore[attr-defined]
        spam_on = bool(getattr(state, "spamguard", False) or getattr(state, "moderation", False))
        return summary, hint, spam_on
    except Exception:
        return (
            "✅ Tickets\n⬜ ID verification\n⬜ Voice verification\n⬜ SpamGuard service\n⬜ Moderation/logging",
            "Health Check will focus on: Ticket Basics.",
            False,
        )


async def _open_service_picker(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

    try:
        from stoney_verify.startup_guards import setup_service_modes

        state = await setup_service_modes.load_service_state(guild.id)
        embed = await setup_service_modes.build_service_picker_embed(guild, state)
        view = setup_service_modes.ServiceModeView(state)  # type: ignore[attr-defined]
        await interaction.response.edit_message(embed=embed, view=view)
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Service picker failed to open: `{type(e).__name__}: {str(e)[:250]}`",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def _fresh_choice_main_payload(guild: discord.Guild) -> tuple[discord.Embed, discord.ui.View]:
    progress_text, done, total, next_step = await _progress_for_home(guild)
    service_summary, service_hint, spam_on = await _service_state_for_home(guild)
    embed = discord.Embed(
        title="🚀 Dank Shield Setup",
        description=(
            "Pick **one path**. You do not need to know any setup commands.\n\n"
            "🧭 **Services** — choose Tickets-only, Verification-only, SpamGuard-only, or any combo.\n"
            "🟢 **Fresh Server** — choose auto-build or build everything yourself.\n"
            "🔵 **Existing Server** — map your current roles/channels with dropdowns.\n"
            "⚙️ **Advanced Setup** — fine-tune ticket menu options, logs, status, and checks."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Selected Services", value=service_summary, inline=True)
    embed.add_field(name="Health Check Focus", value=service_hint[:1024], inline=False)
    if spam_on:
        embed.add_field(
            name="SpamGuard Setup",
            value="Press **Services**, then **SpamGuard Setup**. It shows selected-service state separately from actual guard state.",
            inline=False,
        )
    embed.add_field(name=f"Setup Progress: {done}/{total} complete", value=progress_text or "No setup checks ran.", inline=False)
    embed.add_field(name="Recommended Next Step", value=next_step[:1024], inline=False)
    embed.set_footer(text=f"Guild {guild.id} • start here every time: /dank setup")
    return embed, FreshChoiceHomeView()


class FreshChoiceHomeView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Services", emoji="🧭", style=discord.ButtonStyle.primary, custom_id="stoney_fresh_choice:services", row=0)
    async def services(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_service_picker(interaction)

    @discord.ui.button(label="Fresh Server", emoji="🟢", style=discord.ButtonStyle.success, custom_id="stoney_fresh_choice:fresh", row=0)
    async def fresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🟢 Fresh Server Setup",
            description=(
                "Use this when the server is new or not configured yet.\n\n"
                "A new server can go two ways: let Dank Shield auto-build a starter layout, or build your own layout and only map it to Dank Shield."
            ),
            color=discord.Color.green(),
            timestamp=now_utc(),
        )
        embed.add_field(
            name="✨ Auto-Build Recommended Layout",
            value=(
                "Dank Shield creates missing recommended roles, Discord category folders, channels, and ticket menu options.\n"
                "Choose this if you want the fastest working setup."
            ),
            inline=False,
        )
        embed.add_field(
            name="🛠️ Build It Myself",
            value=(
                "Dank Shield creates nothing. You create your own Discord roles/channels/categories, then map them with dropdowns.\n"
                "Choose this if you want full customization from the beginning."
            ),
            inline=False,
        )
        embed.add_field(
            name="Safety",
            value=(
                "Auto-build only creates missing Dank Shield starter items. It will **not** delete, rename, or overwrite channels, tickets, roles, "
                "categories, messages, or transcripts that were not made by Dank Shield."
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=FreshServerChoiceView())

    @discord.ui.button(label="Existing Server", emoji="🔵", style=discord.ButtonStyle.primary, custom_id="stoney_fresh_choice:existing", row=0)
    async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🔵 Existing Server Setup",
            description=(
                "Use this when you already have channels/roles and want Dank Shield to use them.\n\n"
                "Pick each section below. The bot validates permissions before saving."
            ),
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(
            name="Recommended order",
            value=(
                "1. **Services**\n"
                "2. **Ticket Basics**\n"
                "3. **Verification Roles**\n"
                "4. **Verification Channels**\n"
                "5. **Logs + Status**\n"
                "6. Back → Health Check"
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=solid.ChooseExistingView())

    @discord.ui.button(label="Advanced Setup", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id="stoney_fresh_choice:advanced", row=1)
    async def advanced(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="⚙️ Advanced Setup",
            description="Use these only when you want to fine-tune setup details.",
            color=discord.Color.gold(),
            timestamp=now_utc(),
        )
        embed.add_field(
            name="Plain-English map",
            value=(
                "🧭 **Services** = choose which parts of Dank Shield this server uses.\n"
                "🎫 **Ticket Basics** = actual Discord category folders/channels/roles.\n"
                "🧾 **Ticket Menu Options** = choices users see when opening a ticket.\n"
                "🛡️ **SpamGuard Setup** = selected-service vs actual-active guard state.\n"
                "📌 **Status Channel** = where the bot posts heartbeat/setup status."
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=recommend.AdvancedSetupView())

    @discord.ui.button(label="Health Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="stoney_fresh_choice:health", row=1)
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


class FreshServerChoiceView(solid.BackToSetupView):
    @discord.ui.button(label="Auto-Build Recommended Layout", emoji="✨", style=discord.ButtonStyle.success, custom_id="stoney_fresh_choice:auto_build", row=0)
    async def auto_build(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="✨ Auto-Build Recommended Layout",
            description=(
                "This creates Dank Shield's starter layout for a new server.\n\n"
                "It only creates missing items from the list below. If something already exists, Dank Shield should reuse/skip instead of duplicating where the setup helper supports that."
            ),
            color=discord.Color.green(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Exact Auto-Build List", value=_exact_auto_build_list()[:1024], inline=False)
        embed.add_field(
            name="Safety",
            value=(
                "This will **not** delete, rename, overwrite, or move channels, tickets, roles, categories, messages, or transcripts "
                "that were not made by Dank Shield. If you dislike the generated layout, use Recovery / Start Over → Full Start Over + Remove Bot Items."
            ),
            inline=False,
        )
        embed.add_field(name="Confirm", value="Press **Create This Recommended Layout** only if you want Dank Shield to create the starter layout above.", inline=False)
        await interaction.response.edit_message(embed=embed, view=AutoBuildConfirmView())

    @discord.ui.button(label="Build It Myself", emoji="🛠️", style=discord.ButtonStyle.primary, custom_id="stoney_fresh_choice:manual", row=0)
    async def manual(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🛠️ Build It Myself",
            description=(
                "Use this when the server is new, but you **do not** want Dank Shield's default channel/role names.\n\n"
                "Dank Shield creates nothing here. You make your own Discord structure first, then map it with dropdowns."
            ),
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(
            name="Recommended manual order",
            value=(
                "1. Pick Services.\n"
                "2. Create your own Discord roles/channels/categories.\n"
                "3. Press **Map My Existing Items**.\n"
                "4. Configure Ticket Basics, Verification Roles, Verification Channels, and Logs + Status.\n"
                "5. Customize Ticket Menu Options if needed.\n"
                "6. Run Health Check."
            ),
            inline=False,
        )
        embed.add_field(
            name="Safety",
            value="This path does not create or delete Discord channels, roles, tickets, categories, messages, or transcripts.",
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=ManualFreshSetupView())


class AutoBuildConfirmView(solid.BackToSetupView):
    @discord.ui.button(label="Create This Recommended Layout", emoji="✨", style=discord.ButtonStyle.success, custom_id="stoney_fresh_choice:create_recommended", row=0)
    async def create_recommended(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        try:
            from . import public_setup_defaults

            await public_setup_defaults._setup_defaults_callback(interaction)
            if interaction.guild is not None:
                created, skipped, error = await solid._seed_recommended_categories(interaction.guild)
                msg = (
                    "✅ Recommended layout was handled.\n\n"
                    "**Next:** run `/dank setup`, press **Health Check**, then post the ticket panel with `/ticket-panel post`."
                )
                if error:
                    msg += f"\n\n⚠️ Ticket menu options could not be checked: `{error}`"
                elif created:
                    msg += f"\n\nCreated ticket menu options: {', '.join(f'`{x}`' for x in created)}"
                elif skipped:
                    msg += "\n\nTicket menu options already existed."
                await interaction.followup.send(msg, ephemeral=True)
        except Exception as e:
            msg = f"❌ Auto-build failed: `{type(e).__name__}: {str(e)[:250]}`"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="Build It Myself Instead", emoji="🛠️", style=discord.ButtonStyle.secondary, custom_id="stoney_fresh_choice:manual_from_confirm", row=1)
    async def manual_instead(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await FreshServerChoiceView.manual.callback(FreshServerChoiceView(), interaction, button)  # type: ignore[misc]


class ManualFreshSetupView(solid.BackToSetupView):
    @discord.ui.button(label="Map My Existing Items", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="stoney_fresh_choice:map_existing", row=0)
    async def map_existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🧩 Map My Existing Items",
            description="Pick your own channels/roles/categories with dropdowns. Dank Shield validates permissions before saving.",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(
            name="Sections",
            value=(
                "🎫 **Ticket Basics** — open/archive Discord categories, staff role, transcripts\n"
                "✅ **Verification Roles** — Unverified, Verified, Member\n"
                "🎙️ **Verification Channels** — verify text, support panel, VC verify, VC queue\n"
                "🧾 **Logs + Status** — modlog, join/leave log, bot status"
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=solid.ChooseExistingView())

    @discord.ui.button(label="Customize Ticket Menu", emoji="🧾", style=discord.ButtonStyle.secondary, custom_id="stoney_fresh_choice:ticket_menu", row=1)
    async def ticket_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed, view = await recommend._better_category_manager_payload(guild, title="🧾 Ticket Menu Options")
        await solid._edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="Run Health Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="stoney_fresh_choice:manual_health", row=1)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed = await solid._build_health_embed(guild)
        await solid._edit_or_followup(interaction, embed=embed, view=solid.BackToSetupView())


def _patch() -> None:
    global _PATCHED
    # Recovery wraps whatever builder is stored here. Update that builder so the
    # main /dank setup screen keeps the recovery button while using the clearer
    # setup choice flow.
    try:
        recovery._ORIGINAL_BUILD_MAIN = _fresh_choice_main_payload
        solid._build_main_setup_payload = recovery._build_main_with_recovery
    except Exception:
        solid._build_main_setup_payload = _fresh_choice_main_payload
    _PATCHED = True


_patch()


def register_public_setup_fresh_choice_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _patch()
    print("✅ public_setup_fresh_choice: services + clear setup paths active")


__all__ = ["register_public_setup_fresh_choice_commands"]
