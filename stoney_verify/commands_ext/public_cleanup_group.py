from __future__ import annotations

"""Public /stoney cleanup command family.

Boring/professional command strategy:
- No top-level cleanup command spam.
- Cleanup admin utilities live under /stoney cleanup ...
- The old channel_cleanup_admin module can remain as a legacy fallback, but it
  does not need to be loaded for normal public startup.
"""

from typing import Any, Dict, List, Optional

import discord
from discord import app_commands

from ..globals import now_utc
from .common import _staff_check, reply_once, safe_defer
from .public_setup_group import stoney_group

try:
    from ..channel_cleanup import ensure_channel_cleanup_worker_started
except Exception:
    async def ensure_channel_cleanup_worker_started() -> bool:  # type: ignore
        return False

try:
    from .channel_cleanup_admin import (
        _configured_cleanup_channel_ids,
        _configured_hours_map,
        _default_cleanup_hours,
        _default_cleanup_limit,
        _default_include_pins,
        _format_cleanup_summary,
        _purge_channel_messages,
        _resolve_text_channel_by_id,
        _worker_running,
    )
except Exception:
    _configured_cleanup_channel_ids = None  # type: ignore
    _configured_hours_map = None  # type: ignore
    _default_cleanup_hours = None  # type: ignore
    _default_cleanup_limit = None  # type: ignore
    _default_include_pins = None  # type: ignore
    _format_cleanup_summary = None  # type: ignore
    _purge_channel_messages = None  # type: ignore
    _resolve_text_channel_by_id = None  # type: ignore
    _worker_running = None  # type: ignore


_REGISTERED = False

cleanup_group = app_commands.Group(
    name="cleanup",
    description="Channel cleanup and purge tools.",
)


# ============================================================
# Helpers
# ============================================================

def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _truncate(value: Any, limit: int = 1800) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _helpers_ready() -> bool:
    return bool(
        _configured_cleanup_channel_ids
        and _configured_hours_map
        and _default_cleanup_hours
        and _default_cleanup_limit
        and _default_include_pins
        and _format_cleanup_summary
        and _purge_channel_messages
        and _resolve_text_channel_by_id
    )


async def _staff_only(interaction: discord.Interaction) -> bool:
    if _staff_check(interaction):
        return True
    await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
    return False


async def _guild_or_reply(interaction: discord.Interaction) -> Optional[discord.Guild]:
    guild = interaction.guild
    if guild is None:
        await reply_once(interaction, {"content": "❌ This command must be used inside a server.", "ephemeral": True})
        return None
    return guild


# ============================================================
# /stoney cleanup commands
# ============================================================

@cleanup_group.command(name="status", description="Show configured cleanup channels and cleanup worker status.")
async def cleanup_status(interaction: discord.Interaction) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    if not _helpers_ready():
        return await reply_once(interaction, {"content": "❌ Cleanup helpers are unavailable.", "ephemeral": True})

    ids = _configured_cleanup_channel_ids()  # type: ignore[misc]
    hours_map = _configured_hours_map()  # type: ignore[misc]
    default_hours = _default_cleanup_hours()  # type: ignore[misc]
    default_limit = _default_cleanup_limit()  # type: ignore[misc]
    include_pins = _default_include_pins()  # type: ignore[misc]

    embed = discord.Embed(
        title="🧹 Channel Cleanup Status",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Worker",
        value=(
            f"Running: `{_worker_running(interaction.client) if _worker_running else False}`\n"
            f"Default hours: `{default_hours}`\n"
            f"Default limit/run: `{default_limit}`\n"
            f"Include pinned by default: `{include_pins}`"
        ),
        inline=False,
    )

    if not ids:
        embed.add_field(name="Configured Channels", value="`None configured by ID yet.`", inline=False)
    else:
        lines: List[str] = []
        seen: set[int] = set()
        for cid in ids:
            cid_int = _safe_int(cid, 0)
            if cid_int <= 0 or cid_int in seen:
                continue
            seen.add(cid_int)
            ch = guild.get_channel(cid_int)
            hours = int(hours_map.get(cid_int, default_hours))
            if isinstance(ch, discord.TextChannel):
                lines.append(f"<#{cid_int}> • `{ch.name}` • keep last `{hours}h`")
            else:
                lines.append(f"`{cid_int}` • unresolved in this guild • keep last `{hours}h`")
        embed.add_field(name=f"Configured Channels ({len(lines)})", value="\n".join(lines[:20]) if lines else "`None`", inline=False)
        if len(lines) > 20:
            embed.set_footer(text=f"Showing first 20 of {len(lines)} configured channels")

    await reply_once(interaction, {"embed": embed, "ephemeral": True})


