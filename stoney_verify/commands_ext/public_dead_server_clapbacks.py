from __future__ import annotations

"""Staff panel for Dead Server Clapbacks (Community Tools).

Guild-wide behaviour, so configuration requires Manage Server -- the same
authority bar as the Quiet Server Notice. The panel only writes the
feature's own guild-config keys through the canonical guild_config writer.
"""

import random
from typing import Any, Optional

import discord

from ..panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from ..ui.picker import DankRoleSelect

from stoney_verify.dead_server_clapbacks import (
    ENABLED_KEY,
    PING_ROLE_KEY,
    ClapbackConfig,
    parse_clapback_config,
    pick_clapback_line,
)
from stoney_verify.guild_config import (
    clear_guild_config_keys,
    get_guild_config,
    upsert_guild_config,
)

_ALLOWED_MENTIONS = discord.AllowedMentions.none()
_WRITE_SOURCE = "dead_server_clapbacks_panel"


def _manage_clapbacks(interaction: discord.Interaction) -> bool:
    member = interaction.user
    return bool(
        isinstance(member, discord.Member)
        and (member.guild_permissions.administrator or member.guild_permissions.manage_guild)
    )


async def _private(
    interaction: discord.Interaction,
    content: str = "",
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: dict[str, Any] = {"ephemeral": True, "allowed_mentions": _ALLOWED_MENTIONS}
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


async def _replace(
    interaction: discord.Interaction,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    await interaction.response.edit_message(
        content=content,
        embed=embed,
        view=view,
        allowed_mentions=_ALLOWED_MENTIONS,
    )


def clapback_status_embed(
    config: ClapbackConfig,
    *,
    ping_role_name: str = "",
) -> discord.Embed:
    state = "▶️ On" if config.enabled else "⏸️ Off"
    embed = discord.Embed(
        title="💀 Dead Server Clapbacks",
        description=(
            f"{state} • When a member calls the server or chat dead, Dank Shield "
            "replies with one short comeback instead of letting it sit there."
        ),
        color=discord.Color.green() if config.enabled else discord.Color.orange(),
    )
    if config.ping_role_id > 0:
        role_label = f"@{ping_role_name}" if ping_role_name else "The configured role"
        embed.add_field(
            name="Ping role",
            value=(
                f"{role_label} — members who ping it first are spared the "
                "\"you didn't even ping\" lines."
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="Ping role",
            value=(
                "Not set. The \"you didn't even ping\" lines stay off until a "
                "Stoners/sesh role is chosen here (or set in Community & Pings)."
            ),
            inline=False,
        )
    embed.add_field(
        name="Cooldowns",
        value=(
            f"One reply per channel every **{int(config.channel_cooldown_seconds) // 60} min**, "
            f"and per member every **{int(config.user_cooldown_seconds) // 60} min**."
        ),
        inline=False,
    )
    embed.add_field(
        name="Never fires on",
        value="Bots, the server owner, members with Manage Messages, announcements, DMs, or ticket channels.",
        inline=False,
    )
    embed.set_footer(text="Replies tag the complainer • the role name is plain text and never pinged")
    return embed


async def _current_config(interaction: discord.Interaction) -> Optional[ClapbackConfig]:
    if interaction.guild is None:
        return None
    raw = await get_guild_config(int(interaction.guild.id), refresh=True)
    if str(raw.get("source") or "").startswith("unavailable:"):
        return None
    return parse_clapback_config(raw)


def _ping_role_name(interaction: discord.Interaction, config: ClapbackConfig) -> str:
    guild = interaction.guild
    if guild is None or config.ping_role_id <= 0:
        return ""
    role = guild.get_role(int(config.ping_role_id))
    return str(getattr(role, "name", "") or "") if role is not None else ""


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = PRIVATE_MENU_TTL_SECONDS) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await _private(interaction, "❌ Open your own `/dank home` panel to use these controls.")
        return False


class DeadServerClapbackView(_OwnedView):
    def __init__(self, owner_id: int, config: ClapbackConfig) -> None:
        super().__init__(owner_id)
        self.config = config
        for item in self.children:
            if str(getattr(item, "label", "") or "") == "Turn On / Off":
                item.label = "Turn Off" if config.enabled else "Turn On"
                item.emoji = "⏸️" if config.enabled else "▶️"

    @discord.ui.button(label="Turn On / Off", emoji="▶️", style=discord.ButtonStyle.primary, row=0)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _manage_clapbacks(interaction):
            return await _private(interaction, "❌ Dead Server Clapbacks require **Manage Server**.")
        if interaction.guild is None:
            return await _private(interaction, "❌ Use this in a server.")
        current = await _current_config(interaction)
        if current is None:
            return await _private(interaction, "❌ Server settings are unavailable right now; nothing was changed.")
        try:
            await upsert_guild_config(
                int(interaction.guild.id),
                {
                    ENABLED_KEY: not current.enabled,
                    "__config_write_mode": "explicit_override",
                    "__config_write_source": _WRITE_SOURCE,
                },
            )
        except Exception:
            return await _private(interaction, "❌ The setting could not be saved; nothing was changed.")
        saved = await _current_config(interaction) or parse_clapback_config({})
        await _replace(
            interaction,
            embed=clapback_status_embed(saved, ping_role_name=_ping_role_name(interaction, saved)),
            view=DeadServerClapbackView(self.owner_id, saved),
        )

    @discord.ui.button(label="Set Ping Role", emoji="🌿", style=discord.ButtonStyle.secondary, row=0)
    async def set_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _manage_clapbacks(interaction):
            return await _private(interaction, "❌ Dead Server Clapbacks require **Manage Server**.")
        await _private(
            interaction,
            "Choose the role members should ping before calling the server dead "
            "(usually your Stoners / sesh role). Picking one only affects the "
            "\"you didn't even ping\" lines.",
            view=ClapbackRoleSelectView(self.owner_id),
        )

    @discord.ui.button(label="Preview a Line", emoji="👁️", style=discord.ButtonStyle.secondary, row=0)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        picked = pick_clapback_line(
            self.config,
            did_ping=True,
            ping_role_name=_ping_role_name(interaction, self.config) or "Stoners",
            rng=random.Random(),
        )
        if picked is None:
            return await _private(interaction, "ℹ️ No clapback lines are available.")
        _category, line = picked
        await _private(interaction, f"👁️ **Private preview** — a reply could look like:\n> {line}")

    @discord.ui.button(label="Community Tools", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_community_tools import _center_embed, CommunityToolsView

        await _replace(interaction, embed=_center_embed(), view=CommunityToolsView(self.owner_id))


class ClapbackRoleSelect(DankRoleSelect):
    """House role picker for the clapback ping role.

    Uses the framework's DankRoleSelect wrapper (owner check, house
    wording); the pick saves through the same guild-config write the
    raw RoleSelect used, behind the same Manage Server gate.
    """

    def __init__(self, owner_id: int) -> None:
        super().__init__(
            author_id=int(owner_id),
            on_pick=self._save_role,
            placeholder="Choose the Stoners / sesh ping role",
            min_values=1,
            max_values=1,
            row=0,
        )
        self.custom_id = "dank_clapbacks:ping_role:v1"

    async def _save_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if not _manage_clapbacks(interaction):
            return await _private(interaction, "❌ Dead Server Clapbacks require **Manage Server**.")
        if interaction.guild is None or role is None:
            return await _private(interaction, "❌ No role was chosen.")
        try:
            await upsert_guild_config(
                int(interaction.guild.id),
                {
                    PING_ROLE_KEY: str(int(role.id)),
                    "__config_write_mode": "explicit_override",
                    "__config_write_source": _WRITE_SOURCE,
                },
            )
        except Exception:
            return await _private(interaction, "❌ The role could not be saved; nothing was changed.")
        await _private(
            interaction,
            f"✅ Ping role set to **@{role.name}**. Members who ping it before complaining are spared the \"you didn't even ping\" lines.",
        )


class ClapbackRoleSelectView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, timeout=300)
        self.add_item(ClapbackRoleSelect(self.owner_id))

    @discord.ui.button(label="Clear Ping Role", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def clear_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _manage_clapbacks(interaction):
            return await _private(interaction, "❌ Dead Server Clapbacks require **Manage Server**.")
        if interaction.guild is None:
            return await _private(interaction, "❌ Use this in a server.")
        try:
            await clear_guild_config_keys(
                int(interaction.guild.id),
                [PING_ROLE_KEY],
                source=_WRITE_SOURCE,
            )
        except Exception:
            return await _private(interaction, "❌ The role could not be cleared; nothing was changed.")
        await _private(
            interaction,
            "✅ Clapback-specific ping role cleared. A Stoners/sesh role from Community & Pings may still apply.",
        )


async def open_dead_server_clapbacks(interaction: discord.Interaction) -> None:
    if not _manage_clapbacks(interaction):
        return await _private(interaction, "❌ Dead Server Clapbacks are server-wide and require **Manage Server**.")
    if interaction.guild is None:
        return await _private(interaction, "❌ Open this inside a server.")
    config = await _current_config(interaction)
    if config is None:
        return await _private(interaction, "❌ Server settings are unavailable right now.")
    await _private(
        interaction,
        embed=clapback_status_embed(config, ping_role_name=_ping_role_name(interaction, config)),
        view=DeadServerClapbackView(int(interaction.user.id), config),
    )


__all__ = [
    "ClapbackRoleSelectView",
    "DeadServerClapbackView",
    "clapback_status_embed",
    "open_dead_server_clapbacks",
]
