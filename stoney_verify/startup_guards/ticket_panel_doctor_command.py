from __future__ import annotations

"""Add `/ticket-panel doctor` for operator-grade ticket diagnostics.

This command is intentionally read-only. It does not repair/delete/move anything.
It gives staff a TicketTool-plus view of what is healthy, risky, duplicated, or
stale before members feel the problem.
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

import discord
from discord import app_commands


LEGACY_PUBLIC_PANEL_IDS = {"ticket_create"}
CLEAN_PUBLIC_PANEL_IDS = {"sv:ticket:panel:create:clean:v1"}
TICKET_NAME_RE = re.compile(r"^(?:ticket|closed)-(\d{1,8})$", re.I)
TOPIC_NUMBER_RE = re.compile(r"(?:^|[;\s])ticket_number=(\d{1,8})(?:$|[;\s])", re.I)


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_panel_doctor_command: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_panel_doctor_command: {message}")
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


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _short(value: Any, limit: int = 900) -> str:
    text = _safe_str(value)
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _num_from_channel(ch: discord.TextChannel) -> int:
    try:
        match = TICKET_NAME_RE.match(str(ch.name or ""))
        if match:
            return _safe_int(match.group(1), 0)
    except Exception:
        pass
    try:
        match = TOPIC_NUMBER_RE.search(str(ch.topic or ""))
        if match:
            return _safe_int(match.group(1), 0)
    except Exception:
        pass
    return 0


def _iter_text_channels(guild: discord.Guild) -> List[discord.TextChannel]:
    out: List[discord.TextChannel] = []
    seen: set[int] = set()
    try:
        for ch in list(getattr(guild, "text_channels", []) or []):
            if isinstance(ch, discord.TextChannel) and int(ch.id) not in seen:
                seen.add(int(ch.id))
                out.append(ch)
    except Exception:
        pass
    return out


def _is_ticket_channel(ch: discord.TextChannel) -> bool:
    try:
        return bool(TICKET_NAME_RE.match(str(ch.name or "")))
    except Exception:
        return False


def _message_custom_ids(message: discord.Message) -> set[str]:
    found: set[str] = set()
    try:
        for row in list(getattr(message, "components", None) or []):
            for child in list(getattr(row, "children", None) or []):
                cid = _safe_str(getattr(child, "custom_id", ""))
                if cid:
                    found.add(cid)
    except Exception:
        pass
    return found


async def _panel_message_counts(channel: Optional[discord.TextChannel], *, limit: int = 80) -> Tuple[int, int, int]:
    if not isinstance(channel, discord.TextChannel):
        return (0, 0, 0)
    clean = 0
    legacy = 0
    total = 0
    try:
        async for msg in channel.history(limit=limit):
            ids = _message_custom_ids(msg)
            if not ids:
                continue
            if ids & CLEAN_PUBLIC_PANEL_IDS:
                clean += 1
                total += 1
            elif ids & LEGACY_PUBLIC_PANEL_IDS:
                legacy += 1
                total += 1
    except Exception:
        pass
    return clean, legacy, total


def _db_client(panel_mod: Any) -> Any:
    try:
        return panel_mod._sb()
    except Exception:
        return None


async def _db_ticket_summary(panel_mod: Any, guild_id: int) -> Dict[str, Any]:
    sb = _db_client(panel_mod)
    if sb is None:
        return {"ok": False, "error": "Supabase client unavailable"}

    def sync() -> Dict[str, Any]:
        try:
            rows = getattr(
                sb.table("tickets")
                .select("id,status,channel_id,discord_thread_id,ticket_number,user_id,updated_at,created_at")
                .eq("guild_id", str(guild_id))
                .limit(500)
                .execute(),
                "data",
                None,
            ) or []
            if not isinstance(rows, list):
                rows = []
            counts: Dict[str, int] = {}
            highest = 0
            open_like: List[Dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                status = _safe_str(row.get("status"), "unknown").lower()
                counts[status] = counts.get(status, 0) + 1
                highest = max(highest, _safe_int(row.get("ticket_number"), 0))
                if status in {"open", "claimed"}:
                    open_like.append(row)
            return {"ok": True, "counts": counts, "highest_ticket_number": highest, "open_like": open_like, "row_count": len(rows)}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {_short(e, 240)}"}

    try:
        return await panel_mod._to_thread(sync, {"ok": False, "error": "DB query failed"})
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {_short(e, 240)}"}


async def _db_counter_summary(panel_mod: Any, guild_id: int) -> Dict[str, Any]:
    sb = _db_client(panel_mod)
    if sb is None:
        return {"ok": False, "error": "Supabase client unavailable"}

    def sync() -> Dict[str, Any]:
        try:
            rows = getattr(
                sb.table("ticket_counters")
                .select("last_ticket_number,updated_at")
                .eq("guild_id", str(guild_id))
                .limit(1)
                .execute(),
                "data",
                None,
            ) or []
            if rows and isinstance(rows[0], dict):
                return {"ok": True, "last_ticket_number": _safe_int(rows[0].get("last_ticket_number"), 0), "updated_at": rows[0].get("updated_at")}
            return {"ok": True, "last_ticket_number": 0, "updated_at": None, "missing": True}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {_short(e, 240)}"}

    try:
        return await panel_mod._to_thread(sync, {"ok": False, "error": "DB query failed"})
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {_short(e, 240)}"}


async def _stale_open_rows(guild: discord.Guild, rows: Iterable[Dict[str, Any]]) -> int:
    stale = 0
    for row in rows:
        try:
            cid = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
            ch = guild.get_channel(cid) if cid > 0 else None
            if not isinstance(ch, discord.TextChannel):
                stale += 1
                continue
            name = str(ch.name or "").lower()
            cat = str(getattr(getattr(ch, "category", None), "name", "") or "").lower()
            if name.startswith("closed-") or ("archive" in cat and "ticket" in cat):
                stale += 1
        except Exception:
            stale += 1
    return stale


def _fmt_counts(counts: Dict[str, int]) -> str:
    if not counts:
        return "No ticket rows found."
    wanted = ["open", "claimed", "closed", "deleted", "unknown"]
    parts = []
    used = set()
    for key in wanted:
        if key in counts:
            used.add(key)
            parts.append(f"{key}: **{counts[key]}**")
    for key in sorted(k for k in counts if k not in used):
        parts.append(f"{key}: **{counts[key]}**")
    return " • ".join(parts)


def _line(ok: bool, text: str) -> str:
    return f"{'✅' if ok else '⚠️'} {text}"


async def _doctor_command(panel_mod: Any, interaction: discord.Interaction) -> None:
    if not panel_mod._staff_check(interaction):
        return await panel_mod.reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

    guild = interaction.guild
    if guild is None:
        return await panel_mod.reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

    await panel_mod._defer(interaction, thinking=True)

    active = await panel_mod._active_category(guild)
    archive = await panel_mod._archive_category(guild)
    panel_channel = await panel_mod._panel_channel(guild)
    transcript_channel = await panel_mod._transcript_channel(guild)
    staff_role = await panel_mod._staff_role(guild)

    channels = _iter_text_channels(guild)
    ticket_channels = [ch for ch in channels if _is_ticket_channel(ch)]
    open_named = [ch for ch in ticket_channels if str(ch.name or "").lower().startswith("ticket-")]
    closed_named = [ch for ch in ticket_channels if str(ch.name or "").lower().startswith("closed-")]
    highest_channel_num = max([0] + [_num_from_channel(ch) for ch in ticket_channels])

    db_summary = await _db_ticket_summary(panel_mod, guild.id)
    counter_summary = await _db_counter_summary(panel_mod, guild.id)
    stale_open = 0
    if db_summary.get("ok"):
        stale_open = await _stale_open_rows(guild, db_summary.get("open_like") or [])

    clean_panels, legacy_panels, total_panels = await _panel_message_counts(panel_channel)

    blockers: List[str] = []
    warnings: List[str] = []
    passing: List[str] = []

    if not active:
        blockers.append("Active Tickets category is not configured/found.")
    else:
        missing = panel_mod._missing_category_perms(active, guild.me)
        (blockers if missing else passing).append(
            f"Active category: {active.mention}" + (f" missing {', '.join(missing)}" if missing else "")
        )

    if not staff_role:
        blockers.append("Ticket staff role is not configured/found.")
    else:
        passing.append(f"Ticket staff role: {staff_role.mention}")

    if not panel_channel:
        warnings.append("Ticket panel channel is not configured/found.")
    else:
        missing = panel_mod._missing_text_perms(panel_channel, guild.me)
        (blockers if missing else passing).append(
            f"Panel channel: {panel_channel.mention}" + (f" missing {', '.join(missing)}" if missing else "")
        )

    if not archive:
        warnings.append("Archive category is not configured/found.")
    else:
        missing = panel_mod._missing_category_perms(archive, guild.me)
        (warnings if missing else passing).append(
            f"Archive category: {archive.mention}" + (f" missing {', '.join(missing)}" if missing else "")
        )

    if not transcript_channel:
        warnings.append("Transcript channel is not configured/found.")
    else:
        missing = panel_mod._missing_text_perms(transcript_channel, guild.me)
        (warnings if missing else passing).append(
            f"Transcript channel: {transcript_channel.mention}" + (f" missing {', '.join(missing)}" if missing else "")
        )

    if not db_summary.get("ok"):
        blockers.append(f"Tickets DB query failed: {db_summary.get('error')}")
    else:
        passing.append(f"Tickets DB readable: {_fmt_counts(db_summary.get('counts') or {})}")
        if stale_open:
            warnings.append(f"{stale_open} open/claimed DB row(s) point to missing/closed channels.")

    if not counter_summary.get("ok"):
        warnings.append(f"ticket_counters read failed: {counter_summary.get('error')}")
    else:
        counter_num = _safe_int(counter_summary.get("last_ticket_number"), 0)
        highest_db_num = _safe_int(db_summary.get("highest_ticket_number"), 0) if db_summary.get("ok") else 0
        expected_min = max(highest_channel_num, highest_db_num)
        if counter_num < expected_min:
            warnings.append(f"Ticket counter is behind. counter={counter_num}, highest_seen={expected_min}.")
        else:
            passing.append(f"Ticket counter ready: #{counter_num:04d}.")

    if clean_panels <= 0:
        warnings.append("No clean public ticket panel found in the saved panel channel history.")
    elif clean_panels > 1:
        warnings.append(f"{clean_panels} clean ticket panels found. Keep one active panel to reduce confusion.")
    else:
        passing.append("Exactly one clean public ticket panel found recently.")

    if legacy_panels > 0:
        warnings.append(f"{legacy_panels} old legacy public ticket panel(s) found recently. Delete old panel messages.")

    color = discord.Color.green() if not blockers and not warnings else (discord.Color.orange() if not blockers else discord.Color.red())
    embed = discord.Embed(
        title="🩺 Dank Shield Ticket Doctor",
        description=(
            "Read-only ticket system audit. Use this before assuming members are the problem."
            if not blockers else
            "🚫 Fix blockers before trusting ticket creation."
        ),
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Ticket Channels", value=(
        f"Open-name channels: **{len(open_named)}**\n"
        f"Closed-name channels: **{len(closed_named)}**\n"
        f"Highest channel number seen: **#{highest_channel_num:04d}**"
    ), inline=False)
    embed.add_field(name="Panel Messages", value=(
        f"Clean panels found: **{clean_panels}**\n"
        f"Legacy panels found: **{legacy_panels}**\n"
        f"Total ticket-like panels scanned: **{total_panels}**"
    ), inline=False)
    embed.add_field(name="Blockers", value=panel_mod._field(blockers, "✅ None"), inline=False)
    embed.add_field(name="Warnings", value=panel_mod._field(warnings, "✅ None"), inline=False)
    embed.add_field(name="Passing", value=panel_mod._field(passing[:10], "No passing checks."), inline=False)
    embed.set_footer(text="Better-than-TicketTool rule: visible health, safe lifecycle, no hidden broken state.")

    await panel_mod.reply_once(interaction, {"embed": embed, "ephemeral": True})


def apply() -> bool:
    try:
        from ..commands_ext import public_ticket_panel_clean as panel_mod
    except Exception as e:
        _warn(f"could not import public_ticket_panel_clean: {e!r}")
        return False

    if getattr(panel_mod, "_TICKET_PANEL_DOCTOR_COMMAND_APPLIED", False):
        return True

    original_group = getattr(panel_mod, "_ticket_panel_group", None)
    if not callable(original_group):
        _warn("public_ticket_panel_clean._ticket_panel_group is not callable")
        return False

    def group_wrapper() -> app_commands.Group:
        group = original_group()

        try:
            if group.get_command("doctor") is None:
                @group.command(name="doctor", description="Run a read-only ticket system audit.")
                async def doctor(interaction: discord.Interaction) -> None:
                    await _doctor_command(panel_mod, interaction)
        except Exception as e:
            _warn(f"could not attach doctor command: {e!r}")

        return group

    try:
        setattr(group_wrapper, "_ticket_panel_doctor_wrapped", True)
        panel_mod._ticket_panel_group = group_wrapper
        setattr(panel_mod, "_TICKET_PANEL_DOCTOR_COMMAND_APPLIED", True)
        _log("added /ticket-panel doctor")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
