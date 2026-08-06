from __future__ import annotations

"""UI-first public ``/dank`` command surface.

Discord serializes every nested subcommand into the parent application-command
payload. Dank Shield keeps a few obvious entry commands and moves the full
feature set into buttons, selects, and modals. The compact Welcome group retains
stable entry/upload commands while the canonical Studio owns all live controls.
"""

import json
from typing import Any, Optional

import discord
from discord import app_commands

from .public_setup_group import dank_group

MAX_DANK_PAYLOAD = 8000
DANK_PAYLOAD_SAFETY_LIMIT = 7600
_COMPACTED = False
_ORIGINAL_COMMANDS: dict[str, Any] = {}


def _admin_or_manage(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        permissions = interaction.user.guild_permissions
        return bool(permissions.administrator or permissions.manage_guild)
    except Exception:
        return False


async def _private(
    interaction: discord.Interaction,
    *,
    content: str = "",
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
    if not interaction.response.is_done():
        await interaction.response.send_message(**payload)
    else:
        await interaction.followup.send(**payload)


async def _invoke_saved(name: str, interaction: discord.Interaction) -> None:
    command = _ORIGINAL_COMMANDS.get(str(name))
    callback = getattr(command, "callback", None)
    if not callable(callback):
        return await _private(
            interaction,
            content=f"❌ The **{name}** screen is temporarily unavailable. Open `/dank home` again.",
        )
    await callback(interaction)


async def open_dank_home(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="🛡️ Dank Shield Control Center",
        description=(
            "Choose what you want to manage. The slash-command list stays short "
            "and readable; the full tools live in guided menus like a real "
            "control panel."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="For everyone",
        value="🪪 My Profile • ❓ Help • 📡 Status",
        inline=False,
    )
    embed.add_field(
        name="For server managers",
        value=(
            "⚙️ Setup • 🛡️ Protection • 👋 Welcome & Join • 👥 Members • "
            "🎨 Design • 🎭 Roles & Profiles • 🧾 Logs • 🩺 Diagnostics"
        ),
        inline=False,
    )
    embed.add_field(
        name="Why this is easier",
        value=(
            "Buttons show only the next useful choices, mobile screens stay "
            "uncluttered, and advanced settings no longer flood Discord autocomplete."
        ),
        inline=False,
    )
    embed.set_footer(text="Dank Shield • UI-first command center")
    await _private(
        interaction,
        embed=embed,
        view=DankHomeView(owner_id=interaction.user.id),
    )


async def open_profile_entry(interaction: discord.Interaction) -> None:
    from stoney_verify.profile_signature_studio import open_profile_signature_studio

    await open_profile_signature_studio(interaction)


async def open_help_entry(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="❓ Dank Shield Help",
        description=(
            "Start with `/dank home`. It opens every Dank Shield area without "
            "making you memorize dozens of commands."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Main commands",
        value=(
            "`/dank home` — full control center\n"
            "`/dank setup` — guided server setup\n"
            "`/dank members` — complete member command center\n"
            "`/dank profile` — your profile signature, privacy, platforms, and appearance\n"
            "`/dank status` — live bot status\n"
            "`/dank diagnostics` — read-only manager health report"
        ),
        inline=False,
    )
    embed.add_field(
        name="Welcome Card Studio",
        value=(
            "Use `/dank welcome card-studio` for the complete live join-card "
            "panel, `/dank welcome card-preview` for a production preview, "
            "`/dank welcome card-upload` for custom artwork, and "
            "`/dank welcome card-font-upload` for a licensed custom font."
        ),
        inline=False,
    )
    embed.add_field(
        name="Daily commands remain direct",
        value="Ticket, verification, and moderation commands keep their own normal command families.",
        inline=False,
    )
    await _private(
        interaction,
        embed=embed,
        view=DankHelpView(owner_id=interaction.user.id),
    )


class _OwnedView(discord.ui.View):
    def __init__(self, *, owner_id: int, timeout: float = 900) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await _private(
            interaction,
            content="❌ Open your own `/dank home` panel to use these controls.",
        )
        return False


class DankHomeView(_OwnedView):
    @discord.ui.button(
        label="Setup & Settings",
        emoji="⚙️",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def setup(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _invoke_saved("setup", interaction)

    @discord.ui.button(
        label="Protection",
        emoji="🛡️",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def protection(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _admin_or_manage(interaction):
            return await _private(
                interaction,
                content="❌ Protection settings require **Manage Server** or **Administrator**.",
            )
        from . import public_protection_center

        await public_protection_center._refresh_panel(
            interaction,
            content="🛡️ Protection Center opened from `/dank home`.",
        )

    @discord.ui.button(
        label="Welcome & Join",
        emoji="👋",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def welcome(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        from stoney_verify.welcome_setup_ui import open_welcome_setup

        await open_welcome_setup(interaction)

    @discord.ui.button(
        label="My Profile",
        emoji="🪪",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def profile(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await open_profile_entry(interaction)

    @discord.ui.button(
        label="Members",
        emoji="👥",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def members(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _invoke_saved("members", interaction)

    @discord.ui.button(
        label="Server Design",
        emoji="🎨",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def design(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _admin_or_manage(interaction):
            return await _private(
                interaction,
                content="❌ Server Design requires **Manage Server** or **Administrator**.",
            )
        from . import public_design_bridge

        await public_design_bridge.open_design_studio_from_setup(interaction)

    @discord.ui.button(
        label="Roles & Profiles",
        emoji="🎭",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def roles(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _admin_or_manage(interaction):
            return await open_profile_entry(interaction)
        from .public_self_roles_group import _post_profile_builder

        await _post_profile_builder(interaction, title="Profile Panel")

    @discord.ui.button(
        label="Logs & Activity",
        emoji="🧾",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def logs(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _admin_or_manage(interaction):
            return await _private(
                interaction,
                content="❌ Log settings require **Manage Server** or **Administrator**.",
            )
        from .public_setup_recommend import _open_advanced_logs_activity

        await _open_advanced_logs_activity(interaction)

    @discord.ui.button(
        label="Diagnostics",
        emoji="🩺",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def diagnostics(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _invoke_saved("diagnostics", interaction)

    @discord.ui.button(
        label="Status",
        emoji="📡",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def status(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _invoke_saved("status", interaction)

    @discord.ui.button(
        label="Help",
        emoji="❓",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def help(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await open_help_entry(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        row=2,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.edit_message(
            content="Dank Shield Control Center closed.",
            embed=None,
            view=None,
        )


class DankHelpView(_OwnedView):
    @discord.ui.button(
        label="Open Control Center",
        emoji="🛡️",
        style=discord.ButtonStyle.primary,
    )
    async def home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await open_dank_home(interaction)

    @discord.ui.button(
        label="My Profile",
        emoji="🪪",
        style=discord.ButtonStyle.secondary,
    )
    async def profile(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await open_profile_entry(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.edit_message(
            content="Help closed.",
            embed=None,
            view=None,
        )


def _command_payload(group: app_commands.Group, tree: Any) -> dict[str, Any]:
    try:
        return dict(group.to_dict(tree))
    except TypeError:
        return dict(group.to_dict())


def dank_payload_size(tree: Any) -> int:
    payload = json.dumps(
        _command_payload(dank_group, tree),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return max(len(payload), len(payload.encode("utf-8")))


def _new_command(name: str, description: str, callback: Any) -> app_commands.Command:
    resolved_callback = getattr(callback, "callback", callback)
    if not callable(resolved_callback):
        raise TypeError(
            f"{name} does not provide a callable application-command callback"
        )
    return app_commands.Command(
        name=name,
        description=description,
        callback=resolved_callback,
    )


def _welcome_upload_group() -> app_commands.Group:
    from .public_welcome_group import welcome_card_upload
    from .public_welcome_card_studio import (
        welcome_card_font_clear,
        welcome_card_font_upload,
    )
    from stoney_verify.welcome_card_studio_ui import (
        open_welcome_card_studio,
        send_studio_preview,
    )
    from stoney_verify.welcome_setup_ui import open_welcome_setup

    group = app_commands.Group(
        name="welcome",
        description="Open Welcome & Join, the live Card Studio, or upload assets.",
    )
    group.add_command(
        _new_command(
            "open",
            "Open the complete Welcome & Join control panel.",
            open_welcome_setup,
        )
    )
    group.add_command(
        _new_command(
            "card-studio",
            "Open the complete live Welcome Card Studio.",
            open_welcome_card_studio,
        )
    )
    group.add_command(
        _new_command(
            "card-preview",
            "Preview the exact current production join-card design.",
            send_studio_preview,
        )
    )
    group.add_command(
        _new_command(
            "card-upload",
            "Upload custom join-card background artwork.",
            welcome_card_upload,
        )
    )
    group.add_command(
        _new_command(
            "card-font-upload",
            "Upload a licensed custom join-card font.",
            welcome_card_font_upload,
        )
    )
    group.add_command(
        _new_command(
            "card-font-clear",
            "Remove the uploaded join-card font.",
            welcome_card_font_clear,
        )
    )
    return group


def compact_public_dank_surface(bot: Any, tree: Any) -> int:
    """Replace nested feature trees with stable UI entry points before sync."""
    global _COMPACTED
    _ = bot

    if _COMPACTED:
        size = dank_payload_size(tree)
        if size > DANK_PAYLOAD_SAFETY_LIMIT:
            raise RuntimeError(
                f"/dank command payload grew to {size}/{MAX_DANK_PAYLOAD} after compaction"
            )
        return size

    children = list(getattr(dank_group, "commands", []) or [])
    for command in children:
        name = str(getattr(command, "name", "") or "")
        if name and name not in _ORIGINAL_COMMANDS:
            _ORIGINAL_COMMANDS[name] = command

    for command in children:
        name = str(getattr(command, "name", "") or "")
        if name:
            try:
                dank_group.remove_command(name)
            except Exception:
                pass

    for name in ("setup", "status", "diagnostics", "members"):
        command = _ORIGINAL_COMMANDS.get(name)
        if command is not None and not isinstance(command, app_commands.Group):
            dank_group.add_command(command)

    dank_group.add_command(
        _new_command("home", "Open the Dank Shield control center.", open_dank_home)
    )
    dank_group.add_command(
        _new_command("profile", "Open your profile signature settings.", open_profile_entry)
    )
    dank_group.add_command(
        _new_command("help", "Show the short Dank Shield command guide.", open_help_entry)
    )
    dank_group.add_command(_welcome_upload_group())

    _COMPACTED = True
    size = dank_payload_size(tree)
    children_after = sorted(
        str(getattr(command, "name", "")) for command in dank_group.commands
    )
    print(
        f"✅ public_command_hub compacted /dank payload={size}/{MAX_DANK_PAYLOAD} "
        f"children={children_after}"
    )
    if size > DANK_PAYLOAD_SAFETY_LIMIT:
        raise RuntimeError(
            f"/dank command payload is {size}/{MAX_DANK_PAYLOAD}; "
            f"UI-first safety limit is {DANK_PAYLOAD_SAFETY_LIMIT}"
        )
    return size


__all__ = [
    "DANK_PAYLOAD_SAFETY_LIMIT",
    "MAX_DANK_PAYLOAD",
    "DankHomeView",
    "compact_public_dank_surface",
    "dank_payload_size",
    "open_dank_home",
    "open_help_entry",
    "open_profile_entry",
]
