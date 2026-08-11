from __future__ import annotations

"""Final Discord-visible public command surface.

All implementation modules are registered first so their services, listeners,
persistent views, safety checks, and compatibility shims remain loaded. This
module then compacts only the *application-command tree* into a few stable
doorways backed by action-complete UI centers.
"""

from typing import Any, Optional

import discord
from discord import app_commands

from .public_setup_group import dank_group

_INSTALLED = False

_UPLOAD_CHOICES = [
    app_commands.Choice(name="Join Card Background", value="join_background"),
    app_commands.Choice(name="Exit Card Background", value="exit_background"),
    app_commands.Choice(name="Custom Card Font", value="custom_font"),
]


async def _private(
    interaction: discord.Interaction,
    content: str = "",
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: dict[str, Any] = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if content:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    if view is not None:
        payload["view"] = view
    if interaction.response.is_done():
        await interaction.followup.send(**payload)
    else:
        await interaction.response.send_message(**payload)


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = 900) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await _private(interaction, "❌ Open your own `/dank home` panel to use these controls.")
        return False


def _home_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🛡️ Dank Shield Control Center",
        description=(
            "Everything is menu-first now. Pick the area you need; buttons, selects, and modals "
            "carry out the real actions without flooding Discord autocomplete."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Server systems",
        value="⚙️ Setup • 🛡️ Protection • 🎫 Tickets • ✅ Verification • 👋 Welcome, Join & Exit",
        inline=False,
    )
    embed.add_field(
        name="People & appearance",
        value="👥 Members & Moderation • 🎨 Server Design • 🎭 Roles & Profiles • 🧾 Logs • 🪪 My Profile",
        inline=False,
    )
    embed.add_field(
        name="Utility",
        value="🧰 Community Tools • 📡 Status • 🩺 Diagnostics • 📎 Card Assets • ❓ Help",
        inline=False,
    )
    embed.add_field(
        name="Tiny command surface",
        value=(
            "`/dank home` is the main doorway. `/mod`, `/ticket`, `/tickets`, and `/verify` are optional "
            "fast doorways to the same guided centers. `/dank upload` exists only because Discord "
            "buttons cannot provide a file-attachment field."
        ),
        inline=False,
    )
    embed.set_footer(text="Dank Shield • app-style controls • actions preserved")
    return embed


def _help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="❓ Dank Shield Help",
        description="You no longer need to memorize subcommands. Open a center and follow the controls.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Normal entry",
        value="`/dank home` — the complete Dank Shield control center",
        inline=False,
    )
    embed.add_field(
        name="Optional fast doorways",
        value=(
            "`/mod` — moderation/member center\n"
            "`/ticket` — current ticket controls\n"
            "`/tickets` — queues, ticket setup, routing, categories\n"
            "`/verify` — verification status/repair center"
        ),
        inline=False,
    )
    embed.add_field(
        name="Uploads",
        value=(
            "`/dank upload` — choose **Join Card Background**, **Exit Card Background**, or **Custom Card Font**, "
            "then attach the file. Every non-upload card action remains in the Welcome/Exit menus."
        ),
        inline=False,
    )
    embed.set_footer(text="No hidden capability was removed; only redundant command entry points were consolidated.")
    return embed


def _asset_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📎 Card Assets",
        description=(
            "Discord cannot open an attachment picker from a button, so file uploads use the one compact "
            "`/dank upload` command. Everything else is button-driven."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Upload choices",
        value=(
            "• **Join Card Background** — PNG/JPG/WEBP artwork\n"
            "• **Exit Card Background** — PNG/JPG/WEBP artwork\n"
            "• **Custom Card Font** — validated supported font file"
        ),
        inline=False,
    )
    embed.add_field(
        name="Remove uploaded font",
        value="Use **Clear Uploaded Font** below. No extra slash command is needed.",
        inline=False,
    )
    return embed


