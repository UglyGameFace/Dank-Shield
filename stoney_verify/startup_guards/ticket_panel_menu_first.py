from __future__ import annotations

"""Runtime hotfix: public Create Ticket opens a menu before any form."""

import inspect
from typing import Any, Optional

import discord

_PATCHED = False


def _log(msg: str) -> None:
    try:
        print(f"🧭 ticket_panel_menu_first {msg}")
    except Exception:
        pass


def _warn(msg: str) -> None:
    try:
        print(f"⚠️ ticket_panel_menu_first {msg}")
    except Exception:
        pass


def _s(v: Any, default: str = "") -> str:
    try:
        text = str(v or "").strip()
        return text or default
    except Exception:
        return default


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(str(v or "").strip())
    except Exception:
        return default


def _slug(v: Any) -> str:
    text = _s(v, "support").lower()
    out: list[str] = []
    dash = False
    for ch in text:
        if ch.isalnum():
            out.append(ch)
            dash = False
        elif not dash:
            out.append("-")
            dash = True
    return ("".join(out).strip("-") or "support")[:80]


def _row_slug(row: dict[str, Any]) -> str:
    return _slug(row.get("slug") or row.get("category_slug") or row.get("name") or "support")


def _row_name(row: dict[str, Any]) -> str:
    return _s(row.get("button_label") or row.get("name") or row.get("display_name") or row.get("category_name") or _row_slug(row), "Support")[:100]


def _row_desc(row: dict[str, Any]) -> str:
    return _s(row.get("description") or row.get("intake_type") or _row_slug(row), "Open a ticket")[:100]


def _sort(row: dict[str, Any]) -> int:
    return _i(row.get("sort_order", row.get("position", 999)), 999)


async def _reply(interaction: discord.Interaction, content: str, *, embed: Optional[discord.Embed] = None, view: Optional[discord.ui.View] = None) -> None:
    try:
        kwargs: dict[str, Any] = {"ephemeral": True, "allowed_mentions": discord.AllowedMentions.none()}
        if embed is not None:
            kwargs["embed"] = embed
        else:
            kwargs["content"] = content
        if view is not None:
            kwargs["view"] = view
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
    except Exception:
        pass


