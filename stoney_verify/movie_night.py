from __future__ import annotations

"""Canonical synchronized Movie Night room, voting, queue, and failover state.

The room layer is media-source agnostic. It can coordinate a torrent-backed stream
or another resolver later, but it does not search torrent indexes or discover
copyrighted sources. Search votes approve a catalog query; the media resolver is
responsible for returning lawful/authorized playback candidates.
"""

import math
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

PLAYBACK_ACTIONS = frozenset({"pause", "resume", "seek", "skip"})
SESSION_ACTIONS = frozenset({"end"})
PROGRAMMING_ACTIONS = frozenset({"search", "nominate", "queue", "play_next", "play_variant"})
ALL_ACTIONS = PLAYBACK_ACTIONS | SESSION_ACTIONS | PROGRAMMING_ACTIONS
PRIVATE_VIEWER_LIMIT = 20

def movie_room_lease_key(guild_id: int, channel_id: int) -> str:
    """Stable tracked torrent consumer identity for one Movie Night room."""

    return f"movie:{int(guild_id)}:{int(channel_id)}"



@dataclass
class ViewerState:
    user_id: int
    joined_at: float
    last_seen: float
    position_seconds: float = 0.0
    byte_position: int = 0
    buffered_until_byte: int = 0
    buffered_until_seconds: float = 0.0
    media_duration_seconds: float = 0.0
    paused: bool = False
    sync_ready: bool = True
    sync_ready_at: float = 0.0
    sync_target_position: float = 0.0
    client_session_id: str = ""
    sync_requested: bool = False
    sync_requested_at: float = 0.0

    @property
    def buffered_bytes(self) -> int:
        return max(0, int(self.buffered_until_byte) - int(self.byte_position))


@dataclass
class MovieSourceVariant:
    variant_id: str
    source_ref: str
    created_at: float
    source_id: str = ""
    source_label: str = ""
    file_size: int = 0
    peers: int = 0
    seeds: int = 0
    leechers: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    votes: set[int] = field(default_factory=set)

    def browser_video_risk_key(self) -> int:
        """Conservative cross-browser native video risk based on verified media.

        No torrent title alone proves a particular browser can decode a file.
        Unprobed releases stay unknown; we prefer known H.264/MP4 releases
        only when the competing releases have comparable vote/seed status.
        0=broadly browser-compatible, 1=unknown/browser-dependent, 2=risky.
        """
        meta = dict(self.metadata or {})
        verified = meta.get("verified")
        if not isinstance(verified, Mapping):
            return 1
        video = verified.get("video")
        if not isinstance(video, Mapping) or not video.get("codec"):
            return 1
        codec = str(video.get("codec") or "").strip().casefold()
        container = str(verified.get("container") or "").strip().casefold()
        filename = str(verified.get("filename") or "").strip().casefold()
        fmt = container.split(",")[0] if container else ""
        if filename.endswith((".mkv", ".avi", ".mpeg", ".mpg")) or fmt in {
            "matroska", "avi", "mpeg", "mpegvideo",
        }:
            return 2
        if codec in {"mpeg2video", "mpeg4", "vc1", "wmv3", "theora"}:
            return 2
        if codec in {"h264", "avc", "avc1"} and (
            filename.endswith((".mp4", ".m4v"))
            or fmt in {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}
        ):
            try:
                depth = int(video.get("bit_depth") or 0)
            except (TypeError, ValueError, OverflowError):
                return 1
            return 0 if depth <= 8 else 1
        # WebM/VP8/VP9/AV1 and MP4/HEVC are browser-dependent. Neither
        # native playback nor a video-transcoding fallback is guaranteed.
        return 1

    def browser_audio_risk_key(self) -> int:
        """0=safest for browsers, 1=unknown/conditional, 2=known risky."""

        meta = dict(self.metadata or {})
        verified = meta.get("verified") if isinstance(meta.get("verified"), Mapping) else {}
        tracks = verified.get("audio_tracks") if isinstance(verified, Mapping) else None
        codecs: set[str] = set()
        if isinstance(tracks, list):
            for row in tracks:
                if not isinstance(row, Mapping):
                    continue
                codec = str(row.get("codec") or "").strip().casefold()
                if codec:
                    codecs.add(codec)

        widely_safe = {"aac", "mp3"}
        conditional = {"opus", "vorbis"}
        risky = {
            "ac3", "eac3", "dca", "dts", "truehd", "mlp",
            "flac", "pcm_s16le", "pcm_s24le", "pcm_s32le",
        }

        # The stream's first/default track and per-viewer browser codecs are
        # not interchangeable. The transcoder requires a sidecar when ANY
        # verified track is unsupported. A safe secondary AAC track must not
        # make a mixed DTS/AAC release appear browser-native-safe.
        if codecs & risky:
            return 2
        if codecs:
            if codecs <= widely_safe:
                return 0
            if codecs & conditional:
                return 1
            return 1

        release = (
            meta.get("release_name")
            if isinstance(meta.get("release_name"), Mapping)
            else {}
        )
        audio_tags = " ".join(
            str(value or "")
            for value in list(release.get("audio_tags") or [])
        ).casefold()
        if "aac" in audio_tags:
            return 0
        if "opus" in audio_tags or "vorbis" in audio_tags:
            return 1
        if any(
            token in audio_tags
            for token in ("ddp", "eac3", "dd 5.1", "ac3", "dts", "truehd", "flac")
        ):
            return 2
        return 1

    @property
    def swarm_health(self) -> dict[str, Any]:
        seeds = max(0, int(self.seeds))
        leechers = max(0, int(self.leechers))
        peers = max(seeds + leechers, int(self.peers))
        ratio = round(seeds / max(1, leechers), 3)
        if seeds <= 0:
            label = "dead"
        elif seeds >= 50:
            label = "excellent"
        elif seeds >= 20:
            label = "strong"
        elif seeds >= 5:
            label = "usable"
        else:
            label = "weak"
        return {
            "seeds": seeds,
            "leechers": leechers,
            "peers": peers,
            "seed_leech_ratio": ratio,
            "label": label,
        }

    def quality_efficiency_key(self) -> tuple[int, int, int, int, int]:
        meta = dict(self.metadata or {})
        verified = meta.get("verified") if isinstance(meta.get("verified"), Mapping) else {}
        release = meta.get("release_name") if isinstance(meta.get("release_name"), Mapping) else meta

        video = verified.get("video") if isinstance(verified.get("video"), Mapping) else {}
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        pixels = width * height

        hdr = len(video.get("hdr") or []) if isinstance(video.get("hdr"), list) else 0
        audio_tracks = verified.get("audio_tracks")
        audio_channels = 0
        if isinstance(audio_tracks, list):
            for row in audio_tracks:
                if isinstance(row, Mapping):
                    audio_channels = max(audio_channels, int(row.get("channels") or 0))

        codec = str(video.get("codec") or "").lower()
        codec_efficiency = 3 if codec in {"av1"} else 2 if codec in {"hevc", "h265", "h.265"} else 1 if codec in {"h264", "h.264", "avc"} else 0

        source = str(release.get("source") or "").upper()
        source_weight = {
            "BLURAY REMUX": 7,
            "REMUX": 7,
            "BLURAY": 6,
            "BDRIP": 5,
            "BRRIP": 5,
            "WEB-DL": 5,
            "WEBRIP": 4,
            "HDTV": 3,
            "DVDRIP": 2,
            "DVD": 2,
            "TC": 1,
            "TS": 1,
            "CAM": 0,
        }.get(source, 0)

        health = max(0, int(self.seeds)) * 4 + max(0, int(self.peers))
        size_penalty = max(0, int(self.file_size)) // (256 * 1024 * 1024)
        return (
            pixels,
            hdr + audio_channels,
            codec_efficiency,
            source_weight,
            health - size_penalty,
        )