@cleanup_group.command(name="run", description="Run cleanup now across all configured cleanup channels.")
@app_commands.describe(
    older_than_hours="Override message age cutoff for all configured channels",
    limit_per_channel="Max messages to delete per channel this run",
    include_pinned="Also delete pinned messages",
    dry_run="Preview only, do not delete anything",
    start_worker="Also ensure the background cleanup worker is started",
)
async def cleanup_run(
    interaction: discord.Interaction,
    older_than_hours: Optional[app_commands.Range[int, 1, 8760]] = None,
    limit_per_channel: Optional[app_commands.Range[int, 1, 5000]] = None,
    include_pinned: Optional[bool] = None,
    dry_run: Optional[bool] = False,
    start_worker: Optional[bool] = False,
) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    if not _helpers_ready():
        return await reply_once(interaction, {"content": "❌ Cleanup helpers are unavailable.", "ephemeral": True})

    await safe_defer(interaction, ephemeral=True)

    if start_worker:
        try:
            started = await ensure_channel_cleanup_worker_started()
            try:
                print(f"🧹 Manual /stoney cleanup run started cleanup worker: started={started}")
            except Exception:
                pass
        except Exception:
            pass

    ids = _configured_cleanup_channel_ids()  # type: ignore[misc]
    if not ids:
        return await interaction.followup.send("❌ No configured cleanup channel IDs were found.", ephemeral=True)

    hours_map = _configured_hours_map()  # type: ignore[misc]
    default_hours = _default_cleanup_hours()  # type: ignore[misc]
    use_limit = int(limit_per_channel or _default_cleanup_limit())  # type: ignore[misc]
    use_include_pinned = bool(include_pinned if include_pinned is not None else _default_include_pins())  # type: ignore[misc]

    results: List[Dict[str, Any]] = []
    for cid in ids:
        cid_int = _safe_int(cid, 0)
        if cid_int <= 0:
            continue
        ch = await _resolve_text_channel_by_id(guild, cid_int)  # type: ignore[misc]
        if not isinstance(ch, discord.TextChannel):
            results.append({
                "channel_id": cid_int,
                "channel_name": "unresolved",
                "matched": 0,
                "deleted": 0,
                "failed": 0,
                "error": "Channel not found or not a text channel.",
            })
            continue

        channel_hours = int(older_than_hours or hours_map.get(cid_int, default_hours))
        try:
            result = await _purge_channel_messages(  # type: ignore[misc]
                ch,
                amount=use_limit,
                older_than_hours=channel_hours,
                include_pinned=use_include_pinned,
                dry_run=bool(dry_run),
                bot_member=guild.me,
            )
            results.append(result)
        except Exception as e:
            results.append({
                "channel_id": cid_int,
                "channel_name": str(getattr(ch, "name", "unknown")),
                "matched": 0,
                "deleted": 0,
                "failed": 0,
                "error": _truncate(e, 300),
            })

    await interaction.followup.send(_truncate(_format_cleanup_summary(results, dry_run=bool(dry_run)), 1900), ephemeral=True)  # type: ignore[misc]


@cleanup_group.command(name="purge", description="Purge messages from a chosen text channel right now.")
@app_commands.describe(
    channel="Channel to purge. Defaults to the current channel.",
    amount="Max number of matching messages to delete",
    older_than_hours="Only delete messages older than this many hours",
    include_pinned="Also delete pinned messages",
    dry_run="Preview only, do not delete anything",
)
async def cleanup_purge(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    amount: Optional[app_commands.Range[int, 1, 5000]] = None,
    older_than_hours: Optional[app_commands.Range[int, 1, 8760]] = None,
    include_pinned: Optional[bool] = False,
    dry_run: Optional[bool] = False,
) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    if not _helpers_ready():
        return await reply_once(interaction, {"content": "❌ Cleanup helpers are unavailable.", "ephemeral": True})

    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        return await reply_once(interaction, {"content": "❌ You must run this in a text channel or provide one.", "ephemeral": True})

    await safe_defer(interaction, ephemeral=True)

    use_amount = int(amount or _default_cleanup_limit())  # type: ignore[misc]
    use_hours = int(older_than_hours) if older_than_hours else None

    try:
        result = await _purge_channel_messages(  # type: ignore[misc]
            target,
            amount=use_amount,
            older_than_hours=use_hours,
            include_pinned=bool(include_pinned),
            dry_run=bool(dry_run),
            bot_member=guild.me,
        )
        await interaction.followup.send(_truncate(_format_cleanup_summary([result], dry_run=bool(dry_run)), 1900), ephemeral=True)  # type: ignore[misc]
    except Exception as e:
        await interaction.followup.send(f"❌ Purge failed: `{_truncate(e, 300)}`", ephemeral=True)


# ============================================================
# Registration
# ============================================================

def register_public_cleanup_group_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    _ = bot, tree
    if _REGISTERED:
        return

    try:
        if stoney_group.get_command("cleanup") is None:
            stoney_group.add_command(cleanup_group)
            print("✅ public_cleanup_group: attached /stoney cleanup commands")
        else:
            print("✅ public_cleanup_group: /stoney cleanup already attached")
    except Exception as e:
        print(f"⚠️ public_cleanup_group failed attaching /stoney cleanup: {repr(e)}")
        raise

    _REGISTERED = True


__all__ = ["register_public_cleanup_group_commands", "cleanup_group"]
