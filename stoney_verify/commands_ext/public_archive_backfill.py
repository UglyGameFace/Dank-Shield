from __future__ import annotations

import asyncio
from typing import Any, Optional

import discord
from discord import app_commands

from .common import safe_defer
from .public_setup_group import _require_setup_permission, dank_group
from ..guild_config import get_guild_config

try:
    from ..tickets_new.repository import get_ticket_by_any_channel_id
except Exception:
    async def get_ticket_by_any_channel_id(channel_id: int | str) -> Optional[dict[str, Any]]:  # type: ignore
        return None


# ============================================================
# public_archive_backfill.py
# ------------------------------------------------------------
# Audit #5 / P0: archive category lifecycle.
#
# This command is intentionally narrow:
# - It does not create/delete ticket channels.
# - It does not reopen tickets.
# - It does not rewrite transcript metadata.
# - It only moves channels that are already closed-looking or have a
#   canonical closed DB status into the configured archive category.
#
# This gives server owners a safe migration path for old closed tickets after
# configuring ticket_archive_category_id in guild_configs.
# ============================================================


_BACKFILL_TIMEOUT_SECONDS = 120.0
_PER_CHANNEL_DB_TIMEOUT_SECONDS = 2.5
_PER_CHANNEL_EDIT_TIMEOUT_SECONDS = 8.0
_DEFAULT_SCAN_LIMIT = 250
_MAX_SCAN_LIMIT = 500


_ARCHIVE_BACKFILL_ATTACHED = False


def _short(value: Any, limit: int = 180) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _status_from_row(row: Optional[dict[str, Any]]) -> str:
    try:
        status = str((row or {}).get("status") or "").strip().lower()
        if status in {"active", "reopened"}:
            return "open"
        return status
    except Exception:
        return ""


def _row_ticket_number(row: Optional[dict[str, Any]]) -> str:
    try:
        value = str((row or {}).get("ticket_number") or "").strip()
        return value
    except Exception:
        return ""


def _looks_closed_by_name(channel: discord.TextChannel) -> bool:
    try:
        name = str(channel.name or "").strip().lower()
        return name.startswith("closed-") or name.startswith("closed_") or name.startswith("closedticket-")
    except Exception:
        return False


def _looks_ticket_like_by_name(channel: discord.TextChannel, prefix: str) -> bool:
    try:
        name = str(channel.name or "").strip().lower()
        clean_prefix = str(prefix or "ticket").strip().lower() or "ticket"
        return (
            name.startswith(f"{clean_prefix}-")
            or name.startswith(f"{clean_prefix}_")
            or name.startswith("ticket-")
            or name.startswith("ticket_")
            or _looks_closed_by_name(channel)
        )
    except Exception:
        return False


def _channel_ref(channel: discord.TextChannel) -> str:
    try:
        category = channel.category.name if channel.category else "No category"
        return f"#{channel.name} (`{channel.id}`) — {category}"
    except Exception:
        return f"`{getattr(channel, 'id', 'unknown')}`"


def _field_lines(lines: list[str], *, empty: str = "None", limit: int = 1024) -> str:
    if not lines:
        return empty
    out: list[str] = []
    total = 0
    for line in lines:
        text = str(line)
        extra = len(text) + 1
        if total + extra > limit:
            remaining = max(0, len(lines) - len(out))
            if remaining:
                out.append(f"…and {remaining} more")
            break
        out.append(text)
        total += extra
    return "\n".join(out) if out else empty


async def _ticket_row_for_channel(channel_id: int) -> Optional[dict[str, Any]]:
    try:
        row = await asyncio.wait_for(
            get_ticket_by_any_channel_id(int(channel_id)),
            timeout=_PER_CHANNEL_DB_TIMEOUT_SECONDS,
        )
        return row if isinstance(row, dict) else None
    except asyncio.TimeoutError:
        return {"_lookup_error": "timeout"}
    except Exception as e:
        return {"_lookup_error": repr(e)}


