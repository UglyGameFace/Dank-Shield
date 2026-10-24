from __future__ import annotations

"""Bounded, privacy-safe Member Setup delivery dry run over the existing owners.

Never sends a DM, reserves a claim, scans the guild member list or starts a
worker. Only explicit opt-in candidates from the existing profile store are
examined. The member's canonical completion, consent, quiet hours, limits and
PostgreSQL claim HISTORY are freshly checked by action_delivery_readiness.
"""

import asyncio
from dataclasses import dataclass

import discord

from .action_delivery_readiness import inspect_member_setup_readiness
from .action_member_consent import read_member_setup_opt_in_page
from .member_setup_service import latest_revision, load_guild_setup_state

PAGE_SIZE = 5
SOURCE_TIMEOUT_SECONDS = 8.0
MEMBER_TIMEOUT_SECONDS = 8.0
MAX_PARALLEL_READS = 2

# Allowlist prevents introducing unexpected, untested statuses as "due".
_READINESS_STATES = frozenset({
    "preview_due", "waiting", "quiet", "exhausted", "capped", "blocked",
    "complete", "stale", "opted_out", "excluded", "exempt", "unavailable",
    "ledger_unavailable",
})


class CandidatePreviewUnavailable(RuntimeError):
    """Generic failure: do not expose backend secrets or recipient IDs."""


@dataclass(frozen=True)
class CandidatePreviewPage:
    guild_id: int
    revision: int
    scanned: int
    counts: tuple[tuple[str, int], ...]
    next_after_user_id: int | None = None
    source_status: str = "checked"

    def __post_init__(self) -> None:
        if self.scanned < 0 or self.scanned > PAGE_SIZE or self.guild_id <= 0:
            raise ValueError("Invalid bounded candidate preview")
        if self.source_status not in {"checked", "no_published_revision"}:
            raise ValueError("Unknown source status")
        if sum(count for _, count in self.counts) != self.scanned:
            raise ValueError("Preview tally does not match inspected candidates")
        if any(count < 0 or name not in _READINESS_STATES | {"not_cached", "bot"} for name, count in self.counts):
            raise ValueError("Invalid preview status")
        if self.next_after_user_id is not None and self.scanned != PAGE_SIZE:
            raise ValueError("Continuation requires a full bounded page")

    @property
    def potentially_due(self) -> int:
        return dict(self.counts).get("preview_due", 0)


async def preview_member_setup_opt_in_page(
    guild: discord.Guild, *, after_user_id: int = 0,
) -> CandidatePreviewPage:
    """One opt-in page, read-only owner checks, no Discord REST/member sweeps.

    A missing cached member is **unknown**, not eligible or departed. We
    deliberately do not fetch the member or query another guild. Each
    readiness check repeats consent and owner revision, and uses the real
    claim *snapshot*, not assumed-zero counters. No preview authorizes a DM.
    """
    guild_id = getattr(guild, "id", None)
    if type(guild_id) is not int or guild_id <= 0:
        raise ValueError("A valid guild is required")
    if type(after_user_id) is not int or after_user_id < 0:
        raise ValueError("Invalid candidate cursor")
    if not callable(getattr(guild, "get_member", None)):
        raise ValueError("Guild cache lookup is required")

    try:
        guild_state = await asyncio.wait_for(
            load_guild_setup_state(guild_id, refresh=True),
            timeout=SOURCE_TIMEOUT_SECONDS,
        )
        revision = guild_state.get("current_revision")
        publication = latest_revision(guild_state)
        if (
            not guild_state.get("enabled")
            or type(revision) is not int or revision <= 0
            or not publication or publication.get("revision") != revision
        ):
            return CandidatePreviewPage(
                guild_id, 0, 0, (), source_status="no_published_revision",
            )
        page = await asyncio.wait_for(
            read_member_setup_opt_in_page(
                guild_id, after_user_id=after_user_id, limit=PAGE_SIZE,
            ),
            timeout=SOURCE_TIMEOUT_SECONDS,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        raise CandidatePreviewUnavailable("Candidate source could not be verified") from None

    gate = asyncio.Semaphore(MAX_PARALLEL_READS)

    async def check(uid: int) -> str:
        member = guild.get_member(uid)  # Cache ONLY. Never fetch/sweep a guild.
        if member is None or not isinstance(member, discord.Member):
            return "not_cached"
        if member.guild.id != guild_id or int(member.id) != uid:
            return "not_cached"
        if member.bot:
            return "bot"
        async with gate:
            try:
                readiness = await asyncio.wait_for(
                    inspect_member_setup_readiness(
                        member, expected_revision=revision,
                    ),
                    timeout=MEMBER_TIMEOUT_SECONDS,
                )
                if (
                    not readiness.delivery_authorized
                    and readiness.status in _READINESS_STATES
                ):
                    return readiness.status
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        return "unavailable"

    # A bounded group of five, at most two concurrent owner/ledger readers.
    # No exception data or member IDs are retained in the returned result.
    statuses = await asyncio.gather(*(check(uid) for uid in page.user_ids))
    counts: dict[str, int] = {}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    return CandidatePreviewPage(
        guild_id=guild_id, revision=revision, scanned=len(page.user_ids),
        counts=tuple(sorted(counts.items())),
        next_after_user_id=page.next_after_user_id,
    )
