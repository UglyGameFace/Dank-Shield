from __future__ import annotations

"""Compatibility bridge for the historical Discord invite runtime guard.

Live invite ownership now belongs to the guaranteed listener installed from
``stoney_verify.globals``. Missed-message recovery belongs to
``stoney_verify.invite_reconciliation_runtime``. This module keeps the legacy
``_enforce_message`` entrypoint used by an older bridge, but no longer installs a
second live listener or owns separate sweep state.
"""

from typing import Any

import discord

try:
    from stoney_verify.globals import bot
except Exception:  # pragma: no cover
    bot = None  # type: ignore

from stoney_verify import invite_policy_engine as policy
from stoney_verify import invite_reconciliation_runtime as recovery

_INSTALLED = False


def _log(message: str) -> None:
    try:
        print(f"🛡️ discord_invite_blocker_runtime_guard {message}")
    except Exception:
        pass


async def _send_invite_shield_splash(
    channel: discord.TextChannel,
    *,
    deleted: int = 1,
    source: str = "legacy",
) -> None:
    """Preserve the legacy temporary confirmation used by direct bridge calls."""

    try:
        guild = channel.guild
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
        if perms.manage_messages:
            try:
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


async def _enforce_message(message: discord.Message, *, source: str = "legacy-bridge") -> None:
    """Compatibility entrypoint that delegates all policy/deletion to the engine."""

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

        if not codes:
            return

        try:
            from stoney_verify.startup_guards.invite_shield_sanitize_shared import (
                is_trusted_bump_success_receipt,
            )

            if is_trusted_bump_success_receipt(effective_message):
                return
        except Exception:
            pass

        decision = await policy.decide_invite_message(effective_message, source=source)
        if not decision.should_delete:
            return

        deleted = await policy.delete_message_if_allowed(effective_message, decision)
        await policy.send_invite_decision_modlog(effective_message, decision)
        if not deleted:
            _log(
                "policy approved delete but delete failed "
                f"guild={guild.id} channel={effective_message.channel.id} "
                f"message={effective_message.id} source={source} "
                f"error={decision.delete_error}"
            )
            return

        try:
            from stoney_verify.startup_guards.invite_shield_sanitize_shared import (
                send_mixed_invite_sanitized_notice,
            )

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

        await _report_invite_shield_block_to_spam_guard(
            effective_message,
            decision.blocked_codes or decision.codes,
            source=source,
        )
        _log(
            "legacy bridge deleted invite via central policy "
            f"guild={guild.id} channel={effective_message.channel.id} "
            f"message={effective_message.id} author={effective_message.author.id} "
            f"source={source} rule={decision.rule_id}"
        )
    except Exception as exc:
        _log(f"enforcement failed source={source}: {type(exc).__name__}: {exc}")


async def _sweep_channel_recent_invites(
    channel: discord.TextChannel,
    *,
    reason: str = "legacy",
) -> None:
    """Compatibility adapter to the single authoritative recovery runtime."""

    try:
        await recovery._sweep_channel(channel, reason=f"legacy:{reason}")
    except Exception as exc:
        _log(
            f"recovery bridge failed channel={getattr(channel, 'id', 'unknown')} "
            f"reason={reason}: {type(exc).__name__}: {exc}"
        )


def install() -> bool:
    """Install only the authoritative recovery service, never another live owner."""

    global _INSTALLED
    if _INSTALLED:
        return True
    if bot is None:
        _log("bot unavailable; recovery bridge not installed")
        return False

    try:
        if not recovery.install_invite_reconciliation(bot):
            return False
        _INSTALLED = True
        _log("compatibility active; live owner=globals recovery owner=invite_reconciliation_runtime")
        return True
    except Exception as exc:
        _log(f"install failed: {type(exc).__name__}: {exc}")
        return False


install()

__all__ = [
    "install",
    "_enforce_message",
    "_send_invite_shield_splash",
    "_sweep_channel_recent_invites",
]
