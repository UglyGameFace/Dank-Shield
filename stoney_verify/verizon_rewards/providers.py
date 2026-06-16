from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


EMAIL_SEARCH_TERMS: tuple[str, ...] = (
    "Verizon",
    "Shine",
    "myAccess",
    "my access",
    "reward",
    "Daily Drop",
    "Epic Wins",
    "presale",
    "ticket",
    "gift card",
    "FIFA",
    "sweepstakes",
)


@dataclass(slots=True)
class RewardProviderMessage:
    source: str
    title: str
    body: str
    message_id: str = ""


class RewardProvider(Protocol):
    """Read-only provider interface for future Gmail/IMAP integrations."""

    async def search(self, guild_id: int, terms: tuple[str, ...] = EMAIL_SEARCH_TERMS) -> list[RewardProviderMessage]:
        """Return candidate reward messages.

        Implementations must be read-only and must never store Verizon passwords,
        claim rewards, bypass CAPTCHA, or interact with Verizon account pages.
        """
        ...


class EmailProviderNotConfigured:
    async def search(self, guild_id: int, terms: tuple[str, ...] = EMAIL_SEARCH_TERMS) -> list[RewardProviderMessage]:
        return []
