from __future__ import annotations

"""Canonical synchronized Movie Night room, voting, queue, and failover state.

The room layer is media-source agnostic. It can coordinate a torrent-backed stream
or another resolver later, but it does not search torrent indexes or discover
copyrighted sources. Search votes approve a catalog query; the media resolver is
responsible for returning lawful/authorized playback candidates.
"""

import math
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

PLAYBACK_ACTIONS = frozenset({"pause", "resume", "seek", "skip", "end"})
PROGRAMMING_ACTIONS = frozenset({"search", "nominate", "queue", "play_next"})
ALL_ACTIONS = PLAYBACK_ACTIONS | PROGRAMMING_ACTIONS


@dataclass
class ViewerState:
    user_id: int
    joined_at: float
    last_seen: float
    position_seconds: float = 0.0
    byte_position: int = 0
    buffered_until_byte: int = 0
    paused: bool = False

    @property
    def buffered_bytes(self) -> int:
        return max(0, int(self.buffered_until_byte) - int(self.byte_position))


@dataclass
class MovieCandidate:
    candidate_id: str
    title: str
    proposer_id: int
    created_at: float
    source_ref: str = ""
    metadata_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    votes: set[int] = field(default_factory=set)


@dataclass
class RoomVote:
    vote_id: str
    action: str
    proposer_id: int
    payload: dict[str, Any]
    created_at: float
    expires_at: float
    yes: set[int] = field(default_factory=set)
    no: set[int] = field(default_factory=set)
    resolved: bool = False
    passed: bool = False


@dataclass
class MovieNightRoom:
    room_id: str
    guild_id: int
    channel_id: int
    host_id: int
    stream_token: str
    created_at: float
    host_last_seen: float
    playback_state: str = "paused"
    playback_position: float = 0.0
    playback_anchor_monotonic: float = 0.0
    viewers: dict[int, ViewerState] = field(default_factory=dict)
    votes: dict[str, RoomVote] = field(default_factory=dict)
    candidates: dict[str, MovieCandidate] = field(default_factory=dict)
    queue: list[str] = field(default_factory=list)
    approved_search_query: str = ""
    ended: bool = False

    def current_position(self, now: Optional[float] = None) -> float:
        if self.playback_state != "playing":
            return max(0.0, float(self.playback_position))
        current = time.monotonic() if now is None else float(now)
        return max(
            0.0,
            float(self.playback_position)
            + max(0.0, current - float(self.playback_anchor_monotonic)),
        )


