from __future__ import annotations

"""Discord invite runtime enforcement.

This listener no longer owns invite policy.  It only extracts/hydrates messages,
asks ``stoney_verify.invite_policy_engine`` for a decision, and deletes only when
that central engine returns ``action == "delete"``.
"""

import asyncio
import re
import time
from typing import Any

import discord

try:
    from stoney_verify.globals import bot
except Exception:  # pragma: no cover
    bot = None  # type: ignore

from stoney_verify import invite_policy_engine as policy

_INSTALLED = False
_SWEEP_TASKS: dict[tuple[int, int], asyncio.Task] = {}
_LAST_SWEEP_AT: dict[tuple[int, int], float] = {}
_SPLASH_LAST_AT: dict[tuple[int, int], float] = {}

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _log(message: str) -> None:
    try:
        print(f"🛡️ discord_invite_blocker_runtime_guard {message}")
    except Exception:
        pass


def _message_looks_invite_related(message: discord.Message) -> bool:
    try:
        if policy.extract_invite_codes_from_message(message):
            return True
        text = policy.message_text(message).lower()
        if "discord.gg" in text or "discord.com/invite" in text or "discordapp.com/invite" in text:
            return True
        if bool(getattr(getattr(message, "author", None), "bot", False)) and (
            bool(getattr(message, "embeds", None)) or bool(getattr(message, "components", None))
        ):
            return "discord" in text or "invite" in text
    except Exception:
        pass
    return False


