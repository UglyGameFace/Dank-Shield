from __future__ import annotations

from pathlib import Path


def replace_between(
    text: str,
    start: str,
    end: str,
    replacement: str,
    *,
    label: str,
) -> str:
    start_at = text.find(start)
    if start_at < 0:
        raise SystemExit(f"ERROR: {label} start marker not found")
    end_at = text.find(end, start_at)
    if end_at < 0:
        raise SystemExit(f"ERROR: {label} end marker not found")
    return text[:start_at] + replacement.rstrip() + "\n\n" + text[end_at:]


# ============================================================
# 1. Canonical AIO setup navigation and copy
# ============================================================

path = Path("stoney_verify/commands_ext/public_setup_recommend.py")
text = path.read_text(encoding="utf-8")

copy_replacements = {
    "Test & Launch": "Test Your Setup",
    "Back Home": "Setup Home",
    "More Options": "Manage Setup",
    "Other Settings": "All Features & Settings",
    "Change Setup Type": "Change Setup Plan",
    "Fix Setup or Start Over": "Repair or Restart Setup",
    "Fix or Start Over": "Repair or Restart",
}
for old, new in copy_replacements.items():
    text = text.replace(old, new)

text = text.replace(
    "Follow the recommended next step. Settings you do not need stay under "
    '"**Edit / Manage Setup**."',
    "Follow the recommended next step for the quickest setup. "
    '"Every AIO module stays available under **Manage Setup** without slowing down this path."',
)
text = text.replace(
    'self.more_options.label = "Edit / Manage Setup"',
    'self.more_options.label = "Manage Setup"',
)

close_marker = "async def _open_choose_setup_type("
close_helper = r'''
async def _close_setup(
    interaction: discord.Interaction,
) -> None:
    """Close the interactive setup message without changing configuration."""

    if not await solid._require_setup_permission(interaction):
        return

    embed = discord.Embed(
        title="Setup Closed",
        description=(
            "Nothing else was changed. Run `/dank setup` whenever "
            "you want to continue."
        ),
        color=discord.Color.dark_grey(),
        timestamp=now_utc(),
    )
    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=None,
    )
'''
if "async def _close_setup(" not in text:
    marker_at = text.find(close_marker)
    if marker_at < 0:
        raise SystemExit("ERROR: close helper marker not found")
    text = text[:marker_at] + close_helper.strip() + "\n\n\n" + text[marker_at:]

section_functions = r'''
async def _open_advanced_verification(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="✅ Verification",
        description=(
            "Set up member access, Simple Verify, Voice Verify, and "
            "approved ID/Web verification options."
        ),
        items=(
            "✅ **Core Features** — turn Simple Verify or Voice Verify on or off.",
            "🧭 **Roles & Channels** — choose member roles and verification channels.",
            "⏱️ **Timers & Rules** — adjust verification timing and behavior.",
        ),
        view=AdvancedVerificationView(),
    )


async def _open_advanced_security(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🛡️ Security & SpamGuard",
        description=(
            "Manage SpamGuard, raid protection, AntiNuke, bot access, "
            "and channel permission repairs."
        ),
        items=(
            "🛡️ **Protection Center** — SpamGuard, raid protection, and AntiNuke.",
            "🔐 **Check Bot Access** — find channels Dank Shield cannot inspect.",
            "🛠️ **Fix Channel Permissions** — preview and apply safe permission repairs.",
        ),
        view=AdvancedSecurityView(),
    )


async def _open_advanced_logs_activity(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🧾 Logs & Activity",
        description=(
            "Choose what Dank Shield records and verify that activity tracking "
            "can see the channels it needs."
        ),
        items=(
            "🧾 **Choose What Gets Logged** — select moderation and server events.",
            "🔐 **Check Activity Access** — review activity-tracking coverage.",
            "🧭 **Log Channels** — choose where enabled logs are posted.",
        ),
        view=AdvancedLogsActivityView(),
    )
'''
text = replace_between(
    text,
    "async def _open_advanced_monitoring_repair(",
    "async def _open_config_history(",
    section_functions,
    label="AIO section functions",
)

