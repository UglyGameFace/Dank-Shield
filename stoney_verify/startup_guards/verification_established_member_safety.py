from __future__ import annotations

"""Extra safety for verification fail-closed actions.

Role updates can briefly look unsafe while Discord is applying changes. A member
who has been in the server beyond the join grace window must not be removed by
an automatic verification fail-closed path without staff review.
"""

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import discord

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🛡️ verification_established_member_safety {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ verification_established_member_safety {message}")
    except Exception:
        pass


def _reason_matches(reason: Any) -> bool:
    text = str(reason or "").lower()
    return "fail-closed" in text or "no safe verification role" in text


def _join_age_seconds(member: discord.Member) -> float | None:
    try:
        joined_at = getattr(member, "joined_at", None)
        if joined_at is None:
            return None
        if joined_at.tzinfo is None:
            joined_at = joined_at.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - joined_at.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _looks_established(member: discord.Member) -> bool:
    age = _join_age_seconds(member)
    if age is None:
        return True
    return age >= 10 * 60


async def _send_log(guild: discord.Guild, member: discord.Member, reason: Any) -> None:
    try:
        from stoney_verify.modlog import _get_modlog_channel

        channel = await _get_modlog_channel(guild)  # type: ignore[misc]
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title="🛡️ Verification Fail-Closed Blocked",
            description="Automatic verification removal was blocked because this member is already established in the server.",
            color=discord.Color.gold(),
        )
        embed.add_field(name="User", value=f"{member.mention}\n`{member}`\n`{member.id}`", inline=False)
        embed.add_field(name="Reason", value=str(reason or "No reason")[:1024], inline=False)
        embed.add_field(name="What To Do", value="Review `/dank setup` verification roles and resolve manually if needed.", inline=False)
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.members_new import join_removal_safety as native

        original = getattr(native, "block_or_run_bot_removal", None)
        if not callable(original):
            _warn("native block_or_run_bot_removal missing")
            return False
        if getattr(original, "_verification_established_member_safety", False):
            _PATCHED = True
            return True

        async def wrapped_block_or_run_bot_removal(
            *,
            action: str,
            guild: discord.Guild,
            member: discord.Member,
            reason: Any,
            runner: Callable[[], Awaitable[Any]],
            staff_confirmed: bool = False,
        ) -> Any:
            if not staff_confirmed and isinstance(member, discord.Member) and _reason_matches(reason) and _looks_established(member):
                try:
                    await native.clear_persisted_member_wait_timers(
                        guild.id,
                        member.id,
                        reason="blocked established verification fail-closed action",
                    )
                except Exception:
                    pass
                _warn(f"blocked established verification fail-closed action guild={guild.id} user={member.id}")
                await _send_log(guild, member, reason)
                return None
            return await original(
                action=action,
                guild=guild,
                member=member,
                reason=reason,
                runner=runner,
                staff_confirmed=staff_confirmed,
            )

        setattr(wrapped_block_or_run_bot_removal, "_verification_established_member_safety", True)
        setattr(native, "block_or_run_bot_removal", wrapped_block_or_run_bot_removal)
        _PATCHED = True
        _log("active; established members are protected from verification fail-closed actions")
        return True
    except Exception as e:
        _warn(f"failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
