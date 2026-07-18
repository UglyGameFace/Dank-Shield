from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
RECOMMEND = ROOT / "stoney_verify/commands_ext/public_setup_recommend.py"
FRESH = ROOT / "stoney_verify/commands_ext/public_setup_fresh_choice.py"
TEST = ROOT / "tests/test_setup_single_path_front_door_v2_static.py"


def replace_block(text: str, start: str, end: str, replacement: str) -> str:
    left = text.index(start)
    right = text.index(end, left)
    return text[:left] + replacement.rstrip() + "\n\n" + text[right:]


recommend = RECOMMEND.read_text(encoding="utf-8")

recommend = recommend.replace(
    'description="One screen. One next step. Everything else is tucked under Manage Setup.",',
    'description="One clear next step. Extra tools stay out of the way under More Options.",',
)
recommend = recommend.replace(
    'recommended = "Press **Start / Continue Setup** and choose the setup type."',
    'recommended = "Press **Start Setup** and choose what this server needs."',
)
recommend = recommend.replace(
    'recommended = "Press **Test / Launch** and test with an alt account."',
    'recommended = "Press **Test & Launch** and test with an alt account."',
)
recommend = recommend.replace(
    'recommended = str(next_step or "Press Start / Continue Setup.")[:350]',
    'recommended = str(next_step or "Press Continue Setup.")[:350]',
)
recommend = recommend.replace(
    'value="\\n".join(issues)[:900] if issues else "✅ No required setup problem shown here. Run **Setup Check** for the full truth check.",',
    'value="\\n".join(issues)[:900] if issues else "✅ No required setup problem is blocking the guided path.",',
)
recommend = recommend.replace(
    'label="Fix Next Item",',
    'label="Fix Next Problem",',
    1,
)
recommend = recommend.replace(
    'label="Test / Launch",',
    'label="Test & Launch",',
    1,
)

review_start = recommend.index("class SetupReviewView(discord.ui.View):")
review_end = recommend.index("class SetupHealthHelpView", review_start)
recommend = recommend[:review_start] + '''class SetupReviewView(discord.ui.View):
    """After Setup Check, show only the next correct action and Home."""

    def __init__(self, *, ready: bool) -> None:
        super().__init__(timeout=900)

        if ready:
            self.add_item(SetupReviewLaunchButton())
        else:
            self.add_item(SetupReviewFixNextButton())

        self.add_item(SetupReviewHomeButton())


''' + recommend[review_end:]

recommend = replace_block(
    recommend,
    "class ProductSetupHomeView(discord.ui.View):",
    "class ContinueSetupView(discord.ui.View):",
    '''class ProductSetupHomeView(discord.ui.View):
    """Setup home with one primary action and one secondary escape hatch."""

    def __init__(
        self,
        *,
        ready: bool = False,
        started: bool = False,
    ) -> None:
        super().__init__(timeout=900)
        self.ready = bool(ready)
        self.started = bool(started)

        try:
            self.continue_setup.label = (
                "Test & Launch"
                if self.ready
                else "Continue Setup"
                if self.started
                else "Start Setup"
            )
        except Exception:
            pass

    @discord.ui.button(
        label="Start Setup",
        emoji="▶️",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_home:continue",
        row=0,
    )
    async def continue_setup(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.ready:
            await _open_test_launch(interaction)
            return
        if self.started:
            await _open_guided_setup(interaction)
            return
        await _open_choose_setup_type(interaction)

    @discord.ui.button(
        label="More Options",
        emoji="•••",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_home:more_options",
        row=1,
    )
    async def more_options(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _open_manage_setup(interaction)
''',
)

