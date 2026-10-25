"""Staff-only Live Text enrollment for existing tracked bot-authored embeds."""
from __future__ import annotations

import asyncio
import secrets
from typing import Any, Mapping

import discord

from .embed_studio_core import StudioValidationError
from .embed_studio_live_text import enroll,disable,live_enabled,live_project
from .embed_studio_ui import (
    _StudioView, _home, _say, _menu, _load_library, _save_library,
    _post_blockers, _published, StudioSession,
)


class LiveTextSelect(discord.ui.Select):
    def __init__(self,session: StudioSession,rows: list[tuple[str,dict[str,Any]]]) -> None:
        self.session=session
        opts=[
            discord.SelectOption(
                label=f"{'On' if row.get('live_nonce') else 'Off'} • Channel {str(row['channel_id'])[:19]}",
                description=f"Message {str(row['message_id'])[:20]}",
                value=key,
            ) for key,row in rows
        ] or [discord.SelectOption(label="No eligible Smart Text posts",value="none")]
        super().__init__(placeholder="Choose a bot-owned Smart Text post",options=opts,row=0)

    async def callback(self,interaction: discord.Interaction) -> None:
        key=self.values[0]
        if key=="none":
            return await _say(interaction,"Publish Smart Text in an embed first, then reopen Edit Posted.")
        await _menu(interaction,heading="Manage This Live Text Post",
                    description="Hourly text updates only. No new posts or automatic role controls.",
                    view=LiveTextChoice(self.session,key))


class LiveTextMenu(_StudioView):
    def __init__(self,session: StudioSession,rows: list[tuple[str,dict[str,Any]]]) -> None:
        super().__init__(session)
        self.add_item(LiveTextSelect(session,rows))

    @discord.ui.button(label="Back to Posted",emoji="◀️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction: discord.Interaction,_button: discord.ui.Button) -> None:
        await _published(interaction,self.session)


class LiveTextChoice(_StudioView):
    def __init__(self,session: StudioSession,key: str) -> None:
        super().__init__(session)
        self.key=key
        self.used=False

    async def change(self,interaction: discord.Interaction,*,enable: bool) -> None:
        if not live_enabled():
            return await _say(interaction,"Live Text is off until the owner enables its database worker.")
        if self.used:
            return await _say(interaction,"This control was already submitted.")
        self.used=True
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            source,lib=await _load_library(self.session.guild_id)
            row=lib["published"].get(self.key)
            if not isinstance(row,dict):
                return await _say(interaction,"The tracked post no longer exists.")
            channel_id=int(row.get("channel_id") or 0)
            message_id=int(row.get("message_id") or 0)
            channel=interaction.guild.get_channel(channel_id)
            if not isinstance(channel,discord.TextChannel) or _post_blockers(interaction,channel):
                return await _say(interaction,"Bot permissions or channel settings changed.")
            message=await asyncio.wait_for(channel.fetch_message(message_id),timeout=12)
            if interaction.client.user is None or message.author.id!=interaction.client.user.id:
                return await _say(interaction,"Only Dank Shield-owned messages can use Live Text.")
            old_nonce=str(row.get("live_nonce") or "")
            if enable:
                if old_nonce:
                    return await _say(interaction,"Live Text is already enabled.")
                project=live_project(row.get("live_source"))
                nonce=secrets.token_hex(16)
                row["live_nonce"]=nonce
            else:
                if not old_nonce:
                    return await _say(interaction,"Live Text is already disabled.")
                row["live_nonce"]=""
            if not await _save_library(self.session.guild_id,source,lib):
                return await _say(interaction,"Another moderator changed the post. Reopen Live Text.")
            if enable:
                if not await enroll(self.session.guild_id,channel_id,message_id,
                                    self.session.owner_id,nonce,project):
                    return await _say(interaction,
                        "Live Text enrollment was not confirmed. No automatic edits will happen.")
            else:
                await disable(self.session.guild_id,channel_id,message_id,old_nonce)
        except StudioValidationError as exc:
            return await _say(interaction,str(exc))
        except (discord.Forbidden,discord.HTTPException,asyncio.TimeoutError):
            return await _say(interaction,"Discord could not verify that bot-owned message.")
        except Exception:
            return await _say(interaction,
                "Live Text storage unavailable. Reopen the menu to check the current state.")
        await _say(interaction,
            "✅ Hourly Live Text enabled for this existing post." if enable
            else "✅ Hourly Live Text disabled. Existing message remains.")

    @discord.ui.button(label="Enable Hourly Updates",emoji="✅",style=discord.ButtonStyle.success)
    async def enable(self,interaction: discord.Interaction,_button: discord.ui.Button) -> None:
        await self.change(interaction,enable=True)

    @discord.ui.button(label="Disable Updates",emoji="⏹️",style=discord.ButtonStyle.danger)
    async def stop(self,interaction: discord.Interaction,_button: discord.ui.Button) -> None:
        await self.change(interaction,enable=False)

    @discord.ui.button(label="Back",emoji="◀️",style=discord.ButtonStyle.secondary)
    async def back(self,interaction: discord.Interaction,_button: discord.ui.Button) -> None:
        await open_live_text_menu(interaction,self.session)


async def open_live_text_menu(interaction: discord.Interaction,session: StudioSession) -> None:
    if not live_enabled():
        return await _say(interaction,"Live Text is disabled on this deployment.")
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        _source,lib=await _load_library(session.guild_id)
        rows=[
            (key,row) for key,row in lib["published"].items()
            if isinstance(row,dict) and isinstance(row.get("live_source"),dict)
        ][-20:]
    except Exception:
        return await _say(interaction,"Couldn't read eligible Smart Text posts.")
    await _menu(interaction,heading="🔄 Hourly Live Text",
                description="Choose an existing tracked post. Updates are optional and "
                            "recheck the moderator's access, bot authorship and content "
                            "before editing. A later manual Publish resets enrollment.",
                view=LiveTextMenu(session,rows))
