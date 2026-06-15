from __future__ import annotations

"""Central mutation gate for Dank Shield guild actions.

Dank Shield can now resolve a centralized per-guild runtime context. This module
turns that context into a consistent allow/refuse decision before commands or
buttons create channels, move roles, post panels, update config, or perform
future premium-gated actions.

Design rules:
- no monkey patches
- no Discord mutation
- no database mutation
- no premium billing decision yet
- unsafe public guilds must refuse action instead of guessing IDs
"""

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .guild_context import GuildContext


@dataclass(frozen=True)
class GuildActionDecision:
    allowed: bool
    reason: str
    blockers: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    premium_feature: str = ""

    @property
    def denied(self) -> bool:
        return not self.allowed

    def user_message(self) -> str:
        if self.allowed:
            if self.warnings:
                return "✅ This action is allowed, but review: " + "; ".join(self.warnings)
            return "✅ This action is allowed."
        details = "; ".join(self.blockers) if self.blockers else self.reason
        return f"❌ {self.reason}\n{details}".strip()


def _clean_keys(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _missing_for_feature(context: GuildContext, feature: str) -> tuple[str, ...]:
    selected = str(feature or "").strip().lower()
    if selected in {"ticket", "tickets", "ticketing", "ticket_panel", "ticket-panel"}:
        return context.missing_ticket_keys
    if selected in {"verify", "verification", "vc_verify", "vc-verification"}:
        return context.missing_verify_keys
    if selected in {"log", "logs", "logging", "modlog"}:
        return context.missing_log_keys
    if selected in {"setup", "guild_setup", "server_setup", "protection", "automod", "generic"}:
        return tuple()
    return tuple()


def decide_guild_action(
    context: GuildContext,
    *,
    action: str,
    feature: str = "generic",
    required_keys: Sequence[str] = (),
    premium_feature: str = "",
) -> GuildActionDecision:
    """Return a consistent allow/refuse decision for one guild action."""

    action_name = str(action or "guild action").strip() or "guild action"
    blockers: list[str] = []
    warnings: list[str] = []

    if context.unsafe_to_act:
        blockers.append(
            "This server is not safely configured yet. Run `/dank setup` or `/dank diagnostics` first."
        )

    missing = list(_missing_for_feature(context, feature))
    for key in required_keys:
        text = str(key or "").strip()
        if text and context.get_id(text, 0) <= 0:
            missing.append(text)

    clean_missing = _clean_keys(missing)
    if clean_missing:
        blockers.append("Missing required config: " + ", ".join(clean_missing))

    if premium_feature:
        warnings.append(f"Premium gate pending for `{premium_feature}`. Current build treats this as an audit-only warning.")

    if blockers:
        return GuildActionDecision(
            allowed=False,
            reason=f"Cannot run {action_name} safely.",
            blockers=tuple(blockers),
            warnings=tuple(warnings),
            premium_feature=str(premium_feature or ""),
        )

    return GuildActionDecision(
        allowed=True,
        reason=f"Can run {action_name} safely.",
        blockers=tuple(),
        warnings=tuple(warnings),
        premium_feature=str(premium_feature or ""),
    )


__all__ = ["GuildActionDecision", "decide_guild_action"]
