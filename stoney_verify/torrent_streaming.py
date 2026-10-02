from __future__ import annotations

"""Bounded progressive torrent media runtime for Dank Shield.

This module owns torrent lifecycle, playable-file selection, piece priorities,
seek reprioritization, signed stream URLs, and cleanup. It deliberately does not
search torrent indexes or discover copyrighted content. Callers must supply the
magnet or .torrent source they are authorized to use.
"""

import asyncio
import hashlib
import hmac
import mimetypes
import os
import re
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

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
    query = raw.split("?", 1)[1]
    match = _BTIH_RE.search(query)
    if not match:
        return ""
    value = match.group(1).strip().lower()
    return f"btih:{value}" if value else ""


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


@dataclass(frozen=True)
class TorrentFileCandidate:
    index: int
    path: str
    size: int


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
        self.max_sessions = _env_int("DANK_TORRENT_MAX_SESSIONS", 1, minimum=1, maximum=4)
        self.max_metadata_bytes = _env_int(
            "DANK_TORRENT_MAX_METADATA_BYTES",
            4 * 1024 * 1024,
            minimum=64 * 1024,
            maximum=16 * 1024 * 1024,
        )
        self.max_file_bytes = _env_int(
            "DANK_TORRENT_MAX_FILE_BYTES",
            2 * 1024 * 1024 * 1024,
            minimum=8 * 1024 * 1024,
            maximum=16 * 1024 * 1024 * 1024,
        )
        self.max_torrent_bytes = _env_int(
            "DANK_TORRENT_MAX_TOTAL_BYTES",
            4 * 1024 * 1024 * 1024,
            minimum=16 * 1024 * 1024,
            maximum=32 * 1024 * 1024 * 1024,
        )
        self.readahead_bytes = _env_int(
            "DANK_TORRENT_READAHEAD_BYTES",
            16 * 1024 * 1024,
            minimum=2 * 1024 * 1024,
            maximum=128 * 1024 * 1024,
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
        self.stream_secret = (
            str(os.getenv("DANK_TORRENT_STREAM_SECRET", "") or "").strip()
            or str(os.getenv("BOT_API_SHARED_SECRET", "") or "").strip()
        )
        self._sessions: dict[str, TorrentStreamSession] = {}
        self._lock = asyncio.Lock()
        self._gate = asyncio.Semaphore(self.max_sessions)
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
                "DANK_TORRENT_CONNECTION_LIMIT", 80, minimum=10, maximum=300
            ),
            "active_downloads": self.max_sessions,
            "active_seeds": 0,
            "active_limit": self.max_sessions,
            "download_rate_limit": _env_int(
                "DANK_TORRENT_DOWNLOAD_RATE_BYTES",
                8 * 1024 * 1024,
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

    async def start_magnet(self, magnet: str, *, guild_id: int, owner_id: int) -> TorrentStreamSession:
        raw = str(magnet or "").strip()
        identity = magnet_identity(raw)
        if not identity:
            raise ValueError("That is not a valid BitTorrent magnet link.")

        await self.cleanup_expired()
        async with self._gate:
            token = secrets.token_urlsafe(18)
            save_root = Path(tempfile.mkdtemp(prefix=f"{token}-", dir=str(self.root)))
            handle = None
            try:
                atp = self.lt.parse_magnet_uri(raw)
                atp.save_path = str(save_root)
                handle = self._session.add_torrent(atp)
                info = await self._wait_metadata(handle)
                return await self._finalize_session(
                    token=token,
                    save_root=save_root,
                    handle=handle,
                    info=info,
                    guild_id=guild_id,
                    owner_id=owner_id,
                    source_kind="magnet",
                    source_identity=identity,
                )
            except Exception:
                self._safe_remove_handle(handle)
                shutil.rmtree(save_root, ignore_errors=True)
                raise

    async def start_torrent_bytes(
        self,
        payload: bytes,
        *,
        guild_id: int,
        owner_id: int,
    ) -> TorrentStreamSession:
        data = bytes(payload or b"")
        if not data or len(data) > self.max_metadata_bytes:
            raise ValueError("The .torrent metadata file is empty or exceeds the configured limit.")

        await self.cleanup_expired()
        async with self._gate:
            token = secrets.token_urlsafe(18)
            save_root = Path(tempfile.mkdtemp(prefix=f"{token}-", dir=str(self.root)))
            torrent_path = save_root / "source.torrent"
            torrent_path.write_bytes(data)
            handle = None
            try:
                info = self.lt.torrent_info(str(torrent_path))
                atp = self.lt.add_torrent_params()
                atp.ti = info
                atp.save_path = str(save_root)
                handle = self._session.add_torrent(atp)
                source_identity = self._info_identity(info)
                return await self._finalize_session(
                    token=token,
                    save_root=save_root,
                    handle=handle,
                    info=info,
                    guild_id=guild_id,
                    owner_id=owner_id,
                    source_kind="torrent",
                    source_identity=source_identity,
                )
            except Exception:
                self._safe_remove_handle(handle)
                shutil.rmtree(save_root, ignore_errors=True)
                raise

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
            return f"btih:{str(hashes.v1).lower()}"
        except Exception:
            try:
                return f"btih:{str(info.info_hash()).lower()}"
            except Exception:
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
    ) -> TorrentStreamSession:
        candidates = self._playable_candidates(info)
        if not candidates:
            raise ValueError("This torrent does not contain a supported playable video file.")

        selected = candidates[0]
        files = info.files()
        file_offset = int(files.file_offset(selected.index))
        piece_length = int(info.piece_length())
        if piece_length <= 0:
            raise RuntimeError("Torrent metadata reported an invalid piece length.")

        first_piece = file_offset // piece_length
        last_piece = (file_offset + selected.size - 1) // piece_length

        priorities = [0] * int(files.num_files())
        priorities[selected.index] = 1
        handle.prioritize_files(priorities)

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
            file_index=selected.index,
            file_path=selected.path,
            file_name=_safe_name(selected.path),
            file_size=selected.size,
            file_offset=file_offset,
            piece_length=piece_length,
            first_piece=first_piece,
            last_piece=last_piece,
            created_at=time.monotonic(),
            last_access=time.monotonic(),
        )
        async with self._lock:
            self._sessions[token] = session

        self.prioritize_range(session, 0, min(session.file_size - 1, self.bootstrap_bytes - 1))
        if session.file_size > self.tail_probe_bytes:
            self.prioritize_range(
                session,
                max(0, session.file_size - self.tail_probe_bytes),
                session.file_size - 1,
                readahead=False,
            )
        return session

    def prioritize_range(
        self,
        session: TorrentStreamSession,
        start: int,
        end: int,
        *,
        readahead: bool = True,
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
            ahead_end = min(session.file_size - 1, end + self.readahead_bytes)
            ahead_last = (session.file_offset + ahead_end) // session.piece_length
            for piece in range(last + 1, ahead_last + 1):
                if session.first_piece <= piece <= session.last_piece:
                    updates.append((piece, 6))

        if updates:
            session.handle.prioritize_pieces(updates)
        session.last_access = time.monotonic()

    async def wait_range(
        self,
        session: TorrentStreamSession,
        start: int,
        end: int,
        *,
        timeout: Optional[float] = None,
    ) -> bool:
        self.prioritize_range(session, start, end)
        wait = self.buffer_wait_seconds if timeout is None else max(0.5, float(timeout))
        deadline = time.monotonic() + wait

        global_start = session.file_offset + max(0, int(start))
        global_end = session.file_offset + min(int(end), session.file_size - 1)
        first = global_start // session.piece_length
        last = global_end // session.piece_length

        while time.monotonic() < deadline:
            if all(bool(session.handle.have_piece(piece)) for piece in range(first, last + 1)):
                session.last_access = time.monotonic()
                return True
            status = session.handle.status()
            error = str(getattr(status, "error", "") or "").strip()
            if error:
                return False
            await asyncio.sleep(0.15)
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

    def status(self, session: TorrentStreamSession) -> dict[str, Any]:
        status = session.handle.status()
        try:
            progress = session.handle.file_progress()[session.file_index]
        except Exception:
            progress = 0
        return {
            "token": session.token,
            "name": session.file_name,
            "size": session.file_size,
            "downloaded": int(progress or 0),
            "progress": min(1.0, max(0.0, float(progress or 0) / max(1, session.file_size))),
            "download_rate": int(getattr(status, "download_rate", 0) or 0),
            "upload_rate": int(getattr(status, "upload_rate", 0) or 0),
            "peers": int(getattr(status, "num_peers", 0) or 0),
            "seeds": int(getattr(status, "num_seeds", 0) or 0),
            "state": str(getattr(status, "state", "") or ""),
            "error": str(getattr(status, "error", "") or ""),
        }

    async def get(self, token: str) -> Optional[TorrentStreamSession]:
        async with self._lock:
            session = self._sessions.get(str(token or ""))
        if session is not None:
            session.last_access = time.monotonic()
        return session

    def _signature(self, session: TorrentStreamSession, expires: int) -> str:
        key = self.stream_secret.encode("utf-8")
        payload = f"{session.token}:{int(expires)}:{session.secret}".encode("utf-8")
        return hmac.new(key, payload, hashlib.sha256).hexdigest()

    def stream_url(self, session: TorrentStreamSession, *, ttl_seconds: int = 3600) -> str:
        if not self.public_base_url or not self.stream_secret:
            return ""
        expires = int(time.time()) + max(60, min(int(ttl_seconds), 21600))
        signature = self._signature(session, expires)
        filename = quote(session.file_name, safe="")
        return (
            f"{self.public_base_url}/media/torrent/stream/{session.token}/{filename}"
            f"?exp={expires}&sig={signature}"
        )

    async def validate_stream_access(self, token: str, expires: str, signature: str) -> bool:
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
        expected = self._signature(session, exp)
        return bool(signature) and hmac.compare_digest(expected, str(signature))

    async def cleanup_expired(self) -> int:
        now = time.monotonic()
        async with self._lock:
            expired = [
                token
                for token, session in self._sessions.items()
                if now - session.last_access > self.idle_ttl_seconds
            ]
        for token in expired:
            await self.remove(token)
        return len(expired)

    async def remove(self, token: str) -> bool:
        async with self._lock:
            session = self._sessions.pop(str(token or ""), None)
        if session is None:
            return False
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
    "TorrentFileCandidate",
    "TorrentMediaManager",
    "TorrentStreamSession",
    "find_magnet",
    "get_torrent_manager",
    "is_playable_filename",
    "is_torrent_filename",
    "magnet_identity",
    "media_content_type",
    "parse_http_range",
]
