from __future__ import annotations

"""Read-only server status for the staff `/dank home`.

Each source is loaded independently with a short time limit. A source that is
slow or broken shows as unknown instead of blocking or failing the home
screen. Nothing here writes config or changes Discord state.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, TypeVar

import discord

T = TypeVar("T")

HOME_STATUS_SOURCE_TIMEOUT_SECONDS = 1.8
HOME_ATTENTION_LIMIT = 4

PROTECTION_LABELS = {
    "off": "Off",
    "safe": "On · Safe",
    "strict": "On · Strict",
    "custom": "On · Custom",
}


@dataclass(frozen=True)
class AttentionItem:
    text: str
    feature_key: str
    button_label: str


@dataclass(frozen=True)
class HomeStatus:
    guild_name: str
    setup_done: Optional[bool] = None
    protection: Optional[str] = None
    open_tickets: Optional[int] = None
    pending_verification: Optional[int] = None

    @property
    def setup_label(self) -> str:
        if self.setup_done is None:
            return "Unknown"
        return "Done" if self.setup_done else "Not finished"

    @property
    def protection_label(self) -> str:
        return PROTECTION_LABELS.get(str(self.protection or ""), "Unknown")

    @property
    def tickets_label(self) -> str:
        return "Unknown" if self.open_tickets is None else str(self.open_tickets)

    @property
    def attention(self) -> list[AttentionItem]:
        items: list[AttentionItem] = []
        if self.setup_done is False:
            items.append(AttentionItem("Setup isn't finished yet.", "setup_center", "Finish setup"))
        if self.pending_verification:
            count = int(self.pending_verification)
            people = "person is" if count == 1 else "people are"
            items.append(
                AttentionItem(f"{count} {people} waiting to be verified.", "verification", "Review verifications")
            )
        if self.protection == "off":
            items.append(AttentionItem("Spam and raid protection is off.", "protection", "Turn on protection"))
        if self.open_tickets:
            count = int(self.open_tickets)
            items.append(
                AttentionItem(
                    f"{count} open ticket{'s' if count != 1 else ''}.",
                    "tickets",
                    "Open tickets",
                )
            )
        return items[:HOME_ATTENTION_LIMIT]

    @property
    def headline(self) -> str:
        count = len(self.attention)
        if count == 0:
            return "Everything looks good"
        return "1 thing needs you" if count == 1 else f"{count} things need you"


async def _bounded(loader: Callable[[], Awaitable[T]], timeout: float) -> Optional[T]:
    try:
        return await asyncio.wait_for(loader(), timeout=timeout)
    except Exception:
        return None


def _cfg_value(cfg: Any, key: str) -> Any:
    from .public_protection_center import _cfg_value as protection_cfg_value

    return protection_cfg_value(cfg, key, None)


def _truthy(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return None


def pending_verification_count(guild: discord.Guild, cfg: Any) -> Optional[int]:
    """Members currently holding this server's saved Unverified role."""

    try:
        role_id = int(_cfg_value(cfg, "unverified_role_id") or 0)
    except (TypeError, ValueError):
        return None
    if role_id <= 0:
        return None
    role = guild.get_role(role_id)
    if role is None:
        return None
    return sum(1 for member in role.members if not getattr(member, "bot", False))


async def load_home_status(
    guild: discord.Guild,
    *,
    timeout: float = HOME_STATUS_SOURCE_TIMEOUT_SECONDS,
) -> HomeStatus:
    async def load_config() -> Any:
        from stoney_verify.guild_config import get_guild_config

        return await get_guild_config(int(guild.id))

    async def load_protection_mode(cfg: Any) -> Optional[str]:
        from .public_protection_center import _load_spam_settings, _protection_state

        spam, source = await _load_spam_settings(int(guild.id))
        if str(source or "").startswith("unavailable:"):
            return None
        return _protection_state(cfg, spam)

    async def load_open_tickets() -> int:
        from stoney_verify.tickets_new.repository import list_open_tickets_for_guild

        return len(await list_open_tickets_for_guild(guild_id=int(guild.id)))

    cfg, open_tickets = await asyncio.gather(
        _bounded(load_config, timeout),
        _bounded(load_open_tickets, timeout),
    )
    protection = None
    setup_done = None
    pending = None
    if cfg is not None:
        protection = await _bounded(lambda: load_protection_mode(cfg), timeout)
        setup_done = _truthy(_cfg_value(cfg, "setup_completed"))
        try:
            pending = pending_verification_count(guild, cfg)
        except Exception:
            pending = None

    return HomeStatus(
        guild_name=str(getattr(guild, "name", "") or "This server"),
        setup_done=setup_done,
        protection=protection,
        open_tickets=open_tickets,
        pending_verification=pending,
    )


__all__ = [
    "AttentionItem",
    "HomeStatus",
    "PROTECTION_LABELS",
    "load_home_status",
    "pending_verification_count",
]