class MovieNightManager:
    def __init__(
        self,
        *,
        host_grace_seconds: float = 45.0,
        viewer_ttl_seconds: float = 35.0,
        vote_ttl_seconds: float = 45.0,
    ) -> None:
        self.host_grace_seconds = max(10.0, float(host_grace_seconds))
        self.viewer_ttl_seconds = max(10.0, float(viewer_ttl_seconds))
        self.vote_ttl_seconds = max(10.0, float(vote_ttl_seconds))
        self._rooms: dict[str, MovieNightRoom] = {}

    def create_room(
        self,
        *,
        guild_id: int,
        channel_id: int,
        host_id: int,
        stream_token: str,
        now: Optional[float] = None,
    ) -> MovieNightRoom:
        current = time.monotonic() if now is None else float(now)
        room = MovieNightRoom(
            room_id=secrets.token_urlsafe(12),
            guild_id=int(guild_id),
            channel_id=int(channel_id),
            host_id=int(host_id),
            stream_token=str(stream_token),
            created_at=current,
            host_last_seen=current,
            playback_anchor_monotonic=current,
        )
        room.viewers[int(host_id)] = ViewerState(
            user_id=int(host_id),
            joined_at=current,
            last_seen=current,
        )
        self._rooms[room.room_id] = room
        return room

    def get(self, room_id: str) -> Optional[MovieNightRoom]:
        return self._rooms.get(str(room_id or ""))

    def heartbeat(
        self,
        room_id: str,
        *,
        user_id: int,
        position_seconds: float,
        byte_position: int,
        buffered_until_byte: int,
        paused: bool,
        now: Optional[float] = None,
    ) -> MovieNightRoom:
        room = self._require_room(room_id)
        current = time.monotonic() if now is None else float(now)
        uid = int(user_id)
        viewer = room.viewers.get(uid)
        if viewer is None:
            viewer = ViewerState(user_id=uid, joined_at=current, last_seen=current)
            room.viewers[uid] = viewer
        viewer.last_seen = current
        viewer.position_seconds = max(0.0, float(position_seconds))
        viewer.byte_position = max(0, int(byte_position))
        viewer.buffered_until_byte = max(
            viewer.byte_position,
            int(buffered_until_byte),
        )
        viewer.paused = bool(paused)
        if uid == int(room.host_id):
            room.host_last_seen = current
            # A returning host immediately regains playback authority. Any
            # unresolved failover-only playback vote is cancelled, while
            # collaborative search/queue votes remain alive.
            for vote in room.votes.values():
                if not vote.resolved and vote.action in PLAYBACK_ACTIONS:
                    vote.resolved = True
                    vote.passed = False
        self._expire_votes(room, current)
        return room

    def host_active(self, room: MovieNightRoom, *, now: Optional[float] = None) -> bool:
        current = time.monotonic() if now is None else float(now)
        host = room.viewers.get(int(room.host_id))
        if host is None:
            return False
        return (
            current - float(host.last_seen) <= self.host_grace_seconds
            and current - float(room.host_last_seen) <= self.host_grace_seconds
        )

    def active_viewers(
        self,
        room: MovieNightRoom,
        *,
        now: Optional[float] = None,
    ) -> set[int]:
        current = time.monotonic() if now is None else float(now)
        return {
            int(uid)
            for uid, viewer in room.viewers.items()
            if current - float(viewer.last_seen) <= self.viewer_ttl_seconds
        }

    def required_yes_votes(
        self,
        room: MovieNightRoom,
        *,
        now: Optional[float] = None,
    ) -> int:
        voters = self.active_viewers(room, now=now)
        return max(1, math.floor(len(voters) / 2) + 1)

    def propose_vote(
        self,
        room_id: str,
        *,
        proposer_id: int,
        action: str,
        payload: Optional[dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> RoomVote:
        room = self._require_room(room_id)
        current = time.monotonic() if now is None else float(now)
        normalized = str(action or "").strip().lower()
        if normalized not in ALL_ACTIONS:
            raise ValueError("Unsupported Movie Night vote action.")

        active = self.active_viewers(room, now=current)
        proposer = int(proposer_id)
        if proposer not in active:
            raise PermissionError("Only active Movie Night viewers may start votes.")

        if normalized in PLAYBACK_ACTIONS and self.host_active(room, now=current):
            if proposer != int(room.host_id):
                raise PermissionError(
                    "Playback voting activates only while the host is away."
                )

        vote = RoomVote(
            vote_id=secrets.token_urlsafe(10),
            action=normalized,
            proposer_id=proposer,
            payload=dict(payload or {}),
            created_at=current,
            expires_at=current + self.vote_ttl_seconds,
            yes={proposer},
        )
        room.votes[vote.vote_id] = vote
        self._resolve_vote(room, vote, current)
        return vote

    def cast_vote(
        self,
        room_id: str,
        vote_id: str,
        *,
        user_id: int,
        approve: bool,
        now: Optional[float] = None,
    ) -> RoomVote:
        room = self._require_room(room_id)
        current = time.monotonic() if now is None else float(now)
        self._expire_votes(room, current)
        vote = room.votes.get(str(vote_id or ""))
        if vote is None:
            raise LookupError("Movie Night vote not found.")
        if vote.resolved:
            return vote

        uid = int(user_id)
        if uid not in self.active_viewers(room, now=current):
            raise PermissionError("Only active Movie Night viewers may vote.")

        vote.yes.discard(uid)
        vote.no.discard(uid)
        (vote.yes if approve else vote.no).add(uid)
        self._resolve_vote(room, vote, current)
        return vote

    def nominate(
        self,
        room_id: str,
        *,
        user_id: int,
        title: str,
        source_ref: str = "",
        metadata_ref: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        now: Optional[float] = None,
    ) -> MovieCandidate:
        room = self._require_room(room_id)
        current = time.monotonic() if now is None else float(now)
        uid = int(user_id)
        if uid not in self.active_viewers(room, now=current):
            raise PermissionError("Only active Movie Night viewers may nominate movies.")
        clean_title = " ".join(str(title or "").split())[:180]
        if not clean_title:
            raise ValueError("Movie title is required.")

        candidate = MovieCandidate(
            candidate_id=secrets.token_urlsafe(9),
            title=clean_title,
            proposer_id=uid,
            source_ref=str(source_ref or "").strip()[:1000],
            metadata_ref=str(metadata_ref or "").strip()[:1000],
            metadata=dict(metadata or {}),
            created_at=current,
            votes={uid},
        )
        room.candidates[candidate.candidate_id] = candidate
        return candidate

    def vote_candidate(
        self,
        room_id: str,
        candidate_id: str,
        *,
        user_id: int,
        approve: bool = True,
        now: Optional[float] = None,
    ) -> MovieCandidate:
        room = self._require_room(room_id)
        current = time.monotonic() if now is None else float(now)
        uid = int(user_id)
        if uid not in self.active_viewers(room, now=current):
            raise PermissionError("Only active Movie Night viewers may vote on movies.")
        candidate = room.candidates.get(str(candidate_id or ""))
        if candidate is None:
            raise LookupError("Movie candidate not found.")
        if approve:
            candidate.votes.add(uid)
        else:
            candidate.votes.discard(uid)
        return candidate

    def ranked_candidates(
        self,
        room_id: str,
        *,
        now: Optional[float] = None,
    ) -> list[MovieCandidate]:
        room = self._require_room(room_id)
        active = self.active_viewers(room, now=now)
        return sorted(
            room.candidates.values(),
            key=lambda item: (
                -len(item.votes & active),
                float(item.created_at),
                item.title.lower(),
            ),
        )

    def queue_winner(
        self,
        room_id: str,
        *,
        candidate_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> MovieCandidate:
        room = self._require_room(room_id)
        if candidate_id:
            candidate = room.candidates.get(str(candidate_id))
            if candidate is None:
                raise LookupError("Movie candidate not found.")
        else:
            ranked = self.ranked_candidates(room_id, now=now)
            if not ranked:
                raise LookupError("No movie candidates are available.")
            candidate = ranked[0]

        if candidate.candidate_id not in room.queue:
            room.queue.append(candidate.candidate_id)
        return candidate

    def next_queued(self, room_id: str) -> Optional[MovieCandidate]:
        room = self._require_room(room_id)
        while room.queue:
            candidate_id = room.queue[0]
            candidate = room.candidates.get(candidate_id)
            if candidate is not None:
                return candidate
            room.queue.pop(0)
        return None

    def group_buffer_corridor(
        self,
        room_id: str,
        *,
        now: Optional[float] = None,
        max_skew_bytes: int = 64 * 1024 * 1024,
    ) -> Optional[tuple[int, int, int]]:
        room = self._require_room(room_id)
        active_ids = self.active_viewers(room, now=now)
        viewers = [
            room.viewers[uid]
            for uid in active_ids
            if uid in room.viewers
        ]
        if not viewers:
            return None

        positions = sorted(max(0, int(v.byte_position)) for v in viewers)
        center = positions[len(positions) // 2]
        skew = max(4 * 1024 * 1024, int(max_skew_bytes))
        relevant = [
            v
            for v in viewers
            if abs(int(v.byte_position) - center) <= skew
        ] or viewers

        start = min(max(0, int(v.byte_position)) for v in relevant)
        buffered_end = min(
            max(int(v.byte_position), int(v.buffered_until_byte))
            for v in relevant
        )
        leader = max(
            max(int(v.byte_position), int(v.buffered_until_byte))
            for v in relevant
        )
        return start, buffered_end, leader

    def apply_host_action(
        self,
        room_id: str,
        *,
        host_id: int,
        action: str,
        payload: Optional[dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> MovieNightRoom:
        room = self._require_room(room_id)
        if int(host_id) != int(room.host_id):
            raise PermissionError("Only the Movie Night host may directly control playback.")
        current = time.monotonic() if now is None else float(now)
        room.host_last_seen = current
        self._apply_action(room, str(action or "").lower(), dict(payload or {}), current)
        return room

    def _resolve_vote(
        self,
        room: MovieNightRoom,
        vote: RoomVote,
        now: float,
    ) -> None:
        active = self.active_viewers(room, now=now)
        yes = len(vote.yes & active)
        no = len(vote.no & active)
        required = max(1, math.floor(len(active) / 2) + 1)

        if yes >= required:
            vote.resolved = True
            vote.passed = True
            self._apply_action(room, vote.action, vote.payload, now)
            return

        if no >= required or now >= vote.expires_at:
            vote.resolved = True
            vote.passed = False

    def _expire_votes(self, room: MovieNightRoom, now: float) -> None:
        for vote in room.votes.values():
            if not vote.resolved and now >= vote.expires_at:
                self._resolve_vote(room, vote, now)

    def _apply_action(
        self,
        room: MovieNightRoom,
        action: str,
        payload: dict[str, Any],
        now: float,
    ) -> None:
        if action == "pause":
            room.playback_position = room.current_position(now)
            room.playback_state = "paused"
            room.playback_anchor_monotonic = now
        elif action == "resume":
            room.playback_position = room.current_position(now)
            room.playback_state = "playing"
            room.playback_anchor_monotonic = now
        elif action == "seek":
            room.playback_position = max(0.0, float(payload.get("seconds", 0.0) or 0.0))
            room.playback_anchor_monotonic = now
        elif action in {"skip", "play_next"}:
            if room.queue:
                room.queue.pop(0)
            room.playback_position = 0.0
            room.playback_anchor_monotonic = now
            room.playback_state = "paused"
        elif action == "end":
            room.playback_position = room.current_position(now)
            room.playback_state = "ended"
            room.ended = True
        elif action == "search":
            query = " ".join(str(payload.get("query", "") or "").split())[:180]
            if query:
                room.approved_search_query = query
        elif action in {"nominate", "queue"}:
            candidate_id = str(payload.get("candidate_id", "") or "")
            if candidate_id and candidate_id in room.candidates and candidate_id not in room.queue:
                room.queue.append(candidate_id)

    def _require_room(self, room_id: str) -> MovieNightRoom:
        room = self.get(room_id)
        if room is None:
            raise LookupError("Movie Night room not found.")
        if room.ended:
            raise RuntimeError("Movie Night room has ended.")
        return room


_MANAGER: Optional[MovieNightManager] = None


def get_movie_night_manager() -> MovieNightManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = MovieNightManager()
    return _MANAGER


__all__ = [
    "ALL_ACTIONS",
    "MovieCandidate",
    "MovieNightManager",
    "MovieNightRoom",
    "PLAYBACK_ACTIONS",
    "PROGRAMMING_ACTIONS",
    "RoomVote",
    "ViewerState",
    "get_movie_night_manager",
]
