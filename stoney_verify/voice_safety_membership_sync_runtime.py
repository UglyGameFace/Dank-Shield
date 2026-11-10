"""Voice Safety membership lifecycle: one-time grandfathering + leave opt-out.

Membership is the opt-in, decided by the server owner: being a member of the
server authorizes monitoring, and leaving the server ends it. The join
listener stays unwired because joining needs no record — admission reads
membership, not a stored agreement.

The one-time grandfathering, also decided by the server owner, records
members who were already verified when the disclosed Verify panel arrived:
they joined under the server's stated join agreement (verify, agree to voice
monitoring, or decline and exit). The backfill records exactly those members
— verified-role holders only — under the auditable
``membership_grandfather_v1`` source, once per guild, and never runs again.
Those records are now an audit trail; admission no longer depends on them.
Leaving still withdraws any agreement.
"""
from __future__ import annotations

import asyncio
from typing import Any

_INSTALL_ATTR = "_voice_safety_membership_sync_installed"
_BACKFILL_MARKER = "voice_safety_grandfather_backfill_v1"


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
    """One-time grandfathering with the bounded, authoritative membership recovery."""
    from stoney_verify.guild_config import get_guild_config, upsert_guild_config
    from stoney_verify.members_new.membership_authority import collect_membership_snapshot
    from stoney_verify.startup_recovery_coordinator import startup_recovery_slot
    from stoney_verify.voice_safety_consent import sync_guild_membership_agreement

    for guild in list(getattr(bot, "guilds", None) or []):
        gid = getattr(guild, "id", 0)
        try:
            if not await _voice_safety_enabled(gid):
                continue
            async with startup_recovery_slot(gid, "voice safety grandfather backfill"):
                # Recheck policy/marker after waiting for the shared recovery slot.
                cfg = await get_guild_config(gid, refresh=True)
                if cfg.get("voice_safety_enabled") is not True or cfg.get(_BACKFILL_MARKER):
                    continue
                verified_role_id = cfg.get("verified_role_id")
                if not verified_role_id:
                    _warn(
                        f"grandfather backfill skipped guild={gid}: "
                        "no verified role configured, cannot establish who verified"
                    )
                    continue
                snapshot = await collect_membership_snapshot(guild)
                result = await sync_guild_membership_agreement(
                    guild, members=snapshot.members, verified_role_id=verified_role_id
                )
                if result["failed"] or not snapshot.authoritative:
                    _warn(
                        f"grandfather backfill incomplete guild={gid} "
                        f"recorded={result['recorded']} failed={result['failed']} "
                        f"authoritative={snapshot.authoritative}"
                    )
                    continue
                await upsert_guild_config(gid, {_BACKFILL_MARKER: True})
                _log(
                    f"grandfather backfill complete guild={gid} "
                    f"recorded={result['recorded']} skipped={result['skipped']}"
                )
        except Exception as exc:
            _warn(f"grandfather backfill failed guild={gid}: {type(exc).__name__}")


async def _on_member_join(member: Any) -> None:
    """Joining a Discord guild never authorizes monitoring.

    New members opt in through the Verify click on the disclosed panel, so
    no join listener is installed. Kept as a named no-op so the deliberate
    absence is visible next to the leave handler.
    """
    del member


async def _on_member_remove(member: Any) -> None:
    """Withdraw affirmative consent when a member leaves; no auto regrant."""
    if getattr(member, "bot", False):
        return
    guild = getattr(member, "guild", None)
    if guild is None:
        return
    from .voice_safety_consent import withdraw_agreement_on_leave
    try:
        await withdraw_agreement_on_leave(guild.id, member.id)
    except Exception as exc:
        # No speech content, secrets, or error body in diagnostics.
        print(f"Voice Safety consent withdrawal failed: {type(exc).__name__}")


def install_voice_safety_membership_sync(bot: Any) -> bool:
    """Attach the leave-withdrawal listener and the one-time backfill. Idempotent.

    Deliberately does NOT install a join listener: joining never records
    agreement. The backfill runs once, on ready, and is marker-gated.
    """
    if bot is None or getattr(bot, _INSTALL_ATTR, False):
        return True
    try:
        bot.add_listener(_on_member_remove, "on_member_remove")

        async def _on_ready() -> None:
            # Do not synchronously flood Discord REST during bot readiness.
            task = getattr(bot, "_voice_safety_membership_backfill_task", None)
            if task is None or task.done():
                task = asyncio.create_task(
                    _run_backfill(bot), name="voice_safety_grandfather_backfill"
                )
                setattr(bot, "_voice_safety_membership_backfill_task", task)

        bot.add_listener(_on_ready, "on_ready")
        setattr(bot, _INSTALL_ATTR, True)
        _log("installed voice safety sync (leave withdrawal + one-time grandfather backfill)")
        return True
    except Exception as exc:
        _warn(f"install failed: {exc!r}")
        return False
