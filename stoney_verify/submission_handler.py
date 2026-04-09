# stoney_verify/submission_handlers.py
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import discord

from .globals import *  # noqa: F401,F403

from .tickets import (
    is_verification_ticket_channel,
    find_ticket_owner_retry,
    _parse_webhook_id_from_url,  # type: ignore
)

from .commands_ext.common import (
    ACTIVE_DECISION_PANEL_MSG_ID,
    RECENT_SUBMISSION_TOKENS,
    RECENT_SUBMISSION_MSG_IDS,
    RUNTIME_STATS,
    KICK_TIMER_TASKS,
    KICK_TIMER_STARTS,
    KICK_TIMER_STARTED_BY,
    token_is_expired,
    make_custom_id,
    extract_token_from_message,
    mark_ticket_activity,
)

try:
    from .store import (
        sb_get_token_info,
        sb_set_submitted,
        sb_set_submitted_at,
    )
except Exception:
    def sb_get_token_info(token: str) -> Optional[Dict[str, Any]]:  # type: ignore
        return None

    def sb_set_submitted(token: str) -> None:  # type: ignore
        return None

    def sb_set_submitted_at(token: str, submitted_at=None) -> None:  # type: ignore
        return None


try:
    from .commands_ext.kick_timers import (
        _cancel_kick_timer,
        kick_timer_persist_delete,
    )
except Exception:
    def _cancel_kick_timer(channel_id: int) -> bool:
        try:
            task = KICK_TIMER_TASKS.get(int(channel_id))
            if task and not task.done():
                task.cancel()
                return True
        except Exception:
            pass
        return False

    async def kick_timer_persist_delete(channel_id: int) -> None:
        return None


async def _cleanup_old_staff_decision_panels(channel: discord.TextChannel) -> None:
    """
    Remove older bot-posted staff decision panels so only the newest one remains.
    """
    try:
        me = channel.guild.me
        if me is None and bot.user:
            try:
                me = channel.guild.get_member(bot.user.id) or await channel.guild.fetch_member(bot.user.id)
            except Exception:
                me = None

        if me is None:
            return

        perms = channel.permissions_for(me)
        if not perms.read_message_history:
            return

        async for msg in channel.history(limit=50):
            try:
                if not msg.author or not bot.user or int(msg.author.id) != int(bot.user.id):
                    continue
                if msg.content and "🧾 **Staff Decision Panel**" in msg.content:
                    try:
                        await msg.delete(reason="Cleanup old staff decision panel (new submission)")
                    except Exception:
                        pass
            except Exception:
                continue
    except Exception:
        pass


async def _cancel_submission_related_timers(channel: discord.TextChannel) -> None:
    """
    Cancel any running verification no-response timer for this ticket channel.
    """
    try:
        _cancel_kick_timer(int(channel.id))
    except Exception:
        pass

    try:
        KICK_TIMER_TASKS.pop(int(channel.id), None)
        KICK_TIMER_STARTS.pop(int(channel.id), None)
        KICK_TIMER_STARTED_BY.pop(int(channel.id), None)
    except Exception:
        pass

    try:
        await kick_timer_persist_delete(int(channel.id))
    except Exception:
        pass


