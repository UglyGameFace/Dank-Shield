from __future__ import annotations

"""Production-safe wording for `/ticket-panel doctor`.

The original doctor command is useful, but its wording exposed internal migration
logic. This override keeps the same read-only checks while using language a normal
server owner or staff member can understand.
"""

from typing import Any, Dict, Iterable, List, Optional

import discord


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_panel_doctor_production_wording: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_panel_doctor_production_wording: {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _fmt_num(value: Any) -> str:
    return f"#{_safe_int(value, 0):04d}"


def _category_count(category: Optional[discord.CategoryChannel]) -> int:
    if not isinstance(category, discord.CategoryChannel):
        return 0
    try:
        return len(list(category.channels or []))
    except Exception:
        return 0


def _category_capacity_text(category: Optional[discord.CategoryChannel]) -> str:
    if not isinstance(category, discord.CategoryChannel):
        return "Ticket category: **missing**\nNext ticket category: **not available**\nCapacity: **unknown**"

    count = _category_count(category)
    soft_limit = 49
    auto_create = True
    try:
        from . import ticket_overflow_category_guard as overflow
        soft_limit = int(overflow._soft_limit())
        auto_create = bool(overflow._auto_create_enabled())
    except Exception:
        pass

    status = "Ready"
    if count >= 50:
        status = "Full"
    elif count >= soft_limit:
        status = "At overflow threshold"
    elif count >= max(1, soft_limit - 5):
        status = "Getting close"

    return (
        f"Next ticket category: {category.mention}\n"
        f"Channels in category: **{count}/50**\n"
        f"Overflow threshold: **{soft_limit}/50**\n"
        f"Overflow auto-create: **{'Enabled' if auto_create else 'Disabled'}**\n"
        f"Status: **{status}**"
    )


def _sequence_from_rows(doc_mod: Any, guild: discord.Guild, rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    current_db_highest = 0
    history_highest = 0
    history_count = 0
    current_count = 0

    for row in rows or []:
        try:
            number = _safe_int(row.get("ticket_number"), 0)
            if number <= 0:
                continue
            cid = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
            channel = guild.get_channel(cid) if cid > 0 else None
            if isinstance(channel, discord.TextChannel) and doc_mod._is_ticket_channel(channel):
                current_count += 1
                current_db_highest = max(current_db_highest, number, doc_mod._num_from_channel(channel))
            else:
                history_count += 1
                history_highest = max(history_highest, number)
        except Exception:
            continue

    return {
        "current_db_highest": current_db_highest,
        "current_count": current_count,
        "history_highest": history_highest,
        "history_count": history_count,
    }


def _counts_text(doc_mod: Any, counts: Dict[str, int]) -> str:
    try:
        return doc_mod._fmt_counts(counts)
    except Exception:
        if not counts:
            return "No ticket rows found."
        return " • ".join(f"{k}: **{v}**" for k, v in sorted(counts.items()))


async def _read_ticket_rows(panel_mod: Any, guild_id: int) -> List[Dict[str, Any]]:
    """Read ticket rows for history display only."""
    try:
        sb = panel_mod._sb()
    except Exception:
        sb = None
    if sb is None:
        return []

    def sync() -> List[Dict[str, Any]]:
        try:
            rows = getattr(
                sb.table("tickets")
                .select("ticket_number,channel_id,discord_thread_id,status")
                .eq("guild_id", str(guild_id))
                .limit(1000)
                .execute(),
                "data",
                None,
            ) or []
            return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        except Exception as e:
            _warn(f"ticket history row read failed guild={guild_id}: {type(e).__name__}: {e!r}")
            return []

    try:
        return await panel_mod._to_thread(sync, [])
    except Exception:
        return []


async def _production_doctor_command(doc_mod: Any, panel_mod: Any, interaction: discord.Interaction) -> None:
    if not panel_mod._staff_check(interaction):
        return await panel_mod.reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

    guild = interaction.guild
    if guild is None:
        return await panel_mod.reply_once(interaction, {"content": "❌ Server only.", "ephemeral": True})

    await panel_mod._defer(interaction, thinking=True)

    active = await panel_mod._active_category(guild)
    archive = await panel_mod._archive_category(guild)
    panel_channel = await panel_mod._panel_channel(guild)
    transcript_channel = await panel_mod._transcript_channel(guild)
    staff_role = await panel_mod._staff_role(guild)

    channels = doc_mod._iter_text_channels(guild)
    ticket_channels = [ch for ch in channels if doc_mod._is_ticket_channel(ch)]
    open_named = [ch for ch in ticket_channels if str(ch.name or "").lower().startswith("ticket-")]
    closed_named = [ch for ch in ticket_channels if str(ch.name or "").lower().startswith("closed-")]
    highest_channel_num = max([0] + [doc_mod._num_from_channel(ch) for ch in ticket_channels])

    db_summary = await doc_mod._db_ticket_summary(panel_mod, guild.id)
    counter_summary = await doc_mod._db_counter_summary(panel_mod, guild.id)
    all_rows = await _read_ticket_rows(panel_mod, guild.id)

    stale_open = 0
    sequence = {"current_db_highest": 0, "history_highest": 0, "history_count": 0}
    if db_summary.get("ok"):
        stale_open = await doc_mod._stale_open_rows(guild, db_summary.get("open_like") or [])
        sequence = _sequence_from_rows(doc_mod, guild, all_rows)

    clean_panels, old_panels, total_panels = await doc_mod._panel_message_counts(panel_channel)

    counter_num = _safe_int(counter_summary.get("last_ticket_number"), 0) if counter_summary.get("ok") else 0
    current_db_highest = _safe_int(sequence.get("current_db_highest"), 0)
    history_highest = _safe_int(sequence.get("history_highest"), 0)
    history_count = _safe_int(sequence.get("history_count"), 0)
    trusted_highest = max(counter_num, highest_channel_num, current_db_highest, 0)
    next_ticket_number = trusted_highest + 1

    blockers: List[str] = []
    warnings: List[str] = []
    info: List[str] = []
    passing: List[str] = []

    if not active:
        blockers.append("Active ticket category is missing. New tickets may fail until setup is fixed.")
    else:
        missing = panel_mod._missing_category_perms(active, guild.me)
        if missing:
            blockers.append(f"Ticket category {active.mention} is missing bot permissions: {', '.join(missing)}.")
        else:
            passing.append(f"Next ticket category: {active.mention}")

        count = _category_count(active)
        if count >= 50:
            blockers.append(f"Ticket category {active.mention} is full. Add or enable overflow categories before creating more tickets.")
        else:
            try:
                from . import ticket_overflow_category_guard as overflow
                threshold = int(overflow._soft_limit())
            except Exception:
                threshold = 49
            if count >= threshold:
                info.append(f"Ticket overflow is active or ready because the selected category is at **{count}/50** channels.")

    if not staff_role:
        blockers.append("Ticket staff role is missing. Staff-only ticket controls may not work correctly.")
    else:
        passing.append(f"Ticket staff role: {staff_role.mention}")

    if not panel_channel:
        warnings.append("Public ticket panel channel is missing. Staff may need to rerun setup or repost the panel.")
    else:
        missing = panel_mod._missing_text_perms(panel_channel, guild.me)
        if missing:
            blockers.append(f"Panel channel {panel_channel.mention} is missing bot permissions: {', '.join(missing)}.")
        else:
            passing.append(f"Panel channel: {panel_channel.mention}")

    if not archive:
        warnings.append("Archive category is missing. Closed-ticket organization may be limited.")
    else:
        missing = panel_mod._missing_category_perms(archive, guild.me)
        if missing:
            warnings.append(f"Archive category {archive.mention} is missing bot permissions: {', '.join(missing)}.")
        else:
            passing.append(f"Archive category: {archive.mention}")

    if not transcript_channel:
        warnings.append("Transcript channel is missing. Ticket transcript posting may be limited.")
    else:
        missing = panel_mod._missing_text_perms(transcript_channel, guild.me)
        if missing:
            warnings.append(f"Transcript channel {transcript_channel.mention} is missing bot permissions: {', '.join(missing)}.")
        else:
            passing.append(f"Transcript channel: {transcript_channel.mention}")

    if not db_summary.get("ok"):
        blockers.append(f"Ticket database check failed: {db_summary.get('error')}")
    else:
        passing.append(f"Ticket database is readable: {_counts_text(doc_mod, db_summary.get('counts') or {})}")
        if stale_open:
            warnings.append(
                f"{stale_open} active ticket record(s) need cleanup because the linked channel is missing or already closed. "
                "New tickets are not blocked, but staff should repair or archive those records when possible."
            )

    if not counter_summary.get("ok"):
        warnings.append(f"Ticket number counter check failed: {counter_summary.get('error')}")
    elif counter_num < max(highest_channel_num, current_db_highest):
        warnings.append(
            "Ticket number counter is behind current Dank Shield ticket data. "
            f"Counter: {_fmt_num(counter_num)} • current highest: {_fmt_num(max(highest_channel_num, current_db_highest))}. "
            "New tickets will still use the safest available next number."
        )
    else:
        passing.append(f"Ticket number counter is ready: {_fmt_num(counter_num)}")

    if history_count > 0:
        info.append(
            "Saved ticket history was found. "
            f"Highest saved history number: {_fmt_num(history_highest)} across {history_count} record(s). "
            "This is kept for reference only and does not change the next new Dank Shield ticket number."
        )

    if clean_panels <= 0:
        warnings.append("No active Dank Shield public ticket panel was found recently in the saved panel channel.")
    elif clean_panels > 1:
        warnings.append(f"{clean_panels} active Dank Shield public ticket panels were found recently. Keep one active panel to reduce member confusion.")
    else:
        passing.append("Exactly one active Dank Shield public ticket panel was found recently.")

    if old_panels > 0:
        info.append(f"{old_panels} old public ticket panel message(s) were found recently. They are blocked from opening tickets, but deleting old panel messages will reduce confusion.")

    color = discord.Color.green() if not blockers and not warnings else (discord.Color.orange() if not blockers else discord.Color.red())
    embed = discord.Embed(
        title="🩺 Dank Shield Ticket Doctor",
        description="Read-only ticket system check for setup, numbering, panels, category capacity, and ticket safety.",
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Ticket Numbering", value=(
        f"Current Dank Shield counter: **{_fmt_num(counter_num)}**\n"
        f"Highest current channel number: **{_fmt_num(highest_channel_num)}**\n"
        f"Highest current database number: **{_fmt_num(current_db_highest)}**\n"
        f"Next Dank Shield ticket: **{_fmt_num(next_ticket_number)}**"
    ), inline=False)
    embed.add_field(name="Category Capacity", value=_category_capacity_text(active), inline=False)
    embed.add_field(name="Ticket Channels", value=(
        f"Open ticket channels: **{len(open_named)}**\n"
        f"Closed ticket channels: **{len(closed_named)}**\n"
        f"Ticket-like channels scanned: **{len(ticket_channels)}**"
    ), inline=False)
    embed.add_field(name="Public Panels", value=(
        f"Active Dank Shield panels found: **{clean_panels}**\n"
        f"Old panel messages found: **{old_panels}**\n"
        f"Panel messages scanned: **{total_panels}**"
    ), inline=False)
    embed.add_field(name="Blockers", value=panel_mod._field(blockers, "✅ None"), inline=False)
    embed.add_field(name="Warnings", value=panel_mod._field(warnings, "✅ None"), inline=False)
    embed.add_field(name="Info", value=panel_mod._field(info, "No extra notes."), inline=False)
    embed.add_field(name="Passing Checks", value=panel_mod._field(passing[:10], "No passing checks."), inline=False)
    embed.set_footer(text="Read-only check. This command reports issues but does not change channels, tickets, setup, or history.")

    await panel_mod.reply_once(interaction, {"embed": embed, "ephemeral": True})


def apply() -> bool:
    try:
        from . import ticket_panel_doctor_command as doc_mod
        from ..commands_ext import public_ticket_panel_clean as panel_mod
    except Exception as e:
        _warn(f"could not import doctor dependencies: {e!r}")
        return False

    try:
        async def patched_doctor(panel_mod_arg: Any, interaction: discord.Interaction) -> None:
            return await _production_doctor_command(doc_mod, panel_mod_arg, interaction)

        doc_mod._doctor_command = patched_doctor
        setattr(doc_mod, "_TICKET_PANEL_DOCTOR_PRODUCTION_WORDING_APPLIED", True)

        try:
            from . import ticket_panel_repair_records_command as repair_mod
            repair_mod.apply()
        except Exception as e:
            _warn(f"repair-records command load failed: {e!r}")

        _log("patched /ticket-panel doctor with production-safe wording, history notes, repair-records command, and category capacity")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
