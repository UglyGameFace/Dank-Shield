from __future__ import annotations

"""Host failover and quorum voting for shared media sessions."""

import math
import time
from dataclasses import dataclass, field
from typing import Any, Optional

DEFAULT_HOST_AWAY_SECONDS = 120.0
DEFAULT_VOTE_TTL_SECONDS = 60.0

ACTION_PAUSE = "pause"
ACTION_RESUME = "resume"
ACTION_SEEK = "seek"
ACTION_SWITCH_FILE = "switch_file"
ACTION_REBUFFER = "rebuffer"
ACTION_END_STREAM = "end_stream"
ACTION_SEARCH_MEDIA = "search_media"
ACTION_PLAY_RESULT = "play_result"
ACTION_QUEUE_RESULT = "queue_result"
ACTION_PLAY_NEXT = "play_next"

DESTRUCTIVE_ACTIONS = frozenset(
    {
        ACTION_END_STREAM,
        ACTION_PLAY_RESULT,
        ACTION_PLAY_NEXT,
        ACTION_SWITCH_FILE,
    }
)
SEARCH_ACTIONS = frozenset(
    {
        ACTION_SEARCH_MEDIA,
        ACTION_PLAY_RESULT,
        ACTION_QUEUE_RESULT,
        ACTION_PLAY_NEXT,
    }
)


def normalize_media_action(value: str) -> str:
    action = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    allowed = {
        ACTION_PAUSE,
        ACTION_RESUME,
        ACTION_SEEK,
        ACTION_SWITCH_FILE,
        ACTION_REBUFFER,
        ACTION_END_STREAM,
        ACTION_SEARCH_MEDIA,
        ACTION_PLAY_RESULT,
        ACTION_QUEUE_RESULT,
        ACTION_PLAY_NEXT,
    }
    if action not in allowed:
        raise ValueError("Unsupported shared media action.")
    return action


def normalize_search_query(value: str) -> str:
    query = " ".join(str(value or "").strip().split())
    if not query:
        raise ValueError("Search query cannot be empty.")
    return query[:180]


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
        destructive: Optional[bool] = None,
    ) -> MediaVote:
        if not self.host_away:
            raise PermissionError("The host is active; voting is not available.")
        self.prune()
        if self.active is not None:
            raise RuntimeError("Another media action vote is already active.")

        normalized_action = normalize_media_action(action)
        normalized_payload = dict(payload or {})

        if normalized_action == ACTION_SEARCH_MEDIA:
            normalized_payload["query"] = normalize_search_query(
                normalized_payload.get("query", "")
            )
        elif normalized_action in {
            ACTION_PLAY_RESULT,
            ACTION_QUEUE_RESULT,
            ACTION_PLAY_NEXT,
        }:
            result_id = str(normalized_payload.get("result_id", "") or "").strip()
            if not result_id:
                raise ValueError("A media result id is required for that action.")
            normalized_payload["result_id"] = result_id[:160]

        is_destructive = (
            normalized_action in DESTRUCTIVE_ACTIONS
            if destructive is None
            else bool(destructive)
        )

        now = time.monotonic()
        vote = MediaVote(
            action=normalized_action,
            payload=normalized_payload,
            created_by=int(user_id),
            created_at=now,
            expires_at=now + max(15.0, float(self.policy.ttl_seconds)),
            destructive=is_destructive,
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
    "ACTION_END_STREAM",
    "ACTION_PAUSE",
    "ACTION_PLAY_NEXT",
    "ACTION_PLAY_RESULT",
    "ACTION_QUEUE_RESULT",
    "ACTION_REBUFFER",
    "ACTION_RESUME",
    "ACTION_SEARCH_MEDIA",
    "ACTION_SEEK",
    "ACTION_SWITCH_FILE",
    "DEFAULT_HOST_AWAY_SECONDS",
    "DEFAULT_VOTE_TTL_SECONDS",
    "DESTRUCTIVE_ACTIONS",
    "HostLease",
    "MediaVote",
    "MediaVoteManager",
    "SEARCH_ACTIONS",
    "VotePolicy",
    "normalize_media_action",
    "normalize_search_query",
]
