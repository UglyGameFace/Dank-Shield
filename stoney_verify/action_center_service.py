from __future__ import annotations

"""Action & Reminder Center slice 1: read-only authority-driven member inbox.

This feature coordinates existing owners. It never stores verification truth,
updates setup completion, claims reminders, sends messages, or starts a worker.
Future scheduling must be designed against the canonical source/delivery owner
instead of spawning another reminder ledger.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping

import discord

from .guild_config import get_guild_config
from .member_setup_service import SECTION_LABELS
from .role_truth import member_is_pending_verification
from .verification_reminders import parse_policy

MAX_ACTIONS = 8
SOURCE_TIMEOUT_SECONDS = 8.0
ACTION_ROUTES = frozenset({"verification", "member_setup"})
SEVERITIES = frozenset({"required", "recommended", "announcement"})


@dataclass(frozen=True)
class ActionItem:
    key: str
    title: str
    description: str
    severity: str
    owner: str
    route: str
    target: str = "member"
    condition: str = "pending"
    schedule: str = "on_open"
    delivery: str = "private_panel"

    def __post_init__(self) -> None:
        if self.route not in ACTION_ROUTES or self.severity not in SEVERITIES:
            raise ValueError("Action must have a canonical route and severity")
        if self.target != "member" or self.condition != "pending":
            raise ValueError("Action Center may not invent member or completion truth")
        if self.delivery != "private_panel" or self.schedule != "on_open":
            raise ValueError("Foundation is on-demand, not a notification worker")
        if not self.key or len(self.title) > 90 or len(self.description) > 480:
            raise ValueError("Invalid action text")


@dataclass(frozen=True)
class ActionSnapshot:
    guild_id: int
    user_id: int
    items: tuple[ActionItem, ...]
    checked_sources: tuple[str, ...]
    unavailable_sources: tuple[str, ...]
    verification_reminders_enabled: bool = False

    @property
    def is_clear(self) -> bool:
        # Never tell members they're all caught up if a source could not load.
        return not self.items and not self.unavailable_sources and len(self.checked_sources) == 2


def _safe_id(value: Any) -> int:
    try:
        return max(0, int(value))
    except (ValueError, TypeError, OverflowError):
        return 0


def verification_action(*, member_pending: bool, cfg: Mapping[str, Any]) -> ActionItem | None:
    if not member_pending:
        return None
    return ActionItem(
        key="verification:pending",
        title="Complete verification",
        description="Your server's verification role is still pending. Open the configured verification channel or ask staff for access.",
        severity="required",
        owner="role_truth",
        route="verification",
    )


def setup_action(status: Mapping[str, Any]) -> ActionItem | None:
    if not status.get("enabled") or status.get("is_current") or not status.get("pending_sections"):
        return None
    parts = [
        SECTION_LABELS.get(str(x), str(x).title())
        for x in status.get("pending_sections", ())
    ][:4]
    priority = "required" if status.get("access_gated") or str(status.get("severity")) in (
        "required", "access_gated"
    ) else "recommended"
    revision = _safe_id(status.get("current_revision"))
    return ActionItem(
        key=f"member_setup:revision:{revision}",
        title="Review Member Setup",
        description=(
            f"Revision {revision} needs a review of: {', '.join(parts)}. "
            "Your saved choices remain intact until you confirm them."
        ),
        severity=priority,
        owner="member_setup",
        route="member_setup",
    )


async def _read_verification(member: discord.Member) -> tuple[ActionItem | None, bool]:
    cfg = await get_guild_config(member.guild.id, refresh=True)
    if not isinstance(cfg, Mapping) or str(cfg.get("source", "")).startswith("unavailable:"):
        raise RuntimeError("Authoritative guild configuration unavailable")
    has_mapping = all(_safe_id(cfg.get(key)) > 0 for key in (
        "unverified_role_id", "verified_role_id",
    ))
    if not has_mapping:
        # A server without both mapped verification roles isn't known pending.
        return None, False
    # role_truth is the canonical verifier. Its synchronous cache/lookup path
    # must never block the gateway/event loop while rendering this inbox.
    pending = await asyncio.to_thread(member_is_pending_verification, member)
    return verification_action(member_pending=pending, cfg=cfg), parse_policy(cfg).enabled


async def _read_member_setup(member: discord.Member) -> ActionItem | None:
    # Reuse the real member-setup owner's grandfathering, exemption, revision
    # and access-gate rules instead of reconstructing them in this module.
    from .commands_ext.public_member_setup import _member_payload
    _guild_state, _effective, status = await _member_payload(member)
    return setup_action(status)


async def load_member_actions(member: discord.Member) -> ActionSnapshot:
    if not isinstance(member, discord.Member):
        raise ValueError("A server member is required")
    tasks = await asyncio.gather(
        asyncio.wait_for(_read_verification(member), timeout=SOURCE_TIMEOUT_SECONDS),
        asyncio.wait_for(_read_member_setup(member), timeout=SOURCE_TIMEOUT_SECONDS),
        return_exceptions=True,
    )
    actions: list[ActionItem] = []
    checked: list[str] = []
    unavailable: list[str] = []
    reminders_enabled = False

    for name, value in zip(("verification", "member_setup"), tasks):
        if isinstance(value, BaseException):
            if isinstance(value, asyncio.CancelledError):
                raise value
            # Do not leak database messages or other members' data into Discord.
            unavailable.append(name)
            continue
        checked.append(name)
        if name == "verification":
            item, reminders_enabled = value
        else:
            item = value
        if item is not None:
            actions.append(item)
    order = {"required": 0, "recommended": 1, "announcement": 2}
    actions.sort(key=lambda item: (order[item.severity], item.key))
    return ActionSnapshot(
        guild_id=int(member.guild.id),
        user_id=int(member.id),
        items=tuple(actions[:MAX_ACTIONS]),
        checked_sources=tuple(checked),
        unavailable_sources=tuple(unavailable),
        verification_reminders_enabled=reminders_enabled,
    )
