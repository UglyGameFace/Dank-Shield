from __future__ import annotations

"""Harden the public Create Ticket -> Confirm flow.

The clean ticket panel already has DB/open-ticket checks. This guard protects the
member-facing interaction layer so old category menus cannot create tickets,
Confirm cannot double-submit, form-based categories can still open Discord
modals correctly, and broken ticket setup is caught before members waste time.
"""

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import discord

_MENU_TTL_SECONDS = 900
_SESSIONS: Dict[Tuple[int, int], Dict[str, Any]] = {}
_CONFIRM_LOCKS: Dict[Tuple[int, int], asyncio.Lock] = {}


def _log(message: str) -> None:
    try:
        print(f"✅ public_ticket_confirm_hardening_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ public_ticket_confirm_hardening_guard: {message}")
    except Exception:
        pass


def _session_key(guild_id: int, user_id: int) -> Tuple[int, int]:
    return (int(guild_id), int(user_id))


def _new_session(guild_id: int, user_id: int) -> str:
    session_id = uuid.uuid4().hex[:16]
    _SESSIONS[_session_key(guild_id, user_id)] = {"id": session_id, "created": time.monotonic()}
    return session_id


def _session_is_current(guild_id: int, user_id: int, session_id: str) -> bool:
    data = _SESSIONS.get(_session_key(guild_id, user_id)) or {}
    if str(data.get("id") or "") != str(session_id or ""):
        return False
    created = float(data.get("created") or 0.0)
    return bool(created and (time.monotonic() - created) <= _MENU_TTL_SECONDS)


def _consume_session(guild_id: int, user_id: int, session_id: str) -> bool:
    key = _session_key(guild_id, user_id)
    if not _session_is_current(guild_id, user_id, session_id):
        return False
    _SESSIONS.pop(key, None)
    return True


def _disable_view(view: discord.ui.View) -> None:
    try:
        for item in view.children:
            try:
                item.disabled = True
            except Exception:
                pass
    except Exception:
        pass


def _member_from_interaction(i: discord.Interaction) -> Optional[discord.Member]:
    try:
        return i.user if isinstance(i.user, discord.Member) else None
    except Exception:
        return None


async def _stale_reply(pt: Any, i: discord.Interaction) -> None:
    await pt._ephemeral(
        i,
        "That ticket menu is stale. Press **Create Ticket** again and use the newest menu.",
    )


def _session_matches_interaction(i: discord.Interaction, owner_id: int, session_id: str) -> bool:
    try:
        guild = i.guild
        member = _member_from_interaction(i)
        if guild is None or member is None:
            return False
        if int(member.id) != int(owner_id):
            return False
        return _session_is_current(int(guild.id), int(member.id), session_id)
    except Exception:
        return False


def _build_category_embed(pt: Any, row: Dict[str, Any]) -> discord.Embed:
    try:
        return pt._category_embed(row)
    except Exception:
        embed = discord.Embed(
            title="Confirm Ticket Category",
            description="Press **Confirm** to open the ticket, or **Back** to choose again.",
            color=discord.Color.blurple(),
        )
        return embed