recommend = replace_block(
    recommend,
    "class ContinueSetupView(discord.ui.View):",
    "class ManageSetupView(discord.ui.View):",
    '''class ContinueSetupView(discord.ui.View):
    """The normal setup path: fix the current step or go home."""

    def __init__(
        self,
        *,
        target: str,
        requirement_key: str = "",
        ready: bool,
    ) -> None:
        super().__init__(timeout=900)
        self.target = str(target)
        self.requirement_key = str(requirement_key or "")
        self.ready = bool(ready)

    @discord.ui.button(
        label="Fix This Step",
        emoji="➡️",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_guided:fix_next",
        row=0,
    )
    async def fix_next(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _open_guided_target(
            interaction,
            self.target,
            self.requirement_key,
        )

    @discord.ui.button(
        label="Back Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_guided:home",
        row=1,
    )
    async def home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _home_edit(interaction)
''',
)

recommend = replace_block(
    recommend,
    "class ManageSetupView(discord.ui.View):",
    "class AdvancedCoreSetupView(discord.ui.View):",
    '''class ManageSetupView(discord.ui.View):
    """Secondary tools kept out of the normal guided setup path."""

    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Change Setup Type",
        emoji="🧭",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_more:change_type",
        row=0,
    )
    async def change_type(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_choose_setup_type(interaction)

    @discord.ui.button(
        label="Advanced Settings",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_more:advanced",
        row=0,
    )
    async def advanced_settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_advanced_settings(interaction)

    @discord.ui.button(
        label="Setup Check / Diagnostics",
        emoji="🩺",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_more:health",
        row=1,
    )
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_health_check(interaction)

    @discord.ui.button(
        label="Reset / Recovery",
        emoji="🧯",
        style=discord.ButtonStyle.danger,
        custom_id="dank_setup_more:recovery",
        row=1,
    )
    async def recovery(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_advanced_danger_zone(interaction)

    @discord.ui.button(
        label="Help",
        emoji="❓",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_more:help",
        row=2,
    )
    async def help_faq(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.edit_message(
            embed=_build_setup_help_embed(),
            view=ManageSetupView(),
        )

    @discord.ui.button(
        label="Back Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_more:home",
        row=2,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _home_edit(interaction)


class AdvancedSettingsHubView(discord.ui.View):
    """Literal, task-based advanced setting groups."""

    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Features, Roles & Channels",
        emoji="🧩",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_advanced_hub:core",
        row=0,
    )
    async def core(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_advanced_core_setup(interaction)

    @discord.ui.button(
        label="Tickets",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_advanced_hub:tickets",
        row=0,
    )
    async def tickets(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_advanced_member_experience(interaction)

    @discord.ui.button(
        label="Logs, Protection & Repairs",
        emoji="🛡️",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_advanced_hub:safety",
        row=1,
    )
    async def safety(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_advanced_monitoring_repair(interaction)

    @discord.ui.button(
        label="Server Design",
        emoji="🎨",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_advanced_hub:design",
        row=1,
    )
    async def design(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_advanced_appearance(interaction)

    @discord.ui.button(
        label="Back to More Options",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_advanced_hub:back",
        row=2,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_manage_setup(interaction)

    @discord.ui.button(
        label="Back Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_advanced_hub:home",
        row=2,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _home_edit(interaction)
''',
)

# Keep future steps out of the current-step screen.
recommend, count = re.subn(
    r'\n    remaining = \[.*?\n    embed\.set_footer\(',
    '\n    embed.set_footer(',
    recommend,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f"guided future-step block replacement count={count}")

