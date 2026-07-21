from __future__ import annotations

from pathlib import Path


def replace_between(text: str, start: str, end: str, replacement: str, *, label: str) -> str:
    start_at = text.find(start)
    if start_at < 0:
        raise SystemExit(f"ERROR: {label} start marker not found")
    end_at = text.find(end, start_at)
    if end_at < 0:
        raise SystemExit(f"ERROR: {label} end marker not found")
    return text[:start_at] + replacement.rstrip() + "\n\n" + text[end_at:]


path = Path("stoney_verify/commands_ext/public_setup_solid.py")
text = path.read_text(encoding="utf-8")

nav_view = r'''
class SetupNavView(discord.ui.View):
    """Universal navigation for nested setup tools."""

    def __init__(self) -> None:
        super().__init__(timeout=900)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        try:
            item_label = (
                getattr(item, "label", None)
                or getattr(item, "placeholder", None)
                or getattr(item, "custom_id", None)
                or "setup item"
            )
        except Exception:
            item_label = "setup item"

        await safe_interaction_error(
            interaction,
            title="Setup Action Failed",
            error=error,
            hint=(
                f"The **{item_label}** action failed safely. Nothing was changed. "
                "Press **Back to All Features**, **Setup Home**, or reopen `/dank setup`."
            ),
            view=self,
        )

    @discord.ui.button(
        label="Back to All Features",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_nested:features",
        row=4,
    )
    async def all_features(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        from . import public_setup_recommend as recommend
        await recommend._open_advanced_settings(interaction)

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_nested:home",
        row=4,
    )
    async def setup_home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )
        await _safe_defer_update(interaction)
        embed, view = await _build_main_setup_payload(guild)
        await _edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_nested:close",
        row=4,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
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
        await _edit_or_followup(interaction, embed=embed, view=None)
'''
text = replace_between(text, "class SetupNavView(", "BackToSetupView = SetupNavView", nav_view, label="nested setup navigation")
path.write_text(text, encoding="utf-8")


path = Path("stoney_verify/commands_ext/public_setup_recommend.py")
text = path.read_text(encoding="utf-8")
old_existing = '''    await interaction.response.edit_message(embed=embed, view=solid.ChooseExistingView())
'''
new_existing = '''    from . import public_setup_full_customization as customization

    await interaction.response.edit_message(
        embed=embed,
        view=customization.FullChooseExistingView(),
    )
'''
if old_existing not in text:
    raise SystemExit("ERROR: direct existing-server route marker not found")
text = text.replace(old_existing, new_existing, 1)
text = text.replace(
    'value="1. Ticket setup\\n2. Member roles\\n3. Verification channels\\n4. Log channels\\n5. Timers and rules"',
    'value="Choose only the section you need: roles, ticket folders, member channels, staff/log channels, or timers and rules."',
)
path.write_text(text, encoding="utf-8")


path = Path("stoney_verify/commands_ext/public_setup_full_customization.py")
text = path.read_text(encoding="utf-8")
text = text.replace("Basic Verify", "Simple Verify")
text = text.replace(
    '"Choose another item, press **Back to Setup**, "\n            "or press **Setup Check**."',
    '"Choose another item, press **Back to All Features**, or use **Review Setup** from Manage Setup."',
)

back_helpers = r'''
async def _back_to_all_features(interaction: discord.Interaction) -> None:
    from . import public_setup_recommend as recommend
    await recommend._open_advanced_settings(interaction)


async def _setup_home(interaction: discord.Interaction) -> None:
    from . import public_setup_recommend as recommend
    await recommend._home_edit(interaction)


async def _close_setup(interaction: discord.Interaction) -> None:
    from . import public_setup_recommend as recommend
    await recommend._close_setup(interaction)
'''
text = replace_between(text, "async def _back_to_setup(", "def _bot_member(", back_helpers, label="full customization navigation helpers")

back_view = r'''
class SetupBackView(discord.ui.View):
    """Parent, home, and close routes shared by customization pages."""

    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Back to All Features",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_customization:features",
        row=4,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _back_to_all_features(interaction)

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_customization:home",
        row=4,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _setup_home(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_customization:close",
        row=4,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _close_setup(interaction)
'''
text = replace_between(text, "class SetupBackView(", "class FullChooseExistingView(", back_view, label="full customization navigation view")

registration = r'''
def install_full_customization() -> bool:
    """Compatibility entrypoint; integration is now explicit, not patched."""
    global _PATCHED
    _PATCHED = True
    return True


def register_public_setup_full_customization_commands(
    bot: Any,
    tree: Any,
) -> None:
    global _REGISTERED
    _ = bot, tree
    if _REGISTERED:
        return
    install_full_customization()
    _REGISTERED = True
    _log("direct full customization routes ready")
'''
text = replace_between(text, "def _patch_module(", "__all__ =", registration, label="full customization direct registration")
text = text.replace(
    '__all__ = ["register_public_setup_full_customization_commands", "install_full_customization"]',
    '__all__ = [\n    "register_public_setup_full_customization_commands",\n    "install_full_customization",\n    "FullChooseExistingView",\n]',
)
path.write_text(text, encoding="utf-8")


