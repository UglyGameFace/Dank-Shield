from __future__ import annotations

"""4D.5 runtime diagnostics for a one-member Action Center canary.

Read-only, owner-only caller. Never expose connection credentials, canary
identifiers from environment, profile data or error payloads. Runtime build
proof is OBSERVATION, not an attestation of matching GitHub main.
"""

import asyncio
import os
import re
from dataclasses import dataclass

from .action_canary_audit import audit_ready_sync
from .action_canary_delivery import ENABLE_ENV, GUILD_ENV, USER_ENV, REVISION_ENV
from .action_center_postgres import ledger_ready_sync
from .runtime_release import runtime_release_snapshot
from .verification_reminder_postgres import DATABASE_URL_ENV

TIMEOUT_SECONDS = 8.0
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class CanaryReadinessReport:
    declared_sha: str
    source_fingerprint: str
    release_verified: bool
    db_configured: bool
    claims_ready: bool
    audit_ready: bool
    flags_complete: bool
    target_guild_match: bool
    delivery_activated: bool = False

    def __post_init__(self) -> None:
        if self.delivery_activated is not False:
            raise ValueError("Read-only readiness may not activate delivery")

    @property
    def blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if not self.release_verified:
            blockers.append("Deployed source has not been matched to approved GitHub main")
        if not self.db_configured:
            blockers.append("Private reminder PostgreSQL connection not configured")
        if not self.claims_ready:
            blockers.append("Private Member Setup claim ledger unavailable")
        if not self.audit_ready:
            blockers.append("Private one-shot operator audit unavailable")
        if not self.flags_complete or not self.target_guild_match:
            blockers.append("Exact-guild/member/revision canary switches disabled or unmatched")
        return tuple(blockers)


async def read_canary_readiness(guild_id: int) -> CanaryReadinessReport:
    """Read both table schemas only when a DSN exists. No SQL mutations.

    The bot runtime cannot establish what the CURRENT GitHub main commit is,
    so release_verified remains False until an externally checked deployment
    proof is supplied in a separately reviewed rollout. Declared SHA and local
    fingerprint are included for manual comparison, never accepted as a pass.
    """
    if type(guild_id) is not int or guild_id <= 0:
        raise ValueError("One real server is required")
    release = runtime_release_snapshot()
    declared = str(release.get("declared_sha") or "")
    sha = declared if _SHA_RE.fullmatch(declared) else ""
    fingerprint = str(release.get("source_fingerprint") or "unavailable")
    db_configured = bool(os.getenv(DATABASE_URL_ENV, "").strip())
    all_flags = (
        os.getenv(ENABLE_ENV, "") == "1"
        and bool(re.fullmatch(r"[1-9][0-9]{0,19}", os.getenv(GUILD_ENV, "")))
        and bool(re.fullmatch(r"[1-9][0-9]{0,19}", os.getenv(USER_ENV, "")))
        and bool(re.fullmatch(r"[1-9][0-9]{0,19}", os.getenv(REVISION_ENV, "")))
    )
    guild_matches = bool(all_flags and os.getenv(GUILD_ENV) == str(guild_id))
    claims = audit = False
    if db_configured:
        try:
            claims, audit = await asyncio.wait_for(
                asyncio.gather(
                    asyncio.to_thread(ledger_ready_sync),
                    asyncio.to_thread(audit_ready_sync),
                ),
                timeout=TIMEOUT_SECONDS,
            )
            claims = claims is True
            audit = audit is True
        except asyncio.CancelledError:
            raise
        except Exception:
            claims = audit = False
    return CanaryReadinessReport(
        declared_sha=sha,
        source_fingerprint=fingerprint[:24],
        release_verified=False,
        db_configured=db_configured,
        claims_ready=claims,
        audit_ready=audit,
        flags_complete=bool(all_flags),
        target_guild_match=guild_matches,
    )