advanced_functions = '''async def _open_advanced_core_setup(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🧩 Features, Roles & Channels",
        description="Change enabled features, timers, and saved role/channel mappings.",
        items=(
            "🧩 **Features On / Off** — choose which services run.",
            "⏱️ **Timers & Behavior** — timers, naming, and flow settings.",
            "🧭 **Detailed Role / Channel Mapping** — deliberately remap saved items.",
        ),
        view=AdvancedCoreSetupView(),
    )


async def _open_advanced_member_experience(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🎫 Tickets",
        description="Edit the choices members can select when they open a ticket.",
        items=(
            "🧾 **Ticket Choices** — edit what members can request.",
        ),
        view=AdvancedMemberExperienceView(),
    )


async def _open_advanced_monitoring_repair(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🛡️ Logs, Protection & Repairs",
        description="Manage logging, protection tools, and permission repair.",
        items=(
            "🧾 **Modlog Tracking** — choose which server events are recorded.",
            "🛡️ **Protection** — open the Protection Center.",
            "🛠️ **Permission Repair** — preview and repair saved setup channel permissions.",
        ),
        view=AdvancedMonitoringRepairView(),
    )


async def _open_advanced_appearance(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🎨 Server Design",
        description="Open the server design, preview, and rollback tools.",
        items=(
            "🎨 **Server Design** — fonts, frames, emojis, preview, and rollback.",
        ),
        view=AdvancedAppearanceView(),
    )


async def _open_advanced_danger_zone(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🧯 Reset / Recovery",
        description="Use this only when you deliberately want to recover or start setup over.",
        items=(
            "🧯 **Recovery / Start Over** — safely reset or recover setup.",
        ),
        view=AdvancedDangerZoneView(),
        danger=True,
    )
'''
recommend = replace_block(
    recommend,
    "async def _open_advanced_core_setup(",
    "async def _open_manage_setup(",
    advanced_functions,
)

manage_functions = '''async def _open_advanced_settings(
    interaction: discord.Interaction,
) -> None:
    if not await solid._require_setup_permission(interaction):
        return

    embed = discord.Embed(
        title="⚙️ Advanced Settings",
        description="These are optional settings. They are not part of the normal guided setup path.",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="🧩 Features, Roles & Channels", value="Feature switches, timers, and detailed saved-item mapping.", inline=False)
    embed.add_field(name="🎫 Tickets", value="Ticket choices shown to members.", inline=False)
    embed.add_field(name="🛡️ Logs, Protection & Repairs", value="Modlog tracking, Protection Center, and permission repair.", inline=False)
    embed.add_field(name="🎨 Server Design", value="Visual design, preview, and rollback tools.", inline=False)

    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=AdvancedSettingsHubView(),
    )


async def _open_manage_setup(
    interaction: discord.Interaction,
) -> None:
    """Open secondary setup tools without interrupting the guided path."""

    if not await solid._require_setup_permission(interaction):
        return

    embed = discord.Embed(
        title="••• More Options",
        description=(
            "Normal setup is done from **Back Home → Start / Continue Setup**. "
            "Use this screen only when you intentionally need one of these extra tools."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="🧭 Change Setup Type", value="Switch the kind of setup this server uses.", inline=False)
    embed.add_field(name="⚙️ Advanced Settings", value="Edit optional feature, ticket, logging, protection, mapping, or design settings.", inline=False)
    embed.add_field(name="🩺 Setup Check / Diagnostics", value="Run the full setup truth check manually.", inline=False)
    embed.add_field(name="🧯 Reset / Recovery", value="Repair or deliberately start setup over.", inline=False)

    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=ManageSetupView(),
    )
'''
recommend = replace_block(
    recommend,
    "async def _open_manage_setup(",
    "def _setup_bool(",
    manage_functions,
)

recommend = recommend.replace(
    '"Advanced Options • Back to Advanced returns to the grouped menu"',
    '"Advanced Settings • use Back to Advanced Settings to return"',
)

# Replace the four advanced submenu classes while keeping their canonical class names.
recommend = replace_block(
    recommend,
    "class AdvancedCoreSetupView(discord.ui.View):",
    "class AdvancedMemberExperienceView(discord.ui.View):",
    '''class AdvancedCoreSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Features On / Off", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_setup_advanced_core:services", row=0)
    async def services(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_services(interaction)

    @discord.ui.button(label="Timers & Behavior", emoji="⏱️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_advanced_core:timers_behavior", row=0)
    async def timers_behavior(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_timers_behavior(interaction)

    @discord.ui.button(label="Detailed Role / Channel Mapping", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank_setup_advanced_core:existing", row=1)
    async def detailed_mapping(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_existing_server(interaction)

    @discord.ui.button(label="Back to Advanced Settings", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_advanced_core:back", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Back Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_advanced_core:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _home_edit(interaction)
''',
)

