from __future__ import annotations

"""Task-based /dank setup workflow hub.

This guard keeps the existing setup actions/guards, but organizes them into a
clear user workflow so old utility buttons are not mixed together randomly.
"""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_MAIN: Any = None


def _log(message: str) -> None:
    try:
        print(f"🧭 setup_smart_home_menu_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_smart_home_menu_guard {message}")
    except Exception:
        pass


def _bar(done: int, total: int) -> str:
    try:
        total = max(1, int(total or 1))
        done = max(0, min(total, int(done or 0)))
        filled = int(round((done / total) * 10))
        return "█" * filled + "░" * (10 - filled)
    except Exception:
        return "░" * 10


def _compact_progress(progress_text: str) -> str:
    lines: list[str] = []
    for raw in str(progress_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("✅"):
            continue
        lines.append(line)
    return "\n".join(lines[:5]) or "All required setup checks are passing."


def _next_action_text(done: int, total: int, next_step: str) -> str:
    step = str(next_step or "Choose Setup Type").strip()
    try:
        if int(done or 0) >= int(total or 1):
            return "✅ **Ready:** use **Setup Health** after big server changes. Open only the section you actually need."
    except Exception:
        pass
    if "choose setup type" in step.lower():
        return "1. Open **Core Setup**.\n2. Choose the setup type.\n3. Run **Setup Health** from the home screen."
    return f"1. Fix: **{step}**.\n2. Run **Setup Health** again from the home screen."


def _choice_summary(choice_text: str) -> str:
    text = str(choice_text or "").strip()
    if not text:
        return "No setup type chosen yet."
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[:3])[:700]


def _is_owner_interaction(interaction: discord.Interaction) -> bool:
    try:
        return bool(interaction.guild and int(interaction.user.id) == int(interaction.guild.owner_id))
    except Exception:
        return False


def _workflow_embed(title: str, description: str, *, color: discord.Color | None = None) -> discord.Embed:
    from stoney_verify.globals import now_utc

    embed = discord.Embed(title=title, description=description, color=color or discord.Color.blurple(), timestamp=now_utc())
    embed.set_footer(text="/dank setup • workflow hub")
    return embed


async def _edit(interaction: discord.Interaction, *, embed: discord.Embed, view: discord.ui.View) -> None:
    try:
        from stoney_verify.commands_ext import public_setup_fresh_choice as fresh

        await fresh._edit_setup_message(interaction, embed=embed, view=view)
    except Exception:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.edit_message(embed=embed, view=view)


async def _send_error(interaction: discord.Interaction, label: str, exc: BaseException) -> None:
    msg = f"❌ {label}: `{type(exc).__name__}: {str(exc)[:240]}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


async def _require_setup(interaction: discord.Interaction) -> bool:
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        return bool(await solid._require_setup_permission(interaction))
    except Exception:
        return False


async def _open_services(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.startup_guards import setup_service_modes as modes
        from stoney_verify.commands_ext import public_setup_solid as solid

        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        state = await modes.load_service_state(guild.id)
        embed = await modes.build_service_picker_embed(guild, state)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=modes.ServiceModeView(state), ephemeral=True)
        else:
            await interaction.response.edit_message(embed=embed, view=modes.ServiceModeView(state))
    except Exception as exc:
        await _send_error(interaction, "Services failed", exc)


