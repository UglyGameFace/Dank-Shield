from __future__ import annotations

"""Voice Safety membership-agreement sync.

Voice Safety is a membership rule in guilds that enable it: every current
member is in agreement, joining records agreement, and leaving withdraws it
(the opt-out). This runtime performs a recovery-budgeted authoritative grandfathering backfill and
keeps agreement records in step with Discord membership events.

Only guilds with ``voice_safety_enabled`` are touched. Storage failures are
logged and never raise into Discord event dispatch.
"""

import asyncio
from typing import Any

_INSTALL_ATTR = "_voice_safety_membership_sync_installed"
_BACKFILL_MARKER = "voice_safety_membership_backfill_v2"


def _log(message: str) -> None:
    try:
        print(f"🎙️ voice_safety_membership_sync {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ voice_safety_membership_sync {message}")
    except Exception:
        pass


async def _voice_safety_enabled(guild_id: Any) -> bool:
    from stoney_verify.guild_config import get_guild_config

    try:
        cfg = await get_guild_config(guild_id, refresh=True)
        return bool(cfg.get("voice_safety_enabled") is True)
    except Exception:
        return False


async def _run_backfill(bot: Any) -> None:
    """Backfill with existing bounded, authoritative membership recovery."""
    from stoney_verify.guild_config import get_guild_config, upsert_guild_config
    from stoney_verify.members_new.membership_authority import collect_membership_snapshot
    from stoney_verify.startup_recovery_coordinator import startup_recovery_slot
    from stoney_verify.voice_safety_consent import sync_guild_membership_agreement

    for guild in list(getattr(bot, "guilds", None) or []):
        gid = getattr(guild, "id", 0)
        try:
            if not await _voice_safety_enabled(gid):
                continue
            async with startup_recovery_slot(gid, "voice safety membership backfill"):
                # Recheck policy/marker after waiting for the shared recovery slot.
                cfg = await get_guild_config(gid, refresh=True)
                if cfg.get("voice_safety_enabled") is not True or cfg.get(_BACKFILL_MARKER):
                    continue
                snapshot = await collect_membership_snapshot(guild)
                result = await sync_guild_membership_agreement(
                    guild, members=snapshot.members
                )
                if result["failed"] or not snapshot.authoritative:
                    _warn(
                        f"backfill incomplete guild={gid} "
                        f"recorded={result['recorded']} failed={result['failed']} "
                        f"authoritative={snapshot.authoritative}"
                    )
                    continue
                await upsert_guild_config(gid, {_BACKFILL_MARKER: True})
                _log(
                    f"backfill complete guild={gid} "
                    f"recorded={result['recorded']} skipped={result['skipped']}"
                )
        except Exception as exc:
            _warn(f"backfill failed guild={gid}: {type(exc).__name__}")


async def _on_member_join(member: Any) -> None:
    """Joining a voice-safety guild records agreement: it is required to be in."""
    try:
        if getattr(member, "bot", False):
            return
        guild = getattr(member, "guild", None)
        if guild is None or not await _voice_safety_enabled(guild.id):
            return
        from stoney_verify.voice_safety_consent import record_membership_agreement

        await record_membership_agreement(guild.id, member.id)
    except Exception as exc:
        _warn(
            f"join agreement failed guild={getattr(getattr(member, 'guild', None), 'id', '?')} "
            f"user={getattr(member, 'id', '?')}: {type(exc).__name__}"
        )


async def _on_member_remove(member: Any) -> None:
    """Leaving is the opt-out: withdraw the agreement record on any removal."""
    try:
        guild = getattr(member, "guild", None)
        if guild is None or not await _voice_safety_enabled(guild.id):
            return
        from stoney_verify.voice_safety_consent import withdraw_agreement_on_leave

        await withdraw_agreement_on_leave(guild.id, member.id)
    except Exception as exc:
        _warn(
            f"leave withdrawal failed guild={getattr(getattr(member, 'guild', None), 'id', '?')} "
            f"user={getattr(member, 'id', '?')}: {type(exc).__name__}"
        )


def install_voice_safety_membership_sync(bot: Any) -> bool:
    """Attach join/remove listeners and the one-time backfill. Idempotent."""
    if bot is None or getattr(bot, _INSTALL_ATTR, False):
        return True
    try:
        bot.add_listener(_on_member_join, "on_member_join")
        bot.add_listener(_on_member_remove, "on_member_remove")

        async def _on_ready() -> None:
            # Do not synchronously flood Discord REST during bot readiness.
            task = getattr(bot, "_voice_safety_membership_backfill_task", None)
            if task is None or task.done():
                task = asyncio.create_task(
                    _run_backfill(bot), name="voice_safety_membership_backfill"
                )
                setattr(bot, "_voice_safety_membership_backfill_task", task)

        bot.add_listener(_on_ready, "on_ready")
        setattr(bot, _INSTALL_ATTR, True)
        _log("installed membership agreement sync (join/remove/backfill)")
        return True
    except Exception as exc:
        _warn(f"install failed: {exc!r}")
        return False