path = Path("stoney_verify/commands_ext/public_setup_recovery.py")
text = path.read_text(encoding="utf-8")
text = text.replace("_ORIGINAL_BUILD_MAIN = None\n", "")
text = text.replace("Recovery / Start Over", "Repair / Restart")
text = text.replace("Fresh Server or Existing Server setup", "Quick Setup")
text = text.replace("Fresh Server or Existing Server", "Quick Setup")
text = text.replace("Run Health Check", "Run Review Setup")
text = text.replace("Run **Setup Check**", "Run **Review Setup**")
text = text.replace(
    '"Run `/dank setup` → Advanced Setup → Ticket Menu Options → Create Recommended Ticket Menu."',
    '"Run `/dank setup` → Manage Setup → All Features & Settings → Tickets → Ticket Choices."',
)
text = text.replace(
    '"Run `/dank setup` → Existing Server and pick the correct roles/channels."',
    '"Run `/dank setup` → Manage Setup → All Features & Settings → Setup Plan & Server Items → Choose Roles & Channels."',
)
recovery_registration = r'''
def register_public_setup_recovery_commands(bot: Any, tree: Any) -> None:
    """Register recovery helpers without replacing the canonical setup home."""
    global _PATCHED
    _ = bot, tree
    _PATCHED = True
    print("✅ public_setup_recovery: direct repair/restart center ready")
'''
text = replace_between(text, "async def _build_main_with_recovery(", "__all__ =", recovery_registration, label="recovery direct registration")
path.write_text(text, encoding="utf-8")


path = Path("stoney_verify/commands_ext/public_setup_cleanup.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "This patches /dank setup -> Recovery / Start Over with cleanup options that",
    "This provides direct Repair / Restart cleanup options that",
)
text = text.replace("Back to Recovery", "Back to Repair & Restart")
text = text.replace("Setup Recovery Center", "Repair & Restart Setup")
text = text.replace("Recovery actions", "Repair actions")
cleanup_registration = r'''
def register_public_setup_cleanup_commands(bot: Any, tree: Any) -> None:
    """Register direct cleanup helpers without replacing recovery owners."""
    global _PATCHED
    _ = bot, tree
    _PATCHED = True
    print("✅ public_setup_cleanup: direct selective cleanup UX ready")
'''
text = replace_between(text, "def _patch() -> None:", "__all__ =", cleanup_registration, label="cleanup direct registration")
text = text.replace(
    '__all__ = ["register_public_setup_cleanup_commands", "collect_setup_cleanup_candidates"]',
    '__all__ = [\n    "register_public_setup_cleanup_commands",\n    "collect_setup_cleanup_candidates",\n    "patched_recovery_embed",\n    "PatchedRecoveryCenterView",\n]',
)
path.write_text(text, encoding="utf-8")


path = Path("stoney_verify/config_history_ui.py")
text = path.read_text(encoding="utf-8")
text = text.replace("_back_to_other_settings", "_back_to_all_features")
text = text.replace("Back to Other Settings", "Back to All Features")
text = text.replace("Back Home", "Setup Home")
close_helper = r'''
async def _close_setup(interaction: discord.Interaction) -> None:
    from .commands_ext import public_setup_recommend as recommend
    await recommend._close_setup(interaction)
'''
if "async def _close_setup(" not in text:
    marker = "def _history_embed("
    at = text.find(marker)
    if at < 0:
        raise SystemExit("ERROR: config history close helper marker not found")
    text = text[:at] + close_helper.strip() + "\n\n\n" + text[at:]

old_history_home = '''    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:home",
        row=2,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_home(interaction)
'''
new_history_home = old_history_home + r'''

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:close",
        row=2,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)
'''
if old_history_home not in text:
    raise SystemExit("ERROR: history home button block not found")
text = text.replace(old_history_home, new_history_home, 1)

old_backup_home = '''    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:backup_home",
        row=2,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_home(interaction)
'''
new_backup_home = old_backup_home + r'''

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:backup_close",
        row=2,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)
'''
if old_backup_home not in text:
    raise SystemExit("ERROR: backup home button block not found")
text = text.replace(old_backup_home, new_backup_home, 1)

old_detail_settings = '''    @discord.ui.button(
        label="Back to All Features",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:detail_settings",
        row=2,
    )
    async def settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_to_all_features(interaction)
'''
new_detail_settings = old_detail_settings + r'''

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:detail_home",
        row=3,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_home(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:detail_close",
        row=3,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)
'''
if old_detail_settings not in text:
    raise SystemExit("ERROR: detail settings block not found")
