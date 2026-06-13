from __future__ import annotations

"""Stabilize ticket panel doctor and remove setup-health false positives.

The older doctor path had layered wording patches and treated optional ticket pieces
as hard setup failures.  This guard keeps the command read-only, makes the output
single-pass/clear, checks the saved panel message before falling back to history,
and makes optional archive/transcript/panel-message placement advisory instead of
blocking ticket creation.
"""

from typing import Any, Dict, Iterable, List, Optional, Tuple

import discord


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_panel_doctor_stability_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_panel_doctor_stability_guard: {message}")
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


def _fmt_num(value: Any) -> str:
    return f"#{_safe_int(value, 0):04d}"


def _trim_lines(lines: Iterable[str], *, limit: int = 1000, empty: str = "✅ None") -> str:
    kept: List[str] = []
    total = 0
    for raw in lines:
        line = str(raw or "").strip()
        if not line:
            continue
        item = f"• {line}"
        if total + len(item) + 1 > limit:
            kept.append("• …more hidden to keep this Discord card readable")
            break
        kept.append(item)
        total += len(item) + 1
    return "\n".join(kept) if kept else empty


def _cfg_get(panel_mod: Any, cfg: Any, *names: str) -> Any:
    try:
        helper = getattr(panel_mod, "_cfg_get", None)
        if callable(helper):
            return helper(cfg, *names)
    except Exception:
        pass
    for name in names:
        try:
            value = cfg.get(name) if hasattr(cfg, "get") else getattr(cfg, name, None)
            if value not in {None, "", 0, "0"}:
                return value
        except Exception:
            pass
    return None


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


async def _saved_panel_message_status(panel_mod: Any, guild: discord.Guild, panel_channel: Optional[discord.TextChannel]) -> Tuple[bool, str]:
    if not isinstance(panel_channel, discord.TextChannel):
        return False, "No saved public ticket panel channel is configured."
    try:
        cfg = await panel_mod._cfg(int(guild.id))
    except Exception:
        cfg = None
    message_id = _safe_int(_cfg_get(panel_mod, cfg, "ticket_panel_message_id", "public_ticket_panel_message_id", "panel_message_id"), 0)
    if message_id <= 0:
        return False, "Panel channel is saved, but no panel message ID is saved yet. Repost the panel if members cannot see it."
    try:
        message = await panel_channel.fetch_message(message_id)
        ids = _message_custom_ids(message)
        clean_ids = set(getattr(panel_mod, "PANEL_BUTTON_CUSTOM_IDS", set()) or set())
        if ids & clean_ids:
            return True, f"Saved active panel message found in {panel_channel.mention}."
        return False, f"Saved panel message exists in {panel_channel.mention}, but it does not contain the current Dank Shield button. Repost the panel."
    except discord.NotFound:
        return False, f"Saved panel message was deleted from {panel_channel.mention}. Repost the panel."
    except discord.Forbidden:
        return False, f"I cannot read the saved panel message in {panel_channel.mention}. Fix channel permissions."
    except Exception as exc:
        return False, f"Could not verify saved panel message in {panel_channel.mention}: {type(exc).__name__}."


def _category_count(category: Optional[discord.CategoryChannel]) -> int:
    if not isinstance(category, discord.CategoryChannel):
        return 0
    try:
        return len(list(category.channels or []))
    except Exception:
        return 0


