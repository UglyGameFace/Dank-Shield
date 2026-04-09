from __future__ import annotations

import asyncio
import traceback
from typing import Optional

import discord

from .globals import *
from .tickets import is_verification_ticket_channel


def _submission_debug(msg: str) -> None:
    try:
        print(f"🧾 [SUBMISSION] {msg}")
    except Exception:
        pass


async def _find_existing_panel_for_token(
    channel: discord.TextChannel,
    token: str,
) -> Optional[discord.Message]:
    """
    Try to resolve the active decision panel from cache first,
    then fall back to scanning recent bot messages.
    """
    try:
        panel_id = int(ACTIVE_DECISION_PANEL_MSG_ID.get(token) or 0)
    except Exception:
        panel_id = 0

    if panel_id > 0:
        try:
            msg = await channel.fetch_message(panel_id)
            if msg and msg.author and bot.user and msg.author.id == bot.user.id:
                return msg
        except Exception:
            pass

    try:
        me = channel.guild.me
        if not me:
            return None
        if not channel.permissions_for(me).read_message_history:
            return None
    except Exception:
        return None

    try:
        async for m in channel.history(limit=75):
            try:
                if not m.author or not bot.user or m.author.id != bot.user.id:
                    continue
                if not m.content:
                    continue
                if "🧾 **Staff Decision Panel**" not in m.content:
                    continue
                if token and token in m.content:
                    return m
            except Exception:
                continue
    except Exception:
        pass

    return None


async def _delete_old_panels(
    channel: discord.TextChannel,
    *,
    keep_message_id: int = 0,
) -> None:
    """
    Remove older staff decision panels in this ticket so only one survives.
    """
    try:
        me = channel.guild.me
        if not me:
            return
        perms = channel.permissions_for(me)
        if not perms.read_message_history:
            return
    except Exception:
        return

    try:
        async for m in channel.history(limit=75):
            try:
                if not m.author or not bot.user or m.author.id != bot.user.id:
                    continue
                if not m.content or "🧾 **Staff Decision Panel**" not in m.content:
                    continue
                if keep_message_id and int(m.id) == int(keep_message_id):
                    continue
                await m.delete(reason="Cleanup old staff decision panel")
            except Exception:
                continue
    except Exception:
        pass