recommend = replace_block(
    recommend,
    "class AdvancedMemberExperienceView(discord.ui.View):",
    "class AdvancedMonitoringRepairView(discord.ui.View):",
    '''class AdvancedMemberExperienceView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Ticket Choices", emoji="🧾", style=discord.ButtonStyle.primary, custom_id="dank_setup_advanced_members:ticket_menu", row=0)
    async def ticket_choices(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_ticket_menu(interaction)

    @discord.ui.button(label="Back to Advanced Settings", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_advanced_members:back", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Back Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_advanced_members:home", row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _home_edit(interaction)
''',
)

recommend = replace_block(
    recommend,
    "class AdvancedMonitoringRepairView(discord.ui.View):",
    "class AdvancedAppearanceView(discord.ui.View):",
    '''class AdvancedMonitoringRepairView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Modlog Tracking", emoji="🧾", style=discord.ButtonStyle.primary, custom_id="dank_setup_advanced_monitoring:modlog_tracking", row=0)
    async def modlog_tracking(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_modlog_tracking(interaction)

    @discord.ui.button(label="Protection", emoji="🛡️", style=discord.ButtonStyle.primary, custom_id="dank_setup_advanced_monitoring:protection", row=0)
    async def protection(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_protection_options(interaction)

    @discord.ui.button(label="Permission Repair", emoji="🛠️", style=discord.ButtonStyle.primary, custom_id="dank_setup_advanced_monitoring:permission_repair", row=1)
    async def permission_repair(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_permission_repair(interaction)

    @discord.ui.button(label="Back to Advanced Settings", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_advanced_monitoring:back", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Back Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_advanced_monitoring:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _home_edit(interaction)
''',
)

appearance_start = recommend.index("class AdvancedAppearanceView(discord.ui.View):")
danger_start = recommend.index("class AdvancedDangerZoneView(discord.ui.View):", appearance_start)
appearance = recommend[appearance_start:danger_start]
appearance = appearance.replace('label="Back to Advanced"', 'label="Back to Advanced Settings"')
appearance = appearance.replace('await _open_manage_setup(interaction)', 'await _open_advanced_settings(interaction)')
recommend = recommend[:appearance_start] + appearance + recommend[danger_start:]

danger_start = recommend.index("class AdvancedDangerZoneView(discord.ui.View):")
danger_end = recommend.index("_SETUP_TEST_TICKET_LOCKS", danger_start)
danger = recommend[danger_start:danger_end]
danger = danger.replace('label="Back to Advanced"', 'label="Back to More Options"')
recommend = recommend[:danger_start] + danger + recommend[danger_end:]

# Compact setup type chooser: the select descriptions explain the choices.
recommend = recommend.replace(
    '"Pick the closest match. You can change it later.\\n\\n"\n            "**Choose one setup type:**\\n"\n            "🏠 Basic Server — tickets and normal server tools\\n"\n            "✅ Basic Verify — one simple Verify button\\n"\n            "🎫 Help Desk — support tickets\\n"\n            "🎙️ Voice Verify — verification with a staff voice check\\n"\n            "⚙️ Custom — choose features yourself"',
    '"Choose the closest match from the menu below. "\n            "You can change it later from **More Options**."',
)
recommend, count = re.subn(
    r'\n    for choice in choices:\n        embed\.add_field\(.*?\n\n    if not fresh\.id_verify_allowed_for_guild\(guild\):',
    '\n\n    if not fresh.id_verify_allowed_for_guild(guild):',
    recommend,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f"setup choice field removal count={count}")

# Remove stale old-route copy from the legacy-compatible chooser.
recommend = recommend.replace(
    "Next, choose your existing roles/channels or create missing basics.",
    "Next, return to the guided setup and continue one required step at a time.",
)
recommend = recommend.replace(
    "• Press **Use My Existing Server** if your roles/channels already exist.\\n"
    "• Press **Create Missing Items** if you want Dank Shield to create missing basics.\\n"
    "• Press **Health Check** when you think setup is ready.",
    "Press **Continue Setup** on Setup Home. Dank Shield will show only the next required step.",
)
recommend = recommend.replace(
    "Nothing else was changed. Use **Use My Existing Server** while this is repaired.",
    "Nothing else was changed. Return to Setup Home and try again.",
)

