from __future__ import annotations

"""Shared Dank Shield Pro gate for premium feature activation points.

Phase 3 (Slice 0). The two established gate patterns — the design-studio
pilot (``commands_ext/public_design_studio_v2.py``) and the Phase 2 resolver
(``embed_studio_availability.py``) — repeat the same steps: grandfather
short-circuit, unconfigured short-circuit, status resolution, then the
shared guild action guard. New gates call :func:`premium_decision` instead
of copying those steps a third time; the existing patterns are intentionally
not migrated (no churn).

Semantics (the governing rule from Phases 1-2):

- ``grandfathered=True`` -> proceed (a guild already using the feature never
  reaches the gate).
- Membership unconfigured -> proceed; tiers do not exist yet, so gate code
  must not change any behavior.
- Configured + Pro -> proceed.
- Configured + free -> the guard's denied decision is returned and the
  caller renders :func:`pro_denial_embed` and makes no state change.
- Resolution failures fail open (proceed), matching the guard's own
  warning-only fallback for resolver errors: a gate must never break a
  feature because a status or context lookup hiccuped.
"""

import logging
from typing import TYPE_CHECKING, Any, Optional

import discord

from stoney_verify.services import membership

if TYPE_CHECKING:
    from stoney_verify.guild_action_guard import GuildActionDecision

logger = logging.getLogger(__name__)

PRO_PANEL_ROUTE = "`/dank home` → **Setup & Server Settings** → **Dank Shield Pro**"


async def premium_decision(
    *,
    guild_id: int,
    client: Any,
    action: str,
    premium_feature: str,
    grandfathered: bool = False,
) -> Optional["GuildActionDecision"]:
    """None = proceed. A denied decision = render the Pro denial and stop.

    - grandfathered=True short-circuits before any check (pilot semantics:
      a guild already using the feature never reaches the gate).
    - membership unconfigured -> None (governing rule).
    - otherwise resolve status and run the guard with feature="setup".
    """

    if grandfathered:
        return None
    if not membership.membership_configured():
        return None
    gid = int(guild_id or 0)
    try:
        status = await membership.guild_pro_status(gid, client=client)
    except Exception:
        logger.warning("premium status resolution failed for guild %s", gid, exc_info=True)
        return None
    if status == membership.STATUS_UNCONFIGURED:
        return None
    from stoney_verify.guild_action_guard import decide_guild_action
    from stoney_verify.guild_context import get_guild_context

    try:
        context = await get_guild_context(gid)
    except Exception:
        logger.warning("premium gate context resolution failed for guild %s", gid, exc_info=True)
        return None
    decision = decide_guild_action(
        context,
        action=str(action or "use this feature"),
        feature="setup",
        premium_feature=str(premium_feature or ""),
        pro_status_resolver=membership.pro_status_resolver(status),
    )
    return decision if decision.denied else None


def pro_denial_embed(
    premium_feature: str,
    decision: Optional["GuildActionDecision"] = None,
    *,
    detail: str = "",
) -> discord.Embed:
    """The pilot's gold Pro embed shape, with per-feature copy from the caller.

    ``detail`` replaces the middle paragraph (what the feature does / what
    stays free); the feature name line and the route to the Pro panel are
    standard. ``decision`` is accepted so call sites can pass the guard's
    outcome through; the standard copy is rendered regardless (the guard's
    own blocker text stays available to callers via ``decision``).
    """

    feature = str(premium_feature or "").strip()
    body = str(detail or "").strip() or (
        "Your server is not covered by a Pro account yet. Subscribe to "
        "Dank Shield Pro and link this server to use it."
    )
    description = (
        f"`{feature}` is a **Dank Shield Pro** feature.\n\n"
        f"{body}\n\n"
        f"Open {PRO_PANEL_ROUTE} to subscribe or link this server."
    )
    return discord.Embed(
        title="⭐ Dank Shield Pro",
        description=description,
        color=discord.Color.gold(),
    )


__all__ = [
    "PRO_PANEL_ROUTE",
    "premium_decision",
    "pro_denial_embed",
]
