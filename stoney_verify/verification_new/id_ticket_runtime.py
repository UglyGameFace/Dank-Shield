from __future__ import annotations

"""Canonical runtime for posting the allowlisted ID-verification ticket panel.

The public ticket creator intentionally relies on the managed ticket category's
inherited permissions. Older verification code tried to rewrite bot, owner, and
staff overwrites immediately after the ticket was created. That redundant write
could raise Discord 50013 even though the bot and requester already had all
required effective permissions, leaving a valid ticket without its ID panel.

This module first checks effective access. It only invokes the legacy repair
routine when access is genuinely missing, then requires the panel poster to
report an actual ``posted`` or ``updated`` result.
"""

from typing import Any, Awaitable, Callable

import discord

from ..setup_engine.verification_modes import effective_verification_mode


ConfigLoader = Callable[[int], Awaitable[Any]]
AccessRepair = Callable[[discord.TextChannel, discord.Member], Awaitable[bool]]
PanelPoster = Callable[..., Awaitable[Any]]


def _log(message: str) -> None:
    try:
        print(f"id_ticket_runtime: {message}")
    except Exception:
        pass


def _required_owner_access(perms: Any) -> bool:
    return bool(
        getattr(perms, "view_channel", False)
        and getattr(perms, "send_messages", False)
        and getattr(perms, "read_message_history", False)
    )


def _required_bot_access(perms: Any) -> bool:
    return bool(
        getattr(perms, "view_channel", False)
        and getattr(perms, "send_messages", False)
        and getattr(perms, "read_message_history", False)
        and getattr(perms, "embed_links", False)
    )


def _bot_member(guild: discord.Guild) -> discord.Member | None:
    try:
        if isinstance(guild.me, discord.Member):
            return guild.me
    except Exception:
        pass

    try:
        state = getattr(guild, "_state", None)
        user = getattr(state, "user", None)
        user_id = int(getattr(user, "id", 0) or 0)
        member = guild.get_member(user_id)
        return member if isinstance(member, discord.Member) else None
    except Exception:
        return None


def effective_ticket_access_ready(
    channel: discord.TextChannel,
    member: discord.Member,
) -> bool:
    """Return whether inherited/current permissions already support the panel."""
    try:
        bot_member = _bot_member(channel.guild)
        if bot_member is None:
            return False
        owner_perms = channel.permissions_for(member)
        bot_perms = channel.permissions_for(bot_member)
        return _required_owner_access(owner_perms) and _required_bot_access(bot_perms)
    except Exception:
        return False


async def post_allowlisted_id_ticket_panel(
    channel: discord.TextChannel,
    member: discord.Member,
    *,
    config_loader: ConfigLoader,
    access_repair: AccessRepair,
    panel_poster: PanelPoster,
    site_url: str,
    ttl_minutes: int,
    allow_regen: bool,
) -> bool:
    """Post the real ID panel only for the canonical allowlisted ID mode."""
    try:
        cfg = await config_loader(int(channel.guild.id))
    except Exception as exc:
        _log(
            f"config lookup failed guild={getattr(channel.guild, 'id', 0)} "
            f"channel={getattr(channel, 'id', 0)} error={type(exc).__name__}"
        )
        return False

    try:
        mode = effective_verification_mode(channel.guild, cfg)
    except Exception as exc:
        _log(
            f"mode resolution failed guild={getattr(channel.guild, 'id', 0)} "
            f"channel={getattr(channel, 'id', 0)} error={type(exc).__name__}"
        )
        return False

    if mode != "id_verify":
        _log(
            f"panel blocked by canonical mode guild={channel.guild.id} "
            f"channel={channel.id} mode={mode}"
        )
        return False

    if not effective_ticket_access_ready(channel, member):
        try:
            repaired = bool(await access_repair(channel, member))
        except Exception as exc:
            _log(
                f"access repair crashed guild={channel.guild.id} channel={channel.id} "
                f"user={member.id} error={type(exc).__name__}"
            )
            return False
        if not repaired:
            _log(
                f"access not ready guild={channel.guild.id} channel={channel.id} "
                f"user={member.id}"
            )
            return False
    else:
        _log(
            f"using inherited ticket access guild={channel.guild.id} "
            f"channel={channel.id} user={member.id}"
        )

    try:
        result = await panel_poster(
            channel,
            requester_id=int(member.id),
            reason="auto-routed from public ticket panel",
            site_url=str(site_url or ""),
            ttl_minutes=int(ttl_minutes or 20),
            allow_regen=bool(allow_regen),
        )
    except Exception as exc:
        _log(
            f"panel post crashed guild={channel.guild.id} channel={channel.id} "
            f"user={member.id} error={type(exc).__name__}: {exc}"
        )
        return False

    posted = result is True or str(result or "").strip().lower() in {"posted", "updated"}
    if not posted:
        _log(
            f"panel poster returned no success guild={channel.guild.id} "
            f"channel={channel.id} user={member.id} result={result!r}"
        )
        return False

    _log(
        f"panel ready guild={channel.guild.id} channel={channel.id} "
        f"user={member.id} result={result}"
    )
    return True


__all__ = [
    "effective_ticket_access_ready",
    "post_allowlisted_id_ticket_panel",
]
