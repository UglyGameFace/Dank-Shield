from __future__ import annotations

"""Guaranteed live Discord invite enforcement.

This is the final live gateway path:
message -> central invite policy engine -> delete only if policy says delete.

It does not create a second policy. It only makes sure the central engine is
actually called for live messages.
"""

import builtins
from typing import Any

import discord


_INSTALLED_FLAG = "_dank_invite_live_enforcer_guard_installed"


def _log(message: str) -> None:
    try:
        print(f"🛡️ invite_live_enforcer {message}")
    except Exception:
        pass


def apply() -> None:
    if getattr(builtins, _INSTALLED_FLAG, False):
        return

    try:
        from stoney_verify.globals import bot
    except Exception as exc:
        _log(f"failed_to_load_bot: {type(exc).__name__}: {exc}")
        return

    try:
        intents = getattr(bot, "intents", None)
        if intents is not None and not bool(getattr(intents, "message_content", False)):
            _log(
                "WARNING: bot.intents.message_content is false. "
                "Normal invite text may be invisible unless Message Content Intent is enabled in Discord Developer Portal."
            )
    except Exception:
        pass

    @bot.listen("on_message")
    async def _dank_live_invite_enforcer(message: discord.Message) -> None:
        try:
            guild = getattr(message, "guild", None)
            if guild is None:
                return

            # Do not delete Dank Shield's own messages.
            try:
                if getattr(message, "author", None) and getattr(bot, "user", None):
                    if int(getattr(message.author, "id", 0) or 0) == int(getattr(bot.user, "id", 0) or 0):
                        return
            except Exception:
                pass

            from stoney_verify.invite_policy_engine import (
                decide_invite_message,
                delete_message_if_allowed,
                decision_summary,
                extract_invite_codes_from_message,
                send_invite_decision_modlog,
            )

            codes = extract_invite_codes_from_message(message)
            if not codes:
                return

            decision = await decide_invite_message(
                message,
                source="live_gateway_enforcer",
                refresh_policy=True,
            )

            if decision.should_delete:
                deleted = await delete_message_if_allowed(message, decision)
                _log(
                    "decision "
                    f"guild={getattr(guild, 'id', 0)} "
                    f"channel={getattr(getattr(message, 'channel', None), 'id', 0)} "
                    f"author={getattr(getattr(message, 'author', None), 'id', 0)} "
                    f"codes={','.join(decision.codes[:5])} "
                    f"blocked={','.join(decision.blocked_codes[:5])} "
                    f"rule={decision.rule_id} "
                    f"action={decision.action} "
                    f"deleted={deleted} "
                    f"error={decision.delete_error or '-'}"
                )
                try:
                    await send_invite_decision_modlog(message, decision)
                except Exception:
                    pass
                return

            # Log only the non-delete invite cases so we can diagnose why it stayed.
            _log(
                "allowed "
                f"guild={getattr(guild, 'id', 0)} "
                f"codes={','.join(decision.codes[:5])} "
                f"rule={decision.rule_id} "
                f"action={decision.action} "
                f"invite_shield={decision.invite_shield_enabled} "
                f"link_shield={decision.link_shield_enabled} "
                f"reason={decision.reason[:180]}"
            )

            if decision.action in {"log_only", "warn"}:
                try:
                    await send_invite_decision_modlog(message, decision)
                except Exception:
                    pass

        except Exception as exc:
            _log(f"live_enforcer_error: {type(exc).__name__}: {exc}")

    setattr(builtins, _INSTALLED_FLAG, True)
    _log("active; live messages now call central invite policy engine")
