from __future__ import annotations

"""Add `/ticket-panel repair-records` for safe stale ticket record cleanup.

The command is preview-first. It only repairs open/claimed ticket records when the
linked Discord channel is missing or already closed. It never deletes ticket rows,
channels, messages, transcripts, or saved history.
"""

from datetime import timezone
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_panel_repair_records_command: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_panel_repair_records_command: {message}")
    except Exception:
        pass


def _now_iso() -> str:
    return discord.utils.utcnow().astimezone(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _fmt_num(value: Any) -> str:
    number = _safe_int(value, 0)
    return f"#{number:04d}" if number > 0 else "unknown number"


def _actor_name(member: discord.Member | discord.User) -> str:
    return str(getattr(member, "display_name", None) or getattr(member, "name", None) or member)


def _db(panel_mod: Any) -> Any:
    try:
        return panel_mod._sb()
    except Exception:
        return None


def _is_already_closed_channel(channel: discord.TextChannel) -> bool:
    try:
        name = str(channel.name or "").lower()
        if name.startswith("closed-"):
            return True
        category_name = str(getattr(getattr(channel, "category", None), "name", "") or "").lower()
        return "archive" in category_name and "ticket" in category_name
    except Exception:
        return False


def _channel_state(guild: discord.Guild, row: Dict[str, Any]) -> tuple[bool, str, Optional[discord.TextChannel]]:
    cid = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
    if cid <= 0:
        return True, "linked channel id is missing", None
    channel = guild.get_channel(cid)
    if not isinstance(channel, discord.TextChannel):
        return True, "linked channel no longer exists", None
    if _is_already_closed_channel(channel):
        return True, "linked channel is already closed or archived", channel
    return False, "linked channel is still active", channel


async def _read_active_rows(panel_mod: Any, guild_id: int) -> List[Dict[str, Any]]:
    sb = _db(panel_mod)
    if sb is None:
        return []

    def sync() -> List[Dict[str, Any]]:
        try:
            rows = getattr(
                sb.table("tickets")
                .select("id,guild_id,user_id,username,status,channel_id,discord_thread_id,ticket_number,channel_name,category,created_at,updated_at")
                .eq("guild_id", str(guild_id))
                .in_("status", ["open", "claimed"])
                .limit(1000)
                .execute(),
                "data",
                None,
            ) or []
            return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        except Exception as e:
            _warn(f"read active rows failed guild={guild_id}: {type(e).__name__}: {e!r}")
            return []

    try:
        return await panel_mod._to_thread(sync, [])
    except Exception:
        return []


async def _close_stale_row(panel_mod: Any, row: Dict[str, Any], actor: discord.Member | discord.User, reason: str) -> bool:
    sb = _db(panel_mod)
    if sb is None:
        return False
    ticket_id = _safe_str(row.get("id"))
    if not ticket_id:
        return False

    now = _now_iso()
    actor_id = _safe_str(getattr(actor, "id", None))
    actor_name = _actor_name(actor)
    close_reason = f"Ticket record repaired by staff: {reason}."

    patch = {
        "status": "closed",
        "updated_at": now,
        "closed_at": row.get("closed_at") or now,
        "closed_by": actor_id,
        "closed_by_name": actor_name,
        "closed_reason": close_reason,
        "close_reason": close_reason,
    }

    def sync() -> bool:
        try:
            result = sb.table("tickets").update(patch).eq("id", str(ticket_id)).in_("status", ["open", "claimed"]).execute()
            rows = getattr(result, "data", None) or []
            return bool(rows)
        except Exception as e:
            _warn(f"repair update failed ticket_id={ticket_id}: {type(e).__name__}: {e!r}")
            return False

    try:
        return bool(await panel_mod._to_thread(sync, False))
    except Exception:
        return False


def _row_label(row: Dict[str, Any], reason: str, channel: Optional[discord.TextChannel]) -> str:
    number = _fmt_num(row.get("ticket_number"))
    status = _safe_str(row.get("status"), "unknown")
    owner = _safe_str(row.get("username") or row.get("user_id"), "unknown user")
    channel_text = channel.mention if isinstance(channel, discord.TextChannel) else _safe_str(row.get("channel_id") or row.get("discord_thread_id"), "no channel")
    return f"{number} • {status} • {owner} • {channel_text} • {reason}"


async def _repair_records_command(panel_mod: Any, interaction: discord.Interaction, apply_changes: bool = False) -> None:
    if not panel_mod._staff_check(interaction):
        return await panel_mod.reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

    guild = interaction.guild
    if guild is None:
        return await panel_mod.reply_once(interaction, {"content": "❌ Server only.", "ephemeral": True})

    await panel_mod._defer(interaction, thinking=True)

    rows = await _read_active_rows(panel_mod, guild.id)
    candidates: List[tuple[Dict[str, Any], str, Optional[discord.TextChannel]]] = []
    for row in rows:
        stale, reason, channel = _channel_state(guild, row)
        if stale:
            candidates.append((row, reason, channel))

    if not candidates:
        embed = discord.Embed(
            title="🧰 Ticket Record Repair",
            description="No stale active ticket records were found. Nothing needs repair.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Read-only check completed. No records were changed.")
        return await panel_mod.reply_once(interaction, {"embed": embed, "ephemeral": True})

    changed = 0
    failed = 0
    lines: List[str] = []

    for row, reason, channel in candidates[:25]:
        label = _row_label(row, reason, channel)
        if apply_changes:
            ok = await _close_stale_row(panel_mod, row, interaction.user, reason)
            if ok:
                changed += 1
                lines.append(f"✅ {label}")
            else:
                failed += 1
                lines.append(f"⚠️ {label}")
        else:
            lines.append(f"• {label}")

    overflow = max(0, len(candidates) - len(lines))
    if overflow:
        lines.append(f"…and {overflow} more record(s). Run again after this batch if needed.")

    if apply_changes:
        description = (
            f"Repaired **{changed}** stale active ticket record(s)."
            if changed else
            "No records were changed."
        )
        if failed:
            description += f" **{failed}** record(s) could not be updated."
        footer = "Changed records were marked closed. No channels, messages, transcripts, or history were deleted."
        color = discord.Color.green() if changed and not failed else discord.Color.orange()
    else:
        description = (
            f"Found **{len(candidates)}** active ticket record(s) that can be safely repaired. "
            "This preview did not change anything."
        )
        footer = "Run again with apply_changes:true to mark these records closed."
        color = discord.Color.orange()

    embed = discord.Embed(
        title="🧰 Ticket Record Repair",
        description=description,
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Records",
        value=panel_mod._field(lines, "No records."),
        inline=False,
    )
    embed.set_footer(text=footer)
    await panel_mod.reply_once(interaction, {"embed": embed, "ephemeral": True})


def apply() -> bool:
    try:
        from ..commands_ext import public_ticket_panel_clean as panel_mod
    except Exception as e:
        _warn(f"could not import public_ticket_panel_clean: {e!r}")
        return False

    if getattr(panel_mod, "_TICKET_PANEL_REPAIR_RECORDS_COMMAND_APPLIED", False):
        return True

    original_group = getattr(panel_mod, "_ticket_panel_group", None)
    if not callable(original_group):
        _warn("public_ticket_panel_clean._ticket_panel_group is not callable")
        return False

    def group_wrapper() -> app_commands.Group:
        group = original_group()
        try:
            if group.get_command("repair-records") is None:
                @group.command(name="repair-records", description="Preview or repair stale ticket database records.")
                @app_commands.describe(apply_changes="False previews only. True marks safe stale records as closed.")
                async def repair_records(interaction: discord.Interaction, apply_changes: bool = False) -> None:
                    await _repair_records_command(panel_mod, interaction, apply_changes=apply_changes)
        except Exception as e:
            _warn(f"could not attach repair-records command: {e!r}")
        return group

    try:
        setattr(group_wrapper, "_ticket_panel_repair_records_wrapped", True)
        panel_mod._ticket_panel_group = group_wrapper
        setattr(panel_mod, "_TICKET_PANEL_REPAIR_RECORDS_COMMAND_APPLIED", True)
        _log("added /ticket-panel repair-records")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