def _form_questions(row: Dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from stoney_verify.startup_guards import ticket_forms_foundation_guard as forms

        questions = forms._category_questions(row)
        return list(questions or [])
    except Exception:
        return []


def _short_list(lines: List[str], *, limit: int = 900) -> str:
    if not lines:
        return "None"
    out: List[str] = []
    size = 0
    for raw in lines:
        line = str(raw or "").strip()
        if not line:
            continue
        next_size = size + len(line) + 3
        if next_size > limit:
            remaining = max(0, len(lines) - len(out))
            if remaining:
                out.append(f"…and {remaining} more")
            break
        out.append(f"• {line}")
        size = next_size
    return "\n".join(out) or "None"


async def _ticket_setup_preflight(pt: Any, guild: discord.Guild) -> tuple[Optional[discord.CategoryChannel], Optional[discord.Role], List[str], List[str]]:
    blockers: List[str] = []
    warnings: List[str] = []
    try:
        parent = await asyncio.wait_for(pt._active_category(guild), timeout=6.0)
    except asyncio.TimeoutError:
        parent = None
        blockers.append("Active Tickets category lookup timed out.")
    except Exception as exc:
        parent = None
        blockers.append(f"Active Tickets category lookup failed: {type(exc).__name__}.")

    try:
        staff = await asyncio.wait_for(pt._staff_role(guild), timeout=6.0)
    except asyncio.TimeoutError:
        staff = None
        blockers.append("Ticket staff role lookup timed out.")
    except Exception as exc:
        staff = None
        blockers.append(f"Ticket staff role lookup failed: {type(exc).__name__}.")

    if parent is None:
        blockers.append("Active Tickets category is not saved or could not be found.")
    else:
        try:
            missing = list(pt._missing_category_perms(parent, guild.me) or [])
            if missing:
                blockers.append(f"Dank Shield is missing in **{parent.name}**: {', '.join(missing)}.")
        except Exception as exc:
            blockers.append(f"Could not inspect bot permissions on Active Tickets category: {type(exc).__name__}.")
        try:
            blockers.extend(list(pt._ticket_category_shape_blockers(parent, staff) or []))
        except Exception as exc:
            blockers.append(f"Could not inspect Active Tickets privacy shape: {type(exc).__name__}.")

    if staff is None:
        blockers.append("Ticket staff role is not saved or could not be found.")

    try:
        panel = await asyncio.wait_for(pt._panel_channel(guild), timeout=4.0)
        if isinstance(panel, discord.TextChannel):
            missing_panel = list(pt._missing_text_perms(panel, guild.me) or [])
            if missing_panel:
                warnings.append(f"Ticket panel channel {panel.mention} has bot permission issues: {', '.join(missing_panel)}.")
    except Exception:
        pass

    # De-duplicate while preserving order.
    deduped_blockers: List[str] = []
    seen: set[str] = set()
    for line in blockers:
        key = str(line)
        if key and key not in seen:
            seen.add(key)
            deduped_blockers.append(key)
    deduped_warnings: List[str] = []
    seen.clear()
    for line in warnings:
        key = str(line)
        if key and key not in seen:
            seen.add(key)
            deduped_warnings.append(key)
    return parent if isinstance(parent, discord.CategoryChannel) else None, staff if isinstance(staff, discord.Role) else None, deduped_blockers, deduped_warnings


def _ticket_setup_problem_embed(guild: discord.Guild, blockers: List[str], warnings: Optional[List[str]] = None) -> discord.Embed:
    embed = discord.Embed(
        title="🎫 Ticket Setup Needs Repair",
        description=(
            "I stopped before opening the ticket menu because tickets cannot be created safely right now. "
            "Fix the setup first so members do not hit dead-end forms or failed ticket creation."
        ),
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Blockers", value=_short_list(blockers), inline=False)
    if warnings:
        embed.add_field(name="Warnings", value=_short_list(list(warnings)), inline=False)
    embed.add_field(
        name="Fastest Fix",
        value="Run `/dank setup` → **Safety & Repair** → **Preview/Fix Permissions**, then run **Setup Health**. If staff/category mappings are missing, use **Core Setup** → **Use Existing Roles/Channels** first.",
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • Dank Shield ticket preflight")
    return embed


async def _reply_ticket_setup_blocked(pt: Any, i: discord.Interaction, blockers: List[str], warnings: Optional[List[str]] = None) -> None:
    guild = i.guild
    if guild is None:
        return await pt._ephemeral(i, "❌ Ticket setup is not ready.")
    await pt._ephemeral(
        i,
        "❌ Ticket setup needs repair before members can open tickets.",
        embed=_ticket_setup_problem_embed(guild, blockers, warnings),
    )


async def _open_form_without_consuming_response(pt: Any, i: discord.Interaction, row: Dict[str, Any], questions: list[dict[str, Any]]) -> bool:
    """Open the dashboard form modal from a Confirm click.

    The old hardening flow edited the message first ("Opening your ticket…"),
    which consumed the interaction response. Discord modals must be the first
    response to a component interaction, so form categories failed with
    "Could not open the ticket form." This path sends the modal first and only
    marks the old menu stale after Discord accepts it.
    """
    try:
        from stoney_verify.startup_guards import ticket_forms_foundation_guard as forms

        await i.response.send_modal(forms.DashboardTicketFormModal(pt, row, questions))
        return True
    except Exception as exc:
        _warn(f"failed to open ticket form before response consumption: {type(exc).__name__}: {exc}")
        try:
            await pt._ephemeral(i, "❌ Could not open the ticket form. Please try again. I did not consume this menu, so you can press Confirm again.")
        except Exception:
            pass
        return False


class GuardedTicketConfirmView(discord.ui.View):
    def __init__(self, pt: Any, rows: List[Dict[str, Any]], row: Dict[str, Any], owner_id: int, session_id: str) -> None:
        super().__init__(timeout=_MENU_TTL_SECONDS)
        self.pt = pt
        self.rows = rows
        self.row = row
        self.owner_id = int(owner_id)
        self.session_id = str(session_id)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green, emoji="✅")
    async def confirm(self, i: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild = i.guild
        member = _member_from_interaction(i)
        if guild is None or member is None or int(member.id) != self.owner_id:
            return await self.pt._ephemeral(i, "Only the member who opened this ticket menu can use it.")
        key = _session_key(guild.id, member.id)
        lock = _CONFIRM_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _CONFIRM_LOCKS[key] = lock
        if lock.locked():
            return await self.pt._ephemeral(i, "Already opening that ticket. Please wait a second.")
        async with lock:
            if not _session_is_current(guild.id, member.id, self.session_id):
                return await _stale_reply(self.pt, i)

            _parent, _staff, blockers, warnings = await _ticket_setup_preflight(self.pt, guild)
            if blockers:
                return await _reply_ticket_setup_blocked(self.pt, i, blockers, warnings)

            questions = _form_questions(self.row)
            if questions:
                opened = await _open_form_without_consuming_response(self.pt, i, self.row, questions)
                if opened:
                    _consume_session(guild.id, member.id, self.session_id)
                return None

            if not _consume_session(guild.id, member.id, self.session_id):
                return await _stale_reply(self.pt, i)
            _disable_view(self)
            try:
                await i.response.edit_message(
                    content="Opening your ticket…",
                    embed=_build_category_embed(self.pt, self.row),
                    view=self,
                )
            except Exception:
                pass
            await self.pt._create_ticket(i, self.row)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def back(self, i: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _session_matches_interaction(i, self.owner_id, self.session_id):
            return await _stale_reply(self.pt, i)
        embed = discord.Embed(
            title="Create Ticket",
            description="Choose the type of ticket you want to open.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Pick a category. You can review it before anything is created.")
        await self.pt._edit_or_reply(
            i,
            content="Choose a ticket type.",
            embed=embed,
            view=GuardedTicketSelectView(self.pt, self.rows, self.owner_id, self.session_id),
        )


class GuardedTicketSelect(discord.ui.Select):
    def __init__(self, pt: Any, rows: List[Dict[str, Any]], owner_id: int, session_id: str) -> None:
        self.pt = pt
        self.rows = rows
        self.owner_id = int(owner_id)
        self.session_id = str(session_id)
        options = [
            discord.SelectOption(
                label=pt._row_name(r),
                value=pt._row_slug(r),
                description=pt._row_desc(r),
                emoji="🎫",
            )
            for r in rows[:25]
        ]
        super().__init__(placeholder="Choose a ticket type", min_values=1, max_values=1, options=options)

    async def callback(self, i: discord.Interaction) -> None:
        if not _session_matches_interaction(i, self.owner_id, self.session_id):
            return await _stale_reply(self.pt, i)
        slug = self.pt._safe_str(self.values[0], "support")
        row = next((r for r in self.rows if self.pt._row_slug(r) == slug), {"slug": slug, "name": "Support"})
        await self.pt._edit_or_reply(
            i,
            content="Confirm this ticket type.",
            embed=_build_category_embed(self.pt, row),
            view=GuardedTicketConfirmView(self.pt, self.rows, row, self.owner_id, self.session_id),
        )


class GuardedTicketSelectView(discord.ui.View):
    def __init__(self, pt: Any, rows: List[Dict[str, Any]], owner_id: int, session_id: str) -> None:
        super().__init__(timeout=_MENU_TTL_SECONDS)
        self.add_item(GuardedTicketSelect(pt, rows, owner_id, session_id))


async def _guarded_handle_panel_button(pt: Any, i: discord.Interaction) -> None:
    guild = i.guild
    member = _member_from_interaction(i)
    await pt._defer(i, True)
    if guild is None or member is None:
        return await pt._ephemeral(i, "❌ This must be used inside a server.")

    try:
        existing = await asyncio.wait_for(pt._existing_open(guild, member), timeout=6.0)
    except asyncio.TimeoutError:
        existing = None
    if existing:
        return await pt._ephemeral(i, f"You already have an open ticket: {existing.mention}")

    _parent, _staff, blockers, warnings = await _ticket_setup_preflight(pt, guild)
    if blockers:
        return await _reply_ticket_setup_blocked(pt, i, blockers, warnings)

    try:
        rows, warning = await asyncio.wait_for(pt._load_rows(guild), timeout=6.0)
    except asyncio.TimeoutError:
        rows, warning = [dict(x) for x in pt.DEFAULT_ROWS], "Ticket category loading timed out; using fallback categories."

    session_id = _new_session(guild.id, member.id)
    embed = discord.Embed(
        title="Create Ticket",
        description="Choose the type of ticket you want to open.",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Pick a category. You can review it before anything is created. Newest menu wins.")
    setup_notices = list(warnings)
    if warning:
        setup_notices.append(pt._short(warning, 900))
    if setup_notices:
        embed.add_field(name="Setup Notice", value=_short_list(setup_notices), inline=False)

    await pt._ephemeral(
        i,
        "Choose a ticket type.",
        embed=embed,
        view=GuardedTicketSelectView(pt, rows, member.id, session_id),
    )


def apply() -> bool:
    try:
        from ..commands_ext import public_ticket_panel_clean as pt
    except Exception as exc:
        _warn(f"could not import public_ticket_panel_clean: {exc!r}")
        return False

    if getattr(pt, "_PUBLIC_TICKET_CONFIRM_HARDENING_GUARD_APPLIED", False):
        return True

    try:
        pt._handle_panel_button = lambda i: _guarded_handle_panel_button(pt, i)
        pt._PUBLIC_TICKET_CONFIRM_HARDENING_GUARD_APPLIED = True
        _log("patched public ticket menu session, confirm hardening, form-safe modal response flow, and ticket setup preflight blockers")
        return True
    except Exception as exc:
        _warn(f"patch failed: {exc!r}")
        return False


apply()

__all__ = ["apply"]
