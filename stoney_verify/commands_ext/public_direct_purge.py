from __future__ import annotations

"""Small direct purge facade for Dank Shield's compact public command surface.

The destructive implementations remain owned by the existing cleanup/member
modules. This file only restores an obvious `/dank purge ...` doorway and
forwards every invocation to the canonical handlers so permission checks,
previews, confirmations, scan locks, and audit behavior cannot drift.
"""

from typing import Any, Optional

import discord
from discord import app_commands

from .public_setup_group import dank_group


async def _invoke(command: Any, interaction: discord.Interaction, /, *args: Any, **kwargs: Any) -> Any:
    callback = getattr(command, "callback", command)
    if not callable(callback):
        raise RuntimeError("Canonical purge handler is unavailable")
    return await callback(interaction, *args, **kwargs)


@app_commands.describe(
    channel="Channel to purge. Defaults to the current channel.",
    amount="Max matching messages to delete or scan per channel.",
    older_than_hours="Only delete messages older than this many hours. Ignored for user-target purge.",
    include_pinned="Also include pinned messages.",
    dry_run="For channel purge: preview only. User-target purge always previews first.",
    user="Optional user whose messages should be targeted.",
    user_id="Raw Discord user ID, including a user who already left.",
    scope="For user-target purge: this channel or the whole server.",
)
@app_commands.choices(
    scope=[
        app_commands.Choice(name="This channel", value="channel"),
        app_commands.Choice(name="Whole server", value="server"),
    ]
)
async def purge_messages(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    amount: Optional[app_commands.Range[int, 1, 100000]] = None,
    older_than_hours: Optional[app_commands.Range[int, 1, 8760]] = None,
    include_pinned: bool = False,
    dry_run: bool = False,
    user: Optional[discord.User] = None,
    user_id: Optional[str] = None,
    scope: str = "channel",
) -> None:
    from .public_cleanup_group import cleanup_purge

    await _invoke(
        cleanup_purge,
        interaction,
        channel=channel,
        amount=amount,
        older_than_hours=older_than_hours,
        include_pinned=include_pinned,
        dry_run=dry_run,
        user=user,
        user_id=user_id,
        scope=scope,
    )


@app_commands.describe(
    inactive_days="Only members quiet this many days are eligible. Default 90.",
    grace_days="Protect newer members inside this many days. Default 14.",
    include_low_confidence="Include low-confidence scan candidates. Default false.",
    reason="Reason stored in Discord audit log and Dank Shield activity history.",
)
async def purge_members(
    interaction: discord.Interaction,
    inactive_days: app_commands.Range[int, 7, 730] = 90,
    grace_days: app_commands.Range[int, 1, 90] = 14,
    include_low_confidence: bool = False,
    reason: str = "Purge-all inactive verified/resident cleanup",
) -> None:
    from .public_members_cleanup_group import members_purge_all

    await _invoke(
        members_purge_all,
        interaction,
        inactive_days=int(inactive_days),
        grace_days=int(grace_days),
        include_low_confidence=include_low_confidence,
        reason=reason,
    )


def build_direct_purge_group() -> app_commands.Group:
    group = app_commands.Group(
        name="purge",
        description="Direct message and inactive-member purge tools.",
    )
    group.add_command(
        app_commands.Command(
            name="messages",
            description="Purge channel messages or preview/delete one user's messages.",
            callback=purge_messages,
        )
    )
    group.add_command(
        app_commands.Command(
            name="members",
            description="Preview and purge strictly eligible inactive verified/resident members.",
            callback=purge_members,
        )
    )
    return group


def install_direct_purge_group() -> app_commands.Group:
    existing = dank_group.get_command("purge")
    if existing is not None:
        try:
            dank_group.remove_command("purge")
        except Exception:
            pass

    group = build_direct_purge_group()
    dank_group.add_command(group)
    return group


__all__ = [
    "build_direct_purge_group",
    "install_direct_purge_group",
    "purge_members",
    "purge_messages",
]