advanced_settings = r'''
async def _open_advanced_settings(
    interaction: discord.Interaction,
) -> None:
    if not await solid._require_setup_permission(interaction):
        return

    embed = discord.Embed(
        title="🧰 All Features & Settings",
        description=(
            "Everything Dank Shield can configure is grouped by purpose below. "
            "The normal Quick Setup only asks for required items."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="🧩 Setup Plan & Server Items",
        value="Change core modules, roles, channels, folders, timers, and rules.",
        inline=False,
    )
    embed.add_field(
        name="🎫 Tickets",
        value="Ticket panels, staff routing, folders, and member ticket choices.",
        inline=False,
    )
    embed.add_field(
        name="✅ Verification",
        value="Simple Verify, Voice Verify, approved ID/Web flows, roles, and channels.",
        inline=False,
    )
    embed.add_field(
        name="🛡️ Security & SpamGuard",
        value="SpamGuard, raids, AntiNuke, access checks, and permission repairs.",
        inline=False,
    )
    embed.add_field(
        name="🧾 Logs & Activity",
        value="Logging choices, log channels, and activity-tracking access.",
        inline=False,
    )
    embed.add_field(
        name="🎨 Server Design",
        value="Smart Auto-Detect, previews, styling, and undo tools.",
        inline=False,
    )
    embed.add_field(
        name="💾 Backups & History",
        value="Back up selected configuration areas and restore only what you choose.",
        inline=False,
    )
    embed.set_footer(text="All Features & Settings • choose one category")

    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=AdvancedSettingsHubView(),
    )
'''
text = replace_between(
    text,
    "async def _open_advanced_settings(",
    "async def _open_manage_setup(",
    advanced_settings,
    label="all features hub payload",
)

manage_setup = r'''
async def _open_manage_setup(
    interaction: discord.Interaction,
) -> None:
    """Open the task-based management hub for the AIO bot."""

    if not await solid._require_setup_permission(interaction):
        return

    embed = discord.Embed(
        title="⚙️ Manage Setup",
        description=(
            "Use **Quick Setup** for the fastest guided path. Use this hub to "
            "change a plan, manage any AIO module, review problems, or repair setup."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="🧭 Change Setup Plan",
        value="Choose a different recommended plan or select your own core modules.",
        inline=False,
    )
    embed.add_field(
        name="🧰 All Features & Settings",
        value="Open Tickets, Verification, Security, Logs, Design, Backups, and more.",
        inline=False,
    )
    embed.add_field(
        name="🩺 Review Setup",
        value="See what is ready, optional, missing, or configured incorrectly.",
        inline=False,
    )
    embed.add_field(
        name="🧯 Repair or Restart Setup",
        value="Use recovery tools only when setup is broken or you intentionally want a reset.",
        inline=False,
    )
    embed.set_footer(text="Manage Setup • Quick Setup remains available from Setup Home")

    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=ManageSetupView(),
    )
'''
text = replace_between(
    text,
    "async def _open_manage_setup(",
    "def _setup_bool(",
    manage_setup,
    label="manage setup payload",
)

setup_review = r'''
class SetupReviewView(discord.ui.View):
    """After Setup Check, show only the next correct action and navigation."""

    def __init__(self, *, ready: bool) -> None:
        super().__init__(timeout=900)

        if ready:
            self.add_item(SetupReviewLaunchButton())
        else:
            self.add_item(SetupReviewFixNextButton())

        self.add_item(SetupReviewHomeButton())
        close_button = discord.ui.Button(
            label="Close",
            emoji="✖️",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_review:close",
            row=2,
        )
        close_button.callback = self._close
        self.add_item(close_button)

    async def _close(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await _close_setup(interaction)
'''
text = replace_between(
    text,
    "class SetupReviewView(",
    "class SetupHealthHelpView(",
    setup_review,
    label="setup review navigation",
)