def _bot_can_move(channel: discord.TextChannel, archive_category: discord.CategoryChannel) -> tuple[bool, str]:
    try:
        me = channel.guild.me
        if me is None:
            return False, "bot member unavailable"
        source_perms = channel.permissions_for(me)
        archive_perms = archive_category.permissions_for(me)
        if not source_perms.view_channel:
            return False, "bot cannot view source channel"
        if not source_perms.manage_channels:
            return False, "bot lacks Manage Channels on source channel"
        if not archive_perms.view_channel:
            return False, "bot cannot view archive category"
        if not archive_perms.manage_channels:
            return False, "bot lacks Manage Channels in archive category"
        return True, ""
    except Exception as e:
        return False, repr(e)


async def _move_to_archive(channel: discord.TextChannel, archive_category: discord.CategoryChannel) -> tuple[bool, str]:
    try:
        await asyncio.wait_for(
            channel.edit(
                category=archive_category,
                sync_permissions=False,
                reason="Dank Shield archive backfill: move closed ticket to archive category",
            ),
            timeout=_PER_CHANNEL_EDIT_TIMEOUT_SECONDS,
        )
        return True, ""
    except asyncio.TimeoutError:
        return False, "Discord channel edit timed out"
    except discord.Forbidden:
        return False, "Discord denied the move. Check Manage Channels and category permissions."
    except discord.HTTPException as e:
        return False, f"Discord HTTP error: {e}"
    except Exception as e:
        return False, repr(e)


async def _run_archive_backfill(
    *,
    guild: discord.Guild,
    dry_run: bool,
    limit: int,
) -> discord.Embed:
    cfg = await get_guild_config(guild.id, refresh=True)
    archive_category_id = int(getattr(cfg, "effective_ticket_archive_category_id", 0) or 0)
    active_category_id = int(getattr(cfg, "ticket_category_id", 0) or 0)
    ticket_prefix = str(getattr(cfg, "ticket_prefix", "ticket") or "ticket")

    archive_obj = guild.get_channel(archive_category_id) if archive_category_id > 0 else None
    if not isinstance(archive_obj, discord.CategoryChannel):
        embed = discord.Embed(
            title="🚫 Archive Backfill Blocked",
            description="No valid archive ticket category is configured. Set the archive category first, then run backfill again.",
            color=discord.Color.red(),
        )
        embed.add_field(name="Configured archive category ID", value=f"`{archive_category_id or 'missing'}`", inline=False)
        return embed

    me = guild.me
    if me is None:
        return discord.Embed(
            title="🚫 Archive Backfill Blocked",
            description="Bot member is not available in cache yet. Try again after the bot is fully ready.",
            color=discord.Color.red(),
        )

    archive_perms = archive_obj.permissions_for(me)
    blockers: list[str] = []
    if not archive_perms.view_channel:
        blockers.append(f"Bot cannot view archive category **{archive_obj.name}**.")
    if not archive_perms.manage_channels:
        blockers.append(f"Bot needs Manage Channels in archive category **{archive_obj.name}**.")

    if blockers:
        embed = discord.Embed(title="🚫 Archive Backfill Blocked", color=discord.Color.red())
        embed.add_field(name="Blockers", value=_field_lines([f"• {b}" for b in blockers]), inline=False)
        return embed

    scan_limit = max(1, min(int(limit or _DEFAULT_SCAN_LIMIT), _MAX_SCAN_LIMIT))
    channels = list(guild.text_channels)[:scan_limit]

    scanned = 0
    candidates = 0
    moved = 0
    skipped = 0
    errors = 0
    moved_lines: list[str] = []
    would_move_lines: list[str] = []
    skipped_lines: list[str] = []
    error_lines: list[str] = []

    for channel in channels:
        scanned += 1

        if int(getattr(channel, "category_id", 0) or 0) == int(archive_obj.id):
            skipped += 1
            continue

        row = await _ticket_row_for_channel(channel.id)
        lookup_error = str((row or {}).get("_lookup_error") or "")
        row_status = _status_from_row(row)
        has_row = bool(row and not lookup_error)

        name_closed = _looks_closed_by_name(channel)
        ticket_like = _looks_ticket_like_by_name(channel, ticket_prefix)
        in_active_category = bool(active_category_id and int(getattr(channel, "category_id", 0) or 0) == active_category_id)

        should_move = False
        reason = ""
        if row_status == "closed":
            should_move = True
            reason = "DB status is closed"
        elif name_closed and (ticket_like or in_active_category or has_row):
            should_move = True
            reason = "channel name looks closed"

        if not should_move:
            skipped += 1
            continue

        candidates += 1
        can_move, why_not = _bot_can_move(channel, archive_obj)
        if not can_move:
            errors += 1
            error_lines.append(f"• {_channel_ref(channel)} — {why_not}")
            continue

        ticket_no = _row_ticket_number(row)
        details = f"• {_channel_ref(channel)} — {reason}{f' / ticket #{ticket_no}' if ticket_no else ''}"

        if dry_run:
            would_move_lines.append(details)
            continue

        ok, move_error = await _move_to_archive(channel, archive_obj)
        if ok:
            moved += 1
            moved_lines.append(details)
        else:
            errors += 1
            error_lines.append(f"{details} — {_short(move_error, 220)}")

    color = discord.Color.blurple() if dry_run else (discord.Color.green() if errors == 0 else discord.Color.gold())
    embed = discord.Embed(
        title="🗄️ Ticket Archive Backfill Preview" if dry_run else "🗄️ Ticket Archive Backfill Complete",
        description=(
            f"Archive category: {archive_obj.mention if hasattr(archive_obj, 'mention') else archive_obj.name}\n"
            f"Mode: **{'dry run / preview only' if dry_run else 'move channels'}**"
        ),
        color=color,
    )
    embed.add_field(
        name="Summary",
        value=(
            f"Scanned: `{scanned}`\n"
            f"Candidates: `{candidates}`\n"
            f"Moved: `{moved}`\n"
            f"Skipped: `{skipped}`\n"
            f"Errors: `{errors}`"
        ),
        inline=False,
    )
    if dry_run:
        embed.add_field(name="Would Move", value=_field_lines(would_move_lines, empty="None found"), inline=False)
        embed.set_footer(text="Run again with dry_run:false to move these channels.")
    else:
        embed.add_field(name="Moved", value=_field_lines(moved_lines, empty="None moved"), inline=False)
    if error_lines:
        embed.add_field(name="Errors", value=_field_lines(error_lines, empty="None"), inline=False)
    if lookup_error:
        embed.set_footer(text="Some DB lookup errors/timeouts may be omitted when channels are skipped by name/category checks.")
    return embed


