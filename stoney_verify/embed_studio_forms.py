"""Private, persistent response forms attached to exact Dank Shield Studio posts."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
import re
import secrets
from time import time
from typing import Any, Mapping

import discord

from .embed_studio_core import (
    STUDIO_KEY, StudioValidationError, link_view, validate_project, validate_studio_form,
)
from .embed_studio_storage_v2 import read_library, storage_v2_enabled, verify_project

FLAG = "DANK_EMBED_STUDIO_FORMS_ENABLED"
PREFIX = "dank:es:form:v1:"
TABLE = "dank_embed_studio_form_responses"
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_INSTALLED: set[int] = set()


class StudioFormError(StudioValidationError):
    pass


def forms_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return (str(env.get(FLAG) or "").strip().lower() in ("1","true","yes","on")
            and storage_v2_enabled(env))


def form_view(project: Mapping[str, Any], token: str = "") -> discord.ui.View | None:
    draft = validate_project(project)
    form = draft["form"]
    if not form:
        return link_view(draft)
    if not TOKEN_RE.fullmatch(token):
        raise StudioFormError("Form button token is unavailable. Publishing is blocked.")
    if not form["questions"]:
        raise StudioFormError("Add at least one question before publishing a form.")
    view = discord.ui.View(timeout=None)
    for link in draft["links"]:
        view.add_item(discord.ui.Button(label=link["label"],url=link["url"],
                                        style=discord.ButtonStyle.link))
    view.add_item(discord.ui.Button(label=form["button_label"],
                                    custom_id=PREFIX+token,style=discord.ButtonStyle.primary))
    return view


def _client():
    verify_project()
    from .globals import get_supabase
    db = get_supabase()
    if db is None:
        raise StudioFormError("Form responses storage is unavailable.")
    return db


async def lookup_form(guild_id: int, channel_id: int, message_id: int, token: str) -> dict[str, Any]:
    if not forms_enabled() or min(guild_id,channel_id,message_id)<=0 or not TOKEN_RE.fullmatch(token):
        raise StudioFormError("This form is unavailable or outdated.")
    from .guild_config import get_guild_config
    cfg = await asyncio.wait_for(get_guild_config(guild_id,refresh=True),timeout=12)
    if str(cfg.get("source") or "").startswith("unavailable:"):
        raise StudioFormError("Could not verify that form. No response was saved.")
    _source,lib = await asyncio.wait_for(read_library(guild_id,cfg),timeout=12)
    row=lib["published"].get(f"{channel_id}:{message_id}")
    if (not isinstance(row,Mapping) or row.get("form_token")!=token
            or int(row.get("channel_id") or 0)!=channel_id
            or int(row.get("message_id") or 0)!=message_id):
        raise StudioFormError("This form is no longer active.")
    form = validate_studio_form(row.get("form"))
    if form is None or not form["questions"]:
        raise StudioFormError("This form is no longer active.")
    return form


def clean_answers(form: Mapping[str, Any], values: list[str]) -> list[dict[str, str]]:
    checked = validate_studio_form(form)
    if checked is None or len(checked["questions"])!=len(values):
        raise StudioFormError("The form changed. Open it again.")
    result=[]
    total=0
    for question,value in zip(checked["questions"],values):
        if not isinstance(value,str):
            raise StudioFormError("Invalid answer.")
        answer=value.strip()
        max_length=1000 if question["style"]=="paragraph" else 250
        if len(answer)>max_length or (question["required"] and not answer):
            raise StudioFormError("One of your answers was missing or too long.")
        total+=len(answer)
        result.append({"question":question["label"],"answer":answer})
    if total>4000:
        raise StudioFormError("Answers are too long; shorten them to 4000 characters total.")
    return result


def _store_sync(guild_id: int,channel_id: int,message_id: int,user_id: int,
                answers: list[dict[str,str]], slot: int) -> None:
    _client().table(TABLE).insert({
        "guild_id":str(guild_id),"channel_id":str(channel_id),
        "message_id":str(message_id),"user_id":str(user_id),
        "window_slot":slot,"answers":answers,
    }).execute()


async def store_answers(guild_id: int, channel_id: int, message_id: int, user_id: int,
                        answers: list[dict[str,str]], *, now: float | None = None) -> None:
    if not forms_enabled() or min(guild_id,channel_id,message_id,user_id)<=0:
        raise StudioFormError("Form responses cannot be saved right now.")
    window=int((time() if now is None else now)//300)
    try:
        await asyncio.wait_for(asyncio.to_thread(_store_sync,guild_id,channel_id,
                                                 message_id,user_id,answers,window),timeout=12)
    except Exception as exc:
        raise StudioFormError(
            "Could not save your form. If you recently submitted, try after five minutes."
        ) from exc


async def recent_responses(guild_id: int) -> list[dict[str,Any]]:
    if not forms_enabled() or guild_id<=0:
        raise StudioFormError("Form response inbox is not available.")
    def run():
        response=_client().table(TABLE).select(
            "id,channel_id,message_id,user_id,answers,created_at"
        ).eq("guild_id",str(guild_id)).order("created_at",desc=True).limit(20).execute()
        data=getattr(response,"data",None)
        if not isinstance(data,list) or any(not isinstance(row,Mapping) for row in data):
            raise StudioFormError("Malformed response from forms inbox.")
        return [dict(row) for row in data]
    return await asyncio.wait_for(asyncio.to_thread(run),timeout=12)


async def _answer(interaction: discord.Interaction,text: str) -> None:
    options={"ephemeral":True,"allowed_mentions":discord.AllowedMentions.none()}
    if interaction.response.is_done():
        await interaction.followup.send(text,**options)
    else:
        await interaction.response.send_message(text,**options)


class StudioResponseModal(discord.ui.Modal):
    def __init__(self, *, guild_id: int, channel_id: int,message_id: int,
                 actor_id: int, token: str, form: dict[str,Any]) -> None:
        super().__init__(title=form["title"],timeout=600)
        self.guild_id,self.channel_id,self.message_id=guild_id,channel_id,message_id
        self.actor_id,self.token=actor_id,token
        self.questions=form["questions"]
        self.inputs=[]
        for item in self.questions:
            control=discord.ui.TextInput(
                label=item["label"],placeholder=item["placeholder"] or None,
                style=(discord.TextStyle.paragraph if item["style"]=="paragraph"
                       else discord.TextStyle.short),
                required=item["required"],
                max_length=1000 if item["style"]=="paragraph" else 250,
            )
            self.add_item(control)
            self.inputs.append(control)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if (interaction.guild is None or int(interaction.guild.id)!=self.guild_id
                or int(interaction.user.id)!=self.actor_id):
            return await _answer(interaction,"This form is no longer for your session.")
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            form=await lookup_form(self.guild_id,self.channel_id,self.message_id,self.token)
            if form["questions"]!=self.questions:
                return await _answer(interaction,"The questions changed. Open the form again.")
            answers=clean_answers(form,[str(i.value or "") for i in self.inputs])
            await store_answers(self.guild_id,self.channel_id,self.message_id,
                                self.actor_id,answers)
        except StudioValidationError as exc:
            return await _answer(interaction,str(exc))
        except Exception:
            return await _answer(interaction,"Your form could not be saved. Please retry later.")
        await _answer(interaction,"✅ Your response was received privately by server staff.")


async def _click(interaction: discord.Interaction,token: str) -> None:
    guild,message,channel=interaction.guild,interaction.message,interaction.channel
    if not guild or not channel or not message:
        return await _answer(interaction,"This form only works in its server.")
    if not interaction.client.user or message.author.id!=interaction.client.user.id:
        return await _answer(interaction,"Only official Dank Shield forms are supported.")
    if not isinstance(interaction.user,discord.Member):
        return await _answer(interaction,"Open this form from inside its server.")
    try:
        form=await lookup_form(int(guild.id),int(channel.id),int(message.id),token)
    except StudioValidationError as exc:
        return await _answer(interaction,str(exc))
    except Exception:
        return await _answer(interaction,"Form verification is temporarily unavailable.")
    await interaction.response.send_modal(StudioResponseModal(
        guild_id=int(guild.id),channel_id=int(channel.id),message_id=int(message.id),
        actor_id=int(interaction.user.id),token=token,form=form,
    ))


async def _listener(interaction: discord.Interaction) -> None:
    if interaction.type is not discord.InteractionType.component:
        return
    data=interaction.data if isinstance(interaction.data,dict) else {}
    custom=str(data.get("custom_id") or "")
    if not custom.startswith(PREFIX):
        return
    token=custom[len(PREFIX):]
    if not TOKEN_RE.fullmatch(token):
        return await _answer(interaction,"Invalid form button.")
    await _click(interaction,token)


def install_embed_studio_form_runtime(bot: Any) -> bool:
    if id(bot) in _INSTALLED:
        return True
    bot.add_listener(_listener,"on_interaction")
    _INSTALLED.add(id(bot))
    return True
