from __future__ import annotations

"""Host failover and quorum voting for shared media sessions."""

import math
import time
from dataclasses import dataclass, field
from typing import Any, Optional

DEFAULT_HOST_AWAY_SECONDS = 120.0
DEFAULT_VOTE_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class VotePolicy:
    quorum_fraction: float = 0.50
    pass_fraction: float = 0.60
    destructive_pass_fraction: float = 0.67
    min_quorum: int = 2
    ttl_seconds: float = DEFAULT_VOTE_TTL_SECONDS


@dataclass
class MediaVote:
    action: str
    payload: dict[str, Any]
    created_by: int
    created_at: float
    expires_at: float
    destructive: bool = False
    yes: set[int] = field(default_factory=set)
    no: set[int] = field(default_factory=set)

    @property
    def total_votes(self) -> int:
        return len(self.yes | self.no)


@dataclass
class HostLease:
    owner_id: int
    last_active_at: float
    explicitly_away: bool = False

    def touch(self, user_id: int) -> bool:
        if int(user_id) != int(self.owner_id):
            return False
        self.last_active_at = time.monotonic()
        self.explicitly_away = False
        return True

    def set_away(self, user_id: int, away: bool = True) -> bool:
        if int(user_id) != int(self.owner_id):
            return False
        self.explicitly_away = bool(away)
        if not away:
            self.last_active_at = time.monotonic()
        return True

    def is_away(self, *, away_seconds: float = DEFAULT_HOST_AWAY_SECONDS) -> bool:
        if self.explicitly_away:
            return True
        return time.monotonic() - float(self.last_active_at) >= max(15.0, float(away_seconds))


class MediaVoteManager:
    def __init__(
        self,
        *,
        owner_id: int,
        away_seconds: float = DEFAULT_HOST_AWAY_SECONDS,
        policy: Optional[VotePolicy] = None,
    ) -> None:
        self.lease = HostLease(
            owner_id=int(owner_id),
            last_active_at=time.monotonic(),
        )
        self.away_seconds = max(15.0, float(away_seconds))
        self.policy = policy or VotePolicy()
        self.active: Optional[MediaVote] = None

    @property
    def host_away(self) -> bool:
        return self.lease.is_away(away_seconds=self.away_seconds)

    def host_touch(self, user_id: int) -> bool:
        return self.lease.touch(int(user_id))

    def set_host_away(self, user_id: int, away: bool = True) -> bool:
        return self.lease.set_away(int(user_id), away)

    def host_reclaim(self, user_id: int) -> bool:
        if not self.lease.touch(int(user_id)):
            return False
        self.active = None
        return True

    def start_vote(
        self,
        *,
        user_id: int,
        action: str,
        payload: Optional[dict[str, Any]] = None,
        destructive: bool = False,
    ) -> MediaVote:
        if not self.host_away:
            raise PermissionError("The host is active; voting is not available.")
        self.prune()
        if self.active is not None:
            raise RuntimeError("Another media action vote is already active.")

        now = time.monotonic()
        vote = MediaVote(
            action=str(action or "").strip(),
            payload=dict(payload or {}),
            created_by=int(user_id),
            created_at=now,
            expires_at=now + max(15.0, float(self.policy.ttl_seconds)),
            destructive=bool(destructive),
        )
        vote.yes.add(int(user_id))
        self.active = vote
        return vote

    def cast(self, *, user_id: int, approve: bool) -> MediaVote:
        self.prune()
        vote = self.active
        if vote is None:
            raise LookupError("No active media vote.")
        uid = int(user_id)
        vote.yes.discard(uid)
        vote.no.discard(uid)
        (vote.yes if approve else vote.no).add(uid)
        return vote

    def required_quorum(self, eligible_count: int) -> int:
        eligible = max(1, int(eligible_count))
        return min(
            eligible,
            max(
                int(self.policy.min_quorum),
                int(math.ceil(eligible * float(self.policy.quorum_fraction))),
            ),
        )

    def evaluate(self, *, eligible_count: int) -> tuple[str, dict[str, int | float]]:
        self.prune()
        vote = self.active
        if vote is None:
            return "none", {}

        quorum = self.required_quorum(eligible_count)
        cast = vote.total_votes
        yes = len(vote.yes)
        no = len(vote.no)
        pass_fraction = (
            float(self.policy.destructive_pass_fraction)
            if vote.destructive
            else float(self.policy.pass_fraction)
        )

        details: dict[str, int | float] = {
            "eligible": max(1, int(eligible_count)),
            "quorum": quorum,
            "cast": cast,
            "yes": yes,
            "no": no,
            "pass_fraction": pass_fraction,
        }

        if cast < quorum:
            return "pending", details
        approval = yes / max(1, cast)
        details["approval"] = approval
        if approval >= pass_fraction:
            return "passed", details
        if no / max(1, cast) > (1.0 - pass_fraction):
            return "failed", details
        return "pending", details

    def consume_if_passed(self, *, eligible_count: int) -> Optional[MediaVote]:
        status, _ = self.evaluate(eligible_count=eligible_count)
        if status != "passed":
            return None
        vote = self.active
        self.active = None
        return vote

    def prune(self) -> None:
        vote = self.active
        if vote is not None and time.monotonic() >= float(vote.expires_at):
            self.active = None


__all__ = [
    "DEFAULT_HOST_AWAY_SECONDS",
    "DEFAULT_VOTE_TTL_SECONDS",
    "HostLease",
    "MediaVote",
    "MediaVoteManager",
    "VotePolicy",
]