@dataclass
class MovieCandidate:
    candidate_id: str
    title: str
    proposer_id: int
    created_at: float
    source_ref: str = ""
    metadata_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    variants: dict[str, MovieSourceVariant] = field(default_factory=dict)
    selected_variant_id: str = ""
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
    executed: bool = False
    execution_error: str = ""


@dataclass
class MovieNightRoom:
    room_id: str
    guild_id: int
    channel_id: int
    host_id: int
    stream_token: str
    created_at: float
    host_last_seen: float
    mode: str = "watch_party"
    private_allowed_viewers: set[int] = field(default_factory=set)
    playback_state: str = "paused"
    playback_position: float = 0.0
    playback_anchor_monotonic: float = 0.0
    playback_rate: float = 1.0
    viewers: dict[int, ViewerState] = field(default_factory=dict)
    votes: dict[str, RoomVote] = field(default_factory=dict)
    candidates: dict[str, MovieCandidate] = field(default_factory=dict)
    queue: list[str] = field(default_factory=list)
    approved_search_query: str = ""
    current_candidate_id: str = ""
    current_variant_id: str = ""
    buffering_since: float = 0.0
    buffering_cooldown_until: float = 0.0
    ended: bool = False

    def current_position(self, now: Optional[float] = None) -> float:
        if self.playback_state != "playing":
            return max(0.0, float(self.playback_position))
        current = time.monotonic() if now is None else float(now)
        rate = min(2.0, max(0.5, float(self.playback_rate or 1.0)))
        return max(
            0.0,
            float(self.playback_position)
            + max(0.0, current - float(self.playback_anchor_monotonic)) * rate,
        )