home_view = r'''
class ProductSetupHomeView(discord.ui.View):
    """Setup Home with one fast path, management, and a clean exit."""

    def __init__(
        self,
        *,
        ready: bool = False,
        started: bool = False,
        completed: bool = False,
    ) -> None:
        super().__init__(timeout=900)
        self.ready = bool(ready)
        self.started = bool(started)
        self.completed = bool(completed)

        if self.completed:
            self.continue_setup.label = "View Setup Summary"
            self.continue_setup.emoji = "✅"
        elif self.ready:
            self.continue_setup.label = "Test Your Setup"
            self.continue_setup.emoji = "🧪"
        elif self.started:
            self.continue_setup.label = "Continue Quick Setup"
            self.continue_setup.emoji = "➡️"
        else:
            self.continue_setup.label = "Start Quick Setup"
            self.continue_setup.emoji = "⚡"

    @discord.ui.button(
        label="Start Quick Setup",
        emoji="⚡",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_home:continue",
        row=0,
    )
    async def continue_setup(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if self.completed:
            await _open_completed_summary(interaction)
            return
        if self.ready:
            await _open_test_launch(interaction)
            return
        if self.started:
            await _open_guided_setup(interaction)
            return
        await _open_choose_setup_type(interaction)

    @discord.ui.button(
        label="Manage Setup",
        emoji="⚙️",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_home:manage",
        row=1,
    )
    async def more_options(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _open_manage_setup(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_home:close",
        row=1,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _close_setup(interaction)
'''
text = replace_between(
    text,
    "class ProductSetupHomeView(",
    "class ContinueSetupView(",
    home_view,
    label="quick setup home view",
)

continue_view = r'''
class ContinueSetupView(discord.ui.View):
    """The Quick Setup path: perform one action, go home, or close."""

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
        label="Set Up This Step",
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
        _ = button
        await _open_guided_target(
            interaction,
            self.target,
            self.requirement_key,
        )

    @discord.ui.button(
        label="Setup Home",
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
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_guided:close",
        row=1,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _close_setup(interaction)
'''
text = replace_between(
    text,
    "class ContinueSetupView(",
    "class ManageSetupView(",
    continue_view,
    label="quick setup navigation",
)

manage_view = r'''
class ManageSetupView(discord.ui.View):
    """Task-based AIO setup management hub."""

    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Change Setup Plan",
        emoji="🧭",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_manage:plan",
        row=0,
    )
    async def change_type(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _open_choose_setup_type(interaction)

    @discord.ui.button(
        label="All Features & Settings",
        emoji="🧰",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_manage:features",
        row=0,
    )
    async def advanced_settings(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _open_advanced_settings(interaction)

    @discord.ui.button(
        label="Review Setup",
        emoji="🩺",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_manage:review",
        row=1,
    )
    async def health(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _open_health_check(interaction)

    @discord.ui.button(
        label="Repair or Restart Setup",
        emoji="🧯",
        style=discord.ButtonStyle.danger,
        custom_id="dank_setup_manage:repair",
        row=1,
    )
    async def recovery(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _open_advanced_danger_zone(interaction)

    @discord.ui.button(
        label="Help",
        emoji="❓",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_manage:help",
        row=2,
    )
    async def help_faq(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.edit_message(
            embed=_build_setup_help_embed(),
            view=ManageSetupView(),
        )

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_manage:home",
        row=3,
    )
    async def home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_manage:close",
        row=3,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _close_setup(interaction)
'''
text = replace_between(
    text,
    "class ManageSetupView(",
    "class AdvancedSettingsHubView(",
    manage_view,
    label="manage setup view",
)

advanced_hub_view = r'''
class AdvancedSettingsHubView(discord.ui.View):
    """AIO feature categories with predictable navigation."""

    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Setup Plan & Server Items",
        emoji="🧩",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_features:core",
        row=0,
    )
    async def core(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_core_setup(interaction)

    @discord.ui.button(
        label="Tickets",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_features:tickets",
        row=0,
    )
    async def tickets(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_member_experience(interaction)

    @discord.ui.button(
        label="Verification",
        emoji="✅",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_features:verification",
        row=1,
    )
    async def verification(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_verification(interaction)

    @discord.ui.button(
        label="Security & SpamGuard",
        emoji="🛡️",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_features:security",
        row=1,
    )
    async def security(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_security(interaction)

    @discord.ui.button(
        label="Logs & Activity",
        emoji="🧾",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_features:logs",
        row=2,
    )
    async def logs_activity(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_logs_activity(interaction)

    @discord.ui.button(
        label="Server Design",
        emoji="🎨",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:design",
        row=2,
    )
    async def design(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_appearance(interaction)

    @discord.ui.button(
        label="Backups & History",
        emoji="💾",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:history",
        row=3,
    )
    async def history(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _open_config_history(interaction)

    @discord.ui.button(
        label="Back to Manage Setup",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:back",
        row=4,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_manage_setup(interaction)

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:home",
        row=4,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:close",
        row=4,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)
'''
text = replace_between(
    text,
    "class AdvancedSettingsHubView(",
    "class AdvancedCoreSetupView(",
    advanced_hub_view,
    label="AIO feature hub view",
)

