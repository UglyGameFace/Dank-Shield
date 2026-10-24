"""Bounded, at-most-once publication of opted-in Embed Studio one-shot jobs."""
from __future__ import annotations

import asyncio
import discord
from typing import Any, Mapping

from .embed_studio_core import (
    StudioValidationError, validate_project, resolve_smart_text, render_embeds, link_view,
)
from .embed_studio_schedules import claim_due, finish, schedules_enabled

_TASK: asyncio.Task[None] | None = None
POLL_SECONDS = 30
STARTUP_DELAY_SECONDS = 125
PARALLEL = 2
_ALLOWED_MENTIONS = discord.AllowedMentions.none()


async def _dispatch(bot: Any, job: Mapping[str, Any]) -> tuple[int, str]:
    try:
        gid = int(job["guild_id"])
        cid = int(job["channel_id"])
        uid = int(job["scheduled_by"])
        model = validate_project(job["project"])
        if model["role_actions"]:
            return 0, "role_actions_not_supported"
        guild = bot.get_guild(gid)
        if guild is None:
            return 0, "guild_not_cached"
        channel = guild.get_channel(cid)
        if not isinstance(channel, discord.TextChannel):
            return 0, "channel_missing"
        owner = guild.get_member(uid)
        if owner is None:
            try:
                owner = await asyncio.wait_for(guild.fetch_member(uid), timeout=7)
            except (discord.HTTPException, asyncio.TimeoutError):
                return 0, "schedule_author_missing"
        if (int(owner.id) != int(guild.owner_id)
                and not (owner.guild_permissions.manage_guild
                         or owner.guild_permissions.administrator)):
            return 0, "author_not_staff"
        me = guild.me
        if not isinstance(me, discord.Member):
            return 0, "bot_membership_missing"
        perms = channel.permissions_for(me)
        if not all((perms.view_channel, perms.send_messages, perms.embed_links)):
            return 0, "channel_permissions_changed"
        draft = resolve_smart_text(
            model, guild_name=str(guild.name), channel_name=str(channel.name),
            member_count=getattr(guild, "member_count", None),
        )
        embeds = render_embeds(draft)
        view = link_view(draft)
        message = await asyncio.wait_for(
            channel.send(content=draft["content"] or None, embeds=embeds,
                         view=view, allowed_mentions=_ALLOWED_MENTIONS),
            timeout=15,
        )
        return int(message.id), ""
    except StudioValidationError:
        return 0, "draft_validation_failed"
    except (discord.Forbidden, discord.HTTPException):
        return 0, "discord_send_failed"
    except asyncio.TimeoutError:
        # An ambiguous Discord send timeout must never be auto-retried.
        return 0, "send_timeout_unconfirmed"
    except (TypeError, KeyError, ValueError, AttributeError):
        return 0, "invalid_schedule_data"
    except Exception:
        return 0, "unexpected_send_failure"


async def _loop(bot: Any) -> None:
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    semaphore = asyncio.Semaphore(PARALLEL)
    while schedules_enabled():
        try:
            due = await claim_due()
            async def one(job: dict[str, Any]) -> None:
                async with semaphore:
                    # Job ID is server-owned, no member or message content in logs.
                    msg_id, error = await _dispatch(bot, job)
                    try:
                        await finish(str(job["id"]), message_id=msg_id, error_code=error)
                    except Exception as exc:
                        print(f"⚠️ studio_schedule finalize unavailable={type(exc).__name__}; no replay")
            await asyncio.gather(*(one(row) for row in due))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"⚠️ studio_schedule indexed claim unavailable={type(exc).__name__}")
            await asyncio.sleep(120)
        await asyncio.sleep(POLL_SECONDS)


def start_embed_studio_schedule_worker(bot: Any) -> asyncio.Task[None] | None:
    global _TASK
    if not schedules_enabled():
        return None
    if _TASK is None or _TASK.done():
        _TASK = asyncio.create_task(_loop(bot), name="embed-studio-schedule")
    return _TASK