text = text.replace(old_detail_settings, new_detail_settings, 1)

old_picker_back = '''    @discord.ui.button(
        label="Back to Version",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:picker_back",
        row=2,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_config_version_detail(interaction, self.version_id)
'''
new_picker_back = old_picker_back + r'''

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:picker_home",
        row=3,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_home(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:picker_close",
        row=3,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)
'''
if old_picker_back not in text:
    raise SystemExit("ERROR: picker back block not found")
text = text.replace(old_picker_back, new_picker_back, 1)
path.write_text(text, encoding="utf-8")


path = Path("tests/test_setup_navigation_ux_overhaul_behavior.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    'assert labels(view) == ["Setup Home", "Close"]',
    'assert labels(view) == ["Back to All Features", "Setup Home", "Close"]',
)
path.write_text(text, encoding="utf-8")

Path("tests/test_setup_nested_navigation_behavior.py").write_text(
    r'''from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord

from stoney_verify import config_history_ui
from stoney_verify.commands_ext import public_setup_cleanup as cleanup
from stoney_verify.commands_ext import public_setup_full_customization as customization
from stoney_verify.commands_ext import public_setup_recommend as recommend
from stoney_verify.commands_ext import public_setup_recovery as recovery
from stoney_verify.commands_ext import public_setup_solid as solid


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def labels(view: discord.ui.View) -> list[str]:
    return [
        str(getattr(child, "label", "") or "")
        for child in view.children
        if isinstance(child, discord.ui.Button)
    ]


def test_generic_nested_setup_navigation_is_predictable() -> None:
    assert labels(solid.SetupNavView()) == [
        "Back to All Features",
        "Setup Home",
        "Close",
    ]


def test_full_customization_pages_share_parent_home_close() -> None:
    views = (
        customization.FullChooseExistingView(),
        customization.RoleCustomizationPageOne(),
        customization.RoleCustomizationPageTwo(),
        customization.DiscordCategoryCustomizationView(),
        customization.ChannelCustomizationPageOne(),
        customization.ChannelCustomizationPageTwo(),
        customization.LogStatusCustomizationView(),
    )
    for view in views:
        view_labels = labels(view)
        assert "Back to All Features" in view_labels
        assert "Setup Home" in view_labels
        assert "Close" in view_labels
        row_counts: dict[int, int] = {}
        for child in view.children:
            row = int(getattr(child, "row", 0) or 0)
            row_counts[row] = row_counts.get(row, 0) + 1
        assert all(count <= 5 for count in row_counts.values())
        assert len(view.children) <= 25


def test_full_customization_registration_does_not_replace_solid_classes() -> None:
    before = solid.ChooseExistingView
    customization._PATCHED = False
    customization.install_full_customization()
    assert solid.ChooseExistingView is before


def test_recovery_registration_does_not_replace_setup_home() -> None:
    before = solid._build_main_setup_payload
    recovery._PATCHED = False
    recovery.register_public_setup_recovery_commands(None, None)
    assert solid._build_main_setup_payload is before


def test_cleanup_registration_does_not_replace_recovery_owners() -> None:
    before_embed = recovery._build_recovery_embed
    before_view = recovery.RecoveryCenterView
    cleanup._PATCHED = False
    cleanup.register_public_setup_cleanup_commands(None, None)
    assert recovery._build_recovery_embed is before_embed
    assert recovery.RecoveryCenterView is before_view


def test_existing_server_route_uses_direct_customization_view(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def allowed(interaction: Any) -> bool:
        return True

    class Response:
        async def edit_message(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(solid, "_require_setup_permission", allowed)
    interaction = SimpleNamespace(response=Response())
    run(recommend._open_existing_server(interaction))
    assert isinstance(captured.get("view"), customization.FullChooseExistingView)


def test_config_history_navigation_matches_aio_hierarchy() -> None:
    main = config_history_ui.ConfigHistoryView([])
    assert labels(main) == [
        "Choose Backup Contents",
        "Refresh",
        "Back to All Features",
        "Setup Home",
        "Close",
    ]

    backup = config_history_ui.BackupContentsView()
    assert "Setup Home" in labels(backup)
    assert "Close" in labels(backup)

    detail = config_history_ui.ConfigVersionDetailView(
        1,
        {
            "changed_items": ["ticket_prefix"],
            "missing_items": ["ticket_prefix"],
        },
    )
    assert "Back to All Features" in labels(detail)
    assert "Setup Home" in labels(detail)
    assert "Close" in labels(detail)
''',
    encoding="utf-8",
)

print("✅ Nested setup screens now have parent, home, and close routes")
print("✅ Full customization uses direct integration instead of class patching")
print("✅ Recovery and cleanup no longer replace setup owners")
print("✅ Backups & History uses the AIO navigation language")
print("✅ Added behavioral nested-navigation and ownership tests")