core_view = r'''
class AdvancedCoreSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Choose Core Modules", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_setup_core:services", row=0)
    async def services(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_services(interaction)

    @discord.ui.button(label="Timers & Rules", emoji="⏱️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_core:timers", row=0)
    async def timers_behavior(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_timers_behavior(interaction)

    @discord.ui.button(label="Choose Roles & Channels", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank_setup_core:mapping", row=1)
    async def detailed_mapping(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_existing_server(interaction)

    @discord.ui.button(label="Back to All Features", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_core:back", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_core:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_core:close", row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)
'''
text = replace_between(
    text,
    "class AdvancedCoreSetupView(",
    "class AdvancedMemberExperienceView(",
    core_view,
    label="core settings view",
)

tickets_view = r'''
class AdvancedMemberExperienceView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Ticket Choices", emoji="🧾", style=discord.ButtonStyle.primary, custom_id="dank_setup_tickets:choices", row=0)
    async def ticket_choices(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_ticket_menu(interaction)

    @discord.ui.button(label="Roles & Channels", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank_setup_tickets:mapping", row=0)
    async def mapping(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_existing_server(interaction)

    @discord.ui.button(label="Timers & Rules", emoji="⏱️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_tickets:rules", row=1)
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_timers_behavior(interaction)

    @discord.ui.button(label="Back to All Features", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_tickets:back", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_tickets:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_tickets:close", row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)
'''
text = replace_between(
    text,
    "class AdvancedMemberExperienceView(",
    "class AdvancedMonitoringRepairView(",
    tickets_view,
    label="ticket settings view",
)

specialized_views = r'''
class AdvancedVerificationView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Choose Core Modules", emoji="✅", style=discord.ButtonStyle.primary, custom_id="dank_setup_verify:features", row=0)
    async def features(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_services(interaction)

    @discord.ui.button(label="Roles & Channels", emoji="🧭", style=discord.ButtonStyle.primary, custom_id="dank_setup_verify:mapping", row=0)
    async def mapping(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_existing_server(interaction)

    @discord.ui.button(label="Timers & Rules", emoji="⏱️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_verify:rules", row=1)
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_timers_behavior(interaction)

    @discord.ui.button(label="Back to All Features", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_verify:back", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_verify:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_verify:close", row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)


class AdvancedSecurityView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Protection Center", emoji="🛡️", style=discord.ButtonStyle.primary, custom_id="dank_setup_security:protection", row=0)
    async def protection(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_protection_options(interaction)

    @discord.ui.button(label="Check Bot Access", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id="dank_setup_security:access", row=0)
    async def bot_access(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_bot_access_check(interaction)

    @discord.ui.button(label="Fix Channel Permissions", emoji="🛠️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_security:repair", row=1)
    async def permission_repair(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_permission_repair(interaction)

    @discord.ui.button(label="Back to All Features", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_security:back", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_security:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_security:close", row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)


class AdvancedLogsActivityView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Choose What Gets Logged", emoji="🧾", style=discord.ButtonStyle.primary, custom_id="dank_setup_logs:events", row=0)
    async def modlog_tracking(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_modlog_tracking(interaction)

    @discord.ui.button(label="Check Activity Access", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id="dank_setup_logs:access", row=0)
    async def bot_access(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_bot_access_check(interaction)

    @discord.ui.button(label="Log Channels", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank_setup_logs:channels", row=1)
    async def channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_existing_server(interaction)

    @discord.ui.button(label="Back to All Features", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_logs:back", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_logs:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_logs:close", row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)
'''
text = replace_between(
    text,
    "class AdvancedMonitoringRepairView(",
    "class AdvancedAppearanceView(",
    specialized_views,
    label="verification security logs views",
)

