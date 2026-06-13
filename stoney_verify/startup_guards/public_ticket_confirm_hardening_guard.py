from __future__ import annotations

"""Harden the public Create Ticket -> Confirm flow.

The clean ticket panel already has DB/open-ticket checks. This guard protects the
member-facing interaction layer so old category menus cannot create tickets,
Confirm cannot double-submit, and the button disables immediately while the
create path runs.
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
    if warning:
        embed.add_field(name="Setup Notice", value=pt._short(warning, 900), inline=False)

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
        _log("patched public ticket menu session and confirm hardening")
        return True
    except Exception as exc:
        _warn(f"patch failed: {exc!r}")
        return False


apply()

__all__ = ["apply"]
