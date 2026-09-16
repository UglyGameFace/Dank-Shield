from __future__ import annotations

"""Dank Shield-owned browser for guild roles, channels, and categories.

Discord's native entity selectors are convenient, but they hand discovery and
rendering to the client. That makes large-server setup inconsistent and gives us
no deterministic paging, search, or empty-state behavior. This module keeps
resource discovery inside Dank Shield while leaving feature validation and
persistence with the feature that requested the selection.
"""

from dataclasses import dataclass
from math import ceil
from typing import Any, Awaitable, Callable, Iterable, Optional, Sequence

import discord

from .picker import DankChoice, DankPickerView

ResourcePickAction = Callable[[discord.Interaction, Any], Awaitable[None]]
ResourcePredicate = Callable[[Any], bool]

_PAGE_SIZE = 25
_ALLOWED_KINDS = frozenset({"role", "category", "text", "voice", "stage", "forum", "channel"})


@dataclass(frozen=True)
class DankResourceCandidate:
    resource_id: int
    resource_type: str
    label: str
    description: str
    emoji: str

    @property
    def value(self) -> str:
        return f"{self.resource_type}:{self.resource_id}"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _clean_query(value: Any) -> str:
    text = str(value or "").strip()
    if (text.startswith("<#") or text.startswith("<@&")) and text.endswith(">"):
        inner = text[2:-1] if text.startswith("<#") else text[3:-1]
        if inner.strip().isdigit():
            return inner.strip()
    return text.casefold()


def _channel_kind(channel: Any) -> str:
    ctype = getattr(channel, "type", None)
    if isinstance(channel, discord.CategoryChannel) or ctype == discord.ChannelType.category:
        return "category"
    if isinstance(channel, discord.TextChannel) or ctype in {discord.ChannelType.text, discord.ChannelType.news}:
        return "text"
    if isinstance(channel, discord.VoiceChannel) or ctype == discord.ChannelType.voice:
        return "voice"
    if getattr(discord.ChannelType, "stage_voice", None) is not None and ctype == discord.ChannelType.stage_voice:
        return "stage"
    if getattr(discord.ChannelType, "forum", None) is not None and ctype == discord.ChannelType.forum:
        return "forum"
    return "channel"


def _kind_allowed(kind: str, allowed: frozenset[str]) -> bool:
    if kind == "role":
        return "role" in allowed
    if "channel" in allowed:
        return True
    return kind in allowed


def _resource_id(resource: Any) -> int:
    return _safe_int(getattr(resource, "id", 0), 0)


def _resource_name(resource: Any) -> str:
    rid = _resource_id(resource)
    return str(getattr(resource, "name", "") or "").strip() or f"resource-{rid}"


def _resource_description(resource: Any, kind: str) -> str:
    if kind == "role":
        members = len(list(getattr(resource, "members", []) or []))
        suffix = " • managed" if bool(getattr(resource, "managed", False)) else ""
        return f"Role • {members} member{'s' if members != 1 else ''}{suffix}"

    if kind == "category":
        children = len(list(getattr(resource, "channels", []) or []))
        return f"Category • {children} child channel{'s' if children != 1 else ''}"

    labels = {
        "text": "Text channel",
        "voice": "Voice channel",
        "stage": "Stage channel",
        "forum": "Forum channel",
        "channel": "Server channel",
    }
    base = labels.get(kind, "Server channel")
    parent = getattr(resource, "category", None)
    parent_name = str(getattr(parent, "name", "") or "").strip()
    return f"{base} • {parent_name}" if parent_name else base


def _resource_emoji(kind: str) -> str:
    return {
        "role": "🎭",
        "category": "🗂️",
        "voice": "🔊",
        "stage": "🎙️",
        "forum": "💬",
        "text": "💬",
        "channel": "💬",
    }.get(kind, "🔎")


def _iter_resources(guild: discord.Guild, allowed: frozenset[str]) -> Iterable[tuple[str, Any]]:
    if "role" in allowed:
        for role in list(getattr(guild, "roles", []) or []):
            yield "role", role

    if any(kind != "role" for kind in allowed):
        for channel in list(getattr(guild, "channels", []) or []):
            kind = _channel_kind(channel)
            if _kind_allowed(kind, allowed):
                yield kind, channel