appearance_view = r'''
class AdvancedAppearanceView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Open Server Design",
        emoji="🎨",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_design:open",
        row=0,
    )
    async def server_design(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await solid._require_setup_permission(interaction):
            return
        from . import public_design_bridge
        await public_design_bridge.open_design_studio_from_setup(interaction)

    @discord.ui.button(label="Back to All Features", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_design:back", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_design:home", row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_design:close", row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)
'''
text = replace_between(
    text,
    "class AdvancedAppearanceView(",
    "class AdvancedDangerZoneView(",
    appearance_view,
    label="server design view",
)

danger_view = r'''
class AdvancedDangerZoneView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Open Repair & Restart Tools", emoji="🧯", style=discord.ButtonStyle.danger, custom_id="dank_setup_repair:open", row=0)
    async def recovery(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_recovery_center(interaction)

    @discord.ui.button(label="Back to Manage Setup", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_repair:back", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_manage_setup(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_repair:home", row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_repair:close", row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)
'''
text = replace_between(
    text,
    "class AdvancedDangerZoneView(",
    "_SETUP_TEST_TICKET_LOCKS:",
    danger_view,
    label="repair setup view",
)

launch_view = r'''
class LaunchTestView(discord.ui.View):
    """Render test actions only for enabled features."""

    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        super().__init__(timeout=900)
        self.state = dict(state or {})
        actions: list[tuple[str, str, discord.ButtonStyle, str, Any]] = []

        if self.state.get("tickets"):
            actions.extend([
                ("Post Ticket Panel", "🎫", discord.ButtonStyle.success, "dank_setup_test:ticket_panel", self._post_ticket_panel),
                ("Create Test Ticket", "🧪", discord.ButtonStyle.success, "dank_setup_test:test_ticket", self._create_test_ticket),
            ])
        if self.state.get("basic_verify"):
            actions.append(("Post Simple Verify Panel", "✅", discord.ButtonStyle.success, "dank_setup_test:verify_panel", self._post_basic_verify))
        if not self.state.get("completed"):
            actions.append(("Finish Setup", "🏁", discord.ButtonStyle.primary, "dank_setup_test:finish", self._finish))
        actions.extend([
            ("Review Setup", "🩺", discord.ButtonStyle.secondary, "dank_setup_test:review", self._review),
            ("Setup Home", "🏠", discord.ButtonStyle.secondary, "dank_setup_test:home", self._home),
            ("Close", "✖️", discord.ButtonStyle.secondary, "dank_setup_test:close", self._close),
        ])

        for index, (label, emoji, style, custom_id, callback) in enumerate(actions):
            button = discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=custom_id,
                row=min(4, index // 2),
            )
            button.callback = callback
            self.add_item(button)

    async def _post_ticket_panel(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        state = await _launch_state(guild)
        if not state.get("tickets"):
            return await interaction.response.send_message("🎫 Tickets are OFF. Open **Manage Setup** to turn them on.", ephemeral=True)
        try:
            from .public_ticket_panel_commands import post_ticket_panel_callback
            await post_ticket_panel_callback(interaction)
        except Exception as exc:
            await interaction.response.send_message(
                "❌ Could not post the ticket panel: " f"`{type(exc).__name__}: {str(exc)[:220]}`",
                ephemeral=True,
            )

    async def _post_basic_verify(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        state = await _launch_state(guild)
        if not state.get("basic_verify"):
            return await interaction.response.send_message("✅ Simple Verify is OFF. Open **Manage Setup** to turn it on.", ephemeral=True)
        try:
            from .public_verify_basic_panel import verify_panel
            await verify_panel(interaction)
        except Exception as exc:
            await interaction.response.send_message(
                "❌ Could not post the Simple Verify panel: " f"`{type(exc).__name__}: {str(exc)[:220]}`",
                ephemeral=True,
            )

    async def _create_test_ticket(self, interaction: discord.Interaction) -> None:
        await _create_setup_test_ticket(interaction)

    async def _finish(self, interaction: discord.Interaction) -> None:
        await _finish_setup(interaction)

    async def _review(self, interaction: discord.Interaction) -> None:
        await _open_health_check(interaction)

    async def _home(self, interaction: discord.Interaction) -> None:
        await _home_edit(interaction)

    async def _close(self, interaction: discord.Interaction) -> None:
        await _close_setup(interaction)
'''
text = replace_between(
    text,
    "class LaunchTestView(",
    "class FinishedSetupView(",
    launch_view,
    label="test setup view",
)