@dank_group.command(name="archive-backfill", description="Move old closed ticket channels into the configured archive category.")
@app_commands.describe(
    dry_run="Preview only when true. Set false to actually move channels.",
    limit="Maximum number of text channels to scan. Default 250, max 500.",
)
async def archive_backfill(
    interaction: discord.Interaction,
    dry_run: bool = True,
    limit: app_commands.Range[int, 1, _MAX_SCAN_LIMIT] = _DEFAULT_SCAN_LIMIT,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    try:
        embed = await asyncio.wait_for(
            _run_archive_backfill(guild=guild, dry_run=bool(dry_run), limit=int(limit)),
            timeout=_BACKFILL_TIMEOUT_SECONDS,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
    except asyncio.TimeoutError:
        await interaction.followup.send(
            "⏱️ Archive backfill timed out before finishing. Try again with a smaller `limit`.",
            ephemeral=True,
        )
    except Exception as e:
        try:
            print(f"❌ archive_backfill failed guild={getattr(guild, 'id', 'unknown')}: {repr(e)}")
        except Exception:
            pass
        await interaction.followup.send(f"❌ Archive backfill failed: `{_short(repr(e), 300)}`", ephemeral=True)


def register_public_archive_backfill_commands(bot: Any, tree: Any) -> None:
    _ = bot
    _ = tree
    try:
        print("✅ public_archive_backfill: attached /dank archive-backfill command")
    except Exception:
        pass


__all__ = ["register_public_archive_backfill_commands"]