def build_resource_candidates(
    guild: discord.Guild,
    *,
    resource_kinds: Sequence[str],
    query: str = "",
    predicate: Optional[ResourcePredicate] = None,
) -> list[DankResourceCandidate]:
    """Build deterministic cache-backed candidates for a guild resource browser."""

    allowed = frozenset(str(kind or "").strip().lower() for kind in resource_kinds)
    allowed = frozenset(kind for kind in allowed if kind in _ALLOWED_KINDS)
    if not allowed:
        return []

    needle = _clean_query(query)
    out: list[DankResourceCandidate] = []
    seen: set[tuple[str, int]] = set()

    for kind, resource in _iter_resources(guild, allowed):
        rid = _resource_id(resource)
        if rid <= 0:
            continue
        identity = (kind, rid)
        if identity in seen:
            continue
        seen.add(identity)

        if predicate is not None:
            try:
                if not bool(predicate(resource)):
                    continue
            except Exception:
                continue

        name = _resource_name(resource)
        mention = str(getattr(resource, "mention", "") or "")
        if needle and needle not in name.casefold() and needle not in str(rid) and needle not in mention.casefold():
            continue

        out.append(
            DankResourceCandidate(
                resource_id=rid,
                resource_type=kind,
                label=name,
                description=_resource_description(resource, kind),
                emoji=_resource_emoji(kind),
            )
        )

    return out


def _candidate_choice(candidate: DankResourceCandidate) -> DankChoice:
    return DankChoice(
        label=_clip(candidate.label, 100),
        value=candidate.value,
        description=_clip(candidate.description, 100),
        emoji=candidate.emoji,
    )


async def _safe_ephemeral(interaction: discord.Interaction, message: str) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                message,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.followup.send(
                message,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception:
        pass


class _PageButton(discord.ui.Button):
    def __init__(self, owner: "DankGuildResourceBrowserView", *, delta: int, label: str, emoji: str, disabled: bool) -> None:
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, row=2, disabled=disabled)
        self.owner_view = owner
        self.delta = int(delta)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.owner_view.turn_page(interaction, self.delta)


class _SearchButton(discord.ui.Button):
    def __init__(self, owner: "DankGuildResourceBrowserView") -> None:
        super().__init__(label="Search", emoji="🔎", style=discord.ButtonStyle.primary, row=2)
        self.owner_view = owner

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(getattr(interaction.user, "id", 0) or 0) != self.owner_view.author_id:
            return await _safe_ephemeral(interaction, "❌ This resource browser belongs to another admin.")
        await interaction.response.send_modal(
            DankResourceSearchModal(
                browser=self.owner_view,
                current_query=self.owner_view.query,
            )
        )


class _ClearSearchButton(discord.ui.Button):
    def __init__(self, owner: "DankGuildResourceBrowserView") -> None:
        super().__init__(label="Clear Search", emoji="🧹", style=discord.ButtonStyle.secondary, row=2)
        self.owner_view = owner

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.owner_view.show(interaction, query="", page=0)


