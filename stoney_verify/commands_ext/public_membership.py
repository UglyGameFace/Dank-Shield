from __future__ import annotations

"""Dank Shield Pro panel: plan status, covered servers, subscribe entry.

Reached from /dank home → Setup & Server Settings → Dank Shield Pro
(registry key ``dank_shield_pro``). The panel is a thin view over
``stoney_verify.services.membership``:

- unconfigured membership (no Pro SKU ids in env): an inert explainer —
  no buttons, no gates, exactly the pre-membership behavior;
- configured: this server's plan status, the viewer's own Pro status and
  covered servers (link this server / unlink, cap of 3), and — for
  viewers without Pro — Discord premium subscribe buttons, rendered only
  while SKUs are configured.

Link/unlink callbacks act only on the clicker's own link rows and
re-verify the clicker's entitlement at click time.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import discord

from stoney_verify.services import membership

logger = logging.getLogger(__name__)

PRO_INCLUDES: tuple[str, ...] = (
    "AI Voice Safety + Live Captions",
    "Dank Design per-category styles, zone templates & Smart Repair",
    "AntiNuke Strict Lockdown",
    "Share Router downloads",
    "Embed Studio scheduling, recurring posts & campaigns",
    f"One subscription covers up to {membership.MAX_LINKED_GUILDS} of your servers",
)

_LINK_NOTICES = {
    membership.LINK_LINKED: "✅ This server is now covered by your Dank Shield Pro.",
    membership.LINK_ALREADY_LINKED: "This server is already linked to your Pro.",
    membership.LINK_CAP_REACHED: "All of your covered-server slots are in use. Unlink a server first.",
    membership.LINK_GUILD_OWNED: "This server is already covered by a different Pro account.",
    membership.LINK_UNAVAILABLE: "Linking isn't available right now. Please try again later.",
}


@dataclass
class _ProPanelState:
    configured: bool
    guild_id: int = 0
    guild_status: str = membership.STATUS_UNCONFIGURED
    current_owner: Optional[int] = None
    viewer_id: int = 0
    viewer_pro: bool = False
    viewer_links: tuple[tuple[int, str], ...] = ()
    sku_ids: tuple[int, ...] = ()
    notice: str = ""


def _guild_name(client: Any, guild_id: int) -> str:
    guild = client.get_guild(int(guild_id)) if hasattr(client, "get_guild") else None
    return str(getattr(guild, "name", "") or "") or f"Server {guild_id}"


async def _load_state(interaction: discord.Interaction, *, notice: str = "") -> _ProPanelState:
    client = interaction.client
    guild = interaction.guild
    state = _ProPanelState(
        configured=membership.membership_configured(),
        guild_id=int(guild.id) if guild is not None else 0,
        viewer_id=int(interaction.user.id),
        sku_ids=membership.pro_sku_ids(),
        notice=notice,
    )
    if not state.configured:
        return state
    state.viewer_pro = await membership.user_has_active_pro(client, state.viewer_id)
    if state.viewer_pro:
        linked_ids = await membership.links_for_user(state.viewer_id)
        state.viewer_links = tuple((gid, _guild_name(client, gid)) for gid in linked_ids)
    if state.guild_id:
        state.current_owner = await membership.owner_of_guild(state.guild_id)
        state.guild_status = await membership.guild_pro_status(state.guild_id, client=client)
    return state


def _pro_embed(state: _ProPanelState) -> discord.Embed:
    embed = discord.Embed(title="⭐ Dank Shield Pro", color=discord.Color.gold())
    if state.notice:
        embed.description = state.notice
    if not state.configured:
        embed.add_field(
            name="Status",
            value=(
                "Membership isn't switched on yet — every feature works exactly as it does today.\n\n"
                "When Pro launches, one subscription will include:\n"
                + "\n".join(f"• {item}" for item in PRO_INCLUDES)
            ),
            inline=False,
        )
        return embed

    if state.guild_status == membership.STATUS_PRO:
        server_line = "⭐ **Pro** — this server is covered."
        if state.current_owner == state.viewer_id:
            server_line += " You linked it."
    else:
        server_line = "Free — this server is not covered by a Pro account."
    embed.add_field(name="This server", value=server_line, inline=False)

    if state.viewer_pro:
        if state.viewer_links:
            covered = "\n".join(f"• {name}" for _gid, name in state.viewer_links)
        else:
            covered = "No servers linked yet."
        embed.add_field(
            name=f"Your covered servers ({len(state.viewer_links)}/{membership.MAX_LINKED_GUILDS})",
            value=covered,
            inline=False,
        )
    else:
        embed.add_field(
            name="Your Pro",
            value=(
                "Not subscribed yet. Pro includes:\n"
                + "\n".join(f"• {item}" for item in PRO_INCLUDES)
            ),
            inline=False,
        )
    return embed


class _LinkCurrentButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Link this server",
            emoji="🔗",
            style=discord.ButtonStyle.success,
            custom_id="dank_pro:link_current:v1",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        if not await membership.user_has_active_pro(interaction.client, int(interaction.user.id)):
            state = await _load_state(interaction, notice="Your Pro subscription isn't active, so nothing was linked.")
        else:
            result = await membership.link_guild(int(interaction.user.id), int(guild.id))
            state = await _load_state(interaction, notice=_LINK_NOTICES.get(result, _LINK_NOTICES[membership.LINK_UNAVAILABLE]))
        await interaction.response.edit_message(embed=_pro_embed(state), view=DankProView(state))


class _UnlinkButton(discord.ui.Button):
    def __init__(self, guild_id: int, guild_name: str) -> None:
        super().__init__(
            label=f"Unlink {guild_name}"[:80],
            emoji="✂️",
            style=discord.ButtonStyle.danger,
            custom_id=f"dank_pro:unlink:{int(guild_id)}:v1",
        )
        self.guild_id = int(guild_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        removed = await membership.unlink_guild(int(interaction.user.id), self.guild_id)
        notice = "✅ Server unlinked from your Pro." if removed else "That server wasn't linked to your Pro."
        state = await _load_state(interaction, notice=notice)
        await interaction.response.edit_message(embed=_pro_embed(state), view=DankProView(state))


class DankProView(discord.ui.View):
    def __init__(self, state: _ProPanelState) -> None:
        super().__init__(timeout=900)
        self.state = state
        if not state.configured:
            return
        if not state.viewer_pro:
            for sku_id in state.sku_ids:
                self.add_item(discord.ui.Button(style=discord.ButtonStyle.premium, sku_id=int(sku_id)))
            return
        linked_here = any(gid == state.guild_id for gid, _name in state.viewer_links)
        if (
            state.guild_id
            and not linked_here
            and state.current_owner is None
            and len(state.viewer_links) < membership.MAX_LINKED_GUILDS
        ):
            self.add_item(_LinkCurrentButton())
        for gid, name in state.viewer_links:
            self.add_item(_UnlinkButton(gid, name))


async def open_membership_panel(interaction: discord.Interaction) -> None:
    state = await _load_state(interaction)
    embed = _pro_embed(state)
    view = DankProView(state)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


__all__ = ["DankProView", "open_membership_panel"]