class MovieNightManager:
    def __init__(
        self,
        *,
        host_grace_seconds: float = 45.0,
        viewer_ttl_seconds: float = 35.0,
        vote_ttl_seconds: float = 45.0,
        empty_room_ttl_seconds: float = 1800.0,
        buffer_low_seconds: float = 4.0,
        buffer_resume_seconds: float = 12.0,
        buffer_max_hold_seconds: float = 20.0,
        late_join_min_buffer_seconds: float = 8.0,
        late_join_max_buffer_seconds: float = 15.0,
        late_join_sync_tolerance_seconds: float = 2.5,
    ) -> None:
        self.host_grace_seconds = max(10.0, float(host_grace_seconds))
        self.viewer_ttl_seconds = max(10.0, float(viewer_ttl_seconds))
        self.vote_ttl_seconds = max(10.0, float(vote_ttl_seconds))
        self.empty_room_ttl_seconds = max(300.0, float(empty_room_ttl_seconds))
        self.buffer_low_seconds = max(1.0, float(buffer_low_seconds))
        self.buffer_resume_seconds = max(
            self.buffer_low_seconds + 1.0,
            float(buffer_resume_seconds),
        )
        self.buffer_max_hold_seconds = max(5.0, float(buffer_max_hold_seconds))
        self.late_join_min_buffer_seconds = max(
            3.0,
            float(late_join_min_buffer_seconds),
        )
        self.late_join_max_buffer_seconds = max(
            self.late_join_min_buffer_seconds,
            float(late_join_max_buffer_seconds),
        )
        self.late_join_sync_tolerance_seconds = max(
            0.5,
            float(late_join_sync_tolerance_seconds),
        )
        self._rooms: dict[str, MovieNightRoom] = {}

    def create_room(
        self,
        *,
        guild_id: int,
        channel_id: int,
        host_id: int,
        stream_token: str,
        mode: str = "watch_party",
        now: Optional[float] = None,
    ) -> MovieNightRoom:
        current = time.monotonic() if now is None else float(now)
        normalized_mode = str(mode or "watch_party").strip().casefold()
        if normalized_mode not in {"watch_party", "private", "standalone"}:
            raise ValueError(
                "Movie Night room mode must be watch_party, private, or standalone."
            )
        existing = self.active_room_for_channel(guild_id, channel_id)
        if existing is not None:
            raise RuntimeError("A Movie Night room is already active in this channel.")
        room = MovieNightRoom(
            room_id=secrets.token_urlsafe(12),
            guild_id=int(guild_id),
            channel_id=int(channel_id),
            host_id=int(host_id),
            stream_token=str(stream_token),
            created_at=current,
            host_last_seen=current,
            mode=normalized_mode,
            private_allowed_viewers=(
                {int(host_id)} if normalized_mode == "private" else set()
            ),
            playback_anchor_monotonic=current,
        )
        room.viewers[int(host_id)] = ViewerState(
            user_id=int(host_id),
            joined_at=current,
            last_seen=current,
            sync_ready=True,
            sync_ready_at=current,
        )
        self._rooms[room.room_id] = room
        return room

    def get(self, room_id: str) -> Optional[MovieNightRoom]:
        return self._rooms.get(str(room_id or ""))

    def retire_room(self, room_id: str) -> bool:
        """Forget an ended room after its external resources are released."""

        key = str(room_id or "")
        room = self._rooms.get(key)
        if room is None:
            return False
        if not room.ended:
            raise RuntimeError("Cannot retire an active Movie Night room.")
        self._rooms.pop(key, None)
        return True

    def active_room_for_channel(
        self,
        guild_id: int,
        channel_id: int,
    ) -> Optional[MovieNightRoom]:
        matches = [
            room
            for room in self._rooms.values()
            if not room.ended
            and int(room.guild_id) == int(guild_id)
            and int(room.channel_id) == int(channel_id)
        ]
        if not matches:
            return None
        return max(matches, key=lambda room: float(room.created_at))

    def active_rooms_for_guild(self, guild_id: int) -> tuple[MovieNightRoom, ...]:
        matches = [
            room
            for room in self._rooms.values()
            if not room.ended and int(room.guild_id) == int(guild_id)
        ]
        return tuple(
            sorted(
                matches,
                key=lambda room: float(room.created_at),
                reverse=True,
            )
        )

    @staticmethod
    def user_can_access(room: MovieNightRoom, user_id: int) -> bool:
        mode = str(getattr(room, "mode", "watch_party") or "watch_party")
        uid = int(user_id)
        if mode == "standalone":
            return uid == int(room.host_id)
        if mode != "private":
            return True
        if uid == int(room.host_id):
            return True
        return uid in set(getattr(room, "private_allowed_viewers", set()) or set())

    def invite_private_viewer(
        self,
        room_id: str,
        *,
        host_id: int,
        user_id: int,
    ) -> MovieNightRoom:
        room = self._require_room(room_id)
        if str(getattr(room, "mode", "watch_party") or "watch_party") != "private":
            raise RuntimeError("Viewer invites are only available for a Private Session.")
        if int(host_id) != int(room.host_id):
            raise PermissionError("Only the Private Session host can invite viewers.")

        uid = int(user_id)
        if uid <= 0:
            raise ValueError("Choose a valid viewer.")
        allowed = room.private_allowed_viewers
        allowed.add(int(room.host_id))
        if uid in allowed:
            return room
        if len(allowed) >= PRIVATE_VIEWER_LIMIT:
            raise RuntimeError(
                f"Private Sessions support up to {PRIVATE_VIEWER_LIMIT} viewers total."
            )
        allowed.add(uid)
        return room

    def remove_private_viewer(
        self,
        room_id: str,
        *,
        host_id: int,
        user_id: int,
    ) -> MovieNightRoom:
        room = self._require_room(room_id)
        if str(getattr(room, "mode", "watch_party") or "watch_party") != "private":
            raise RuntimeError("Viewer management is only available for a Private Session.")
        if int(host_id) != int(room.host_id):
            raise PermissionError("Only the Private Session host can remove viewers.")

        uid = int(user_id)
        if uid == int(room.host_id):
            raise ValueError("The Private Session host cannot remove themselves.")
        room.private_allowed_viewers.discard(uid)
        room.viewers.pop(uid, None)
        return room

    def promote_private_to_watch_party(
        self,
        room_id: str,
        *,
        host_id: int,
    ) -> MovieNightRoom:
        """Convert the live Private Session into a Watch Party in place.

        The room id, media lease, queue, playback clock, viewers, and host stay
        untouched. Only the access/collaboration mode changes, so every signed
        Watch page observes the transition on its next state poll.
        """

        room = self._require_room(room_id)
        if int(host_id) != int(room.host_id):
            raise PermissionError("Only the Private Session host can start the Watch Party.")
        mode = str(getattr(room, "mode", "watch_party") or "watch_party")
        if mode == "watch_party":
            return room
        if mode != "private":
            raise RuntimeError("This Cinema session cannot be promoted to a Watch Party.")

        room.mode = "watch_party"
        # Private allowlisting no longer controls access after promotion.
        # Drop stale ids so no later code accidentally interprets them as
        # authoritative after the room becomes collaborative.
        room.private_allowed_viewers.clear()
        return room

    def active_room_for_user(
        self,
        guild_id: int,
        channel_id: int,
        user_id: int,
    ) -> Optional[MovieNightRoom]:
        room = self.active_room_for_channel(guild_id, channel_id)
        if room is None or not self.user_can_access(room, int(user_id)):
            return None
        return room

    def active_rooms_for_guild(self, guild_id: int) -> tuple[MovieNightRoom, ...]:
        return tuple(
            sorted(
                (
                    room
                    for room in self._rooms.values()
                    if not room.ended and int(room.guild_id) == int(guild_id)
                ),
                key=lambda room: float(room.created_at),
            )
        )

    def room_last_presence(self, room: MovieNightRoom) -> float:
        stamps = [
            float(room.created_at),
            float(room.host_last_seen),
        ]
        stamps.extend(float(viewer.last_seen) for viewer in room.viewers.values())
        return max(stamps) if stamps else float(room.created_at)

    def room_empty_expired(
        self,
        room: MovieNightRoom,
        *,
        now: Optional[float] = None,
    ) -> bool:
        if room.ended:
            return False
        current = time.monotonic() if now is None else float(now)
        if self.active_viewers(room, now=current):
            return False
        return (
            current - self.room_last_presence(room)
            >= self.empty_room_ttl_seconds
        )

    def inactive_room_candidates(
        self,
        *,
        now: Optional[float] = None,
    ) -> tuple[MovieNightRoom, ...]:
        current = time.monotonic() if now is None else float(now)
        return tuple(
            room
            for room in self._rooms.values()
            if self.room_empty_expired(room, now=current)
        )


    def join_room(
        self,
        room_id: str,
        *,
        user_id: int,
        now: Optional[float] = None,
    ) -> MovieNightRoom:
        room = self._require_room(room_id)
        current = time.monotonic() if now is None else float(now)
        uid = int(user_id)
        if not self.user_can_access(room, uid):
            raise PermissionError("This is a private Dank Cinema viewing session.")
        viewer = room.viewers.get(uid)
        if viewer is None:
            target = room.current_position(current)
            ready = bool(
                uid == int(room.host_id)
                or not room.stream_token
                or (
                    room.playback_state == "paused"
                    and target <= 1.0
                )
            )
            room.viewers[uid] = ViewerState(
                user_id=uid,
                joined_at=current,
                last_seen=current,
                sync_ready=ready,
                sync_ready_at=current if ready else 0.0,
                sync_target_position=target,
            )
        else:
            viewer.last_seen = current
        if uid == int(room.host_id):
            room.host_last_seen = current
        return room

    def leave_room(
        self,
        room_id: str,
        *,
        user_id: int,
    ) -> MovieNightRoom:
        room = self._require_room(room_id)
        room.viewers.pop(int(user_id), None)
        return room

    def heartbeat(
        self,
        room_id: str,
        *,
        user_id: int,
        position_seconds: float,
        byte_position: int,
        buffered_until_byte: int,
        paused: bool,
        buffered_until_seconds: float = 0.0,
        media_duration_seconds: float = 0.0,
        sync_buffer_target_seconds: float = 0.0,
        client_session_id: str = "",
        sync_requested: bool = False,
        now: Optional[float] = None,
    ) -> MovieNightRoom:
        room = self._require_room(room_id)
        current = time.monotonic() if now is None else float(now)
        uid = int(user_id)
        if not self.user_can_access(room, uid):
            raise PermissionError("This is a private Dank Cinema viewing session.")
        viewer = room.viewers.get(uid)
        if viewer is None:
            viewer = ViewerState(user_id=uid, joined_at=current, last_seen=current)
            room.viewers[uid] = viewer
        viewer.last_seen = current

        client_key = str(client_session_id or "").strip()[:96]
        requires_sync_gesture = bool(
            client_key
            and uid != int(room.host_id)
            and room.stream_token
        )
        if client_key and viewer.client_session_id != client_key:
            viewer.client_session_id = client_key
            if uid != int(room.host_id) and room.stream_token:
                viewer.sync_ready = False
                viewer.sync_ready_at = 0.0
                viewer.sync_requested = False
                viewer.sync_requested_at = 0.0
                viewer.sync_target_position = room.current_position(current)

        if (
            bool(sync_requested)
            and uid != int(room.host_id)
            and room.stream_token
        ):
            if not viewer.sync_requested:
                viewer.sync_target_position = room.current_position(current)
                viewer.sync_requested_at = current
            viewer.sync_requested = True

        viewer.position_seconds = max(0.0, float(position_seconds))
        viewer.byte_position = max(0, int(byte_position))
        viewer.buffered_until_byte = max(
            viewer.byte_position,
            int(buffered_until_byte),
        )
        viewer.buffered_until_seconds = max(
            viewer.position_seconds,
            float(buffered_until_seconds or 0.0),
        )
        viewer.media_duration_seconds = max(
            0.0,
            float(media_duration_seconds or 0.0),
        )
        viewer.paused = bool(paused)
        if uid == int(room.host_id):
            viewer.sync_ready = True
            viewer.sync_ready_at = viewer.sync_ready_at or current
            room.host_last_seen = current
            # A returning host immediately regains playback authority. Any
            # unresolved failover-only playback vote is cancelled, while
            # collaborative search/queue votes remain alive.
            for vote in room.votes.values():
                if not vote.resolved and vote.action in PLAYBACK_ACTIONS:
                    vote.resolved = True
                    vote.passed = False
        if not room.stream_token:
            viewer.sync_ready = True
            viewer.sync_ready_at = viewer.sync_ready_at or current
        elif uid != int(room.host_id) and not viewer.sync_ready:
            target = room.current_position(current)
            if not viewer.sync_requested:
                viewer.sync_target_position = target
            drift = abs(float(viewer.position_seconds) - float(target))
            buffered_ahead = max(
                0.0,
                float(viewer.buffered_until_seconds)
                - float(viewer.position_seconds),
            )
            requested = float(sync_buffer_target_seconds or 0.0)
            required_buffer = min(
                self.late_join_max_buffer_seconds,
                max(self.late_join_min_buffer_seconds, requested),
            )
            if viewer.media_duration_seconds > 0:
                remaining = max(
                    0.0,
                    float(viewer.media_duration_seconds)
                    - float(viewer.position_seconds),
                )
                required_buffer = min(
                    required_buffer,
                    max(1.0, remaining),
                )
            sync_intent_ok = bool(
                viewer.sync_requested
                or not requires_sync_gesture
            )
            if (
                sync_intent_ok
                and drift <= self.late_join_sync_tolerance_seconds
                and buffered_ahead >= required_buffer
            ):
                viewer.sync_ready = True
                viewer.sync_ready_at = current

        self._expire_votes(room, current)
        self._update_group_buffer_hold(room, current)
        return room

    def _update_group_buffer_hold(
        self,
        room: MovieNightRoom,
        now: float,
    ) -> None:
        if not room.stream_token or room.ended:
            return

        active_ids = self.buffer_quorum_viewers(room, now=now)
        if len(active_ids) < 2:
            if room.playback_state == "buffering":
                room.playback_state = "playing"
                room.playback_anchor_monotonic = now
                room.buffering_since = 0.0
            return

        room_position = room.current_position(now)
        relevant: list[ViewerState] = []
        for uid in active_ids:
            viewer = room.viewers.get(uid)
            if viewer is None:
                continue
            if abs(float(viewer.position_seconds) - room_position) > 15.0:
                continue
            if (
                viewer.media_duration_seconds > 0
                and viewer.media_duration_seconds - viewer.position_seconds
                <= self.buffer_low_seconds + 1.0
            ):
                continue
            relevant.append(viewer)

        if len(relevant) < 2:
            return

        ahead = [
            max(
                0.0,
                float(viewer.buffered_until_seconds)
                - float(viewer.position_seconds),
            )
            for viewer in relevant
        ]
        weakest = min(ahead) if ahead else 0.0

        if room.playback_state == "playing":
            if (
                now >= float(room.buffering_cooldown_until)
                and weakest < self.buffer_low_seconds
            ):
                room.playback_position = room_position
                room.playback_state = "buffering"
                room.playback_anchor_monotonic = now
                room.buffering_since = now
            return

        if room.playback_state != "buffering":
            return

        held = max(0.0, now - float(room.buffering_since or now))
        if weakest >= self.buffer_resume_seconds:
            room.playback_state = "playing"
            room.playback_anchor_monotonic = now
            room.buffering_since = 0.0
            return

        if held >= self.buffer_max_hold_seconds:
            room.playback_state = "playing"
            room.playback_anchor_monotonic = now
            room.buffering_since = 0.0
            room.buffering_cooldown_until = now + self.buffer_max_hold_seconds

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
        active = {
            int(uid)
            for uid, viewer in room.viewers.items()
            if current - float(viewer.last_seen) <= self.viewer_ttl_seconds
        }
        if str(getattr(room, "mode", "watch_party") or "watch_party") == "private":
            allowed = set(getattr(room, "private_allowed_viewers", set()) or set())
            allowed.add(int(room.host_id))
            return allowed & active
        return active

    def buffer_quorum_viewers(
        self,
        room: MovieNightRoom,
        *,
        now: Optional[float] = None,
    ) -> set[int]:
        active = self.active_viewers(room, now=now)
        return {
            uid
            for uid in active
            if (
                uid == int(room.host_id)
                or (
                    uid in room.viewers
                    and bool(room.viewers[uid].sync_ready)
                )
            )
        }

    def required_yes_votes(
        self,
        room: MovieNightRoom,
        *,
        now: Optional[float] = None,
    ) -> int:
        if str(getattr(room, "mode", "watch_party") or "watch_party") == "private":
            return 1
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
        if not self.user_can_access(room, proposer):
            raise PermissionError("This is a private Dank Cinema viewing session.")
        if proposer not in active:
            raise PermissionError("Only active Movie Night viewers may start votes.")
        if (
            str(getattr(room, "mode", "watch_party") or "watch_party") == "private"
            and proposer != int(room.host_id)
        ):
            raise PermissionError("Only the Private Session host can control this session.")

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
        if not self.user_can_access(room, uid):
            raise PermissionError("This is a private Dank Cinema viewing session.")
        if uid not in self.active_viewers(room, now=current):
            raise PermissionError("Only active Movie Night viewers may vote.")
        if (
            str(getattr(room, "mode", "watch_party") or "watch_party") == "private"
            and uid != int(room.host_id)
        ):
            raise PermissionError("Private Session does not use viewer voting.")

        vote.yes.discard(uid)
        vote.no.discard(uid)
        (vote.yes if approve else vote.no).add(uid)
        self._resolve_vote(room, vote, current)
        return vote

    def claim_vote_execution(
        self,
        room_id: str,
        vote_id: str,
    ) -> bool:
        """Claim one passed vote's external side effect exactly once."""

        room = self.get(room_id)
        if room is None:
            return False
        vote = room.votes.get(str(vote_id or ""))
        if vote is None or not vote.resolved or not vote.passed or vote.executed:
            return False
        vote.executed = True
        return True

    def set_vote_execution_error(
        self,
        room_id: str,
        vote_id: str,
        error: str,
    ) -> None:
        room = self.get(room_id)
        if room is None:
            return
        vote = room.votes.get(str(vote_id or ""))
        if vote is not None:
            vote.execution_error = " ".join(str(error or "").split())[:240]

    def find_candidate_by_title(
        self,
        room_id: str,
        title: str,
    ) -> Optional[MovieCandidate]:
        room = self._require_room(room_id)
        wanted = " ".join(str(title or "").split()).casefold()
        if not wanted:
            return None
        return next(
            (
                item
                for item in room.candidates.values()
                if item.title.casefold() == wanted
            ),
            None,
        )

    def nominate(
        self,
        room_id: str,
        *,
        user_id: int,
        title: str,
        source_ref: str = "",
        metadata_ref: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        auto_vote: bool = True,
        now: Optional[float] = None,
    ) -> MovieCandidate:
        room = self._require_room(room_id)
        current = time.monotonic() if now is None else float(now)
        uid = int(user_id)
        if uid not in self.active_viewers(room, now=current):
            raise PermissionError("Only active Movie Night viewers may nominate movies.")
        if (
            str(getattr(room, "mode", "watch_party") or "watch_party") == "private"
            and uid != int(room.host_id)
        ):
            raise PermissionError("Only the Private Session host can choose movies.")
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
            votes={uid} if auto_vote else set(),
        )
        room.candidates[candidate.candidate_id] = candidate
        return candidate

    def add_variant(
        self,
        room_id: str,
        candidate_id: str,
        *,
        user_id: int,
        source_ref: str,
        source_id: str = "",
        source_label: str = "",
        file_size: int = 0,
        peers: int = 0,
        seeds: int = 0,
        leechers: int = 0,
        metadata: Optional[Mapping[str, Any]] = None,
        auto_vote: bool = True,
        now: Optional[float] = None,
    ) -> MovieSourceVariant:
        room = self._require_room(room_id)
        current = time.monotonic() if now is None else float(now)
        uid = int(user_id)
        if uid not in self.active_viewers(room, now=current):
            raise PermissionError("Only active Movie Night viewers may add source variants.")
        if (
            str(getattr(room, "mode", "watch_party") or "watch_party") == "private"
            and uid != int(room.host_id)
        ):
            raise PermissionError("Only the Private Session host can choose releases.")

        candidate = room.candidates.get(str(candidate_id or ""))
        if candidate is None:
            raise LookupError("Movie candidate not found.")

        source = str(source_ref or "").strip()
        if not source:
            raise ValueError("A source reference is required.")

        existing_variant = next(
            (
                item
                for item in candidate.variants.values()
                if item.source_ref == source
            ),
            None,
        )
        if existing_variant is not None:
            existing_variant.source_id = str(source_id or existing_variant.source_id).strip()[:48]
            existing_variant.source_label = " ".join(
                str(source_label or existing_variant.source_label).split()
            )[:80]
            existing_variant.file_size = max(
                int(existing_variant.file_size),
                max(0, int(file_size or 0)),
            )
            existing_variant.peers = max(0, int(peers or 0))
            existing_variant.seeds = max(0, int(seeds or 0))
            existing_variant.leechers = max(0, int(leechers or 0))
            if metadata:
                existing_variant.metadata = dict(metadata)
            if auto_vote:
                existing_variant.votes.add(uid)
            return existing_variant

        variant = MovieSourceVariant(
            variant_id=secrets.token_urlsafe(9),
            source_ref=source[:2000],
            created_at=current,
            source_id=str(source_id or "").strip()[:48],
            source_label=" ".join(str(source_label or "").split())[:80],
            file_size=max(0, int(file_size or 0)),
            peers=max(0, int(peers or 0)),
            seeds=max(0, int(seeds or 0)),
            leechers=max(0, int(leechers or 0)),
            metadata=dict(metadata or {}),
            votes={uid} if auto_vote else set(),
        )
        candidate.variants[variant.variant_id] = variant
        if not candidate.selected_variant_id:
            candidate.selected_variant_id = variant.variant_id
        return variant

    def vote_variant(
        self,
        room_id: str,
        candidate_id: str,
        variant_id: str,
        *,
        user_id: int,
        approve: bool = True,
        now: Optional[float] = None,
    ) -> MovieSourceVariant:
        room = self._require_room(room_id)
        current = time.monotonic() if now is None else float(now)
        uid = int(user_id)
        if uid not in self.active_viewers(room, now=current):
            raise PermissionError("Only active Movie Night viewers may vote on source variants.")
        if str(getattr(room, "mode", "watch_party") or "watch_party") == "private":
            raise PermissionError("Private Session does not use viewer voting.")

        candidate = room.candidates.get(str(candidate_id or ""))
        if candidate is None:
            raise LookupError("Movie candidate not found.")
        variant = candidate.variants.get(str(variant_id or ""))
        if variant is None:
            raise LookupError("Movie source variant not found.")

        if approve:
            variant.votes.add(uid)
        else:
            variant.votes.discard(uid)
        return variant

    def ranked_variants(
        self,
        room_id: str,
        candidate_id: str,
        *,
        now: Optional[float] = None,
    ) -> list[MovieSourceVariant]:
        room = self._require_room(room_id)
        candidate = room.candidates.get(str(candidate_id or ""))
        if candidate is None:
            raise LookupError("Movie candidate not found.")

        active = self.active_viewers(room, now=now)
        def _rank(item: MovieSourceVariant) -> tuple[Any, ...]:
            votes = len(item.votes & active)
            health = item.swarm_health
            seeds = int(health["seeds"])
            leechers = int(health["leechers"])
            ratio = float(health["seed_leech_ratio"])
            quality = item.quality_efficiency_key()

            # Room votes remain authoritative once people start choosing.
            # Before votes diverge, the default ordering is swarm-first:
            # live seeds, then seed/leech balance, then verified quality.
            return (
                -votes,
                0 if seeds > 0 else 1,
                item.browser_video_risk_key(),
                item.browser_audio_risk_key(),
                -seeds,
                -ratio,
                leechers,
                tuple(-part for part in quality),
                int(item.file_size),
                float(item.created_at),
            )

        return sorted(candidate.variants.values(), key=_rank)

    def select_variant(
        self,
        room_id: str,
        candidate_id: str,
        *,
        variant_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> MovieSourceVariant:
        room = self._require_room(room_id)
        candidate = room.candidates.get(str(candidate_id or ""))
        if candidate is None:
            raise LookupError("Movie candidate not found.")

        if variant_id:
            variant = candidate.variants.get(str(variant_id))
            if variant is None:
                raise LookupError("Movie source variant not found.")
        else:
            ranked = self.ranked_variants(room_id, candidate_id, now=now)
            if not ranked:
                raise LookupError("No source variants are available for this movie.")
            variant = ranked[0]

        candidate.selected_variant_id = variant.variant_id
        return variant

    def set_room_media(
        self,
        room_id: str,
        *,
        host_id: int,
        stream_token: str,
        candidate_id: str = "",
        variant_id: str = "",
    ) -> MovieNightRoom:
        room = self._require_room(room_id)
        if int(host_id) != int(room.host_id):
            raise PermissionError("Only the Movie Night host may replace room media.")
        room.stream_token = str(stream_token or "")
        room.current_candidate_id = str(candidate_id or "")
        room.current_variant_id = str(variant_id or "")
        for uid, viewer in room.viewers.items():
            viewer.sync_target_position = 0.0
            viewer.sync_requested = False
            viewer.sync_requested_at = 0.0
            if int(uid) == int(room.host_id):
                viewer.sync_ready = True
                viewer.sync_ready_at = time.monotonic()
            else:
                viewer.sync_ready = False
                viewer.sync_ready_at = 0.0
        room.playback_position = 0.0
        room.playback_anchor_monotonic = time.monotonic()
        room.playback_state = "paused"
        return room

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
        if str(getattr(room, "mode", "watch_party") or "watch_party") == "private":
            raise PermissionError("Private Session does not use viewer voting.")
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

        def _rank(item: MovieCandidate) -> tuple[Any, ...]:
            movie_votes = len(item.votes & active)
            variants = self.ranked_variants(
                room_id,
                item.candidate_id,
                now=now,
            )
            if variants:
                best = variants[0]
                health = best.swarm_health
                seeds = int(health["seeds"])
                ratio = float(health["seed_leech_ratio"])
                leechers = int(health["leechers"])
                peers = int(health["peers"])
            else:
                seeds = 0
                ratio = 0.0
                leechers = 0
                peers = 0
            return (
                -movie_votes,
                0 if seeds > 0 else 1,
                -seeds,
                -ratio,
                leechers,
                -peers,
                float(item.created_at),
                item.title.lower(),
            )

        return sorted(room.candidates.values(), key=_rank)

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

    def remove_queued(
        self,
        room_id: str,
        *,
        host_id: int,
        candidate_id: str,
    ) -> MovieNightRoom:
        room = self._require_room(room_id)
        if int(host_id) != int(room.host_id):
            raise PermissionError("Only the Cinema host may manage the queue.")
        candidate_key = str(candidate_id or "")
        try:
            room.queue.remove(candidate_key)
        except ValueError:
            raise LookupError("Queued movie not found.")
        return room

    def move_queued(
        self,
        room_id: str,
        *,
        host_id: int,
        candidate_id: str,
        offset: int,
    ) -> MovieNightRoom:
        room = self._require_room(room_id)
        if int(host_id) != int(room.host_id):
            raise PermissionError("Only the Cinema host may manage the queue.")
        candidate_key = str(candidate_id or "")
        try:
            current_index = room.queue.index(candidate_key)
        except ValueError:
            raise LookupError("Queued movie not found.")

        delta = -1 if int(offset) < 0 else 1 if int(offset) > 0 else 0
        if not delta:
            return room
        target_index = max(0, min(len(room.queue) - 1, current_index + delta))
        if target_index == current_index:
            return room
        room.queue[current_index], room.queue[target_index] = (
            room.queue[target_index],
            room.queue[current_index],
        )
        return room

    def clear_queue(
        self,
        room_id: str,
        *,
        host_id: int,
    ) -> MovieNightRoom:
        room = self._require_room(room_id)
        if int(host_id) != int(room.host_id):
            raise PermissionError("Only the Cinema host may manage the queue.")
        room.queue.clear()
        return room

    def group_buffer_corridor(
        self,
        room_id: str,
        *,
        now: Optional[float] = None,
        max_skew_bytes: int = 64 * 1024 * 1024,
    ) -> Optional[tuple[int, int, int]]:
        room = self._require_room(room_id)
        active_ids = self.buffer_quorum_viewers(room, now=now)
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

    def transfer_host(
        self,
        room_id: str,
        *,
        current_host_id: int,
        new_host_id: int,
        now: Optional[float] = None,
    ) -> MovieNightRoom:
        """Transfer Cinema playback authority without replacing the room.

        The canonical playback position is snapshotted at transfer time, then
        the existing playback state continues from the same point under the new
        host. Private Sessions may transfer only to an active already-authorized
        viewer, so the private access boundary does not widen during handoff.
        """

        room = self._require_room(room_id)
        current = time.monotonic() if now is None else float(now)
        old_host = int(current_host_id)
        new_host = int(new_host_id)

        if old_host != int(room.host_id):
            raise PermissionError("Only the current Cinema host can pass host control.")
        if new_host == old_host:
            raise ValueError("Choose another active viewer to receive host control.")

        active = self.active_viewers(room, now=current)
        if new_host not in active:
            raise PermissionError("Host control can only be passed to an active Cinema viewer.")
        if not self.user_can_access(room, new_host):
            raise PermissionError("Host control can only be passed to an authorized Cinema viewer.")

        position = room.current_position(current)
        room.playback_position = position
        room.playback_anchor_monotonic = current
        room.host_id = new_host
        room.host_last_seen = current

        target = room.viewers.get(new_host)
        if target is None:
            raise PermissionError("The selected viewer is no longer in Movie Night.")
        target.last_seen = current
        target.position_seconds = position
        target.sync_target_position = position
        target.sync_ready = True
        target.sync_ready_at = current
        target.sync_requested = False
        target.sync_requested_at = 0.0

        previous = room.viewers.get(old_host)
        if previous is not None:
            previous.sync_target_position = position
            previous.sync_ready = True
            previous.sync_ready_at = previous.sync_ready_at or current

        # Playback failover votes no longer make sense after a deliberate host
        # handoff. Collaborative search/queue/end votes keep their normal state.
        for vote in room.votes.values():
            if not vote.resolved and vote.action in PLAYBACK_ACTIONS:
                vote.resolved = True
                vote.passed = False

        return room


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
        required = self.required_yes_votes(room, now=now)

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
        elif action == "speed":
            position = room.current_position(now)
            try:
                requested = float(payload.get("rate", 1.0) or 1.0)
            except Exception:
                requested = 1.0
            allowed = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)
            room.playback_position = position
            room.playback_rate = min(
                allowed,
                key=lambda value: abs(value - requested),
            )
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
        try:
            empty_ttl = float(
                str(os.getenv("DANK_MOVIE_NIGHT_EMPTY_ROOM_TTL_SECONDS", "1800") or "1800")
            )
        except Exception:
            empty_ttl = 1800.0
        _MANAGER = MovieNightManager(
            empty_room_ttl_seconds=empty_ttl,
        )
    return _MANAGER


__all__ = [
    "ALL_ACTIONS",
    "PRIVATE_VIEWER_LIMIT",
    "MovieCandidate",
    "MovieSourceVariant",
    "MovieNightManager",
    "MovieNightRoom",
    "PLAYBACK_ACTIONS",
    "PROGRAMMING_ACTIONS",
    "SESSION_ACTIONS",
    "RoomVote",
    "ViewerState",
    "get_movie_night_manager",
    "movie_room_lease_key",
]