class DankResourceSearchModal(discord.ui.Modal, title="Search Server Items"):
    query = discord.ui.TextInput(
        label="Name, Discord ID, or mention",
        placeholder="Example: staff, modlog, 123456789…",
        required=False,
        max_length=100,
    )

    def __init__(self, *, browser: "DankGuildResourceBrowserView", current_query: str = "") -> None:
        super().__init__()
        self.browser = browser
        if current_query:
            self.query.default = current_query[:100]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(getattr(interaction.user, "id", 0) or 0) != self.browser.author_id:
            return await _safe_ephemeral(interaction, "❌ This resource browser belongs to another admin.")
        view = self.browser.clone(query=str(self.query.value or ""), page=0)
        await interaction.response.send_message(
            embed=view.embed(),
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class DankGuildResourceBrowserView(DankPickerView):
    """Paged, searchable, owner-locked browser for cached guild resources."""

    def __init__(
        self,
        *,
        guild: discord.Guild,
        author_id: int,
        resource_kinds: Sequence[str],
        on_pick: ResourcePickAction,
        custom_id: str,
        title: str = "Dank Shield Server Item Browser",
        placeholder: str = "Choose a server item…",
        query: str = "",
        page: int = 0,
        predicate: Optional[ResourcePredicate] = None,
        on_home: Optional[Callable[[discord.Interaction], Awaitable[None]]] = None,
        home_label: str = "Back",
        cancel_label: str = "Close",
        include_cancel: bool = True,
        empty_message: str = "No matching server items are available.",
    ) -> None:
        self.guild = guild
        self.author_id = int(author_id or 0)
        self.resource_kinds = tuple(str(kind or "").strip().lower() for kind in resource_kinds)
        self.resource_pick = on_pick
        self.resource_custom_id = str(custom_id or "dank:resource")[:80]
        self.browser_title = str(title or "Dank Shield Server Item Browser")[:256]
        self.browser_placeholder = str(placeholder or "Choose a server item…")[:150]
        self.query = str(query or "").strip()
        self.predicate = predicate
        self.browser_home = on_home
        self.browser_home_label = home_label
        self.browser_cancel_label = cancel_label
        self.browser_include_cancel = bool(include_cancel)
        self.empty_message = str(empty_message or "No matching server items are available.")[:1000]

        self.candidates = build_resource_candidates(
            guild,
            resource_kinds=self.resource_kinds,
            query=self.query,
            predicate=predicate,
        )
        self.page_count = max(1, int(ceil(len(self.candidates) / _PAGE_SIZE)))
        self.page = max(0, min(int(page), self.page_count - 1))
        start = self.page * _PAGE_SIZE
        page_items = self.candidates[start : start + _PAGE_SIZE]

        async def handle_pick(interaction: discord.Interaction, value: str) -> None:
            await self.pick_resource(interaction, value)

        super().__init__(
            author_id=self.author_id,
            choices=[_candidate_choice(item) for item in page_items],
            on_pick=handle_pick,
            custom_id=f"{self.resource_custom_id}:pick"[:100],
            placeholder=(f"Search: {self.query}" if self.query else self.browser_placeholder)[:150],
            timeout=900,
            title=self.browser_title,
            home_label=home_label,
            cancel_label=cancel_label,
            include_cancel=include_cancel,
            on_home=on_home,
        )

        self.add_item(_PageButton(self, delta=-1, label="Previous", emoji="⬅️", disabled=self.page <= 0))
        self.add_item(_PageButton(self, delta=1, label="Next", emoji="➡️", disabled=self.page >= self.page_count - 1))
        self.add_item(_SearchButton(self))
        if self.query:
            self.add_item(_ClearSearchButton(self))

    def clone(self, *, query: Optional[str] = None, page: Optional[int] = None) -> "DankGuildResourceBrowserView":
        return DankGuildResourceBrowserView(
            guild=self.guild,
            author_id=self.author_id,
            resource_kinds=self.resource_kinds,
            on_pick=self.resource_pick,
            custom_id=self.resource_custom_id,
            title=self.browser_title,
            placeholder=self.browser_placeholder,
            query=self.query if query is None else query,
            page=self.page if page is None else page,
            predicate=self.predicate,
            on_home=self.browser_home,
            home_label=self.browser_home_label,
            cancel_label=self.browser_cancel_label,
            include_cancel=self.browser_include_cancel,
            empty_message=self.empty_message,
        )

    def embed(self) -> discord.Embed:
        total = len(self.candidates)
        intro = (
            f"Found **{total}** matching server item{'s' if total != 1 else ''}."
            if self.query
            else f"Browsing **{total}** server item{'s' if total != 1 else ''}."
        )
        embed = discord.Embed(
            title=self.browser_title,
            description=(
                intro
                + "\n\nDank Shield is listing the server cache directly, so this browser can page and search instead of relying on Discord's generic entity picker."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Page", value=f"{self.page + 1} / {self.page_count}", inline=True)
        if self.query:
            embed.add_field(name="Search", value=f"`{_clip(self.query, 80)}`", inline=True)
        if total == 0:
            embed.add_field(name="No matches", value=self.empty_message, inline=False)
        return embed

    def _resolve_value(self, value: str) -> Any:
        try:
            resource_type, raw_id = str(value or "").split(":", 1)
        except ValueError:
            return None
        rid = _safe_int(raw_id, 0)
        if rid <= 0:
            return None
        if resource_type == "role":
            resource = self.guild.get_role(rid)
        else:
            resource = self.guild.get_channel(rid)
        if resource is None:
            return None
        kind = "role" if resource_type == "role" else _channel_kind(resource)
        allowed = frozenset(kind_name for kind_name in self.resource_kinds if kind_name in _ALLOWED_KINDS)
        if not _kind_allowed(kind, allowed):
            return None
        if self.predicate is not None:
            try:
                if not bool(self.predicate(resource)):
                    return None
            except Exception:
                return None
        return resource

    async def pick_resource(self, interaction: discord.Interaction, value: str) -> None:
        if int(getattr(interaction.user, "id", 0) or 0) != self.author_id:
            return await _safe_ephemeral(interaction, "❌ This resource browser belongs to another admin.")
        resource = self._resolve_value(value)
        if resource is None:
            return await _safe_ephemeral(
                interaction,
                "❌ That server item no longer exists or no longer matches this picker. Refresh and try again.",
            )
        await self.resource_pick(interaction, resource)

    async def show(self, interaction: discord.Interaction, *, query: Optional[str] = None, page: Optional[int] = None) -> None:
        if int(getattr(interaction.user, "id", 0) or 0) != self.author_id:
            return await _safe_ephemeral(interaction, "❌ This resource browser belongs to another admin.")
        view = self.clone(query=query, page=page)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    async def turn_page(self, interaction: discord.Interaction, delta: int) -> None:
        await self.show(interaction, page=self.page + int(delta))

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        _ = item
        try:
            print(f"[resource_browser] callback failed: {type(error).__name__}: {error}")
        except Exception:
            pass
        await _safe_ephemeral(
            interaction,
            "⚠️ Dank Shield could not finish that server-item action. Nothing was changed. Reopen the picker and try again.",
        )


__all__ = [
    "DankGuildResourceBrowserView",
    "DankResourceCandidate",
    "DankResourceSearchModal",
    "ResourcePickAction",
    "ResourcePredicate",
    "build_resource_candidates",
]