async def handle_possible_submission(message: discord.Message) -> None:
    """
    Detect a successful webhook-based ID submission inside a verification ticket
    and post the fresh staff decision panel.

    This should only react once per token/submission burst.
    """
    if not isinstance(message.channel, discord.TextChannel):
        return

    if not is_verification_ticket_channel(message.channel):
        return

    if not message.guild:
        return

    me = message.guild.me
    if me is None and bot.user:
        try:
            me = message.guild.get_member(bot.user.id) or await message.guild.fetch_member(bot.user.id)
        except Exception:
            me = None

    if me is None:
        return

    perms = message.channel.permissions_for(me)
    if not (perms.view_channel and perms.send_messages):
        return

    if not getattr(message, "webhook_id", None):
        return

    token = extract_token_from_message(message)

    if not token:
        try:
            await asyncio.sleep(1.2)
            fresh = await message.channel.fetch_message(message.id)
            token = extract_token_from_message(fresh)
        except Exception:
            token = None

    if not token:
        return

    token_info = sb_get_token_info(token)
    if not token_info:
        return

    if token_is_expired(token_info):
        return

    ti_channel = str(token_info.get("channel_id") or "")
    if not ti_channel or ti_channel != str(message.channel.id):
        return

    ti_guild = str(token_info.get("guild_id") or "")
    if ti_guild and ti_guild != str(message.guild.id):
        return

    expected_wh_id = None
    webhook_url = token_info.get("webhook_url")
    if webhook_url:
        try:
            expected_wh_id = _parse_webhook_id_from_url(str(webhook_url))
        except Exception:
            expected_wh_id = None

        if expected_wh_id and int(getattr(message, "webhook_id", 0) or 0) != int(expected_wh_id):
            return
    else:
        print(f"⚠️ Token {token} has no webhook_url stored – proceeding without webhook check.")

    if token_info.get("used", False):
        return

    if token_info.get("submitted", False):
        return

    try:
        sb_set_submitted(token)
    except Exception:
        pass

    try:
        sb_set_submitted_at(token, now_utc())
    except Exception:
        pass

    try:
        RUNTIME_STATS["submissions_seen"] += 1
    except Exception:
        pass

    try:
        mark_ticket_activity(message.channel.id)
    except Exception:
        pass

    try:
        now = now_utc()
        prune_after = max(10, int(SUBMISSION_DEDUPE_SECONDS) * 3)

        for t, ts in list(RECENT_SUBMISSION_TOKENS.items()):
            try:
                if (now - ts).total_seconds() > prune_after:
                    RECENT_SUBMISSION_TOKENS.pop(t, None)
            except Exception:
                RECENT_SUBMISSION_TOKENS.pop(t, None)

        for mid, ts in list(RECENT_SUBMISSION_MSG_IDS.items()):
            try:
                if (now - ts).total_seconds() > prune_after:
                    RECENT_SUBMISSION_MSG_IDS.pop(mid, None)
            except Exception:
                RECENT_SUBMISSION_MSG_IDS.pop(mid, None)

        if int(message.id) in RECENT_SUBMISSION_MSG_IDS:
            return

        last = RECENT_SUBMISSION_TOKENS.get(token)
        if last and (now - last).total_seconds() < int(SUBMISSION_DEDUPE_SECONDS):
            return

        RECENT_SUBMISSION_TOKENS[token] = now
        RECENT_SUBMISSION_MSG_IDS[int(message.id)] = now
    except Exception:
        pass

    await _cancel_submission_related_timers(message.channel)
    await _cleanup_old_staff_decision_panels(message.channel)

    owner = await find_ticket_owner_retry(message.channel)

    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="✅ Approve",
            style=discord.ButtonStyle.success,
            custom_id=make_custom_id("approve", token),
        )
    )
    view.add_item(
        discord.ui.Button(
            label="⛔ Deny & Close",
            style=discord.ButtonStyle.danger,
            custom_id=make_custom_id("denyclose", token),
        )
    )
    view.add_item(
        discord.ui.Button(
            label="🔁 Request Resubmission",
            style=discord.ButtonStyle.secondary,
            custom_id=make_custom_id("resubmit", token),
        )
    )

    panel_msg = await message.channel.send(
        f"🧾 **Staff Decision Panel** for {(owner.mention if owner else 'this ticket')}:",
        view=view,
    )

    try:
        ACTIVE_DECISION_PANEL_MSG_ID[token] = int(panel_msg.id)
    except Exception:
        pass

    try:
        RUNTIME_STATS["panels_posted"] += 1
    except Exception:
        pass


__all__ = [
    "handle_possible_submission",
]