RECOMMEND.write_text(recommend, encoding="utf-8")

fresh = FRESH.read_text(encoding="utf-8")

fresh = fresh.replace(
    'embed.add_field(name="Setup Check Will Require", value=_service_hint_text(state), inline=False)\n',
    '',
)
fresh, count = re.subn(
    r'    embed\.add_field\(\n        name="Next",\n        value=\(.*?\n        inline=False,\n    \)\n',
    '''    embed.add_field(
        name="Next",
        value=(
            "Choose the features this server should use, then press "
            "**Continue Setup**. The guided path handles the rest one step at a time."
        ),
        inline=False,
    )
''',
    fresh,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f"custom next field replacement count={count}")

fresh = replace_block(
    fresh,
    "class CustomServiceModeView(discord.ui.View):",
    "async def _open_custom_service_picker(",
    '''class CustomServiceModeView(discord.ui.View):
    """Custom Setup only: choose services here, then return to one guided path."""

    def __init__(self, state: Any) -> None:
        super().__init__(timeout=900)
        self.add_item(CustomServicePresetSelect(state))
        self.add_item(CustomServiceToggleButton("tickets_enabled", "Tickets", state.tickets, "🎫", 2))
        self.add_item(CustomServiceToggleButton("verification_enabled", "Basic Verify", state.verification, "✅", 2))
        self.add_item(CustomServiceToggleButton("voice_verification_enabled", "Voice Verify", state.voice, "🎙️", 2))
        self.add_item(CustomServiceToggleButton("spam_guard_enabled", "SpamGuard", state.spamguard, "🛡️", 3))
        self.add_item(CustomServiceToggleButton("moderation_enabled", "Logs", state.moderation, "🧾", 3))

    @discord.ui.button(
        label="Continue Setup",
        emoji="➡️",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_custom:continue_guided",
        row=1,
    )
    async def continue_guided(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await recommend._open_guided_setup(interaction)

    @discord.ui.button(
        label="Back",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_custom:back",
        row=4,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await recommend._open_choose_setup_type(interaction)
''',
)

fresh = fresh.replace("**Continue Guided Setup**", "**Continue Setup**")

fresh = replace_block(
    fresh,
    "class SetupTypeChoiceView(solid.BackToSetupView):",
    "def register_public_setup_fresh_choice_commands(",
    '''class SetupTypeChoiceSelect(discord.ui.Select):
    def __init__(self, *, guild: Optional[discord.Guild] = None) -> None:
        choices = _choices_for_guild(guild)
        super().__init__(
            placeholder="Choose what this server needs",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=choice.label,
                    value=choice.key,
                    description=choice.short[:100],
                    emoji=choice.emoji,
                )
                for choice in choices
            ][:25],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SetupTypeChoiceView):
            return
        choice = CHOICES_BY_KEY.get(str(self.values[0]))
        if choice is None:
            return await interaction.response.send_message("❌ Unknown setup type.", ephemeral=True)
        await view._save_and_show(interaction, choice)


class SetupTypeChoiceView(solid.BackToSetupView):
    def __init__(self, *, guild: Optional[discord.Guild] = None) -> None:
        super().__init__()
        self.add_item(SetupTypeChoiceSelect(guild=guild))

    async def _save_and_show(
        self,
        interaction: discord.Interaction,
        choice: PlainSetupChoice,
    ) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        if choice.needs_id and not id_verify_allowed_for_guild(guild):
            return await interaction.response.send_message(
                "🔒 ID/Web verification is not available for this server. Use **Basic Verify** instead.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        await solid._safe_defer_update(interaction)
        await _save_choice(interaction, choice)
        if choice.key == "custom_setup":
            return await _open_custom_service_picker(
                interaction,
                saved_message=(
                    "Saved **Custom setup**. Choose which features this server should use, "
                    "then press **Continue Setup**."
                ),
            )
        await recommend._open_guided_setup(
            interaction,
            saved_message=f"Saved **{choice.label}**.",
        )
''',
)