class CompactDankHomeView(_OwnedView):
    @discord.ui.button(label="Setup & Settings", emoji="⚙️", style=discord.ButtonStyle.success, row=0)
    async def setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_hub import _invoke_saved
        await _invoke_saved("setup", interaction)

    @discord.ui.button(label="Protection", emoji="🛡️", style=discord.ButtonStyle.primary, row=0)
    async def protection(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_hub import _admin_or_manage
        if not _admin_or_manage(interaction):
            return await _private(interaction, "❌ Protection settings require **Manage Server** or **Administrator**.")
        from . import public_protection_center
        await public_protection_center._refresh_panel(
            interaction,
            content="🛡️ Protection Center opened from `/dank home`.",
        )

    @discord.ui.button(label="Tickets", emoji="🎫", style=discord.ButtonStyle.primary, row=0)
    async def tickets(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_ticket_command_center import open_ticket_operations_center
        await open_ticket_operations_center(interaction)

    @discord.ui.button(label="Verification", emoji="✅", style=discord.ButtonStyle.primary, row=0)
    async def verification(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_verify_command_center import open_verify_command_center
        await open_verify_command_center(interaction)

    @discord.ui.button(label="Welcome, Join & Exit", emoji="👋", style=discord.ButtonStyle.primary, row=0)
    async def welcome(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.welcome_setup_ui import open_welcome_setup
        await open_welcome_setup(interaction)

    @discord.ui.button(label="Members & Moderation", emoji="👥", style=discord.ButtonStyle.secondary, row=1)
    async def members(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_mod_command_center import open_mod_command_center
        await open_mod_command_center(interaction)

    @discord.ui.button(label="Server Design", emoji="🎨", style=discord.ButtonStyle.secondary, row=1)
    async def design(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_hub import _admin_or_manage
        if not _admin_or_manage(interaction):
            return await _private(interaction, "❌ Server Design requires **Manage Server** or **Administrator**.")
        from . import public_design_bridge
        await public_design_bridge.open_design_studio_from_setup(interaction)

    @discord.ui.button(label="Roles & Profiles", emoji="🎭", style=discord.ButtonStyle.secondary, row=1)
    async def roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_hub import _admin_or_manage, open_profile_entry
        if not _admin_or_manage(interaction):
            return await open_profile_entry(interaction)
        from .public_self_roles_group import _post_profile_builder
        await _post_profile_builder(interaction, title="Profile Panel")

    @discord.ui.button(label="Logs & Activity", emoji="🧾", style=discord.ButtonStyle.secondary, row=1)
    async def logs(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_hub import _admin_or_manage
        if not _admin_or_manage(interaction):
            return await _private(interaction, "❌ Log settings require **Manage Server** or **Administrator**.")
        from .public_setup_recommend import _open_advanced_logs_activity
        await _open_advanced_logs_activity(interaction)

    @discord.ui.button(label="My Profile", emoji="🪪", style=discord.ButtonStyle.secondary, row=1)
    async def profile(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_hub import open_profile_entry
        await open_profile_entry(interaction)

    @discord.ui.button(label="Community Tools", emoji="🧰", style=discord.ButtonStyle.secondary, row=2)
    async def community(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_community_tools import open_community_tools
        await open_community_tools(interaction, replace_message=True)

    @discord.ui.button(label="Status", emoji="📡", style=discord.ButtonStyle.secondary, row=2)
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_hub import _invoke_saved
        await _invoke_saved("status", interaction)

    @discord.ui.button(label="Diagnostics", emoji="🩺", style=discord.ButtonStyle.secondary, row=2)
    async def diagnostics(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_hub import _invoke_saved
        await _invoke_saved("diagnostics", interaction)

    @discord.ui.button(label="Card Assets", emoji="📎", style=discord.ButtonStyle.secondary, row=2)
    async def assets(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=_asset_embed(),
            view=CardAssetView(self.owner_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Help", emoji="❓", style=discord.ButtonStyle.secondary, row=2)
    async def help(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=_help_embed(),
            view=CompactHelpView(self.owner_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger, row=3)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(content="Dank Shield Control Center closed.", embed=None, view=None)


class CompactHelpView(_OwnedView):
    @discord.ui.button(label="Control Center", emoji="🏠", style=discord.ButtonStyle.primary)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=_home_embed(),
            view=CompactDankHomeView(self.owner_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(content="Help closed.", embed=None, view=None)


class CardAssetView(_OwnedView):
    @discord.ui.button(label="Welcome / Exit Studio", emoji="👋", style=discord.ButtonStyle.primary, row=0)
    async def studio(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.welcome_setup_ui import open_welcome_setup
        await open_welcome_setup(interaction)

    @discord.ui.button(label="Clear Uploaded Font", emoji="🧹", style=discord.ButtonStyle.danger, row=0)
    async def clear_font(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_welcome_card_studio import welcome_card_font_clear
        await welcome_card_font_clear(interaction)

    @discord.ui.button(label="Control Center", emoji="🏠", style=discord.ButtonStyle.secondary, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=_home_embed(),
            view=CompactDankHomeView(self.owner_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def open_compact_dank_home(interaction: discord.Interaction) -> None:
    await _private(
        interaction,
        embed=_home_embed(),
        view=CompactDankHomeView(int(interaction.user.id)),
    )


@app_commands.describe(
    asset="What card asset are you uploading?",
    file="Attach the image or font file.",
)
@app_commands.choices(asset=_UPLOAD_CHOICES)
async def consolidated_asset_upload(
    interaction: discord.Interaction,
    asset: app_commands.Choice[str],
    file: discord.Attachment,
) -> None:
    value = str(getattr(asset, "value", asset) or "").strip().lower()
    if value == "join_background":
        from .public_welcome_group import welcome_card_upload
        return await welcome_card_upload(interaction, background=file)
    if value == "exit_background":
        from .public_exit_card_studio import exit_card_upload
        return await exit_card_upload(interaction, background=file)
    if value == "custom_font":
        from .public_welcome_card_studio import welcome_card_font_upload
        return await welcome_card_font_upload(interaction, font_file=file)
    await _private(interaction, "❌ That upload type is unavailable.")


def _remove_tree_command(tree: Any, name: str) -> None:
    try:
        tree.remove_command(name, guild=None)
    except Exception:
        try:
            commands = getattr(tree, "_global_commands", None)
            if isinstance(commands, dict):
                commands.pop(name, None)
        except Exception:
            pass


def _standalone(name: str, description: str, callback: Any) -> app_commands.Command:
    resolved = getattr(callback, "callback", callback)
    if not callable(resolved):
        raise TypeError(f"{name} callback is not callable")
    return app_commands.Command(name=name, description=description, callback=resolved)


def _compact_dank_children(tree: Any) -> int:
    for item in list(getattr(dank_group, "commands", []) or []):
        try:
            dank_group.remove_command(str(getattr(item, "name", "")))
        except Exception:
            pass

    dank_group.add_command(_standalone("home", "Open the complete Dank Shield control center.", open_compact_dank_home))
    upload = app_commands.Command(
        name="upload",
        description="Upload Join/Exit card artwork or a custom card font.",
        callback=consolidated_asset_upload,
    )
    dank_group.add_command(upload)

    from .public_command_hub import DANK_PAYLOAD_SAFETY_LIMIT, dank_payload_size
    size = dank_payload_size(tree)
    if size > DANK_PAYLOAD_SAFETY_LIMIT:
        raise RuntimeError(
            f"compact v2 /dank payload={size} exceeds safety limit={DANK_PAYLOAD_SAFETY_LIMIT}"
        )
    return size


def install_compact_public_surface_v2(bot: Any, tree: Any) -> dict[str, Any]:
    global _INSTALLED
    from .public_community_tools import ensure_community_tools_runtime
    ensure_community_tools_runtime(bot)

    if _INSTALLED:
        roots = sorted(str(getattr(item, "name", "")) for item in tree.get_commands(guild=None))
        return {"installed": True, "roots": roots}

    from .public_mod_command_center import open_mod_command_center
    from .public_ticket_command_center import (
        open_current_ticket_center,
        open_ticket_operations_center,
    )
    from .public_verify_command_center import open_verify_command_center

    replacements = (
        ("mod", "Open the complete moderation and member action center.", open_mod_command_center),
        ("ticket", "Open controls for the current or selected ticket.", open_current_ticket_center),
        ("tickets", "Open ticket queues, lookup, setup, routing, and category tools.", open_ticket_operations_center),
        ("verify", "Open the complete verification status and repair center.", open_verify_command_center),
    )
    for name, description, callback in replacements:
        _remove_tree_command(tree, name)
        tree.add_command(_standalone(name, description, callback))

    for retired_root in ("ticket-intake", "ticket-category", "ticket-panel"):
        _remove_tree_command(tree, retired_root)

    size = _compact_dank_children(tree)
    roots = sorted(str(getattr(item, "name", "")) for item in tree.get_commands(guild=None))
    expected_roots = {"dank", "mod", "ticket", "tickets", "verify"}
    command_roots = {name for name in roots if name != "View Dank Profile"}
    if command_roots != expected_roots:
        raise RuntimeError(
            f"compact v2 final roots mismatch expected={sorted(expected_roots)} actual={sorted(command_roots)}"
        )

    dank_children = sorted(str(getattr(item, "name", "")) for item in dank_group.commands)
    if dank_children != ["home", "upload"]:
        raise RuntimeError(f"compact v2 /dank children mismatch: {dank_children}")

    _INSTALLED = True
    result = {
        "installed": True,
        "roots": roots,
        "dank_children": dank_children,
        "dank_payload": size,
    }
    print(
        "✅ public_command_surface_v2 compact UI installed "
        f"roots={roots} dank_children={dank_children} payload={size}"
    )
    return result


__all__ = [
    "CardAssetView",
    "CompactDankHomeView",
    "consolidated_asset_upload",
    "install_compact_public_surface_v2",
    "open_compact_dank_home",
]
