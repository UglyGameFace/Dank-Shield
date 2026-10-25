"""Shard-owned hourly refresh of explicit Smart Text in published Studio messages.

No new messages are sent; no role/form controls are edited; edits preserve
existing component views. Every update checks current authoritative Studio
ownership and Discord permissions. Feature off until reviewed migrations.
"""
from __future__ import annotations

import asyncio
from typing import Any, Mapping

import discord

from .embed_studio_core import StudioValidationError, render_embeds, resolve_smart_text
from .embed_studio_live_text import (
    claim_due, digest_project, live_enabled, live_project, mark_result,
)
from .embed_studio_schedule_runtime import _owned_shards

_TASK: asyncio.Task[None] | None = None
STARTUP_QUIET_SECONDS = 125
POLL_SECONDS = 60
PARALLEL = 2
_MENTIONS = discord.AllowedMentions.none()


async def _refresh_one(bot: Any, job: Mapping[str, Any]) -> tuple[str, str, bool]:
    """Return status, digest, and whether to disable permanently."""
    try:
        guild_id = int(job["guild_id"])
        channel_id = int(job["channel_id"])
        message_id = int(job["message_id"])
        staff_id = int(job["created_by"])
        token = str(job["nonce"])
        draft = live_project(job["project"])
        guild = bot.get_guild(guild_id)
        if guild is None:
            return "not_owned", "", False
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return "missing", "", True
        staff = guild.get_member(staff_id)
        if staff is None:
            try:
                staff = await asyncio.wait_for(guild.fetch_member(staff_id), timeout=8)
            except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
                return "permissions", "", False
        if (staff.id != guild.owner_id
                and not (staff.guild_permissions.manage_guild
                         or staff.guild_permissions.administrator)):
            return "permissions", "", True
        bot_member = guild.me
        if not isinstance(bot_member, discord.Member):
            return "permissions", "", False
        perms = channel.permissions_for(bot_member)
        if not all((perms.view_channel, perms.send_messages, perms.embed_links)):
            return "permissions", "", False
        from .embed_studio_ui import _load_library
        _source, lib = await asyncio.wait_for(_load_library(guild_id), timeout=12)
        posted = lib["published"].get(f"{channel_id}:{message_id}")
        if (not isinstance(posted, Mapping)
                or posted.get("live_nonce") != token
                or posted.get("live_source") != draft):
            return "stale", "", True
        resolved = resolve_smart_text(
            draft, guild_name=str(guild.name), channel_name=str(channel.name),
            member_count=getattr(guild, "member_count", None),
        )
        digest = digest_project(resolved)
        if digest == job.get("last_hash"):
            return "unchanged", digest, False
        message = await asyncio.wait_for(channel.fetch_message(message_id), timeout=12)
        if bot.user is None or message.author.id != bot.user.id:
            return "not_owned", "", True
        # Intentionally omit view=...: Discord preserves the existing
        # published link controls. Live updates only replace message text.
        await asyncio.wait_for(
            message.edit(
                content=resolved["content"] or None,
                embeds=render_embeds(resolved),
                allowed_mentions=_MENTIONS,
            ), timeout=15,
        )
        return "updated", digest, False
    except (StudioValidationError, KeyError, TypeError, ValueError):
        return "invalid", "", True
    except (discord.NotFound, discord.Forbidden):
        return "missing", "", True
    except (discord.HTTPException, asyncio.TimeoutError):
        return "api_error", "", False
    except Exception:
        return "api_error", "", False


async def _loop(bot: Any) -> None:
    await asyncio.sleep(STARTUP_QUIET_SECONDS)
    throttle = asyncio.Semaphore(PARALLEL)
    while live_enabled():
        try:
            owner = _owned_shards(bot)
            if owner is None:
                await asyncio.sleep(POLL_SECONDS)
                continue
            jobs = await claim_due(shard_count=owner[0], owned_shards=owner[1])
            async def one(job: dict[str, Any]) -> None:
                async with throttle:
                    status, digest, disable = await _refresh_one(bot, job)
                    try:
                        await mark_result(job,status,digest=digest,disable_job=disable)
                    except Exception as exc:
                        print(f"⚠️ studio_live finalize failed type={type(exc).__name__}")
            await asyncio.gather(*(one(job) for job in jobs))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"⚠️ studio_live due claim failed type={type(exc).__name__}")
            await asyncio.sleep(120)
        await asyncio.sleep(POLL_SECONDS)


def start_embed_studio_live_worker(bot: Any) -> asyncio.Task[None] | None:
    global _TASK
    if not live_enabled():
        return None
    if _TASK is None or _TASK.done():
        _TASK = asyncio.create_task(_loop(bot), name="studio-live-text")
    return _TASK