finished_view = r'''
class FinishedSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Test Again", emoji="🧪", style=discord.ButtonStyle.secondary, custom_id="dank_setup_finished:test", row=0)
    async def test_again(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_test_launch(interaction)

    @discord.ui.button(label="Manage Setup", emoji="⚙️", style=discord.ButtonStyle.primary, custom_id="dank_setup_finished:manage", row=0)
    async def edit_setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_manage_setup(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_finished:home", row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_finished:close", row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)
'''
text = replace_between(
    text,
    "class FinishedSetupView(",
    "def _patch() -> None:",
    finished_view,
    label="finished setup view",
)

text = text.replace(
    'title="🧭 What Should Dank Shield Do?"',
    'title="⚡ Choose a Quick Setup Plan"',
)
text = text.replace(
    '"Choose the closest match from the menu below. "\n            "You can change it later from **Manage Setup**."',
    '"Pick the closest goal. Dank Shield applies smart defaults and then asks only for missing essentials. "\n            "You can change the plan or any AIO module later from **Manage Setup**."',
)
text = text.replace(
    'title="🧭 Guided Setup"',
    'title="⚡ Quick Setup"',
)
text = text.replace(
    '"One step at a time. Dank Shield shows only the "\n            "next required item."',
    '"The fastest path: one required step at a time, with optional AIO tools kept out of the way."',
)
text = text.replace(
    'text=f"Guild {guild.id} • guided setup"',
    'text=f"Guild {guild.id} • Quick Setup"',
)
text = text.replace(
    '"Created from the canonical /dank setup "\n                    "Test Your Setup screen"',
    '"Created from the canonical /dank setup test screen"',
)

path.write_text(text, encoding="utf-8")


# ============================================================
# 2. Core feature picker copy and navigation
# ============================================================

path = Path("stoney_verify/commands_ext/public_setup_fresh_choice.py")
text = path.read_text(encoding="utf-8")

text = text.replace('"Tickets + Server Basics"', '"Recommended Setup"')
text = text.replace(
    '"Sets up support tickets and basic logs. A good choice for most servers that do not need member verification."',
    '"Fast AIO starter: tickets, SpamGuard, and essential logs. Best for most communities."',
)
text = text.replace('"Choose My Own Features"', '"Choose Core Features"')
text = text.replace(
    '"Choose exactly which features you want: tickets, Simple Verify, Voice Verify, SpamGuard, and logs."',
    '"Choose the core modules that require server roles, channels, or permissions. Other AIO tools remain available under Manage Setup."',
)
text = text.replace('"Everything"', '"All Core Features"')
text = text.replace(
    '"Tickets, Simple Verify, Voice Verify, SpamGuard, and logs."',
    '"Tickets, Simple Verify, Voice Verify, SpamGuard, and essential logs."',
)
text = text.replace('title="🧩 Choose Your Features"', 'title="🧩 Choose Core Features"')
text = text.replace(
    '"Choose what you want Dank Shield to do in this server. "\n            "A green button means the feature is ON. A gray button means it is OFF."',
    '"Choose the core modules that need server setup. Green means ON and gray means OFF. "\n            "Design, backups, analytics, and repair tools remain available later under **Manage Setup**."',
)
text = text.replace('name="Your Setup"', 'name="Core Setup Plan"')
text = text.replace('name="Features"', 'name="Core Modules"')
text = text.replace(
    '"Turn the features on or off, then press **Continue Setup**. "\n            "Dank Shield will walk you through the rest one step at a time."',
    '"Choose the core modules, then press **Continue Quick Setup**. "\n            "Dank Shield asks only for the roles, channels, and permissions those modules require."',
)
text = text.replace('label="Continue Setup"', 'label="Continue Quick Setup"')
text = text.replace('label="Back"', 'label="Back to Setup Plans"')
text = text.replace(
    '"Saved **Choose My Own Features**. Dank Shield checks what is already set up and pre-selects matching features. Turn off anything you do not want."',
    '"Saved **Choose Core Features**. Dank Shield checked the existing server and pre-selected matching core modules. Turn off anything you do not want."',
)
text = text.replace(
    'placeholder="What do you want Dank Shield to do?"',
    'placeholder="Choose a setup plan…"',
)

