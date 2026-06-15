from __future__ import annotations

"""Central interaction response guard for Dank Shield.

Discord interactions have one tight rule: acknowledge fast, then send exactly
one clean user-visible outcome. Public-production commands should not each
rebuild their own defer/follow-up error handling.

This module is intentionally small and explicit:
- no monkey patches
- no command tree mutation
- no Discord channel/role/config/database mutation
- no premium decisions yet, but the result shape is compatible with future
  entitlement and feature-gate checks
"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, TypeVar

import discord

T = TypeVar("T")


@dataclass(frozen=True)
class InteractionGuardResult:
    ok: bool
    error_type: str = ""
    error_message: str = ""


def _safe_error_text(error: BaseException, *, limit: int = 300) -> str:
    try:
        text = str(error or "").strip() or repr(error)
    except Exception:
        text = repr(error)
    return text[: max(0, int(limit))]


async def safe_defer_interaction(interaction: discord.Interaction, *, ephemeral: bool = True) -> bool:
    """Acknowledge an interaction once without raising into command handlers."""

    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
            return True
    except Exception:
        return False
    return False


async def safe_send_interaction(
    interaction: discord.Interaction,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    ephemeral: bool = True,
    allowed_mentions: Optional[discord.AllowedMentions] = None,
    **kwargs: Any,
) -> bool:
    """Send a response/follow-up once without leaking Discord response errors."""

    payload: dict[str, Any] = dict(kwargs)
    if content is not None:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    payload.setdefault("ephemeral", ephemeral)
    payload.setdefault("allowed_mentions", allowed_mentions or discord.AllowedMentions.none())

    try:
        if interaction.response.is_done():
            await interaction.followup.send(**payload)
        else:
            await interaction.response.send_message(**payload)
        return True
    except Exception:
        try:
            await interaction.followup.send(**payload)
            return True
        except Exception:
            return False


async def safe_send_error(
    interaction: discord.Interaction,
    error: BaseException,
    *,
    title: str = "❌ Command failed safely",
    guidance: str = "Nothing was changed. Try again, then check `/dank diagnostics` if it keeps happening.",
    ephemeral: bool = True,
) -> bool:
    """Send a clear, non-generic failure message for command exceptions."""

    embed = discord.Embed(
        title=title,
        description=(
            f"`{type(error).__name__}: {_safe_error_text(error)}`\n\n"
            f"{guidance}"
        ),
        color=discord.Color.red(),
    )
    return await safe_send_interaction(interaction, embed=embed, ephemeral=ephemeral)


async def run_guarded_interaction(
    interaction: discord.Interaction,
    action: Callable[[], Awaitable[T]],
    *,
    defer: bool = True,
    ephemeral: bool = True,
    error_title: str = "❌ Command failed safely",
    error_guidance: str = "Nothing was changed. Try again, then check `/dank diagnostics` if it keeps happening.",
) -> InteractionGuardResult:
    """Run one command body behind a consistent defer/error wrapper."""

    if defer:
        await safe_defer_interaction(interaction, ephemeral=ephemeral)

    try:
        await action()
        return InteractionGuardResult(ok=True)
    except Exception as e:
        await safe_send_error(
            interaction,
            e,
            title=error_title,
            guidance=error_guidance,
            ephemeral=ephemeral,
        )
        return InteractionGuardResult(
            ok=False,
            error_type=type(e).__name__,
            error_message=_safe_error_text(e),
        )


__all__ = [
    "InteractionGuardResult",
    "run_guarded_interaction",
    "safe_defer_interaction",
    "safe_send_error",
    "safe_send_interaction",
]