FRESH.write_text(fresh, encoding="utf-8")

TEST.write_text('''from pathlib import Path\n\nRECOMMEND = Path("stoney_verify/commands_ext/public_setup_recommend.py").read_text(encoding="utf-8")\nFRESH = Path("stoney_verify/commands_ext/public_setup_fresh_choice.py").read_text(encoding="utf-8")\n\n\ndef block(source: str, start: str, end: str) -> str:\n    left = source.index(start)\n    right = source.index(end, left)\n    return source[left:right]\n\n\ndef test_home_has_one_primary_path_and_more_options():\n    home = block(RECOMMEND, "class ProductSetupHomeView(", "class ContinueSetupView(")\n    assert "Start Setup" in home\n    assert "Continue Setup" in home\n    assert "Test & Launch" in home\n    assert "More Options" in home\n    assert 'label="Setup Check"' not in home\n    assert 'label="Manage Setup"' not in home\n\n\ndef test_guided_setup_has_only_current_step_and_home():\n    guided = block(RECOMMEND, "class ContinueSetupView(", "class ManageSetupView(")\n    assert "Fix This Step" in guided\n    assert "Back Home" in guided\n    assert "Setup Check" not in guided\n    assert "Change Setup Type" not in guided\n    assert "Advanced Options" not in guided\n\n\ndef test_more_options_uses_literal_labels():\n    more = block(RECOMMEND, "class ManageSetupView(", "class AdvancedCoreSetupView(")\n    for text in ("Change Setup Type", "Advanced Settings", "Setup Check / Diagnostics", "Reset / Recovery", "Help", "Back Home"):\n        assert text in more\n    assert "Member Experience" not in more\n    assert "Core Setup" not in more\n    assert "Monitoring & Repair" not in more\n    assert "Danger Zone" not in more\n\n\ndef test_advanced_hub_uses_plain_task_names():\n    hub = block(RECOMMEND, "class AdvancedSettingsHubView(", "class AdvancedCoreSetupView(")\n    assert "Features, Roles & Channels" in hub\n    assert "Tickets" in hub\n    assert "Logs, Protection & Repairs" in hub\n    assert "Server Design" in hub\n    assert "Member Experience" not in hub\n\n\ndef test_setup_review_has_only_next_action_and_home():\n    review = block(RECOMMEND, "class SetupReviewView(", "class SetupHealthHelpView(")\n    assert "SetupReviewHomeButton" in review\n    assert "SetupReviewAdvancedButton" not in review\n    assert "SetupReviewChangeTypeButton" not in review\n    assert "SetupReviewHelpButton" not in review\n\n\ndef test_custom_setup_stays_on_feature_choice_only():\n    custom = block(FRESH, "class CustomServiceModeView(", "async def _open_custom_service_picker(")\n    assert "Continue Setup" in custom\n    assert 'label="Back"' in custom\n    assert "Setup Check" not in custom\n    assert "Advanced Options" not in custom\n    assert "Setup Home" not in custom\n\n\ndef test_setup_type_uses_one_select_not_button_wall():\n    choice = block(FRESH, "class SetupTypeChoiceSelect(", "def register_public_setup_fresh_choice_commands(")\n    assert "discord.ui.Select" in choice\n    assert "Choose what this server needs" in choice\n    for custom_id in ("dank_setup_choice:basic", "dank_setup_choice:basic_verify", "dank_setup_choice:helpdesk", "dank_setup_choice:voice", "dank_setup_choice:custom"):\n        assert custom_id not in choice\n''', encoding="utf-8")

for path in (RECOMMEND, FRESH, TEST):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

print("PASS: applied one-path /dank setup front-door v2")
