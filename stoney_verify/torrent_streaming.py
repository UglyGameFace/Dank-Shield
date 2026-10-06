from __future__ import annotations

"""Bounded progressive torrent media runtime for Dank Shield.

This module owns torrent lifecycle, playable-file selection, piece priorities,
seek reprioritization, signed stream URLs, and cleanup. It deliberately does not
search torrent indexes or discover copyrighted content. Callers must supply the
magnet or .torrent source they are authorized to use.
"""

import asyncio
import base64
import hashlib
import hmac
import mimetypes
import os
import re
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import parse_qs, quote, urlsplit

from stoney_verify.media_metadata import (
    parse_release_name,
    probe_media_file,
)
from stoney_verify.startup_guards.process_health import current_rss_mb


_PLAYABLE_EXTENSIONS = (
    ".mp4",
    ".m4v",
    ".webm",
    ".mov",
    ".mkv",
    ".avi",
    ".mpeg",
    ".mpg",
)
_MAGNET_RE = re.compile(r"magnet:\?[^\s<>]+", re.IGNORECASE)
_BTIH_RE = re.compile(r"(?:^|&)xt=urn:btih:([^&]+)", re.IGNORECASE)

_MANAGER: Optional["TorrentMediaManager"] = None


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default)) or default).strip())
    except Exception:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.getenv(name, str(default)) or default).strip())
    except Exception:
        value = float(default)
    return max(float(minimum), min(float(maximum), value))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _safe_name(value: str, fallback: str = "video.mp4") -> str:
    raw = str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", raw).strip(" .")
    if not cleaned:
        cleaned = fallback
    return cleaned[:180]


def find_magnet(text: str) -> str:
    match = _MAGNET_RE.search(str(text or ""))
    return match.group(0) if match else ""


def magnet_identity(magnet: str) -> str:
    raw = str(magnet or "").strip()
    if not raw.lower().startswith("magnet:?"):
        return ""
    try:
        query = parse_qs(urlsplit(raw).query)
    except Exception:
        return ""

    values = [
        str(item or "").strip()
        for key, entries in query.items()
        if str(key or "").casefold() == "xt"
        for item in list(entries or [])
    ]

    # Prefer v1 identity for hybrid magnets so older and hybrid references reuse
    # the same active session when they point at the same v1 swarm.
    for text in values:
        if not text.lower().startswith("urn:btih:"):
            continue
        value = text[9:].strip()
        if re.fullmatch(r"[A-Fa-f0-9]{40}", value):
            normalized = value.lower()
            if normalized != "0" * 40:
                return f"btih:{normalized}"
            continue
        if re.fullmatch(r"[A-Za-z2-7]{32}", value):
            try:
                decoded = base64.b32decode(value.upper())
            except Exception:
                continue
            if decoded != b"\x00" * 20:
                return f"btih:{decoded.hex()}"

    for text in values:
        if not text.lower().startswith("urn:btmh:"):
            continue
        value = text[9:].strip().lower()
        if re.fullmatch(r"1220[A-Fa-f0-9]{64}", value):
            if value[4:] == "0" * 64:
                continue
            return f"btmh:{value}"
    return ""


def is_torrent_filename(filename: str) -> bool:
    return str(filename or "").strip().lower().endswith(".torrent")


def is_playable_filename(filename: str) -> bool:
    lowered = str(filename or "").strip().lower()
    return lowered.endswith(_PLAYABLE_EXTENSIONS)


def media_content_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(str(filename or ""))
    if guessed and guessed.startswith("video/"):
        return guessed
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix == ".mkv":
        return "video/x-matroska"
    if suffix == ".avi":
        return "video/x-msvideo"
    return "application/octet-stream"


def parse_http_range(header: str, size: int) -> tuple[int, int, bool]:
    total = max(0, int(size))
    raw = str(header or "").strip()
    if total <= 0:
        raise ValueError("empty media file")
    if not raw:
        return 0, total - 1, False
    if not raw.lower().startswith("bytes="):
        raise ValueError("unsupported range unit")

    spec = raw[6:].strip()
    if "," in spec:
        raise ValueError("multiple ranges are not supported")
    if "-" not in spec:
        raise ValueError("malformed byte range")

    left, right = spec.split("-", 1)
    if not left:
        try:
            suffix = int(right)
        except Exception as exc:
            raise ValueError("malformed suffix range") from exc
        if suffix <= 0:
            raise ValueError("invalid suffix range")
        start = max(0, total - suffix)
        return start, total - 1, True

    try:
        start = int(left)
    except Exception as exc:
        raise ValueError("malformed range start") from exc
    if start < 0 or start >= total:
        raise ValueError("range start outside file")

    if right:
        try:
            end = int(right)
        except Exception as exc:
            raise ValueError("malformed range end") from exc
    else:
        end = total - 1
    if end < start:
        raise ValueError("range end before start")
    return start, min(end, total - 1), True


class TorrentSessionUnavailableError(RuntimeError):
    """The Python session still exists but its libtorrent handle is unusable."""


@dataclass(frozen=True)
class TorrentFileCandidate:
    index: int
    path: str
    size: int


@dataclass(frozen=True)
class TorrentBufferPlan:
    seek: bool
    target_seconds: float
    target_bytes: int
    startup_wait_end: int
    consume_rate: float
    download_rate: float


@dataclass(frozen=True)
class MediaCapacitySnapshot:
    current_rss_mb: float | None
    process_limit_mb: int
    protected_reserve_mb: int
    estimated_session_mb: int
    memory_samples: int
    hard_session_limit: int
    soft_session_limit: int
    per_guild_limit: int
    active_unique_sessions: int
    in_flight_starts: int
    total_leases: int
    shared_sessions: int
    memory_headroom_mb: float | None
    memory_slots_available: int
    session_slots_available: int
    guild_unique_sessions: int
    guild_slots_available: int
    free_disk_bytes: int
    disk_reserve_bytes: int
    committed_file_bytes: int
    admission_allowed: bool
    blocker: str = ""


@dataclass
class TorrentStreamSession:
    token: str
    secret: str
    owner_id: int
    guild_id: int
    source_kind: str
    source_identity: str
    save_root: Path
    handle: Any
    info: Any
    file_index: int
    file_path: str
    file_name: str
    file_size: int
    file_offset: int
    piece_length: int
    first_piece: int
    last_piece: int
    created_at: float
    last_access: float
    startup_started_at: float = 0.0
    metadata_ready_at: float = 0.0
    candidates: tuple[TorrentFileCandidate, ...] = ()
    smoothed_download_rate: float = 0.0
    smoothed_consume_rate: float = 0.0
    last_request_at: float = 0.0
    last_request_bytes: int = 0
    last_request_end: int = -1
    adaptive_readahead_bytes: int = 0
    adaptive_target_seconds: float = 0.0
    stall_count: int = 0
    consumer_playback: dict[str, dict[str, float | int]] = field(default_factory=dict)
    release_metadata: dict[str, Any] = field(default_factory=dict)
    verified_metadata: dict[str, Any] = field(default_factory=dict)
    metadata_probe_running: bool = False
    metadata_probe_attempts: int = 0
    metadata_last_probe_at: float = 0.0
    leases: set[str] = field(default_factory=set)
    lease_guild_ids: dict[str, int] = field(default_factory=dict)
    unleased_hold: bool = False
    reuse_hits: int = 0

    @property
    def absolute_path(self) -> Path:
        root = self.save_root.resolve()
        path = (self.save_root / self.file_path).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise RuntimeError("Torrent file path escaped the session directory.") from exc
        return path