async def _send_invite_shield_splash(channel: discord.TextChannel, *, deleted: int = 1, source: str = "live") -> None:
    """Post a short temporary confirmation that Invite Shield handled an invite."""

    try:
        guild = channel.guild
        key = (int(guild.id), int(channel.id))
        now = time.monotonic()
        last = float(_SPLASH_LAST_AT.get(key, 0.0) or 0.0)
        if now - last < 12.0:
            return
        _SPLASH_LAST_AT[key] = now

        me = guild.me
        if not isinstance(me, discord.Member):
            return
        perms = channel.permissions_for(me)
        if not perms.send_messages:
            return

        count_text = "an invite" if int(deleted or 1) <= 1 else f"{int(deleted)} invites"
        msg = await channel.send(
            f"🛡️ **Dank Shield removed {count_text}.**\n"
            "The Protection Center decision log says exactly which feature approved it.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

        try:
            if perms.manage_messages:
                await msg.delete(delay=8)
        except Exception:
            pass
    except Exception as exc:
        _log(f"splash failed source={source}: {type(exc).__name__}: {exc}")


async def _report_invite_shield_block_to_spam_guard(
    message: discord.Message,
    codes: list[str],
    *,
    source: str,
) -> None:
    try:
        from stoney_verify import spam_guard

        reporter = getattr(spam_guard, "record_invite_shield_block", None)
        if callable(reporter):
            await reporter(message, list(codes or []), source=source)
    except Exception as exc:
        _log(f"spam guard bridge failed source={source}: {type(exc).__name__}: {exc}")


async def _fetch_message_for_enforcement(message: discord.Message) -> discord.Message:
    try:
        channel = getattr(message, "channel", None)
        if isinstance(channel, discord.TextChannel):
            fetched = await channel.fetch_message(int(message.id))
            if isinstance(fetched, discord.Message):
                return fetched
    except Exception:
        pass
    return message


async def _enforce_message(message: discord.Message, *, source: str = "message") -> None:
    try:
        guild = message.guild
        if guild is None or not isinstance(message.author, discord.Member):
            return
        if not isinstance(message.channel, discord.TextChannel):
            return

        effective_message = message
        codes = policy.extract_invite_codes_from_message(effective_message)

        if not codes:
            fetched = await _fetch_message_for_enforcement(message)
            if fetched is not message:
                effective_message = fetched
                codes = policy.extract_invite_codes_from_message(fetched)
                if codes:
                    _log(
                        "REST fetch recovered invite codes "
                        f"guild={guild.id} channel={message.channel.id} message={message.id} source={source} codes={','.join(codes[:5])}"
                    )

        if not codes:
            return

        try:
            from stoney_verify.startup_guards.invite_shield_sanitize_shared import is_trusted_bump_success_receipt

            if is_trusted_bump_success_receipt(effective_message):
                _log(
                    "allowed trusted bump success receipt "
                    f"guild={guild.id} channel={effective_message.channel.id} message={effective_message.id} source={source}"
                )
                return
        except Exception:
            pass

        decision = await policy.decide_invite_message(effective_message, source=source)
        if not decision.should_delete:
            if decision.action == "log_only" and decision.rule_id in {"unknown_invite_target_allowed", "no_delete_feature_enabled"}:
                _log(
                    "invite allowed/logged "
                    f"guild={guild.id} channel={effective_message.channel.id} message={effective_message.id} "
                    f"source={source} rule={decision.rule_id} codes={','.join(decision.codes[:5])}"
                )
            return

        deleted = await policy.delete_message_if_allowed(effective_message, decision)
        await policy.send_invite_decision_modlog(effective_message, decision)

        if not deleted:
            _log(
                "policy approved delete but delete failed "
                f"guild={guild.id} channel={effective_message.channel.id} message={effective_message.id} "
                f"source={source} error={decision.delete_error}"
            )
            return

        try:
            from stoney_verify.startup_guards.invite_shield_sanitize_shared import send_mixed_invite_sanitized_notice

            sanitized = await send_mixed_invite_sanitized_notice(
                effective_message,
                kept_codes=list(decision.internal_codes),
                removed_count=len(decision.blocked_codes or decision.codes),
                source=source,
            )
        except Exception:
            sanitized = False

        if not sanitized:
            await _send_invite_shield_splash(
                effective_message.channel,
                deleted=len(decision.blocked_codes or decision.codes),
                source=source,
            )

        await _report_invite_shield_block_to_spam_guard(effective_message, decision.blocked_codes or decision.codes, source=source)
        _log(
            "deleted invite via central policy "
            f"guild={guild.id} channel={effective_message.channel.id} message={effective_message.id} "
            f"author={effective_message.author.id} source={source} rule={decision.rule_id} codes={','.join((decision.blocked_codes or decision.codes)[:5])}"
        )
    except Exception as exc:
        _log(f"enforcement failed source={source}: {type(exc).__name__}: {exc}")


async def _sweep_channel_recent_invites(channel: discord.TextChannel, *, reason: str = "fallback") -> None:
    try:
        key = (int(channel.guild.id), int(channel.id))
        now = time.monotonic()
        last = float(_LAST_SWEEP_AT.get(key, 0.0) or 0.0)
        if now - last < 8.0:
            return
        _LAST_SWEEP_AT[key] = now

        result = await policy.scan_channel_invites(channel, limit=75, repost_mixed=True, source=f"fallback-sweep:{reason}")
        deleted = int((result or {}).get("deleted") or 0)
        matched = int((result or {}).get("matched") or 0)
        failed = int((result or {}).get("failed") or 0)

        if deleted > 0:
            await _send_invite_shield_splash(channel, deleted=deleted, source=reason)

        if matched or deleted or failed:
            _log(
                "fallback sweep complete "
                f"guild={channel.guild.id} channel={channel.id} reason={reason} "
                f"matched={matched} deleted={deleted} failed={failed}"
            )
    except Exception as exc:
        _log(f"fallback sweep failed channel={getattr(channel, 'id', 'unknown')}: {type(exc).__name__}: {exc}")


async def _delayed_sweep(channel: discord.TextChannel, *, reason: str = "delayed") -> None:
    try:
        await asyncio.sleep(1.5)
        await _sweep_channel_recent_invites(channel, reason=reason)
        await asyncio.sleep(4.0)
        await _sweep_channel_recent_invites(channel, reason=f"{reason}-second-pass")
    finally:
        try:
            _SWEEP_TASKS.pop((int(channel.guild.id), int(channel.id)), None)
        except Exception:
            pass


def _schedule_sweep(channel: Any, *, reason: str = "message") -> None:
    try:
        if not isinstance(channel, discord.TextChannel):
            return
        key = (int(channel.guild.id), int(channel.id))
        task = _SWEEP_TASKS.get(key)
        if task is not None and not task.done():
            return
        loop = asyncio.get_running_loop()
        _SWEEP_TASKS[key] = loop.create_task(_delayed_sweep(channel, reason=reason))
    except Exception as exc:
        _log(f"schedule sweep failed: {type(exc).__name__}: {exc}")


async def _listener(message: discord.Message) -> None:
    try:
        if _message_looks_invite_related(message):
            _schedule_sweep(getattr(message, "channel", None), reason="create")
    except Exception:
        pass
    await _enforce_message(message, source="create")


async def _edit_listener(before: discord.Message, after: discord.Message) -> None:
    _ = before
    try:
        if _message_looks_invite_related(after):
            _schedule_sweep(getattr(after, "channel", None), reason="edit")
    except Exception:
        pass
    await _enforce_message(after, source="edit")


def install() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True
    if bot is None:
        _log("bot unavailable; listener not installed")
        return False
    try:
        existing = list((getattr(bot, "extra_events", {}) or {}).get("on_message") or [])
        if not any(getattr(fn, "__name__", "") == "_listener" and getattr(fn, "__module__", "") == __name__ for fn in existing):
            bot.add_listener(_listener, "on_message")

        existing_edits = list((getattr(bot, "extra_events", {}) or {}).get("on_message_edit") or [])
        if not any(getattr(fn, "__name__", "") == "_edit_listener" and getattr(fn, "__module__", "") == __name__ for fn in existing_edits):
            bot.add_listener(_edit_listener, "on_message_edit")

        _INSTALLED = True
        _log("active; all Discord invite deletes require central invite_policy_engine approval")
        return True
    except Exception as exc:
        _log(f"install failed: {type(exc).__name__}: {exc}")
        return False


install()

__all__ = ["install", "_enforce_message", "_send_invite_shield_splash", "_sweep_channel_recent_invites"]
