from __future__ import annotations

"""Consolidated public ticket panel compatibility layer.

This is the only public /ticket-panel loader used by commands_ext.
It keeps the existing clean Discord UI implementation, then applies the fixes
that must happen before registration:

- the fallback DB insert includes tickets.title and tickets.username
- generated columns like tickets.id are not used in the fallback insert
- the public Create Ticket button acknowledges immediately, so Supabase slowness
  cannot cause a silent Discord "interaction failed"
- category select callbacks are wrapped with visible error replies + logs

No modal-first ticket flow is registered here.
"""

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import discord

_FIXED_TICKET_REQUIRED_COLUMNS: Tuple[str, ...] = (
    "guild_id",
    "user_id",
    "username",
    "title",
    "status",
    "category",
    "channel_id",
    "discord_thread_id",
    "ticket_number",
    "created_at",
    "updated_at",
)

_APPLIED = False


def _log(msg: str) -> None:
    try:
        print(f"✅ public_ticket_panel_clean_v2: {msg}")
    except Exception:
        pass


def _warn(msg: str) -> None:
    try:
        print(f"⚠️ public_ticket_panel_clean_v2: {msg}")
    except Exception:
        pass


def _short(value: Any, limit: int = 220) -> str:
    try:
        text = str(value or "").strip()
    except Exception:
        text = ""
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


async def _safe_error_reply(clean: Any, interaction: discord.Interaction, message: str) -> None:
    try:
        await clean._ephemeral(interaction, message)
    except Exception as e:
        _warn(f"failed to send interaction error reply guild={getattr(getattr(interaction, 'guild', None), 'id', None)} user={getattr(getattr(interaction, 'user', None), 'id', None)} error={type(e).__name__}: {_short(e)}")