class TorrentMediaManager:
    def __init__(self, *, lt_module: Any | None = None) -> None:
        self.lt = lt_module or self._load_libtorrent()
        self.root = Path(
            os.getenv(
                "DANK_TORRENT_DATA_DIR",
                str(Path(os.getenv("DANK_DATA_DIR", "data")) / "torrent_streams"),
            )
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_sessions = _env_int("DANK_TORRENT_MAX_SESSIONS", 4, minimum=1, maximum=16)
        self.soft_session_limit = _env_int(
            "DANK_TORRENT_SOFT_SESSION_LIMIT",
            2,
            minimum=1,
            maximum=self.max_sessions,
        )
        self.allow_burst_sessions = _env_bool("DANK_TORRENT_ALLOW_BURST", False)
        self.process_memory_limit_mb = _env_int(
            "DANK_PROCESS_MEMORY_LIMIT_MB",
            1495,
            minimum=256,
            maximum=262144,
        )
        self.protected_memory_reserve_mb = _env_int(
            "DANK_MOVIE_NIGHT_MEMORY_RESERVE_MB",
            350,
            minimum=128,
            maximum=16384,
        )
        self.estimated_session_memory_mb = _env_int(
            "DANK_TORRENT_ESTIMATED_SESSION_MB",
            96,
            minimum=32,
            maximum=2048,
        )
        self._adaptive_session_memory_mb = float(self.estimated_session_memory_mb)
        self._session_memory_samples = 0
        self.max_unique_per_guild = _env_int(
            "DANK_TORRENT_MAX_UNIQUE_PER_GUILD",
            1,
            minimum=1,
            maximum=self.max_sessions,
        )
        self.max_metadata_bytes = _env_int(
            "DANK_TORRENT_MAX_METADATA_BYTES",
            4 * 1024 * 1024,
            minimum=64 * 1024,
            maximum=16 * 1024 * 1024,
        )
        self.max_file_bytes = _env_int(
            "DANK_TORRENT_MAX_FILE_BYTES",
            25 * 1024 * 1024 * 1024,
            minimum=8 * 1024 * 1024,
            maximum=100 * 1024 * 1024 * 1024,
        )
        self.max_torrent_bytes = _env_int(
            "DANK_TORRENT_MAX_TOTAL_BYTES",
            50 * 1024 * 1024 * 1024,
            minimum=16 * 1024 * 1024,
            maximum=200 * 1024 * 1024 * 1024,
        )
        self.disk_reserve_bytes = _env_int(
            "DANK_TORRENT_DISK_RESERVE_BYTES",
            64 * 1024 * 1024 * 1024,
            minimum=2 * 1024 * 1024 * 1024,
            maximum=1024 * 1024 * 1024 * 1024,
        )
        self.readahead_bytes = _env_int(
            "DANK_TORRENT_READAHEAD_BYTES",
            16 * 1024 * 1024,
            minimum=2 * 1024 * 1024,
            maximum=128 * 1024 * 1024,
        )
        self.min_readahead_bytes = _env_int(
            "DANK_TORRENT_MIN_READAHEAD_BYTES",
            4 * 1024 * 1024,
            minimum=1 * 1024 * 1024,
            maximum=64 * 1024 * 1024,
        )
        self.max_readahead_bytes = _env_int(
            "DANK_TORRENT_MAX_READAHEAD_BYTES",
            64 * 1024 * 1024,
            minimum=self.min_readahead_bytes,
            maximum=256 * 1024 * 1024,
        )
        self.buffer_target_seconds = _env_float(
            "DANK_TORRENT_TARGET_BUFFER_SECONDS",
            30.0,
            minimum=8.0,
            maximum=120.0,
        )
        self.buffer_max_seconds = _env_float(
            "DANK_TORRENT_MAX_BUFFER_SECONDS",
            75.0,
            minimum=self.buffer_target_seconds,
            maximum=180.0,
        )
        self.min_consume_rate = _env_int(
            "DANK_TORRENT_MIN_ESTIMATED_PLAYBACK_BYTES_PER_SECOND",
            512 * 1024,
            minimum=128 * 1024,
            maximum=8 * 1024 * 1024,
        )
        self.bootstrap_bytes = _env_int(
            "DANK_TORRENT_BOOTSTRAP_BYTES",
            8 * 1024 * 1024,
            minimum=1 * 1024 * 1024,
            maximum=64 * 1024 * 1024,
        )
        self.tail_probe_bytes = _env_int(
            "DANK_TORRENT_TAIL_PROBE_BYTES",
            4 * 1024 * 1024,
            minimum=1 * 1024 * 1024,
            maximum=32 * 1024 * 1024,
        )
        self.buffer_wait_seconds = _env_float(
            "DANK_TORRENT_BUFFER_WAIT_SECONDS",
            20.0,
            minimum=3.0,
            maximum=90.0,
        )
        self.time_critical_base_deadline_ms = _env_int(
            "DANK_TORRENT_TIME_CRITICAL_BASE_DEADLINE_MS",
            500,
            minimum=100,
            maximum=5000,
        )
        self.time_critical_step_ms = _env_int(
            "DANK_TORRENT_TIME_CRITICAL_STEP_MS",
            350,
            minimum=50,
            maximum=5000,
        )
        self.metadata_wait_seconds = _env_float(
            "DANK_TORRENT_METADATA_WAIT_SECONDS",
            30.0,
            minimum=5.0,
            maximum=120.0,
        )
        self.idle_ttl_seconds = _env_float(
            "DANK_TORRENT_IDLE_TTL_SECONDS",
            1800.0,
            minimum=120.0,
            maximum=21600.0,
        )
        self.public_base_url = str(os.getenv("DANK_MEDIA_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
        self.stream_secret = str(
            os.getenv("DANK_TORRENT_STREAM_SECRET", "") or ""
        ).strip()
        self._sessions: dict[str, TorrentStreamSession] = {}
        self._identity_index: dict[str, str] = {}
        self._identity_locks: dict[str, asyncio.Lock] = {}
        self._starting = 0
        self._starting_by_guild: dict[int, int] = {}
        self._replacements_in_flight: set[str] = set()
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task[Any]] = None
        self._session = self._build_libtorrent_session()

    @staticmethod
    def _load_libtorrent() -> Any:
        try:
            import libtorrent as lt
        except Exception as exc:
            raise RuntimeError(
                "Torrent streaming requires the pinned libtorrent Python runtime."
            ) from exc
        return lt

    def _build_libtorrent_session(self) -> Any:
        settings = {
            "listen_interfaces": str(
                os.getenv("DANK_TORRENT_LISTEN_INTERFACES", "0.0.0.0:6881,[::]:6881")
                or "0.0.0.0:6881,[::]:6881"
            ),
            "enable_dht": True,
            "enable_lsd": False,
            "enable_upnp": False,
            "enable_natpmp": False,
            "connections_limit": _env_int(
                "DANK_TORRENT_CONNECTION_LIMIT", 200, minimum=20, maximum=300
            ),
            # New Cinema torrents should fan out to useful peers quickly instead
            # of spending their first several seconds discovering the swarm one
            # connection tick at a time.
            "connection_speed": _env_int(
                "DANK_TORRENT_CONNECTION_SPEED", 80, minimum=10, maximum=200
            ),
            "torrent_connect_boost": _env_int(
                "DANK_TORRENT_CONNECT_BOOST", 80, minimum=0, maximum=255
            ),
            "peer_connect_timeout": _env_int(
                "DANK_TORRENT_PEER_CONNECT_TIMEOUT_SECONDS", 8, minimum=3, maximum=30
            ),
            "active_downloads": self.max_sessions,
            "active_seeds": 0,
            "active_limit": self.max_sessions,
            "download_rate_limit": _env_int(
                "DANK_TORRENT_DOWNLOAD_RATE_BYTES",
                64 * 1024 * 1024,
                minimum=128 * 1024,
                maximum=64 * 1024 * 1024,
            ),
            "upload_rate_limit": _env_int(
                "DANK_TORRENT_UPLOAD_RATE_BYTES",
                512 * 1024,
                minimum=16 * 1024,
                maximum=8 * 1024 * 1024,
            ),
        }
        return self.lt.session(settings)

    def _identity_lock(self, identity: str) -> asyncio.Lock:
        key = str(identity or "").strip()
        lock = self._identity_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._identity_locks[key] = lock
        return lock

    def _attach_consumer(
        self,
        session: TorrentStreamSession,
        lease_key: str,
        *,
        guild_id: int = 0,
    ) -> None:
        key = str(lease_key or "").strip()[:180]
        if key:
            session.leases.add(key)
            if int(guild_id) > 0:
                session.lease_guild_ids[key] = int(guild_id)
        else:
            # Legacy/untracked consumers (for example Share Router links) do not
            # have an explicit release callback. Keep the session eligible for
            # idle cleanup, but do not delete it merely because Movie Night
            # releases its final tracked lease.
            session.unleased_hold = True
        session.last_access = time.monotonic()

    @staticmethod
    def session_usable(session: TorrentStreamSession) -> bool:
        handle = getattr(session, "handle", None)
        if handle is None:
            return False
        checker = getattr(handle, "is_valid", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        try:
            handle.status()
            return True
        except Exception:
            return False

    @staticmethod
    def _raise_unavailable_handle(exc: Exception) -> None:
        message = str(exc or "")
        if "invalid torrent handle" in message.casefold():
            raise TorrentSessionUnavailableError(
                "The torrent media session is no longer available."
            ) from exc
        raise exc


    def _reusable_session_unlocked(self, identity: str) -> Optional[TorrentStreamSession]:
        token = self._identity_index.get(str(identity or ""))
        if not token:
            return None
        session = self._sessions.get(token)
        if session is None or session.source_identity != identity:
            self._identity_index.pop(str(identity or ""), None)
            return None
        return session

    async def _reuse_session(
        self,
        identity: str,
        *,
        lease_key: str = "",
        guild_id: int = 0,
    ) -> Optional[TorrentStreamSession]:
        async with self._lock:
            session = self._reusable_session_unlocked(identity)
            if session is None:
                return None
            self._attach_consumer(
                session,
                lease_key,
                guild_id=int(guild_id),
            )
            session.reuse_hits += 1
            return session

    def _replacement_is_releasable(
        self,
        replace_token: str,
        lease_key: str,
    ) -> bool:
        token = str(replace_token or "")
        if not token:
            return False
        session = self._sessions.get(token)
        if session is None or bool(getattr(session, "unleased_hold", False)):
            return False
        leases = set(getattr(session, "leases", set()) or set())
        key = str(lease_key or "").strip()
        if key:
            return not leases or leases <= {key}
        return not leases

    def _committed_file_bytes(self, *, exclude_token: str = "") -> int:
        excluded = str(exclude_token or "")
        return sum(
            max(0, int(getattr(session, "file_size", 0) or 0))
            for token, session in self._sessions.items()
            if token != excluded
        )

    def _effective_session_memory_mb(self) -> int:
        configured = max(
            32,
            min(2048, int(self.estimated_session_memory_mb)),
        )
        learned = max(
            32,
            min(2048, int(round(float(self._adaptive_session_memory_mb)))),
        )
        # The operator-configured estimate is a safety floor. Adaptive learning
        # may raise admission cost when production observations are heavier,
        # but it must never silently undercut the configured reserve model.
        return max(configured, learned)

    def _record_session_memory_observation(
        self,
        before_rss_mb: float | None,
    ) -> None:
        if before_rss_mb is None:
            return
        # Overlapping starts make attribution ambiguous; current RSS still
        # protects admission, but skip learning a misleading per-session delta.
        if int(self._starting) > 1:
            return
        after = current_rss_mb()
        if after is None:
            return
        delta = float(after) - float(before_rss_mb)
        if delta < 8.0:
            return
        observed = max(32.0, min(1024.0, delta))
        alpha = 0.25
        self._adaptive_session_memory_mb = (
            float(self._adaptive_session_memory_mb) * (1.0 - alpha)
            + observed * alpha
        )
        self._session_memory_samples += 1

    @staticmethod
    def _consumer_guild_ids(session: TorrentStreamSession) -> set[int]:
        ids = {
            int(value)
            for value in dict(getattr(session, "lease_guild_ids", {}) or {}).values()
            if int(value) > 0
        }
        if bool(getattr(session, "unleased_hold", False)) and int(session.guild_id) > 0:
            ids.add(int(session.guild_id))
        return ids

    def _guild_unique_session_count(
        self,
        guild_id: int,
        *,
        exclude_token: str = "",
    ) -> int:
        gid = int(guild_id or 0)
        if gid <= 0:
            return 0
        excluded = str(exclude_token or "")
        return sum(
            1
            for token, session in self._sessions.items()
            if token != excluded and gid in self._consumer_guild_ids(session)
        )

    def _capacity_snapshot_unlocked(
        self,
        *,
        replace_token: str = "",
        lease_key: str = "",
        expected_file_bytes: int = 0,
        request_guild_id: int = 0,
    ) -> MediaCapacitySnapshot:
        rss = current_rss_mb()
        process_limit = int(self.process_memory_limit_mb)
        reserve = int(self.protected_memory_reserve_mb)
        estimate = int(self._effective_session_memory_mb())

        releasable = self._replacement_is_releasable(replace_token, lease_key)
        effective_sessions = max(
            0,
            len(self._sessions) - (1 if releasable else 0),
        )
        configured_limit = (
            int(self.max_sessions)
            if self.allow_burst_sessions
            else min(int(self.max_sessions), int(self.soft_session_limit))
        )
        session_slots = max(
            0,
            configured_limit - effective_sessions - int(self._starting),
        )

        headroom: float | None
        memory_slots: int
        if rss is None:
            headroom = None
            memory_slots = 0
        else:
            headroom = float(process_limit) - float(reserve) - float(rss)
            # Reserve estimated memory for starts already admitted but not yet
            # fully reflected in RSS so concurrent metadata/session startup
            # cannot collectively cross the core-bot safety reserve.
            memory_slots = max(
                0,
                int(headroom // max(1, estimate)) - int(self._starting),
            )

        try:
            usage = shutil.disk_usage(self.root)
            free_disk = int(usage.free)
        except Exception:
            free_disk = 0

        exclude = str(replace_token or "") if releasable else ""
        committed = self._committed_file_bytes(exclude_token=exclude)
        guild_unique = self._guild_unique_session_count(
            int(request_guild_id),
            exclude_token=exclude,
        )
        guild_in_flight = (
            int(self._starting_by_guild.get(int(request_guild_id), 0))
            if int(request_guild_id) > 0
            else 0
        )
        guild_slots = (
            max(
                0,
                int(self.max_unique_per_guild)
                - int(guild_unique)
                - int(guild_in_flight),
            )
            if int(request_guild_id) > 0
            else int(self.max_unique_per_guild)
        )
        required_disk = (
            int(self.disk_reserve_bytes)
            + int(committed)
            + max(0, int(expected_file_bytes))
        )

        total_leases = sum(
            len(set(getattr(session, "leases", set()) or set()))
            for session in self._sessions.values()
        )
        shared_sessions = sum(
            1
            for session in self._sessions.values()
            if len(set(getattr(session, "leases", set()) or set())) > 1
        )

        blocker = ""
        if rss is None:
            blocker = "Current process RSS is unavailable; refusing a new media session safely."
        elif memory_slots <= 0:
            blocker = (
                f"Movie Night protected memory reserve would be crossed "
                f"(RSS {rss:.0f} MB, limit {process_limit} MB, reserve {reserve} MB)."
            )
        elif session_slots <= 0:
            if self.allow_burst_sessions:
                blocker = (
                    f"Movie Night reached its hard {self.max_sessions}-unique-torrent limit."
                )
            else:
                blocker = (
                    f"Movie Night reached its conservative {self.soft_session_limit}-unique-torrent "
                    "soft limit. Existing identical torrents can still be shared."
                )
        elif int(request_guild_id) > 0 and guild_slots <= 0:
            blocker = (
                f"This server reached its {self.max_unique_per_guild}-unique-torrent "
                "Movie Night limit. Existing identical torrents can still be shared."
            )
        elif free_disk <= 0:
            blocker = "Free disk could not be measured; refusing a new torrent safely."
        elif free_disk < required_disk:
            blocker = (
                "Torrent storage reserve would be crossed by another unique media session."
            )

        return MediaCapacitySnapshot(
            current_rss_mb=rss,
            process_limit_mb=process_limit,
            protected_reserve_mb=reserve,
            estimated_session_mb=estimate,
            memory_samples=int(self._session_memory_samples),
            hard_session_limit=int(self.max_sessions),
            soft_session_limit=int(self.soft_session_limit),
            per_guild_limit=int(self.max_unique_per_guild),
            active_unique_sessions=len(self._sessions),
            in_flight_starts=int(self._starting),
            total_leases=int(total_leases),
            shared_sessions=int(shared_sessions),
            memory_headroom_mb=headroom,
            memory_slots_available=int(memory_slots),
            session_slots_available=int(session_slots),
            guild_unique_sessions=int(guild_unique),
            guild_slots_available=int(guild_slots),
            free_disk_bytes=int(free_disk),
            disk_reserve_bytes=int(self.disk_reserve_bytes),
            committed_file_bytes=int(committed),
            admission_allowed=not blocker,
            blocker=blocker,
        )

    def capacity_snapshot(self, *, guild_id: int = 0) -> MediaCapacitySnapshot:
        return self._capacity_snapshot_unlocked(
            request_guild_id=int(guild_id),
        )

    def capacity_status(self, *, guild_id: int = 0) -> dict[str, Any]:
        snap = self.capacity_snapshot(guild_id=int(guild_id))
        return {
            "current_rss_mb": snap.current_rss_mb,
            "process_limit_mb": snap.process_limit_mb,
            "protected_reserve_mb": snap.protected_reserve_mb,
            "estimated_session_mb": snap.estimated_session_mb,
            "memory_samples": snap.memory_samples,
            "hard_session_limit": snap.hard_session_limit,
            "soft_session_limit": snap.soft_session_limit,
            "per_guild_limit": snap.per_guild_limit,
            "allow_burst": bool(self.allow_burst_sessions),
            "active_unique_sessions": snap.active_unique_sessions,
            "in_flight_starts": snap.in_flight_starts,
            "total_leases": snap.total_leases,
            "shared_sessions": snap.shared_sessions,
            "memory_headroom_mb": snap.memory_headroom_mb,
            "memory_slots_available": snap.memory_slots_available,
            "session_slots_available": snap.session_slots_available,
            "guild_unique_sessions": snap.guild_unique_sessions,
            "guild_in_flight": (
                int(self._starting_by_guild.get(int(guild_id), 0))
                if int(guild_id) > 0
                else 0
            ),
            "guild_slots_available": snap.guild_slots_available,
            "free_disk_bytes": snap.free_disk_bytes,
            "disk_reserve_bytes": snap.disk_reserve_bytes,
            "committed_file_bytes": snap.committed_file_bytes,
            "admission_allowed": snap.admission_allowed,
            "blocker": snap.blocker,
        }

    def _assert_disk_capacity(
        self,
        expected_file_bytes: int,
        *,
        replace_token: str = "",
        lease_key: str = "",
    ) -> None:
        snap = self._capacity_snapshot_unlocked(
            replace_token=replace_token,
            lease_key=lease_key,
            expected_file_bytes=max(0, int(expected_file_bytes)),
        )
        if snap.free_disk_bytes <= 0:
            raise RuntimeError("Movie Night cannot verify free disk for this torrent.")
        required = (
            snap.disk_reserve_bytes
            + snap.committed_file_bytes
            + max(0, int(expected_file_bytes))
        )
        if snap.free_disk_bytes < required:
            raise RuntimeError(
                "Movie Night disk admission rejected this release: "
                f"{snap.free_disk_bytes / (1024 ** 3):.1f} GiB free, "
                f"{snap.disk_reserve_bytes / (1024 ** 3):.1f} GiB protected reserve, "
                f"{snap.committed_file_bytes / (1024 ** 3):.1f} GiB already committed."
            )

    async def start_magnet(
        self,
        magnet: str,
        *,
        guild_id: int,
        owner_id: int,
        replace_token: str = "",
        lease_key: str = "",
    ) -> TorrentStreamSession:
        raw = str(magnet or "").strip()
        if len(raw) > 8192:
            raise ValueError("Magnet link exceeds the configured input limit.")
        identity = magnet_identity(raw)
        if not identity:
            raise ValueError("That is not a valid BitTorrent magnet link.")

        startup_started_at = time.monotonic()
        await self.cleanup_expired()
        async with self._identity_lock(identity):
            existing = await self._reuse_session(
                identity,
                lease_key=lease_key,
                guild_id=int(guild_id),
            )
            if existing is not None:
                return existing

            start_rss_mb = current_rss_mb()
            await self._reserve_start(
                replace_token=replace_token,
                lease_key=lease_key,
                guild_id=int(guild_id),
            )
            token = secrets.token_urlsafe(18)
            save_root = Path(tempfile.mkdtemp(prefix=f"{token}-", dir=str(self.root)))
            handle = None
            try:
                atp = self.lt.parse_magnet_uri(raw)
                atp.save_path = str(save_root)
                handle = self._session.add_torrent(atp)
                info = await self._wait_metadata(handle)
                metadata_ready_at = time.monotonic()
                return await self._finalize_session(
                    token=token,
                    save_root=save_root,
                    handle=handle,
                    info=info,
                    guild_id=guild_id,
                    owner_id=owner_id,
                    source_kind="magnet",
                    source_identity=identity,
                    lease_key=lease_key,
                    replace_token=replace_token,
                    start_rss_mb=start_rss_mb,
                    startup_started_at=startup_started_at,
                    metadata_ready_at=metadata_ready_at,
                )
            except Exception:
                self._safe_remove_handle(handle)
                shutil.rmtree(save_root, ignore_errors=True)
                raise
            finally:
                await self._release_start(
                    replace_token=replace_token,
                    guild_id=int(guild_id),
                )

    async def start_torrent_bytes(
        self,
        payload: bytes,
        *,
        guild_id: int,
        owner_id: int,
        replace_token: str = "",
        lease_key: str = "",
    ) -> TorrentStreamSession:
        data = bytes(payload or b"")
        if not data or len(data) > self.max_metadata_bytes:
            raise ValueError("The .torrent metadata file is empty or exceeds the configured limit.")

        startup_started_at = time.monotonic()
        await self.cleanup_expired()
        token = secrets.token_urlsafe(18)
        save_root = Path(tempfile.mkdtemp(prefix=f"{token}-", dir=str(self.root)))
        torrent_path = save_root / "source.torrent"
        torrent_path.write_bytes(data)
        handle = None
        reserved = False
        try:
            info = self.lt.torrent_info(str(torrent_path))
            metadata_ready_at = time.monotonic()
            source_identity = self._info_identity(info)
            async with self._identity_lock(source_identity):
                existing = await self._reuse_session(
                    source_identity,
                    lease_key=lease_key,
                    guild_id=int(guild_id),
                )
                if existing is not None:
                    shutil.rmtree(save_root, ignore_errors=True)
                    return existing

                start_rss_mb = current_rss_mb()
                await self._reserve_start(
                    replace_token=replace_token,
                    lease_key=lease_key,
                    guild_id=int(guild_id),
                )
                reserved = True
                atp = self.lt.add_torrent_params()
                atp.ti = info
                atp.save_path = str(save_root)
                handle = self._session.add_torrent(atp)
                return await self._finalize_session(
                    token=token,
                    save_root=save_root,
                    handle=handle,
                    info=info,
                    guild_id=guild_id,
                    owner_id=owner_id,
                    source_kind="torrent",
                    source_identity=source_identity,
                    lease_key=lease_key,
                    replace_token=replace_token,
                    start_rss_mb=start_rss_mb,
                    startup_started_at=startup_started_at,
                    metadata_ready_at=metadata_ready_at,
                )
        except Exception:
            self._safe_remove_handle(handle)
            shutil.rmtree(save_root, ignore_errors=True)
            raise
        finally:
            if reserved:
                await self._release_start(
                    replace_token=replace_token,
                    guild_id=int(guild_id),
                )

    async def _reserve_start(
        self,
        *,
        replace_token: str = "",
        lease_key: str = "",
        guild_id: int = 0,
    ) -> None:
        token = str(replace_token or "")
        async with self._lock:
            if token and token in self._replacements_in_flight:
                raise RuntimeError("This Movie Night media replacement is already in progress.")

            snapshot = self._capacity_snapshot_unlocked(
                replace_token=token,
                lease_key=lease_key,
                request_guild_id=int(guild_id),
            )
            if not snapshot.admission_allowed:
                raise RuntimeError(snapshot.blocker)

            if token and self._replacement_is_releasable(token, lease_key):
                self._replacements_in_flight.add(token)
            self._starting += 1
            gid = int(guild_id or 0)
            if gid > 0:
                self._starting_by_guild[gid] = (
                    int(self._starting_by_guild.get(gid, 0)) + 1
                )

    async def _release_start(
        self,
        *,
        replace_token: str = "",
        guild_id: int = 0,
    ) -> None:
        token = str(replace_token or "")
        async with self._lock:
            self._starting = max(0, self._starting - 1)
            gid = int(guild_id or 0)
            if gid > 0:
                remaining = max(
                    0,
                    int(self._starting_by_guild.get(gid, 0)) - 1,
                )
                if remaining:
                    self._starting_by_guild[gid] = remaining
                else:
                    self._starting_by_guild.pop(gid, None)
            if token:
                self._replacements_in_flight.discard(token)

    def ensure_cleanup_task(self) -> None:
        try:
            if self._cleanup_task is not None and not self._cleanup_task.done():
                return
            self._cleanup_task = asyncio.create_task(
                self._cleanup_loop(),
                name="torrent_stream_cleanup",
            )
        except RuntimeError:
            return

    async def _cleanup_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(60.0)
                await self.cleanup_expired()
        except asyncio.CancelledError:
            return

    async def _wait_metadata(self, handle: Any) -> Any:
        deadline = time.monotonic() + self.metadata_wait_seconds
        while time.monotonic() < deadline:
            if bool(handle.has_metadata()):
                info = handle.torrent_file()
                if info is not None:
                    return info
            status = handle.status()
            error = str(getattr(status, "error", "") or "").strip()
            if error:
                raise RuntimeError(f"Torrent metadata failed: {error}")
            await asyncio.sleep(0.25)
        raise TimeoutError("Timed out waiting for torrent metadata.")

    def _info_identity(self, info: Any) -> str:
        try:
            hashes = info.info_hashes()
            v1 = str(getattr(hashes, "v1", "") or "").strip().lower()
            if re.fullmatch(r"[a-f0-9]{40}", v1) and v1 != "0" * 40:
                return f"btih:{v1}"

            v2 = str(getattr(hashes, "v2", "") or "").strip().lower()
            if re.fullmatch(r"[a-f0-9]{64}", v2) and v2 != "0" * 64:
                return f"btmh:1220{v2}"
        except Exception:
            pass

        try:
            legacy = str(info.info_hash()).strip().lower()
            if legacy:
                return f"btih:{legacy}"
        except Exception:
            pass
        return f"torrent:{secrets.token_hex(12)}"

    def _playable_candidates(self, info: Any) -> list[TorrentFileCandidate]:
        files = info.files()
        count = int(files.num_files())
        candidates: list[TorrentFileCandidate] = []
        total = 0
        for index in range(count):
            size = int(files.file_size(index))
            total += max(0, size)
            path = str(files.file_path(index))
            if size > 0 and size <= self.max_file_bytes and is_playable_filename(path):
                candidates.append(TorrentFileCandidate(index=index, path=path, size=size))
        if total > self.max_torrent_bytes:
            raise ValueError("Torrent exceeds the configured total disk budget.")
        return sorted(candidates, key=lambda item: item.size, reverse=True)

    async def _finalize_session(
        self,
        *,
        token: str,
        save_root: Path,
        handle: Any,
        info: Any,
        guild_id: int,
        owner_id: int,
        source_kind: str,
        source_identity: str,
        lease_key: str = "",
        replace_token: str = "",
        start_rss_mb: float | None = None,
        startup_started_at: float = 0.0,
        metadata_ready_at: float = 0.0,
    ) -> TorrentStreamSession:
        candidates = self._playable_candidates(info)
        if not candidates:
            raise ValueError("This torrent does not contain a supported playable video file.")

        selected = candidates[0]
        self._assert_disk_capacity(
            selected.size,
            replace_token=replace_token,
            lease_key=lease_key,
        )
        piece_length = int(info.piece_length())
        if piece_length <= 0:
            raise RuntimeError("Torrent metadata reported an invalid piece length.")

        session_created_at = time.monotonic()
        session = TorrentStreamSession(
            token=token,
            secret=secrets.token_urlsafe(24),
            owner_id=int(owner_id),
            guild_id=int(guild_id),
            source_kind=source_kind,
            source_identity=source_identity,
            save_root=save_root,
            handle=handle,
            info=info,
            candidates=tuple(candidates),
            file_index=selected.index,
            file_path=selected.path,
            file_name=_safe_name(selected.path),
            file_size=selected.size,
            file_offset=0,
            piece_length=piece_length,
            first_piece=0,
            last_piece=0,
            created_at=session_created_at,
            last_access=session_created_at,
            startup_started_at=(
                float(startup_started_at)
                if float(startup_started_at or 0.0) > 0
                else session_created_at
            ),
            metadata_ready_at=(
                float(metadata_ready_at)
                if float(metadata_ready_at or 0.0) > 0
                else session_created_at
            ),
            leases={str(lease_key).strip()[:180]} if str(lease_key or "").strip() else set(),
            lease_guild_ids=(
                {str(lease_key).strip()[:180]: int(guild_id)}
                if str(lease_key or "").strip() and int(guild_id) > 0
                else {}
            ),
            unleased_hold=not bool(str(lease_key or "").strip()),
        )
        self._select_candidate(session, selected)

        async with self._lock:
            self._sessions[token] = session
            self._identity_index[source_identity] = token
        self._record_session_memory_observation(start_rss_mb)
        return session

    def _select_candidate(
        self,
        session: TorrentStreamSession,
        selected: TorrentFileCandidate,
    ) -> None:
        files = session.info.files()
        priorities = [0] * int(files.num_files())
        priorities[selected.index] = 1
        session.handle.prioritize_files(priorities)

        session.file_index = int(selected.index)
        session.file_path = str(selected.path)
        session.file_name = _safe_name(selected.path)
        session.file_size = int(selected.size)
        session.file_offset = int(files.file_offset(selected.index))
        session.release_metadata = parse_release_name(session.file_name)
        session.verified_metadata = {}
        session.metadata_probe_running = False
        session.metadata_probe_attempts = 0
        session.metadata_last_probe_at = 0.0
        session.first_piece = session.file_offset // session.piece_length
        session.last_piece = (
            session.file_offset + session.file_size - 1
        ) // session.piece_length
        session.last_access = time.monotonic()

        self.prioritize_range(
            session,
            0,
            min(session.file_size - 1, self.bootstrap_bytes - 1),
        )
        if session.file_size > self.tail_probe_bytes:
            self.prioritize_range(
                session,
                max(0, session.file_size - self.tail_probe_bytes),
                session.file_size - 1,
                readahead=False,
                time_critical=False,
            )

    async def select_file(
        self,
        token: str,
        file_index: int,
        *,
        owner_id: Optional[int] = None,
    ) -> TorrentStreamSession:
        session = await self.get(token)
        if session is None:
            raise LookupError("Torrent stream session not found.")
        if owner_id is not None and int(owner_id) != int(session.owner_id):
            raise PermissionError("Only the member who started this torrent may switch its media file.")
        if len(session.leases) > 1 or session.unleased_hold:
            raise RuntimeError(
                "This torrent is shared by multiple consumers; switch to a separate source "
                "instead of changing the shared file selection."
            )

        selected = next(
            (item for item in session.candidates if int(item.index) == int(file_index)),
            None,
        )
        if selected is None:
            raise ValueError("That torrent file is not an approved playable media candidate.")

        self._select_candidate(session, selected)
        return session

    def _range_is_available(
        self,
        session: TorrentStreamSession,
        start: int,
        end: int,
    ) -> bool:
        if session.file_size <= 0:
            return False
        start = max(0, min(int(start), session.file_size - 1))
        end = max(start, min(int(end), session.file_size - 1))
        global_start = session.file_offset + start
        global_end = session.file_offset + end
        first = global_start // session.piece_length
        last = global_end // session.piece_length
        try:
            return all(
                bool(session.handle.have_piece(piece))
                for piece in range(first, last + 1)
            )
        except Exception:
            return False

    def _metadata_probe_ready(self, session: TorrentStreamSession) -> bool:
        if session.file_size <= 0:
            return False

        head_end = min(
            session.file_size - 1,
            max(2 * 1024 * 1024, min(self.bootstrap_bytes, 8 * 1024 * 1024)) - 1,
        )
        if not self._range_is_available(session, 0, head_end):
            return False

        # MP4/MOV-family files commonly keep the moov atom at the end. The
        # torrent owner already prioritizes this tail region, so do not waste
        # probe attempts until it is actually present.
        suffix = Path(session.file_name).suffix.lower()
        if suffix in {".mp4", ".m4v", ".mov"} and session.file_size > self.tail_probe_bytes:
            tail_start = max(0, session.file_size - self.tail_probe_bytes)
            if not self._range_is_available(
                session,
                tail_start,
                session.file_size - 1,
            ):
                return False
        return True

    def schedule_metadata_probe(self, session: TorrentStreamSession) -> bool:
        if session.verified_metadata.get("available"):
            return False
        if session.metadata_probe_running or session.metadata_probe_attempts >= 3:
            return False
        if not self._metadata_probe_ready(session):
            return False

        now = time.monotonic()
        if session.metadata_last_probe_at and now - session.metadata_last_probe_at < 5.0:
            return False

        session.metadata_probe_running = True
        session.metadata_probe_attempts += 1
        session.metadata_last_probe_at = now

        async def _run() -> None:
            try:
                result = await probe_media_file(
                    session.absolute_path,
                    filename=session.file_name,
                    timeout_seconds=6.0,
                )
                if isinstance(result, dict):
                    session.verified_metadata = result
            except Exception as exc:
                session.verified_metadata = {
                    "origin": "verified_file",
                    "available": False,
                    "reason": f"probe_error:{type(exc).__name__}",
                }
            finally:
                session.metadata_probe_running = False

        try:
            task = asyncio.create_task(
                _run(),
                name=f"torrent_media_probe:{session.token}",
            )
            task.add_done_callback(lambda _task: None)
            return True
        except RuntimeError:
            session.metadata_probe_running = False
            return False

    def prioritize_range(
        self,
        session: TorrentStreamSession,
        start: int,
        end: int,
        *,
        readahead: bool = True,
        readahead_bytes: Optional[int] = None,
        time_critical: bool = True,
    ) -> None:
        start = max(0, min(int(start), session.file_size - 1))
        end = max(start, min(int(end), session.file_size - 1))

        global_start = session.file_offset + start
        global_end = session.file_offset + end
        first = global_start // session.piece_length
        last = global_end // session.piece_length

        updates: list[tuple[int, int]] = []
        for piece in range(first, last + 1):
            if session.first_piece <= piece <= session.last_piece:
                updates.append((piece, 7))

        if readahead:
            ahead_bytes = (
                self.readahead_bytes
                if readahead_bytes is None
                else max(0, int(readahead_bytes))
            )
            ahead_end = min(session.file_size - 1, end + ahead_bytes)
            ahead_last = (session.file_offset + ahead_end) // session.piece_length
            for piece in range(last + 1, ahead_last + 1):
                if session.first_piece <= piece <= session.last_piece:
                    updates.append((piece, 6))

        if updates:
            try:
                session.handle.prioritize_pieces(updates)
            except RuntimeError as exc:
                self._raise_unavailable_handle(exc)

        # Priority 7 tells the ordinary picker what matters. Deadlines switch
        # libtorrent into its dedicated time-critical streaming path, which
        # actively assigns urgent blocks to peers with the shortest estimated
        # download queues instead of merely waiting for rarest-first slots.
        if time_critical:
            set_deadline = getattr(session.handle, "set_piece_deadline", None)
            if callable(set_deadline):
                for offset, piece in enumerate(range(first, last + 1)):
                    if not (session.first_piece <= piece <= session.last_piece):
                        continue
                    deadline_ms = min(
                        30_000,
                        int(self.time_critical_base_deadline_ms)
                        + int(offset) * int(self.time_critical_step_ms),
                    )
                    try:
                        set_deadline(piece, deadline_ms)
                    except RuntimeError as exc:
                        self._raise_unavailable_handle(exc)
                    except Exception:
                        # Older/alternate Python bindings may not expose the
                        # deadline API even though piece priorities still work.
                        break
        session.last_access = time.monotonic()

    def _download_rate(self, session: TorrentStreamSession) -> float:
        try:
            live = float(getattr(session.handle.status(), "download_rate", 0) or 0)
        except Exception:
            live = 0.0
        if live > 0:
            if session.smoothed_download_rate <= 0:
                session.smoothed_download_rate = live
            else:
                session.smoothed_download_rate = (
                    session.smoothed_download_rate * 0.70 + live * 0.30
                )
        return max(0.0, session.smoothed_download_rate or live)

    def record_stream_timing(
        self,
        session: TorrentStreamSession,
        consumer_key: str,
        *,
        event: str,
        start: int = 0,
        end: int = 0,
        elapsed_ms: float = 0.0,
        ready: Optional[bool] = None,
    ) -> None:
        """Record numeric startup timings for one signed stream consumer.

        This is diagnostic state only. It never changes buffering, priorities,
        admission, or playback decisions.
        """

        key = str(consumer_key or "").strip()[:96]
        if not key:
            return
        now = time.monotonic()
        state = session.consumer_playback.setdefault(
            key,
            {
                "last_request_at": 0.0,
                "last_request_bytes": 0,
                "last_request_end": -1,
                "smoothed_consume_rate": 0.0,
                "last_access": now,
            },
        )
        state["last_access"] = now
        name = str(event or "").strip().lower()
        from_start_ms = max(0.0, (now - float(session.created_at)) * 1000.0)

        if name == "request":
            state["request_count"] = int(state.get("request_count", 0) or 0) + 1
            state["last_range_start"] = max(0, int(start))
            state["last_range_end"] = max(int(start), int(end))
            state["last_request_from_start_ms"] = from_start_ms
            if "first_request_from_start_ms" not in state:
                state["first_request_from_start_ms"] = from_start_ms
                state["first_range_start"] = max(0, int(start))
                state["first_range_end"] = max(int(start), int(end))
        elif name == "wait":
            state["last_wait_ms"] = max(0.0, float(elapsed_ms))
            state["last_wait_ready"] = 1 if ready else 0
            if "first_wait_ms" not in state:
                state["first_wait_ms"] = max(0.0, float(elapsed_ms))
                state["first_wait_ready"] = 1 if ready else 0
        elif name == "headers":
            if "first_headers_from_start_ms" not in state:
                state["first_headers_from_start_ms"] = from_start_ms
        elif name == "first_byte":
            if "first_byte_from_start_ms" not in state:
                state["first_byte_from_start_ms"] = from_start_ms

    def consumer_startup_status(
        self,
        session: TorrentStreamSession,
        consumer_key: str,
    ) -> dict[str, Any]:
        key = str(consumer_key or "").strip()[:96]
        state = session.consumer_playback.get(key) if key else None
        if not isinstance(state, dict):
            return {}

        def _ms(name: str) -> int:
            try:
                return max(0, int(round(float(state.get(name, 0.0) or 0.0))))
            except Exception:
                return 0

        startup_origin = float(getattr(session, "startup_started_at", 0.0) or 0.0)
        metadata_ready = float(getattr(session, "metadata_ready_at", 0.0) or 0.0)
        finalized = float(getattr(session, "created_at", 0.0) or 0.0)
        metadata_ms = (
            max(0, int(round((metadata_ready - startup_origin) * 1000.0)))
            if startup_origin > 0 and metadata_ready >= startup_origin
            else 0
        )
        finalize_ms = (
            max(0, int(round((finalized - startup_origin) * 1000.0)))
            if startup_origin > 0 and finalized >= startup_origin
            else 0
        )

        return {
            "metadata_ms": metadata_ms,
            "session_ready_ms": finalize_ms,
            "request_count": max(0, int(state.get("request_count", 0) or 0)),
            "first_request_ms": _ms("first_request_from_start_ms"),
            "first_range_start": max(0, int(state.get("first_range_start", 0) or 0)),
            "first_range_end": max(0, int(state.get("first_range_end", 0) or 0)),
            "first_wait_ms": _ms("first_wait_ms"),
            "first_wait_ready": bool(int(state.get("first_wait_ready", 0) or 0)),
            "first_headers_ms": _ms("first_headers_from_start_ms"),
            "first_byte_ms": _ms("first_byte_from_start_ms"),
            "last_range_start": max(0, int(state.get("last_range_start", 0) or 0)),
            "last_range_end": max(0, int(state.get("last_range_end", 0) or 0)),
            "last_wait_ms": _ms("last_wait_ms"),
            "last_wait_ready": bool(int(state.get("last_wait_ready", 0) or 0)),
        }

    def prepare_playback_request(
        self,
        session: TorrentStreamSession,
        start: int,
        end: int,
        *,
        consumer_key: str = "",
    ) -> TorrentBufferPlan:
        now = time.monotonic()
        start = max(0, min(int(start), session.file_size - 1))
        end = max(start, min(int(end), session.file_size - 1))
        request_bytes = end - start + 1

        key = str(consumer_key or "").strip()[:96]
        playback = None
        if key:
            playback = session.consumer_playback.setdefault(
                key,
                {
                    "last_request_at": 0.0,
                    "last_request_bytes": 0,
                    "last_request_end": -1,
                    "smoothed_consume_rate": 0.0,
                    "last_access": now,
                },
            )
            playback["last_access"] = now
            if len(session.consumer_playback) > 64:
                stale = sorted(
                    session.consumer_playback.items(),
                    key=lambda item: float(item[1].get("last_access", 0.0) or 0.0),
                )
                for stale_key, _state in stale[: len(session.consumer_playback) - 64]:
                    session.consumer_playback.pop(stale_key, None)

        last_end = int((playback or {}).get("last_request_end", session.last_request_end))
        last_at = float((playback or {}).get("last_request_at", session.last_request_at))
        last_bytes = int((playback or {}).get("last_request_bytes", session.last_request_bytes))
        smoothed_consume = float((playback or {}).get("smoothed_consume_rate", session.smoothed_consume_rate))

        seek_threshold = max(session.piece_length * 2, 4 * 1024 * 1024)
        expected_next = last_end + 1
        seek = last_end >= 0 and abs(start - expected_next) > seek_threshold

        if last_at > 0 and last_bytes > 0 and not seek:
            elapsed = now - last_at
            if 0.10 <= elapsed <= 30.0:
                observed = last_bytes / elapsed
                smoothed_consume = (
                    observed if smoothed_consume <= 0
                    else smoothed_consume * 0.75 + observed * 0.25
                )

        consume_rate = max(float(self.min_consume_rate), smoothed_consume)
        download_rate = self._download_rate(session)
        ratio = download_rate / consume_rate if consume_rate > 0 else 0.0

        if seek:
            target_seconds = max(8.0, self.buffer_target_seconds * 0.45)
        elif ratio <= 0.0:
            target_seconds = self.buffer_target_seconds
        elif ratio < 1.10:
            target_seconds = self.buffer_max_seconds
        elif ratio < 1.50:
            target_seconds = min(
                self.buffer_max_seconds,
                self.buffer_target_seconds * 1.70,
            )
        elif ratio < 2.50:
            target_seconds = self.buffer_target_seconds
        else:
            target_seconds = max(12.0, self.buffer_target_seconds * 0.65)

        if session.stall_count:
            target_seconds = min(
                self.buffer_max_seconds,
                target_seconds + min(30.0, session.stall_count * 8.0),
            )

        target_bytes = int(consume_rate * target_seconds)
        target_bytes = max(self.min_readahead_bytes, target_bytes)
        target_bytes = min(self.max_readahead_bytes, target_bytes)
        target_bytes = min(target_bytes, max(0, session.file_size - end - 1))

        first_request = last_end < 0
        startup_extra = 0
        if first_request and (start == 0 or bool(key)):
            # A keyed Movie Night consumer may join in the middle of a shared
            # torrent. Its first Range request is not a seek, but it still
            # needs a real startup buffer around that late-join position.
            startup_extra = min(
                max(self.bootstrap_bytes, self.min_readahead_bytes),
                max(0, session.file_size - end - 1),
            )
        elif seek:
            startup_extra = min(
                max(self.min_readahead_bytes, request_bytes * 2),
                max(0, session.file_size - end - 1),
            )

        startup_wait_end = min(
            session.file_size - 1,
            end + min(target_bytes, startup_extra),
        )

        session.adaptive_readahead_bytes = target_bytes
        session.adaptive_target_seconds = float(target_seconds)
        if playback is not None:
            playback["smoothed_consume_rate"] = smoothed_consume
            playback["last_request_at"] = now
            playback["last_request_bytes"] = request_bytes
            playback["last_request_end"] = end
            playback["last_access"] = now
        else:
            session.smoothed_consume_rate = smoothed_consume
            session.last_request_at = now
            session.last_request_bytes = request_bytes
            session.last_request_end = end
        session.last_access = now

        self.prioritize_range(
            session,
            start,
            end,
            readahead=True,
            readahead_bytes=target_bytes,
        )
        return TorrentBufferPlan(
            seek=seek,
            target_seconds=float(target_seconds),
            target_bytes=int(target_bytes),
            startup_wait_end=int(startup_wait_end),
            consume_rate=float(consume_rate),
            download_rate=float(download_rate),
        )

    async def wait_range(
        self,
        session: TorrentStreamSession,
        start: int,
        end: int,
        *,
        timeout: Optional[float] = None,
        readahead_bytes: Optional[int] = None,
    ) -> bool:
        self.prioritize_range(
            session,
            start,
            end,
            readahead=True,
            readahead_bytes=(
                session.adaptive_readahead_bytes
                if readahead_bytes is None
                else readahead_bytes
            ),
        )
        wait = self.buffer_wait_seconds if timeout is None else max(0.5, float(timeout))
        deadline = time.monotonic() + wait

        global_start = session.file_offset + max(0, int(start))
        global_end = session.file_offset + min(int(end), session.file_size - 1)
        first = global_start // session.piece_length
        last = global_end // session.piece_length

        while time.monotonic() < deadline:
            try:
                complete = all(
                    bool(session.handle.have_piece(piece))
                    for piece in range(first, last + 1)
                )
                if complete:
                    session.last_access = time.monotonic()
                    if session.stall_count > 0:
                        session.stall_count -= 1
                    return True
                status = session.handle.status()
            except RuntimeError as exc:
                self._raise_unavailable_handle(exc)
            error = str(getattr(status, "error", "") or "").strip()
            if error:
                return False
            await asyncio.sleep(0.15)
        session.stall_count = min(20, session.stall_count + 1)
        return False

    async def read_range(
        self,
        session: TorrentStreamSession,
        start: int,
        end: int,
    ) -> bytes:
        start = max(0, int(start))
        end = min(int(end), session.file_size - 1)
        if end < start:
            return b""
        path = session.absolute_path
        length = end - start + 1

        def _read() -> bytes:
            with path.open("rb") as fh:
                fh.seek(start)
                return fh.read(length)

        return await asyncio.to_thread(_read)

    def storage_status(self) -> dict[str, Any]:
        try:
            usage = shutil.disk_usage(self.root)
            total = int(usage.total)
            used = int(usage.used)
            free = int(usage.free)
        except Exception:
            total = used = free = 0
        return {
            "root": str(self.root),
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "max_file_bytes": int(self.max_file_bytes),
            "max_torrent_bytes": int(self.max_torrent_bytes),
            "disk_reserve_bytes": int(self.disk_reserve_bytes),
            "committed_file_bytes": int(self._committed_file_bytes()),
        }

    def status(self, session: TorrentStreamSession) -> dict[str, Any]:
        try:
            status = session.handle.status()
        except RuntimeError as exc:
            self._raise_unavailable_handle(exc)
        try:
            progress = session.handle.file_progress()[session.file_index]
        except Exception:
            progress = 0
        peers = int(getattr(status, "num_peers", 0) or 0)
        seeds = int(getattr(status, "num_seeds", 0) or 0)
        leechers = max(0, peers - seeds)
        distributed = float(getattr(status, "distributed_copies", 0.0) or 0.0)

        return {
            "token": session.token,
            "name": session.file_name,
            "source_identity": session.source_identity,
            "lease_count": len(session.leases),
            "consumer_guilds": sorted(self._consumer_guild_ids(session)),
            "shared": len(session.leases) > 1,
            "reuse_hits": int(session.reuse_hits),
            "unleased_hold": bool(session.unleased_hold),
            "size": session.file_size,
            "downloaded": int(progress or 0),
            "progress": min(1.0, max(0.0, float(progress or 0) / max(1, session.file_size))),
            "download_rate": int(getattr(status, "download_rate", 0) or 0),
            "upload_rate": int(getattr(status, "upload_rate", 0) or 0),
            "peers": peers,
            "seeds": seeds,
            "leechers": leechers,
            "distributed_copies": round(max(0.0, distributed), 3),
            "seed_leech_ratio": round(seeds / max(1, leechers), 3),
            "state": str(getattr(status, "state", "") or ""),
            "error": str(getattr(status, "error", "") or ""),
            "buffer": {
                "target_seconds": round(float(session.adaptive_target_seconds or 0.0), 1),
                "readahead_bytes": int(session.adaptive_readahead_bytes or 0),
                "consume_rate": int(session.smoothed_consume_rate or 0),
                "download_rate": int(session.smoothed_download_rate or 0),
                "stall_count": int(session.stall_count),
            },
            "metadata": {
                "release_name": dict(session.release_metadata or {}),
                "verified": dict(session.verified_metadata or {}),
                "probe_running": bool(session.metadata_probe_running),
                "probe_attempts": int(session.metadata_probe_attempts),
            },
            "playable_files": [
                {
                    "index": item.index,
                    "name": _safe_name(item.path),
                    "path": item.path,
                    "size": item.size,
                    "selected": int(item.index) == int(session.file_index),
                }
                for item in session.candidates
            ],
        }

    async def get(self, token: str) -> Optional[TorrentStreamSession]:
        async with self._lock:
            session = self._sessions.get(str(token or ""))
        if session is not None:
            session.last_access = time.monotonic()
        return session

    def _signature(
        self,
        session: TorrentStreamSession,
        expires: int,
        consumer_key: str = "",
    ) -> str:
        key = self.stream_secret.encode("utf-8")
        consumer = str(consumer_key or "").strip()[:96]
        suffix = f":{consumer}" if consumer else ""
        payload = f"{session.token}:{int(expires)}:{session.secret}{suffix}".encode("utf-8")
        return hmac.new(key, payload, hashlib.sha256).hexdigest()

    @staticmethod
    def browser_audio_compatibility(session: TorrentStreamSession) -> dict[str, Any]:
        """Describe whether browser playback should use the FFmpeg AAC sidecar.

        Verified codec metadata wins. Release-name tags are only a conservative
        fallback while the bounded media probe is still warming up.
        """

        verified = (
            session.verified_metadata
            if isinstance(session.verified_metadata, Mapping)
            else {}
        )
        tracks = verified.get("audio_tracks")
        codecs = {
            str(row.get("codec") or "").strip().casefold()
            for row in tracks
            if isinstance(row, Mapping)
        } if isinstance(tracks, list) else set()
        codecs.discard("")

        browser_safe = {
            "aac",
            "mp3",
            "opus",
            "vorbis",
        }
        risky = {
            "ac3",
            "eac3",
            "dca",
            "dts",
            "truehd",
            "mlp",
            "pcm_s16le",
            "pcm_s24le",
            "pcm_s32le",
        }
        if codecs:
            unsupported = sorted(codec for codec in codecs if codec not in browser_safe)
            return {
                "required": bool(unsupported),
                "reason": (
                    "verified:" + ",".join(unsupported)
                    if unsupported
                    else "verified_browser_safe"
                ),
                "codecs": sorted(codecs),
                "source": "verified",
            }

        release = (
            session.release_metadata
            if isinstance(session.release_metadata, Mapping)
            else {}
        )
        tags = {
            str(value or "").strip().casefold()
            for value in list(release.get("audio_tags") or [])
            if str(value or "").strip()
        }
        if any("aac" in tag or "opus" in tag for tag in tags):
            return {
                "required": False,
                "reason": "release_browser_safe",
                "codecs": sorted(tags),
                "source": "release",
            }
        risky_markers = (
            "ddp",
            "eac3",
            "dd ",
            "ac3",
            "dts",
            "truehd",
            "atmos",
        )
        if any(any(marker in tag for marker in risky_markers) for tag in tags):
            return {
                "required": True,
                "reason": "release_audio_codec",
                "codecs": sorted(tags),
                "source": "release",
            }

        suffix = Path(session.file_name).suffix.casefold()
        # Matroska commonly carries AC-3/E-AC-3/DTS. Do not force a sidecar
        # purely from the container, but ask the player to keep probing.
        return {
            "required": False,
            "reason": "unknown_mkv" if suffix == ".mkv" else "unknown",
            "codecs": [],
            "source": "",
        }

    def compat_audio_url(
        self,
        session: TorrentStreamSession,
        *,
        ttl_seconds: int = 3600,
        consumer_key: str = "",
    ) -> str:
        if not self.public_base_url or not self.stream_secret:
            return ""
        expires = int(time.time()) + max(60, min(int(ttl_seconds), 21600))
        consumer = str(consumer_key or "").strip()[:96]
        signature = self._signature(session, expires, consumer)
        filename = quote(Path(session.file_name).stem + ".m4a", safe="")
        consumer_query = f"&cid={quote(consumer, safe='')}" if consumer else ""
        return (
            f"{self.public_base_url}/media/torrent/audio/{session.token}/{filename}"
            f"?exp={expires}&sig={signature}{consumer_query}"
        )

    def stream_url(
        self,
        session: TorrentStreamSession,
        *,
        ttl_seconds: int = 3600,
        consumer_key: str = "",
    ) -> str:
        if not self.public_base_url or not self.stream_secret:
            return ""
        expires = int(time.time()) + max(60, min(int(ttl_seconds), 21600))
        consumer = str(consumer_key or "").strip()[:96]
        signature = self._signature(session, expires, consumer)
        filename = quote(session.file_name, safe="")
        consumer_query = f"&cid={quote(consumer, safe='')}" if consumer else ""
        return (
            f"{self.public_base_url}/media/torrent/stream/{session.token}/{filename}"
            f"?exp={expires}&sig={signature}{consumer_query}"
        )

    async def validate_stream_access(
        self,
        token: str,
        expires: str,
        signature: str,
        consumer_key: str = "",
    ) -> bool:
        session = await self.get(token)
        if session is None or not self.stream_secret:
            return False
        try:
            exp = int(str(expires or "").strip())
        except Exception:
            return False
        now = int(time.time())
        if exp < now or exp > now + 21660:
            return False
        expected = self._signature(session, exp, consumer_key)
        return bool(signature) and hmac.compare_digest(expected, str(signature))

    async def cleanup_expired(self) -> int:
        """Reclaim only truly idle, unleased sessions.

        Candidate selection and removal happen under the same lock so a session
        cannot be refreshed or leased after being declared stale but before its
        libtorrent handle is removed.
        """

        now = time.monotonic()
        expired: list[TorrentStreamSession] = []
        async with self._lock:
            for token, session in list(self._sessions.items()):
                if session.leases:
                    continue
                if now - float(session.last_access) <= self.idle_ttl_seconds:
                    continue
                self._sessions.pop(token, None)
                if self._identity_index.get(session.source_identity) == token:
                    self._identity_index.pop(session.source_identity, None)
                expired.append(session)

        for session in expired:
            self._safe_remove_handle(session.handle)
            await asyncio.to_thread(shutil.rmtree, session.save_root, True)
        return len(expired)

    async def discard_unusable_session(self, token: str) -> bool:
        """Drop a terminally invalid libtorrent handle from the live registry."""

        clean_token = str(token or "")
        session: Optional[TorrentStreamSession] = None
        async with self._lock:
            current = self._sessions.get(clean_token)
            if current is None or self.session_usable(current):
                return False
            session = self._sessions.pop(clean_token, None)
            if session is not None and self._identity_index.get(session.source_identity) == clean_token:
                self._identity_index.pop(session.source_identity, None)

        if session is None:
            return False
        self._safe_remove_handle(session.handle)
        await asyncio.to_thread(shutil.rmtree, session.save_root, True)
        return True

    async def release_lease(
        self,
        token: str,
        lease_key: str,
        *,
        remove_if_unused: bool = True,
    ) -> bool:
        key = str(lease_key or "").strip()[:180]
        if not key:
            return False

        clean_token = str(token or "")
        detached: Optional[TorrentStreamSession] = None
        async with self._lock:
            session = self._sessions.get(clean_token)
            if session is None:
                return False
            existed = key in session.leases
            session.leases.discard(key)
            session.lease_guild_ids.pop(key, None)
            session.last_access = time.monotonic()
            should_remove = bool(
                remove_if_unused
                and existed
                and not session.leases
                and not session.unleased_hold
            )
            if should_remove:
                # Detach atomically before releasing the manager lock. Otherwise
                # another room can reuse this identity between "last lease
                # released" and remove(), then lose its newly attached handle.
                detached = self._sessions.pop(clean_token, None)
                if (
                    detached is not None
                    and self._identity_index.get(detached.source_identity) == clean_token
                ):
                    self._identity_index.pop(detached.source_identity, None)

        if detached is not None:
            self._safe_remove_handle(detached.handle)
            await asyncio.to_thread(shutil.rmtree, detached.save_root, True)
        return existed

    async def remove(self, token: str, *, force: bool = False) -> bool:
        clean_token = str(token or "")
        async with self._lock:
            session = self._sessions.get(clean_token)
            if session is None:
                return False
            if session.leases and not force:
                return False
            self._sessions.pop(clean_token, None)
            if self._identity_index.get(session.source_identity) == clean_token:
                self._identity_index.pop(session.source_identity, None)
        self._safe_remove_handle(session.handle)
        await asyncio.to_thread(shutil.rmtree, session.save_root, True)
        return True

    def _safe_remove_handle(self, handle: Any) -> None:
        if handle is None:
            return
        try:
            flags = getattr(self.lt.options_t, "delete_files", 1)
            self._session.remove_torrent(handle, flags)
        except Exception:
            try:
                self._session.remove_torrent(handle)
            except Exception:
                pass


def get_torrent_manager() -> TorrentMediaManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = TorrentMediaManager()
    return _MANAGER


__all__ = [
    "MediaCapacitySnapshot",
    "TorrentBufferPlan",
    "TorrentFileCandidate",
    "TorrentMediaManager",
    "TorrentStreamSession",
    "TorrentSessionUnavailableError",
    "find_magnet",
    "get_torrent_manager",
    "is_playable_filename",
    "is_torrent_filename",
    "magnet_identity",
    "media_content_type",
    "parse_http_range",
]