async def _open_permission_repair(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.startup_guards import setup_permission_repair_guard as perm

        helper = getattr(perm, "_open_permission_repair", None)
        if callable(helper):
            return await helper(interaction)
        raise RuntimeError("Permission repair helper unavailable")
    except Exception as exc:
        await _send_error(interaction, "Permission repair failed", exc)


async def _open_protection_center(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify import protection_center_services

        return await protection_center_services.open_protection_center(interaction)
    except Exception as exc:
        await _send_error(interaction, "Protection Center failed", exc)


async def _welcome_health(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_welcome_group as welcome

        return await welcome.open_welcome_health(interaction)
    except Exception as exc:
        await _send_error(interaction, "Welcome health failed", exc)


async def _welcome_preview(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_welcome_group as welcome

        return await welcome.open_welcome_preview(interaction)
    except Exception as exc:
        await _send_error(interaction, "Welcome preview failed", exc)


async def _welcome_post(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_welcome_group as welcome

        return await welcome.post_welcome_message(interaction, channel=None)
    except Exception as exc:
        await _send_error(interaction, "Welcome post failed", exc)


async def _modlog_health(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_modlog_group as modlog

        return await modlog.open_modlog_health(interaction)
    except Exception as exc:
        await _send_error(interaction, "Modlog health failed", exc)


async def _modlog_test(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_modlog_group as modlog

        return await modlog.send_modlog_test(interaction)
    except Exception as exc:
        await _send_error(interaction, "Modlog test failed", exc)


async def _open_embed_builder_center(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify import embed_builder_services

        return await embed_builder_services.open_embed_builder_center(interaction)
    except Exception as exc:
        await _send_error(interaction, "Embed Builder failed", exc)


class SmartSetupHomeView(discord.ui.View):
    def __init__(self, *, ready: bool) -> None:
        super().__init__(timeout=900)
        self.ready = bool(ready)

    @discord.ui.button(label="Setup Health", emoji="🩺", style=discord.ButtonStyle.primary, custom_id="dank_setup_hub:health", row=0)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
        await fresh._open_plain_health(interaction)

    @discord.ui.button(label="Core Setup", emoji="🚀", style=discord.ButtonStyle.success, custom_id="dank_setup_hub:core", row=0)
    async def core(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        embed = _workflow_embed(
            "🚀 Core Setup",
            "Use this section first. It controls setup type, enabled services, and whether Dank Shield creates missing defaults or maps existing roles/channels.",
        )
        embed.add_field(name="Contains", value="🧭 Choose Setup Type\n🧩 Services\n🔗 Use Existing Roles/Channels\n✨ Create Missing Defaults", inline=False)
        await _edit(interaction, embed=embed, view=CoreSetupView())

    @discord.ui.button(label="Safety & Repair", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_hub:safety", row=1)
    async def safety(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        embed = _workflow_embed(
            "🛡️ Safety & Repair",
            "Use this when Setup Health shows permission drift. This section previews and repairs saved setup channel overwrites.",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Protects", value="Unverified visibility, staff/private/log areas, ticket/archive/transcript areas, and saved setup channel overwrites.", inline=False)
        await _edit(interaction, embed=embed, view=SafetyRepairView())

    @discord.ui.button(label="Ticket Setup", emoji="🎫", style=discord.ButtonStyle.secondary, custom_id="dank_setup_hub:tickets", row=1)
    async def tickets(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        embed = _workflow_embed(
            "🎫 Ticket Setup",
            "Use this for public ticket menu choices and ticket-facing options. Channel/permission repair lives in Safety & Repair.",
        )
        embed.add_field(name="Contains", value="🧾 Ticket Menu Choices", inline=False)
        await _edit(interaction, embed=embed, view=TicketSetupView())

    @discord.ui.button(label="Feature Centers", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_setup_hub:features", row=2)
    async def features(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        embed = _workflow_embed(
            "🧩 Feature Centers",
            "This is where the ProBot-style modules live inside setup. Use these after Core Setup so everything stays centralized instead of scattered across random commands.",
        )
        embed.add_field(
            name="Contains",
            value=(
                "🛡️ Protection Center — Automod, Spam Guard, invite/link shield\n"
                "👋 Welcome Center — welcome/start-here message + join/leave flow\n"
                "🎭 Roles Center — pronouns, identity, and self-role panels\n"
                "🧾 Modlog Center — audit/modlog health and test\n"
                "📝 Embed Builder — rules/info/announcement messages\n"
                "🧹 Cleanup + Members — cleanup utilities and member review"
            ),
            inline=False,
        )
        await _edit(interaction, embed=embed, view=FeatureCentersView())

    @discord.ui.button(label="Advanced Tools", emoji="🧰", style=discord.ButtonStyle.secondary, custom_id="dank_setup_hub:advanced", row=3)
    async def advanced(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        embed = _workflow_embed(
            "🧰 Advanced Tools",
            "Use these after the server is basically working. Advanced tools either save detailed options or use their own preview/confirm/undo flow before changing Discord.",
        )
        embed.add_field(name="Contains", value="🔤 Channel Name Fonts\n🛡️ Owner-only protected members\n❓ Help / FAQ", inline=False)
        await _edit(interaction, embed=embed, view=AdvancedSetupView(show_owner_tools=_is_owner_interaction(interaction)))


class CoreSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Choose Setup Type", emoji="🧭", style=discord.ButtonStyle.primary, custom_id="dank_setup_core:choose", row=0)
    async def choose(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
        if not await fresh.solid._require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🧭 Choose Setup Type",
            description="Pick the closest setup style. This saves what the server wants; it does not rename channels or overwrite permissions by itself.",
            color=discord.Color.blurple(),
            timestamp=fresh.now_utc(),
        )
        for choice in fresh.SETUP_CHOICES:
            embed.add_field(name=f"{choice.emoji} {choice.label}", value=f"{choice.short}\nMembers see: {choice.member_sees}", inline=False)
        await interaction.response.edit_message(embed=embed, view=fresh.PlainSetupChoiceView())

    @discord.ui.button(label="Services", emoji="🧩", style=discord.ButtonStyle.secondary, custom_id="dank_setup_core:services", row=0)
    async def services(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_services(interaction)

    @discord.ui.button(label="Use Existing Roles/Channels", emoji="🔗", style=discord.ButtonStyle.secondary, custom_id="dank_setup_core:existing", row=1)
    async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
        await fresh._open_existing_server_setup(interaction)

    @discord.ui.button(label="Create Missing Defaults", emoji="✨", style=discord.ButtonStyle.success, custom_id="dank_setup_core:create", row=1)
    async def create_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
        await fresh._open_create_missing_items(interaction)

    @discord.ui.button(label="Back to Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_core:back", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _back_home(interaction)


class SafetyRepairView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Preview/Fix Permissions", emoji="🛠️", style=discord.ButtonStyle.primary, custom_id="dank_setup_safety:permissions", row=0)
    async def permissions(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_permission_repair(interaction)

    @discord.ui.button(label="Back to Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_safety:back", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _back_home(interaction)


class TicketSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Ticket Menu Choices", emoji="🧾", style=discord.ButtonStyle.primary, custom_id="dank_setup_ticket:menu", row=0)
    async def ticket_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
        await fresh._open_ticket_menu_options(interaction)

    @discord.ui.button(label="Back to Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_ticket:back", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _back_home(interaction)


class FeatureCentersView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Protection Center", emoji="🛡️", style=discord.ButtonStyle.primary, custom_id="dank_setup_features:protection", row=0)
    async def protection(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_protection_center(interaction)

    @discord.ui.button(label="Welcome Center", emoji="👋", style=discord.ButtonStyle.secondary, custom_id="dank_setup_features:welcome", row=0)
    async def welcome(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        embed = _workflow_embed(
            "👋 Welcome Center",
            "Set up the welcome/start-here message and join/leave messaging from one place.",
        )
        embed.add_field(name="Setup path", value="1. Core Setup → Use Existing Roles/Channels → Verification Channels → save welcome/start channel.\n2. Press **Health**.\n3. Press **Preview**.\n4. Press **Post/Update** when ready.", inline=False)
        await _edit(interaction, embed=embed, view=WelcomeCenterView())

    @discord.ui.button(label="Roles Center", emoji="🎭", style=discord.ButtonStyle.secondary, custom_id="dank_setup_features:roles", row=1)
    async def roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        embed = _workflow_embed(
            "🎭 Roles Center",
            "Pronoun, identity, and self-role panels are optional cosmetic role tools. They must never control verification, ticket access, moderation, or staff power.",
        )
        embed.add_field(name="Recommended", value="Use `/dank roles pronouns channel:#your-channel` for pronouns. Use identity roles only if your community explicitly wants that, and keep them optional.", inline=False)
        embed.add_field(name="Safety rule", value="Do not force gender/identity roles before access. Keep them self-serve and skippable.", inline=False)
        await _edit(interaction, embed=embed, view=FeatureBackView())

    @discord.ui.button(label="Modlog Center", emoji="🧾", style=discord.ButtonStyle.secondary, custom_id="dank_setup_features:modlog", row=1)
    async def modlog(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        embed = _workflow_embed(
            "🧾 Modlog Center",
            "Check the saved modlog channel, permissions, and audit listener coverage. Use Core Setup → Use Existing Roles/Channels → Logs + Status to save the channel.",
        )
        await _edit(interaction, embed=embed, view=ModlogCenterView())

    @discord.ui.button(label="Embed Builder", emoji="📝", style=discord.ButtonStyle.secondary, custom_id="dank_setup_features:embed", row=2)
    async def embed_builder(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_embed_builder_center(interaction)

    @discord.ui.button(label="Cleanup + Members", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="dank_setup_features:members", row=2)
    async def cleanup_members(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        embed = _workflow_embed(
            "🧹 Cleanup + Members",
            "Member review, inactivity checks, cleanup tools, and safe purge workflows live here conceptually. These are staff/admin utilities, not setup blockers.",
        )
        embed.add_field(name="Where it lives", value="Use `/dank members` and `/dank cleanup` for now. Setup centralizes the navigation so the product does not feel scattered.", inline=False)
        await _edit(interaction, embed=embed, view=FeatureBackView())

    @discord.ui.button(label="Back to Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_features:back", row=3)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _back_home(interaction)


class WelcomeCenterView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Health", emoji="🩺", style=discord.ButtonStyle.primary, custom_id="dank_setup_welcome:health", row=0)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _welcome_health(interaction)

    @discord.ui.button(label="Preview", emoji="👁️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_welcome:preview", row=0)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _welcome_preview(interaction)

    @discord.ui.button(label="Post/Update", emoji="📌", style=discord.ButtonStyle.success, custom_id="dank_setup_welcome:post", row=0)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _welcome_post(interaction)

    @discord.ui.button(label="Back to Feature Centers", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_welcome:back", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_features_home(interaction)


class ModlogCenterView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Health", emoji="🩺", style=discord.ButtonStyle.primary, custom_id="dank_setup_modlog:health", row=0)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _modlog_health(interaction)

    @discord.ui.button(label="Send Test", emoji="📨", style=discord.ButtonStyle.success, custom_id="dank_setup_modlog:test", row=0)
    async def test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _modlog_test(interaction)

    @discord.ui.button(label="Back to Feature Centers", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_modlog:back", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_features_home(interaction)


class FeatureBackView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Back to Feature Centers", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_feature_back:features", row=0)
    async def features(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_features_home(interaction)

    @discord.ui.button(label="Back to Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_feature_back:home", row=0)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _back_home(interaction)


async def _open_features_home(interaction: discord.Interaction) -> None:
    if not await _require_setup(interaction):
        return
    embed = _workflow_embed(
        "🧩 Feature Centers",
        "This is the centralized home for the ProBot-style modules Dank Shield has added.",
    )
    embed.add_field(name="Sections", value="🛡️ Protection\n👋 Welcome\n🎭 Roles\n🧾 Modlog\n📝 Embeds\n🧹 Cleanup + Members", inline=False)
    await _edit(interaction, embed=embed, view=FeatureCentersView())


class AdvancedSetupView(discord.ui.View):
    def __init__(self, *, show_owner_tools: bool = False) -> None:
        super().__init__(timeout=900)
        try:
            from stoney_verify.startup_guards.setup_channel_font_mode_guard import ChannelFontsButton
            self.add_item(ChannelFontsButton(row=0))
        except Exception:
            pass
        if show_owner_tools:
            try:
                from stoney_verify.startup_guards.owner_safe_members_guard import SafeMembersButton
                self.add_item(SafeMembersButton(row=0))
            except Exception:
                pass

    @discord.ui.button(label="Help / FAQ", emoji="❓", style=discord.ButtonStyle.secondary, custom_id="dank_setup_advanced:help", row=1)
    async def help_faq(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
        if not await fresh.solid._require_setup_permission(interaction):
            return
        await interaction.response.edit_message(embed=fresh._build_setup_help_embed(), view=fresh.solid.BackToSetupView())

    @discord.ui.button(label="Back to Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_advanced:back", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _back_home(interaction)


class SmartSetupToolsView(AdvancedSetupView):
    """Backward-compatible alias for old code paths that still open 'More Options'."""

    pass


async def _back_home(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid
    if not await solid._require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    embed, view = await solid._build_main_setup_payload(interaction.guild)
    await interaction.response.edit_message(embed=embed, view=view)


async def _smart_plain_choice_main_payload(guild: discord.Guild) -> tuple[discord.Embed, discord.ui.View]:
    from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
    from stoney_verify.globals import now_utc

    progress_text, done, total, next_step = await fresh._setup_progress_for_home(guild)
    service_summary, service_hint = await fresh._service_summary_for_home(guild)
    ready = bool(total and done >= total)

    embed = discord.Embed(
        title="🚀 Dank Shield Setup",
        description="Pick one section. No duplicate shortcuts: each setup action lives in exactly one place.",
        color=discord.Color.green() if ready else discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Status", value=(f"`{done}/{total}` complete • `{_bar(done, total)}`\n{_compact_progress(progress_text)}")[:1024], inline=False)
    embed.add_field(name="Next Best Action", value=_next_action_text(done, total, next_step)[:1024], inline=False)
    embed.add_field(name="Selected Setup", value=_choice_summary(service_summary), inline=False)
    embed.add_field(name="Health Focus", value=str(service_hint or "Run Setup Health to confirm selected services and access protections.")[:1024], inline=False)
    embed.add_field(
        name="Workflow",
        value=(
            "🩺 **Setup Health** — only place to run the full setup check.\n"
            "🚀 **Core Setup** — setup type, services, create/use existing defaults.\n"
            "🛡️ **Safety & Repair** — only place to preview/fix permissions.\n"
            "🎫 **Ticket Setup** — ticket menu choices.\n"
            "🧩 **Feature Centers** — Protection, Welcome, Roles, Modlog, Embeds, Cleanup + Members.\n"
            "🧰 **Advanced Tools** — fonts, owner-only protections, help."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • /dank setup • clean workflow hub")
    return embed, SmartSetupHomeView(ready=ready)


def apply() -> bool:
    global _PATCHED, _ORIGINAL_MAIN
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
        from stoney_verify.commands_ext import public_setup_recovery as recovery
        from stoney_verify.commands_ext import public_setup_solid as solid

        _ORIGINAL_MAIN = getattr(fresh, "_plain_choice_main_payload", None)
        fresh._plain_choice_main_payload = _smart_plain_choice_main_payload
        fresh.FreshChoiceHomeView = SmartSetupHomeView
        fresh.FreshServerChoiceView = SmartSetupHomeView
        try:
            recovery._ORIGINAL_BUILD_MAIN = _smart_plain_choice_main_payload
            solid._build_main_setup_payload = recovery._build_main_with_recovery
        except Exception:
            solid._build_main_setup_payload = _smart_plain_choice_main_payload
        _PATCHED = True
        _log("active; /dank setup home centralizes core setup, tickets, safety, and ProBot-style feature centers")
        return True
    except Exception as exc:
        _warn(f"failed: {exc!r}")
        return False


apply()

__all__ = [
    "apply",
    "SmartSetupHomeView",
    "SmartSetupToolsView",
    "CoreSetupView",
    "SafetyRepairView",
    "TicketSetupView",
    "FeatureCentersView",
    "WelcomeCenterView",
    "ModlogCenterView",
    "AdvancedSetupView",
]