custom_view = r'''
class CustomServiceModeView(discord.ui.View):
    """Choose core modules, then return to the single Quick Setup path."""

    def __init__(self, state: Any) -> None:
        super().__init__(timeout=900)
        self.add_item(CustomServicePresetSelect(state))
        self.add_item(CustomServiceToggleButton("tickets_enabled", "Tickets", state.tickets, "🎫", 2))
        self.add_item(CustomServiceToggleButton("verification_enabled", "Simple Verify", state.verification, "✅", 2))
        self.add_item(CustomServiceToggleButton("voice_verification_enabled", "Voice Verify", state.voice, "🎙️", 2))
        self.add_item(CustomServiceToggleButton("spam_guard_enabled", "SpamGuard", state.spamguard, "🛡️", 3))
        self.add_item(CustomServiceToggleButton("moderation_enabled", "Logs", state.moderation, "🧾", 3))

    @discord.ui.button(
        label="Continue Quick Setup",
        emoji="➡️",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_custom:continue_quick",
        row=1,
    )
    async def continue_guided(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await recommend._open_guided_setup(interaction)

    @discord.ui.button(
        label="Back to Setup Plans",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_custom:plans",
        row=4,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await recommend._open_choose_setup_type(interaction)

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_custom:home",
        row=4,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await recommend._home_edit(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_custom:close",
        row=4,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await recommend._close_setup(interaction)
'''
text = replace_between(
    text,
    "class CustomServiceModeView(",
    "async def _open_custom_service_picker(",
    custom_view,
    label="custom core modules view",
)

text = text.replace(
    '"Saved **Choose My Own Features**. Choose which features this server should use, "\n                    "then press **Continue Setup**."',
    '"Saved **Choose Core Features**. Choose the core modules this server should use, "\n                    "then press **Continue Quick Setup**."',
)

path.write_text(text, encoding="utf-8")


# ============================================================
# 3. Retire stale template language still reachable in previews
# ============================================================

path = Path("stoney_verify/setup_new/templates.py")
text = path.read_text(encoding="utf-8")
text = text.replace("Basic server", "Recommended Setup")
text = text.replace("Help desk", "Help Desk / Tickets")
text = text.replace("Custom setup", "Choose Core Features")
text = text.replace("Use This Setup", "Use This Plan")
text = text.replace("Preview Only", "Preview")
text = text.replace("Choose setup type", "Choose a Quick Setup Plan")
text = text.replace(
    "After saving, use **Use My Existing Server** to map existing channels/roles, "
    "or **Create Missing Items** only when something is actually missing.",
    "After saving, **Quick Setup** checks the current server and asks only for missing roles, channels, or permissions.",
)
text = text.replace(
    "There you can turn Tickets, Basic Verify, Voice Verify, SpamGuard, and Logs on/off.",
    "There you can choose Tickets, Simple Verify, Voice Verify, SpamGuard, and essential Logs. Other AIO tools remain under Manage Setup.",
)
text = text.replace(
    "One-button verification only: use **Basic verify** from the main setup choices when available.",
    "One-button verification only: choose **Simple Verify**.",
)
path.write_text(text, encoding="utf-8")


# ============================================================
# 4. Behavioral tests for the AIO navigation contract
# ============================================================