def _current_db_sequence(doc_mod: Any, guild: discord.Guild, rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    current_highest = 0
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
                current_highest = max(current_highest, number, doc_mod._num_from_channel(channel))
            else:
                history_count += 1
                history_highest = max(history_highest, number)
        except Exception:
            continue
    return {
        "current_highest": current_highest,
        "current_count": current_count,
        "history_highest": history_highest,
        "history_count": history_count,
    }


async def _read_ticket_rows(panel_mod: Any, guild_id: int) -> List[Dict[str, Any]]:
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
        except Exception:
            return []

    try:
        return await panel_mod._to_thread(sync, [])
    except Exception:
        return []


async def _stable_health_lines(panel_mod: Any, guild: discord.Guild) -> Tuple[List[str], List[str], List[str]]:
    blockers: List[str] = []
    warnings: List[str] = []
    ok: List[str] = []

    active = await panel_mod._active_category(guild)
    staff = await panel_mod._staff_role(guild)
    panel = await panel_mod._panel_channel(guild)
    archive = await panel_mod._archive_category(guild)
    transcripts = await panel_mod._transcript_channel(guild)

    if not active:
        blockers.append("Choose or create the active ticket category.")
    else:
        missing = panel_mod._missing_category_perms(active, guild.me)
        if missing:
            blockers.append(f"Active Tickets category missing bot permissions: {', '.join(missing)}: {active.mention}.")
        else:
            ok.append(f"Active Tickets category can create/manage ticket channels: {active.mention}.")
        try:
            shape = panel_mod._ticket_category_shape_blockers(active, staff)
        except Exception:
            shape = []
        # Privacy/category-shape recommendations are important, but they should not
        # make setup look broken when the bot can still create private ticket channels.
        warnings.extend([f"Privacy recommendation: {item}" for item in shape[:3]])

    if not staff:
        blockers.append("Choose the ticket staff role.")
    else:
        ok.append(f"Ticket staff role configured: {staff.mention}.")

    if not panel:
        warnings.append("Public ticket panel channel is not saved. Ticket creation can still work after staff repost the panel.")
    else:
        missing = panel_mod._missing_text_perms(panel, guild.me)
        if missing:
            blockers.append(f"Ticket panel channel missing bot permissions: {', '.join(missing)}: {panel.mention}.")
        else:
            ok.append(f"Ticket panel/support channel is usable: {panel.mention}.")

    if not archive:
        ok.append("Archive category is optional and not set. Closing still works; archived organization may be limited.")
    else:
        missing = panel_mod._missing_category_perms(archive, guild.me)
        if missing:
            warnings.append(f"Archive category missing optional bot permissions: {', '.join(missing)}: {archive.mention}.")
        else:
            ok.append(f"Archive category ready: {archive.mention}.")

    if not transcripts:
        ok.append("Transcript channel is optional and not set. Ticket creation is not blocked.")
    else:
        missing = panel_mod._missing_text_perms(transcripts, guild.me)
        if missing:
            warnings.append(f"Transcript channel missing optional bot permissions: {', '.join(missing)}: {transcripts.mention}.")
        else:
            ok.append(f"Transcript channel ready: {transcripts.mention}.")

    try:
        cat_ok, cat_msg = await panel_mod._table_probe("ticket_categories", panel_mod.TICKET_CATEGORY_REQUIRED_COLUMNS)
    except Exception as exc:
        cat_ok, cat_msg = False, f"{type(exc).__name__}: {exc}"
    try:
        tic_ok, tic_msg = await panel_mod._table_probe("tickets", panel_mod.TICKET_REQUIRED_COLUMNS)
    except Exception as exc:
        tic_ok, tic_msg = False, f"{type(exc).__name__}: {exc}"

    if cat_ok:
        ok.append("Supabase ticket category table is readable.")
    else:
        blockers.append(f"Supabase ticket category table check failed: {cat_msg}")
    if tic_ok:
        ok.append("Supabase ticket table is readable.")
    else:
        blockers.append(f"Supabase ticket table check failed: {tic_msg}")
    if (not cat_ok or not tic_ok) and not panel_mod._db_url_present():
        warnings.append("Auto-repair needs SUPABASE_DB_URL or DATABASE_URL in Discloud.")

    return blockers, warnings, ok


async def _stable_doctor_command(doc_mod: Any, panel_mod: Any, interaction: discord.Interaction) -> None:
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
    all_rows = await _read_ticket_rows(panel_mod, int(guild.id))
    sequence = _current_db_sequence(doc_mod, guild, all_rows) if db_summary.get("ok") else {"current_highest": 0, "history_highest": 0, "history_count": 0}
    stale_open = await doc_mod._stale_open_rows(guild, db_summary.get("open_like") or []) if db_summary.get("ok") else 0

    panel_message_ok, panel_message_note = await _saved_panel_message_status(panel_mod, guild, panel_channel)
    clean_panels, old_panels, total_panels = await doc_mod._panel_message_counts(panel_channel, limit=200)

    counter_num = _safe_int(counter_summary.get("last_ticket_number"), 0) if counter_summary.get("ok") else 0
    current_db_highest = _safe_int(sequence.get("current_highest"), 0)
    trusted_highest = max(counter_num, highest_channel_num, current_db_highest, 0)
    next_ticket_number = trusted_highest + 1

    blockers: List[str] = []
    warnings: List[str] = []
    info: List[str] = []

    if active is None:
        blockers.append("Active ticket category is missing.")
    else:
        missing = panel_mod._missing_category_perms(active, guild.me)
        if missing:
            blockers.append(f"Active ticket category {active.mention} is missing: {', '.join(missing)}.")
        elif _category_count(active) >= 50:
            blockers.append(f"Active ticket category {active.mention} is full at 50/50 channels.")

    if staff_role is None:
        blockers.append("Ticket staff role is missing.")

    if panel_channel is None:
        warnings.append("Public ticket panel channel is not saved. Repost the panel after setup.")
    else:
        missing = panel_mod._missing_text_perms(panel_channel, guild.me)
        if missing:
            blockers.append(f"Panel channel {panel_channel.mention} is missing: {', '.join(missing)}.")
        elif panel_message_ok:
            info.append(panel_message_note)
        else:
            # Not a setup blocker: the channel can be correct even if the actual
            # panel message was deleted or not saved yet.
            warnings.append(panel_message_note)

    if archive is None:
        info.append("Archive category is optional and not set.")
    else:
        missing = panel_mod._missing_category_perms(archive, guild.me)
        if missing:
            warnings.append(f"Archive category {archive.mention} is missing optional permissions: {', '.join(missing)}.")

    if transcript_channel is None:
        info.append("Transcript channel is optional and not set.")
    else:
        missing = panel_mod._missing_text_perms(transcript_channel, guild.me)
        if missing:
            warnings.append(f"Transcript channel {transcript_channel.mention} is missing optional permissions: {', '.join(missing)}.")

    if not db_summary.get("ok"):
        blockers.append(f"Ticket database check failed: {db_summary.get('error')}")
    if not counter_summary.get("ok"):
        warnings.append(f"Ticket number counter check failed: {counter_summary.get('error')}")
    elif counter_num < max(highest_channel_num, current_db_highest):
        warnings.append(
            f"Ticket number counter is behind current Dank Shield data: counter {_fmt_num(counter_num)}, current highest {_fmt_num(max(highest_channel_num, current_db_highest))}. New tickets still choose the safe next number."
        )

    if stale_open:
        warnings.append(f"{stale_open} active ticket record(s) point to missing or already-closed channels. New tickets are not blocked.")
    if _safe_int(sequence.get("history_count"), 0) > 0:
        info.append(
            f"Saved/imported ticket history exists: highest {_fmt_num(sequence.get('history_highest'))} across {sequence.get('history_count')} record(s). It is informational only."
        )
    if old_panels > 0:
        info.append(f"{old_panels} old panel message(s) were found recently. They are ignored by the current Create Ticket button.")
    if not panel_message_ok and clean_panels > 0:
        info.append(f"Found {clean_panels} active panel message(s) in recent channel history, but no saved current panel message ID matched.")

    color = discord.Color.green() if not blockers and not warnings else (discord.Color.orange() if not blockers else discord.Color.red())
    summary = "Ready" if not blockers and not warnings else ("Usable with warnings" if not blockers else "Needs fixes")
    embed = discord.Embed(
        title="🩺 Dank Shield Ticket Doctor",
        description=f"Read-only ticket check. Result: **{summary}**.",
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Core setup",
        value=(
            f"Active category: {active.mention if active else '**missing**'}\n"
            f"Panel channel: {panel_channel.mention if panel_channel else '**missing**'}\n"
            f"Staff role: {staff_role.mention if staff_role else '**missing**'}\n"
            f"Archive: {archive.mention if archive else 'optional / not set'}\n"
            f"Transcripts: {transcript_channel.mention if transcript_channel else 'optional / not set'}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Numbering",
        value=(
            f"Counter: **{_fmt_num(counter_num)}**\n"
            f"Highest live channel: **{_fmt_num(highest_channel_num)}**\n"
            f"Highest current DB ticket: **{_fmt_num(current_db_highest)}**\n"
            f"Next ticket should be: **{_fmt_num(next_ticket_number)}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Panels + tickets",
        value=(
            f"Saved panel: **{'found' if panel_message_ok else 'needs repost/check'}**\n"
            f"Recent active panels: **{clean_panels}**\n"
            f"Recent old panels: **{old_panels}**\n"
            f"Open ticket channels: **{len(open_named)}**\n"
            f"Closed ticket channels: **{len(closed_named)}**\n"
            f"Panel messages scanned: **{total_panels}**"
        ),
        inline=False,
    )
    embed.add_field(name="Fix now", value=_trim_lines(blockers, empty="✅ No blockers"), inline=False)
    embed.add_field(name="Warnings", value=_trim_lines(warnings, empty="✅ No warnings"), inline=False)
    embed.add_field(name="Notes", value=_trim_lines(info, empty="No extra notes."), inline=False)
    embed.set_footer(text="Read-only. This does not change setup, channels, tickets, or history.")

    await panel_mod.reply_once(interaction, {"embed": embed, "ephemeral": True})


def apply() -> bool:
    try:
        from . import ticket_panel_doctor_command as doc_mod
        from ..commands_ext import public_ticket_panel_clean as panel_mod
    except Exception as exc:
        _warn(f"could not import doctor dependencies: {exc!r}")
        return False

    ok = True
    try:
        panel_mod._health_lines = lambda guild: _stable_health_lines(panel_mod, guild)
        setattr(panel_mod, "_TICKET_PANEL_HEALTH_FALSE_POSITIVE_GUARD", True)
        _log("patched ticket panel health checks to stop optional setup false positives")
    except Exception as exc:
        ok = False
        _warn(f"health patch failed: {exc!r}")

    try:
        async def doctor(panel_mod_arg: Any, interaction: discord.Interaction) -> None:
            return await _stable_doctor_command(doc_mod, panel_mod_arg, interaction)

        doc_mod._doctor_command = doctor
        setattr(doc_mod, "_TICKET_PANEL_DOCTOR_STABILITY_GUARD", True)
        _log("patched /ticket-panel doctor with single-pass stable output")
    except Exception as exc:
        ok = False
        _warn(f"doctor patch failed: {exc!r}")

    return bool(ok)


apply()

__all__ = ["apply"]