async def handle_possible_submission(message: discord.Message):
    try:
        _submission_debug(
            f"enter message={getattr(message, 'id', 'unknown')} "
            f"channel={getattr(getattr(message, 'channel', None), 'id', 'unknown')}"
        )

        if not isinstance(message.channel, discord.TextChannel):
            _submission_debug(
                f"skip message={getattr(message, 'id', 'unknown')} reason=not_text_channel"
            )
            return

        if not is_verification_ticket_channel(message.channel):
            _submission_debug(
                f"skip message={message.id} channel={message.channel.id} reason=not_verification_ticket_channel"
            )
            return

        if not message.guild:
            _submission_debug(
                f"skip message={message.id} channel={message.channel.id} reason=no_guild"
            )
            return

        me = message.guild.me
        if not me and bot.user:
            try:
                me = message.guild.get_member(bot.user.id) or await message.guild.fetch_member(bot.user.id)
            except Exception:
                me = None

        if not me:
            _submission_debug(f"skip message={getattr(message, 'id', 'unknown')} reason=no_bot_member")
            return

        perms = message.channel.permissions_for(me)
        if not (perms.view_channel and perms.send_messages):
            _submission_debug(
                f"skip message={message.id} channel={message.channel.id} reason=missing_channel_perms"
            )
            return

        webhook_id = int(getattr(message, "webhook_id", 0) or 0)
        if webhook_id <= 0:
            _submission_debug(
                f"skip message={message.id} channel={message.channel.id} reason=not_webhook_message"
            )
            return

        token = extract_token_from_message(message)
        _submission_debug(
            f"token_extract_initial message={message.id} token={token or 'NONE'}"
        )

        if not token:
            try:
                await asyncio.sleep(1.2)
                fresh = await message.channel.fetch_message(message.id)
                token = extract_token_from_message(fresh)
                _submission_debug(
                    f"token_extract_retry message={message.id} token={token or 'NONE'}"
                )
            except Exception as e:
                _submission_debug(
                    f"warn message={message.id} reason=token_retry_fetch_failed error={repr(e)}"
                )
                token = None

        if not token:
            _submission_debug(
                f"skip message={message.id} channel={message.channel.id} reason=no_token_found"
            )
            return

        token_info = sb_get_token_info(token)
        if not token_info:
            _submission_debug(
                f"skip message={message.id} token={token} reason=no_token_info"
            )
            return

        if token_is_expired(token_info):
            _submission_debug(
                f"skip message={message.id} token={token} reason=token_expired"
            )
            return

        ti_channel = str(token_info.get("channel_id") or "")
        if not ti_channel or ti_channel != str(message.channel.id):
            _submission_debug(
                f"skip message={message.id} token={token} reason=channel_mismatch expected={ti_channel} actual={message.channel.id}"
            )
            return

        ti_guild = str(token_info.get("guild_id") or "")
        if ti_guild and ti_guild != str(message.guild.id):
            _submission_debug(
                f"skip message={message.id} token={token} reason=guild_mismatch expected={ti_guild} actual={message.guild.id}"
            )
            return

        expected_wh_id = _parse_webhook_id_from_url(str(token_info.get("webhook_url") or ""))
        if expected_wh_id and webhook_id != int(expected_wh_id):
            _submission_debug(
                f"skip message={message.id} token={token} reason=webhook_mismatch expected={expected_wh_id} actual={webhook_id}"
            )
            return

        existing_panel = await _find_existing_panel_for_token(message.channel, token)
        if existing_panel is not None:
            try:
                ACTIVE_DECISION_PANEL_MSG_ID[token] = int(existing_panel.id)
            except Exception:
                pass
            _submission_debug(
                f"skip message={message.id} token={token} reason=existing_panel panel_id={existing_panel.id}"
            )
            return

        try:
            now = now_utc()
            prune_after = max(10, SUBMISSION_DEDUPE_SECONDS * 3)

            for t, ts in list(RECENT_SUBMISSION_TOKENS.items()):
                if (now - ts).total_seconds() > prune_after:
                    RECENT_SUBMISSION_TOKENS.pop(t, None)

            for mid, ts in list(RECENT_SUBMISSION_MSG_IDS.items()):
                if (now - ts).total_seconds() > prune_after:
                    RECENT_SUBMISSION_MSG_IDS.pop(mid, None)

            if len(RECENT_SUBMISSION_MSG_IDS) > 5000:
                items = sorted(RECENT_SUBMISSION_MSG_IDS.items(), key=lambda kv: kv[1], reverse=True)
                RECENT_SUBMISSION_MSG_IDS.clear()
                for mid, ts in items[:2500]:
                    RECENT_SUBMISSION_MSG_IDS[mid] = ts

            if message.id in RECENT_SUBMISSION_MSG_IDS:
                _submission_debug(
                    f"skip message={message.id} token={token} reason=duplicate_message_id"
                )
                return

            last = RECENT_SUBMISSION_TOKENS.get(token)
            if last and (now - last).total_seconds() < SUBMISSION_DEDUPE_SECONDS:
                _submission_debug(
                    f"skip message={message.id} token={token} reason=duplicate_token_window"
                )
                return

            RECENT_SUBMISSION_TOKENS[token] = now
            RECENT_SUBMISSION_MSG_IDS[message.id] = now
        except Exception as e:
            _submission_debug(
                f"warn message={message.id} token={token} reason=dedupe_tracking_failed error={repr(e)}"
            )

        _cancel_kick_timer(message.channel.id)
        mark_ticket_activity(message.channel.id)

        try:
            sb_set_submitted(token)
            _submission_debug(f"marked_submitted token={token}")
        except Exception as e:
            _submission_debug(f"warn token={token} reason=sb_set_submitted_failed error={repr(e)}")

        try:
            RUNTIME_STATS["submissions_seen"] += 1
        except Exception:
            pass

        try:
            await _delete_old_panels(message.channel)
            _submission_debug(f"deleted_old_panels channel={message.channel.id}")
        except Exception as e:
            _submission_debug(
                f"warn channel={message.channel.id} token={token} reason=delete_old_panels_failed error={repr(e)}"
            )

        _submission_debug(
            f"owner_lookup_begin token={token} channel={message.channel.id}"
        )
        owner = await find_ticket_owner_retry(message.channel)
        _submission_debug(
            f"owner_lookup_done token={token} "
            f"owner_id={getattr(owner, 'id', None) if owner else 'NONE'}"
        )

        _submission_debug(
            f"panel_build_begin token={token} channel={message.channel.id}"
        )
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
        view.add_item(
            discord.ui.Button(
                label="🎙️ Switch to VC Verify",
                style=discord.ButtonStyle.primary,
                custom_id=make_custom_id("vc", token),
            )
        )
        _submission_debug(
            f"panel_build_done token={token} channel={message.channel.id}"
        )

        owner_text = owner.mention if owner else "this ticket"
        token_text = f"`{token}`"

        _submission_debug(
            f"panel_send_begin token={token} channel={message.channel.id}"
        )
        panel_msg = await message.channel.send(
            f"🧾 **Staff Decision Panel** for {owner_text}\n"
            f"Submission token: {token_text}",
            view=view,
        )
        _submission_debug(
            f"panel_send_done token={token} panel_message_id={panel_msg.id} channel={message.channel.id}"
        )

        try:
            ACTIVE_DECISION_PANEL_MSG_ID[token] = int(panel_msg.id)
        except Exception:
            pass

        try:
            RUNTIME_STATS["panels_posted"] += 1
        except Exception:
            pass

        _submission_debug(
            f"posted panel message={panel_msg.id} token={token} channel={message.channel.id}"
        )

    except Exception as e:
        _submission_debug(
            f"fatal message={getattr(message, 'id', 'unknown')} "
            f"channel={getattr(getattr(message, 'channel', None), 'id', 'unknown')} "
            f"error={repr(e)}"
        )
        try:
            traceback.print_exc()
        except Exception:
            pass