async def _maybe(func: Any, *args: Any, **kwargs: Any) -> Any:
    result = func(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _rows(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        for item in raw or []:
            if isinstance(item, dict):
                out.append(dict(item))
    except Exception:
        pass
    seen: set[str] = set()
    final: list[dict[str, Any]] = []
    for row in sorted(out, key=lambda r: (_sort(r), _row_name(r).lower())):
        slug = _row_slug(row)
        if slug in seen:
            continue
        seen.add(slug)
        final.append(row)
    return final[:25]


async def _load_rows(panel: Any, guild: discord.Guild) -> list[dict[str, Any]]:
    for name in ("_fetch_dashboard_ticket_categories", "fetch_dashboard_ticket_categories", "_load_dashboard_ticket_categories"):
        func = getattr(panel, name, None)
        if not callable(func):
            continue
        for arg in (int(guild.id), guild):
            try:
                found = _rows(await _maybe(func, arg))
                if found:
                    return found
            except Exception:
                pass
    try:
        found = _rows(getattr(panel, "_DEFAULT_BOOTSTRAP_CATEGORIES", None))
        if found:
            return found
    except Exception:
        pass
    return [{"name": "Support", "slug": "support", "description": "General support request", "sort_order": 999}]


async def _existing(panel: Any, guild: discord.Guild, member: discord.Member) -> Optional[discord.TextChannel]:
    resolver = getattr(panel, "_resolve_existing_open_ticket_channel", None)
    if callable(resolver):
        try:
            channel = await resolver(guild=guild, owner_id=int(member.id))
            if isinstance(channel, discord.TextChannel):
                return channel
        except Exception:
            pass
    finder = getattr(panel, "find_open_ticket_for_owner", None)
    if callable(finder):
        try:
            row = await finder(guild_id=int(guild.id), owner_id=int(member.id), category=None)
            if isinstance(row, dict):
                cid = _i(row.get("discord_thread_id") or row.get("channel_id"), 0)
                if cid:
                    ch = guild.get_channel(cid)
                    if isinstance(ch, discord.TextChannel):
                        return ch
        except Exception:
            pass
    return None


def _extract_channel(result: Any, guild: discord.Guild) -> Optional[discord.TextChannel]:
    if isinstance(result, discord.TextChannel):
        return result
    if isinstance(result, dict):
        for key in ("channel", "ticket_channel"):
            if isinstance(result.get(key), discord.TextChannel):
                return result[key]
        cid = _i(result.get("channel_id") or result.get("discord_thread_id"), 0)
        if cid:
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.TextChannel):
                return ch
    if isinstance(result, (tuple, list)):
        for item in result:
            ch = _extract_channel(item, guild)
            if ch is not None:
                return ch
    return None


async def _defer(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


async def _create(panel: Any, interaction: discord.Interaction, row: dict[str, Any]) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _reply(interaction, "❌ Server only.")
    await _defer(interaction)
    existing = await _existing(panel, guild, member)
    if existing:
        return await _reply(interaction, f"You already have an open ticket: {existing.mention}")
    creator = getattr(panel, "create_ticket_channel", None)
    if not callable(creator):
        return await _reply(interaction, "❌ Ticket creator is unavailable. Restart the bot.")
    slug = _row_slug(row)
    name = _row_name(row)
    reason = f"{name} ticket opened from ticket menu."
    values = {
        "interaction": interaction,
        "guild": guild,
        "member": member,
        "owner": member,
        "user": member,
        "requester": member,
        "category": slug,
        "category_slug": slug,
        "category_name": name,
        "title": name,
        "reason": reason,
        "priority": "medium",
        "is_ghost": False,
    }
    try:
        sig = inspect.signature(creator)
        kwargs = {k: v for k, v in values.items() if k in sig.parameters and v is not None}
        result = await _maybe(creator, **kwargs) if kwargs else await _maybe(creator, guild, member, slug, reason)
    except TypeError:
        last: Optional[Exception] = None
        result = None
        for args, kwargs in (
            ((), {"guild": guild, "owner": member, "category": slug, "reason": reason}),
            ((), {"guild": guild, "member": member, "category": slug, "reason": reason}),
            ((guild, member, slug, reason), {}),
            ((member, slug, reason), {}),
        ):
            try:
                result = await _maybe(creator, *args, **kwargs)
                last = None
                break
            except Exception as e:
                last = e
        if last:
            return await _reply(interaction, f"❌ Ticket creation failed: `{type(last).__name__}: {str(last)[:250]}`")
    except Exception as e:
        return await _reply(interaction, f"❌ Ticket creation failed: `{type(e).__name__}: {str(e)[:250]}`")
    channel = _extract_channel(result, guild)
    await _reply(interaction, f"✅ Ticket created: {channel.mention}" if channel else "✅ Ticket created.")


class TicketTypeSelect(discord.ui.Select):
    def __init__(self, panel: Any, rows: list[dict[str, Any]]) -> None:
        self.panel = panel
        self.rows = rows
        options = [discord.SelectOption(label=_row_name(r), value=_row_slug(r), description=_row_desc(r), emoji="🎫") for r in rows[:25]]
        super().__init__(placeholder="Choose a ticket type", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        slug = _s(self.values[0], "support")
        row = next((r for r in self.rows if _row_slug(r) == slug), {"name": "Support", "slug": slug})
        await _create(self.panel, interaction, row)


class TicketTypeView(discord.ui.View):
    def __init__(self, panel: Any, rows: list[dict[str, Any]]) -> None:
        super().__init__(timeout=300)
        self.add_item(TicketTypeSelect(panel, rows))


async def _menu_first(panel: Any, original: Any, self_obj: Any, interaction: discord.Interaction, button: Any) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _reply(interaction, "❌ Server only.")
    try:
        check = getattr(panel, "_is_unverified_only_user", None)
        if callable(check) and check(member):
            return await original(self_obj, interaction, button)
    except Exception:
        pass
    existing = await _existing(panel, guild, member)
    if existing:
        return await _reply(interaction, f"You already have an open ticket: {existing.mention}")
    rows = await _load_rows(panel, guild)
    embed = discord.Embed(title="Create Ticket", description="Choose the type of ticket you want to open.", color=discord.Color.blurple())
    embed.set_footer(text="Pick a category. No form needed.")
    await _reply(interaction, "Choose a ticket type.", embed=embed, view=TicketTypeView(panel, rows))


def _copy_meta(src: Any, dst: Any) -> None:
    for attr in ("__discord_ui_model_type__", "__discord_ui_model_kwargs__"):
        if hasattr(src, attr):
            try:
                setattr(dst, attr, getattr(src, attr))
            except Exception:
                pass


def _looks_create(func: Any) -> bool:
    kwargs = getattr(func, "__discord_ui_model_kwargs__", {}) or {}
    text = f"{_s(kwargs.get('label')).lower()} {_s(kwargs.get('custom_id')).lower()}"
    return "create" in text and "ticket" in text


def install_ticket_panel_menu_first_patch() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.tickets_new import panel
    except Exception as e:
        _warn(f"panel import unavailable: {repr(e)}")
        return False
    patched = False
    for name in dir(panel):
        obj = getattr(panel, name, None)
        if not isinstance(obj, type):
            continue
        try:
            if not issubclass(obj, discord.ui.View):
                continue
        except Exception:
            continue
        if "TicketPanel" not in name and "PanelView" not in name:
            continue
        for attr_name, original in list(obj.__dict__.items()):
            if not callable(original) or not _looks_create(original):
                continue
            async def patched_callback(self_obj: Any, interaction: discord.Interaction, button: Any, _original=original) -> None:
                return await _menu_first(panel, _original, self_obj, interaction, button)
            _copy_meta(original, patched_callback)
            setattr(obj, attr_name, patched_callback)
            patched = True
            _log(f"patched {name}.{attr_name}")
    _PATCHED = patched
    if patched:
        _log("Create Ticket opens menu first")
    else:
        _warn("no Create Ticket button patched")
    return patched


install_ticket_panel_menu_first_patch()


__all__ = ["install_ticket_panel_menu_first_patch"]