Path("tests/test_setup_aio_navigation_behavior.py").write_text(
    r'''from __future__ import annotations

from typing import Any

import discord

from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
from stoney_verify.commands_ext import public_setup_recommend as recommend
from stoney_verify.setup_new.templates import build_setup_template_embed


def labels(view: discord.ui.View) -> list[str]:
    return [
        str(getattr(child, "label", "") or "")
        for child in view.children
        if isinstance(child, discord.ui.Button)
    ]


def field_names(embed: discord.Embed) -> list[str]:
    return [str(field.name) for field in embed.fields]


def test_home_has_one_quick_path_management_and_close() -> None:
    view = recommend.ProductSetupHomeView(
        ready=False,
        started=False,
        completed=False,
    )
    assert labels(view) == [
        "Start Quick Setup",
        "Manage Setup",
        "Close",
    ]


def test_manage_setup_is_task_based() -> None:
    view = recommend.ManageSetupView()
    assert labels(view) == [
        "Change Setup Plan",
        "All Features & Settings",
        "Review Setup",
        "Repair or Restart Setup",
        "Help",
        "Setup Home",
        "Close",
    ]


def test_aio_feature_hub_exposes_all_major_categories() -> None:
    view = recommend.AdvancedSettingsHubView()
    assert labels(view) == [
        "Setup Plan & Server Items",
        "Tickets",
        "Verification",
        "Security & SpamGuard",
        "Logs & Activity",
        "Server Design",
        "Backups & History",
        "Back to Manage Setup",
        "Setup Home",
        "Close",
    ]


def test_each_major_subsection_has_back_home_and_close() -> None:
    views = (
        recommend.AdvancedCoreSetupView(),
        recommend.AdvancedMemberExperienceView(),
        recommend.AdvancedVerificationView(),
        recommend.AdvancedSecurityView(),
        recommend.AdvancedLogsActivityView(),
        recommend.AdvancedAppearanceView(),
        recommend.AdvancedDangerZoneView(),
    )
    for view in views:
        view_labels = labels(view)
        assert any(label.startswith("Back to ") for label in view_labels)
        assert "Setup Home" in view_labels
        assert "Close" in view_labels


def test_test_screen_still_hides_disabled_feature_actions() -> None:
    view = recommend.LaunchTestView(
        {
            "tickets": False,
            "basic_verify": True,
            "completed": False,
        }
    )
    assert labels(view) == [
        "Post Simple Verify Panel",
        "Finish Setup",
        "Review Setup",
        "Setup Home",
        "Close",
    ]


def test_custom_core_picker_has_predictable_navigation() -> None:
    state = type(
        "State",
        (),
        {
            "tickets": True,
            "verification": False,
            "voice": False,
            "spamguard": True,
            "moderation": True,
            "as_payload": lambda self: {
                "tickets_enabled": True,
                "verification_enabled": False,
                "voice_verification_enabled": False,
                "spam_guard_enabled": True,
                "moderation_enabled": True,
            },
        },
    )()
    view = fresh.CustomServiceModeView(state)
    view_labels = labels(view)
    assert "Continue Quick Setup" in view_labels
    assert "Back to Setup Plans" in view_labels
    assert "Setup Home" in view_labels
    assert "Close" in view_labels


def test_custom_picker_explains_core_modules_and_aio_tools() -> None:
    state = type(
        "State",
        (),
        {
            "tickets": True,
            "verification": False,
            "voice": False,
            "spamguard": True,
            "moderation": True,
            "as_payload": lambda self: {
                "tickets_enabled": True,
                "verification_enabled": False,
                "voice_verification_enabled": False,
                "spam_guard_enabled": True,
                "moderation_enabled": True,
            },
        },
    )()
    guild = type("Guild", (), {"id": 123})()
    embed = fresh._custom_services_embed(guild, state)
    assert embed.title == "🧩 Choose Core Features"
    assert "Manage Setup" in str(embed.description)
    assert "Core Modules" in field_names(embed)


def test_template_preview_uses_current_quick_setup_language() -> None:
    embed = build_setup_template_embed(
        selected_key="custom_setup",
        guild_name="Example Server",
    )
    rendered = "\n".join(
        [
            str(embed.title or ""),
            str(embed.description or ""),
            *[str(field.value) for field in embed.fields],
        ]
    )
    assert "Use This Plan" in rendered
    assert "Manage Setup" in rendered
    assert "Use My Existing Server" not in rendered
''',
    encoding="utf-8",
)

print("✅ Added AIO Quick Setup and Manage Setup hierarchy")
print("✅ Added dedicated Verification, Security, and Logs/Activity sections")
print("✅ Standardized Back, Setup Home, and Close navigation")
print("✅ Updated core feature picker and legacy preview language")
print("✅ Added behavioral AIO navigation tests")