def _apply_clean_panel_contract() -> Any:
    global _APPLIED

    from . import public_ticket_panel_clean as clean

    clean.TICKET_REQUIRED_COLUMNS = _FIXED_TICKET_REQUIRED_COLUMNS

    if _APPLIED:
        return clean

    original_defer = clean._defer
    original_ephemeral = clean._ephemeral
    original_create_ticket = clean._create_ticket

    async def logged_defer(interaction: discord.Interaction, thinking: bool = False) -> None:
        try:
            await original_defer(interaction, thinking)
        except Exception as e:
            _warn(f"defer failed guild={getattr(getattr(interaction, 'guild', None), 'id', None)} user={getattr(getattr(interaction, 'user', None), 'id', None)} done={getattr(getattr(interaction, 'response', None), 'is_done', lambda: 'unknown')()} error={type(e).__name__}: {_short(e)}")

    async def logged_ephemeral(
        interaction: discord.Interaction,
        content: str,
        *,
        embed: Optional[discord.Embed] = None,
        view: Optional[discord.ui.View] = None,
    ) -> None:
        try:
            await original_ephemeral(interaction, content, embed=embed, view=view)
        except Exception as e:
            _warn(f"ephemeral reply failed guild={getattr(getattr(interaction, 'guild', None), 'id', None)} user={getattr(getattr(interaction, 'user', None), 'id', None)} content={_short(content, 80)!r} error={type(e).__name__}: {_short(e)}")

    async def safe_create_ticket(interaction: discord.Interaction, row: Dict[str, Any]) -> None:
        try:
            await original_create_ticket(interaction, row)
        except Exception as e:
            guild_id = getattr(getattr(interaction, "guild", None), "id", None)
            user_id = getattr(getattr(interaction, "user", None), "id", None)
            _warn(f"create ticket callback crashed guild={guild_id} user={user_id} row={_short(row, 160)} error={type(e).__name__}: {_short(e)}")
            await _safe_error_reply(clean, interaction, f"❌ Ticket creation hit an internal error: `{type(e).__name__}: {_short(e, 160)}`")

    async def safe_select_callback(self: Any, interaction: discord.Interaction) -> None:
        try:
            slug = clean._safe_str(self.values[0], "support")
            row = next((r for r in self.rows if clean._row_slug(r) == slug), {"slug": slug, "name": "Support"})
            await clean._create_ticket(interaction, row)
        except Exception as e:
            guild_id = getattr(getattr(interaction, "guild", None), "id", None)
            user_id = getattr(getattr(interaction, "user", None), "id", None)
            _warn(f"ticket type select crashed guild={guild_id} user={user_id} values={getattr(self, 'values', None)} error={type(e).__name__}: {_short(e)}")
            await _safe_error_reply(clean, interaction, f"❌ Ticket category selection failed: `{type(e).__name__}: {_short(e, 160)}`")

    class SafeTicketSelectView(discord.ui.View):
        def __init__(self, rows: List[Dict[str, Any]]) -> None:
            super().__init__(timeout=300)
            self.add_item(clean.TicketSelect(rows))

        async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
            _warn(f"ticket select view error guild={getattr(getattr(interaction, 'guild', None), 'id', None)} user={getattr(getattr(interaction, 'user', None), 'id', None)} item={type(item).__name__} error={type(error).__name__}: {_short(error)}")
            await _safe_error_reply(clean, interaction, f"❌ Ticket menu failed: `{type(error).__name__}: {_short(error, 160)}`")

    class SafePublicCreateTicketPanelView(discord.ui.View):
        def __init__(self) -> None:
            super().__init__(timeout=None)

        @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.green, emoji="🎫", custom_id=clean.PANEL_BUTTON_CUSTOM_ID)
        async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            _ = button
            guild = interaction.guild
            member = interaction.user if isinstance(interaction.user, discord.Member) else None

            # ACK immediately. This is the important fix for silent "interaction failed"
            # when Supabase/guild-config reads are slow after a close/archive event.
            await clean._defer(interaction, True)

            try:
                if guild is None or member is None:
                    return await clean._ephemeral(interaction, "❌ This must be used inside a server.")

                try:
                    from ..startup_guards.unverified_ticket_panel_flow import _handle_unverified_panel_click
                    if await asyncio.wait_for(_handle_unverified_panel_click(interaction), timeout=2.5):
                        return
                except asyncio.TimeoutError:
                    _warn(f"unverified panel route timed out guild={getattr(guild, 'id', None)} user={getattr(member, 'id', None)}; continuing normal ticket flow")
                except Exception as e:
                    _warn(f"unverified panel route skipped guild={getattr(guild, 'id', None)} user={getattr(member, 'id', None)} error={type(e).__name__}: {_short(e)}")

                try:
                    existing = await asyncio.wait_for(clean._existing_open(guild, member), timeout=6.0)
                except asyncio.TimeoutError:
                    _warn(f"existing-open check timed out guild={guild.id} user={member.id}; continuing so panel does not fail silently")
                    existing = None

                if existing:
                    return await clean._ephemeral(interaction, f"You already have an open ticket: {existing.mention}")

                try:
                    rows, warning = await asyncio.wait_for(clean._load_rows(guild), timeout=6.0)
                except asyncio.TimeoutError:
                    _warn(f"ticket category load timed out guild={guild.id}; using fallback categories")
                    rows, warning = list(clean.DEFAULT_ROWS), "Ticket category loading timed out; using fallback categories."

                embed = discord.Embed(
                    title="Create Ticket",
                    description="Choose the type of ticket you want to open.",
                    color=discord.Color.blurple(),
                )
                embed.set_footer(text="Pick a category. No form needed.")
                if warning:
                    embed.add_field(name="Setup Notice", value=clean._short(warning, 900), inline=False)

                await clean._ephemeral(interaction, "Choose a ticket type.", embed=embed, view=clean.TicketSelectView(rows))
            except Exception as e:
                _warn(f"public create ticket button crashed guild={getattr(guild, 'id', None)} user={getattr(member, 'id', None)} error={type(e).__name__}: {_short(e)}")
                await _safe_error_reply(clean, interaction, f"❌ Ticket panel failed: `{type(e).__name__}: {_short(e, 160)}`")

        async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
            _warn(f"public ticket panel view error guild={getattr(getattr(interaction, 'guild', None), 'id', None)} user={getattr(getattr(interaction, 'user', None), 'id', None)} item={type(item).__name__} error={type(error).__name__}: {_short(error)}")
            await _safe_error_reply(clean, interaction, f"❌ Ticket panel failed: `{type(error).__name__}: {_short(error, 160)}`")

    clean._defer = logged_defer
    clean._ephemeral = logged_ephemeral
    clean._create_ticket = safe_create_ticket
    clean.TicketSelect.callback = safe_select_callback
    clean.TicketSelectView = SafeTicketSelectView
    clean.PublicCreateTicketPanelView = SafePublicCreateTicketPanelView

    _APPLIED = True
    _log("applied DB fallback, immediate ACK, timeout, and interaction logging patches")
    return clean


def register_public_ticket_panel_clean(bot: Any, tree: Any) -> None:
    clean = _apply_clean_panel_contract()
    clean.register_public_ticket_panel_clean(bot, tree)


def __getattr__(name: str) -> Any:
    clean = _apply_clean_panel_contract()
    return getattr(clean, name)


__all__ = ["register_public_ticket_panel_clean"]
