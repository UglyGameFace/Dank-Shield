from __future__ import annotations

"""Small, safe embed/message builder for public guild setup.

This is intentionally per-action and per-guild. It does not create global templates,
does not touch ticket/verification/welcome settings, and never sends publicly
without an explicit staff confirmation button.
"""

from typing import Any, Optional

import discord
from discord import app_commands

from .public_setup_group import _require_setup_permission, stoney_group

_ATTACHED = False

embed_group = app_commands.Group(
    name="embed",
    description="Build and send polished embed messages safely.",
)


def _parse_color(value: Optional[str]) -> discord.Color:
    text = str(value or "").strip().lower().replace("#", "")
    named = {
        "blue": discord.Color.blurple(),
        "blurple": discord.Color.blurple(),
        "green": discord.Color.green(),
        "red": discord.Color.red(),
        "orange": discord.Color.orange(),
        "gold": discord.Color.gold(),
        "purple": discord.Color.purple(),
        "grey": discord.Color.dark_grey(),
        "gray": discord.Color.dark_grey(),
    }
    if not text:
        return discord.Color.blurple()
    if text in named:
        return named[text]
    try:
        if len(text) == 6:
            return discord.Color(int(text, 16))
    except Exception:
        pass
    return discord.Color.blurple()


def _safe_url(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("https://") or text.startswith("http://"):
        return text[:2048]
    return None


def _build_embed(
    *,
    title: str,
    body: str,
    color: Optional[str] = None,
    footer: Optional[str] = None,
    image_url: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=str(title or "Untitled")[:256],
        description=str(body or "")[:4000],
        color=_parse_color(color),
        timestamp=discord.utils.utcnow(),
    )
    if footer:
        embed.set_footer(text=str(footer)[:2048])
    image = _safe_url(image_url)
    if image:
        embed.set_image(url=image)
    thumbnail = _safe_url(thumbnail_url)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return embed


def _missing_send_perms(channel: discord.TextChannel, member: Optional[discord.Member]) -> list[str]:
    if not isinstance(member, discord.Member):
        return ["Resolve bot member"]
    perms = channel.permissions_for(member)
    checks = {
        "View Channel": perms.view_channel,
        "Send Messages": perms.send_messages,
        "Embed Links": perms.embed_links,
        "Read Message History": perms.read_message_history,
    }
    return [name for name, ok in checks.items() if not ok]


async def _send_ephemeral(interaction: discord.Interaction, content: str = "", **kwargs: Any) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=True, **kwargs)
        else:
            await interaction.followup.send(content, ephemeral=True, **kwargs)
    except Exception:
        pass


class EmbedConfirmView(discord.ui.View):
    def __init__(self, *, target: discord.TextChannel, embed: discord.Embed, author_id: int) -> None:
        super().__init__(timeout=900)
        self.target = target
        self.embed = embed
        self.author_id = int(author_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await interaction.response.send_message("Only the staff member who drafted this embed can send it.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Send Embed", emoji="📨", style=discord.ButtonStyle.success, custom_id="dank_embed_builder:send")
    async def send_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        if not await _require_setup_permission(interaction):
            return
        missing = _missing_send_perms(self.target, guild.me)
        if missing:
            return await interaction.response.send_message("❌ Dank Shield is missing in the target channel: " + ", ".join(missing), ephemeral=True)
        try:
            await self.target.send(embed=self.embed, allowed_mentions=discord.AllowedMentions.none())
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
            await interaction.response.edit_message(content=f"✅ Sent to {self.target.mention}.", embed=self.embed, view=self)
        except Exception as exc:
            await interaction.response.send_message(f"❌ Could not send embed: `{type(exc).__name__}: {exc}`", ephemeral=True)

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_embed_builder:cancel")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.edit_message(content="Cancelled. Nothing was sent publicly.", view=self)


@embed_group.command(name="draft", description="Preview an embed, then confirm before sending it publicly.")
@app_commands.describe(
    channel="Channel to send the embed after confirmation.",
    title="Embed title.",
    body="Embed body/description.",
    color="Optional color name or hex, like green or #43b581.",
    footer="Optional footer text.",
    image_url="Optional image URL.",
    thumbnail_url="Optional thumbnail URL.",
)
async def embed_draft(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    title: str,
    body: str,
    color: Optional[str] = None,
    footer: Optional[str] = None,
    image_url: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send_ephemeral(interaction, "❌ This command must be used inside a server.")
    missing = _missing_send_perms(channel, interaction.guild.me)
    if missing:
        return await _send_ephemeral(interaction, "❌ Dank Shield is missing in the target channel: " + ", ".join(missing))
    embed = _build_embed(title=title, body=body, color=color, footer=footer, image_url=image_url, thumbnail_url=thumbnail_url)
    view = EmbedConfirmView(target=channel, embed=embed, author_id=int(interaction.user.id))
    await _send_ephemeral(
        interaction,
        f"Preview for {channel.mention}. Nothing posts publicly until you press **Send Embed**.",
        embed=embed,
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@embed_group.command(name="health", description="Check whether Dank Shield can send embeds in a channel.")
@app_commands.describe(channel="Channel to test.")
async def embed_health(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send_ephemeral(interaction, "❌ This command must be used inside a server.")
    missing = _missing_send_perms(channel, interaction.guild.me)
    if missing:
        return await _send_ephemeral(interaction, "❌ Missing in target channel: " + ", ".join(missing))
    await _send_ephemeral(interaction, f"✅ Dank Shield can send embed messages in {channel.mention}.", allowed_mentions=discord.AllowedMentions.none())


def _attach() -> bool:
    global _ATTACHED
    if _ATTACHED:
        return True
    try:
        if stoney_group.get_command("embed") is not None:
            _ATTACHED = True
            return True
    except Exception:
        pass
    try:
        stoney_group.add_command(embed_group)
        _ATTACHED = True
        return True
    except Exception as exc:
        try:
            print(f"⚠️ public_embed_group failed attaching /dank embed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


def register_public_embed_group_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    if _attach():
        try:
            print("✅ public_embed_group: attached /dank embed builder commands")
        except Exception:
            pass


_attach()

__all__ = ["register_public_embed_group_commands", "embed_group"]
