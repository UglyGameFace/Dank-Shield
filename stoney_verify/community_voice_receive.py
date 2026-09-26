from __future__ import annotations

"""Hardened per-speaker Discord voice receive boundary for Community Hub.

The external voice-receive package owns Discord UDP/RTP transport plumbing.
Dank Shield owns the safety boundary after DAVE decryption: every PCM frame
must still resolve to exactly one Discord user/SSRC pair before it can enter
captioning. Unknown, mismatched, malformed, or non-consented audio is dropped.

No audio is written to disk by this module.
"""

import asyncio
import ctypes.util
import importlib.util
import logging
import os
import platform
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import discord

try:
    import davey  # noqa: F401
except Exception:  # pragma: no cover - capability probe handles this
    davey = None  # type: ignore[assignment]

try:
    from discord.ext import voice_recv
except Exception:  # pragma: no cover - capability probe handles this
    voice_recv = None  # type: ignore[assignment]


log = logging.getLogger(__name__)

PCM_SAMPLE_RATE = 48_000
PCM_CHANNELS = 2
PCM_SAMPLE_WIDTH = 2
PCM_FRAME_ALIGNMENT = PCM_CHANNELS * PCM_SAMPLE_WIDTH

# Exact reviewed receive implementation. This is upstream PR #58's head,
# pinned in requirements.txt so branch movement cannot silently change runtime.
VOICE_RECV_DAVE_COMMIT = "03dd1e2dafe85522cc458441cd5b143b136ac836"
VOICE_RECV_DAVE_SOURCE = "imayhaveborkedit/discord-ext-voice-recv#58"
BUNDLED_OPUS_DISTRIBUTION = "opuslib-next-bundled==0.1.1"


class VoiceReceiveUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VoiceReceiveCapability:
    available: bool
    reason: str
    discord_py_version: str
    dave_available: bool
    receive_extension_available: bool
    inbound_dave_decrypt_available: bool
    opus_available: bool
    opus_library: str


@dataclass(frozen=True, slots=True)
class SpeakerPCMFrame:
    user_id: int
    ssrc: int
    sequence: int
    rtp_timestamp: int
    pcm: bytes
    received_at: float


@dataclass(slots=True)
class VoiceReceiveHealth:
    raw_udp_packets: int = 0
    opus_decode_drops: int = 0
    reader_failures: int = 0
    frames_seen: int = 0
    frames_routed: int = 0
    frames_not_consented: int = 0
    frames_unknown_source: int = 0
    frames_source_mismatch: int = 0
    frames_empty: int = 0
    frames_malformed_pcm: int = 0
    queue_overflow: int = 0
    callback_failures: int = 0

    def snapshot(self) -> dict[str, int]:
        return {
            "raw_udp_packets": self.raw_udp_packets,
            "opus_decode_drops": self.opus_decode_drops,
            "reader_failures": self.reader_failures,
            "frames_seen": self.frames_seen,
            "frames_routed": self.frames_routed,
            "frames_not_consented": self.frames_not_consented,
            "frames_unknown_source": self.frames_unknown_source,
            "frames_source_mismatch": self.frames_source_mismatch,
            "frames_empty": self.frames_empty,
            "frames_malformed_pcm": self.frames_malformed_pcm,
            "queue_overflow": self.queue_overflow,
            "callback_failures": self.callback_failures,
        }


def bundled_opus_library_path() -> Optional[str]:
    """Return the shared libopus shipped by opuslib-next-bundled without importing it."""

    spec = importlib.util.find_spec("opuslib_next")
    origin = getattr(spec, "origin", None) if spec is not None else None
    if not origin:
        return None

    system = platform.system()
    filename = {
        "Linux": "libopus.so",
        "Darwin": "libopus.dylib",
        "Windows": "opus.dll",
    }.get(system)
    if not filename:
        return None

    candidate = Path(str(origin)).resolve().parent / "_native" / filename
    return str(candidate) if candidate.is_file() else None


def ensure_opus_loaded() -> tuple[bool, str]:
    """Load the native Opus library required by discord.py PCM decoding.

    Production prefers the pinned bundled wheel so voice decoding does not depend
    on the hosting image's package set. DANK_OPUS_LIBRARY remains an explicit
    operator override; system discovery is only a final compatibility fallback.
    """

    if discord.opus.is_loaded():
        return True, "already-loaded"

    candidates: list[str] = []

    configured = str(os.getenv("DANK_OPUS_LIBRARY", "") or "").strip()
    if configured:
        candidates.append(configured)

    bundled = bundled_opus_library_path()
    if bundled and bundled not in candidates:
        candidates.append(bundled)

    discovered = ctypes.util.find_library("opus")
    if discovered and str(discovered) not in candidates:
        candidates.append(str(discovered))

    for candidate in ("libopus.so.0", "libopus.so.1", "libopus.so", "opus"):
        if candidate not in candidates:
            candidates.append(candidate)

    failures: list[str] = []
    for candidate in candidates:
        try:
            discord.opus.load_opus(candidate)
        except Exception as exc:
            failures.append(f"{candidate}:{type(exc).__name__}")
            continue
        if discord.opus.is_loaded():
            log.info("Live Captions Opus runtime loaded library=%s", candidate)
            return True, candidate

    if failures:
        log.warning(
            "Live Captions could not load native Opus candidates=%s",
            ", ".join(failures[:6]),
        )
    return False, "not-found"


def voice_receive_capability() -> VoiceReceiveCapability:
    opus_ok, opus_library = ensure_opus_loaded()
    receive_ok = voice_recv is not None
    inbound_dave = False
    if receive_ok:
        try:
            from discord.ext.voice_recv import opus as voice_recv_opus

            packet_decoder = getattr(voice_recv_opus, "PacketDecoder", None)
            inbound_dave = bool(
                packet_decoder is not None
                and callable(getattr(packet_decoder, "_dave_decrypt", None))
            )
        except Exception:
            inbound_dave = False

    dave_ok = davey is not None
    available = bool(receive_ok and dave_ok and inbound_dave and opus_ok)
    if not opus_ok:
        reason = "native Opus runtime is unavailable; install/load libopus before Live Captions starts"
    elif not receive_ok:
        reason = "discord-ext-voice-recv is unavailable"
    elif not dave_ok:
        reason = "davey is unavailable"
    elif not inbound_dave:
        reason = "voice receive dependency does not expose the guarded inbound DAVE decoder"
    else:
        reason = "ready"

    return VoiceReceiveCapability(
        available=available,
        reason=reason,
        discord_py_version=str(getattr(discord, "__version__", "unknown")),
        dave_available=dave_ok,
        receive_extension_available=receive_ok,
        inbound_dave_decrypt_available=inbound_dave,
        opus_available=opus_ok,
        opus_library=opus_library,
    )


def voice_receive_connection_diagnostics(voice_client: Any) -> dict[str, Any]:
    connection = getattr(voice_client, "_connection", None)
    session = getattr(connection, "dave_session", None) if connection is not None else None
    status = getattr(session, "status", None) if session is not None else None
    status_name = (
        str(getattr(status, "name", "") or "").strip()
        or str(status or "").strip()
        or "none"
    )
    try:
        protocol_version = int(getattr(connection, "dave_protocol_version", 0) or 0)
    except (TypeError, ValueError):
        protocol_version = 0
    try:
        epoch = int(getattr(session, "epoch", 0) or 0) if session is not None else 0
    except (TypeError, ValueError):
        epoch = 0
    try:
        mapped_ssrcs = len(getattr(voice_client, "_ssrc_to_id", {}) or {})
    except Exception:
        mapped_ssrcs = 0
    try:
        listening = bool(getattr(voice_client, "is_listening", lambda: False)())
    except Exception:
        listening = False
    return {
        "dave_session_present": session is not None,
        "dave_session_ready": bool(getattr(session, "ready", False)) if session is not None else False,
        "dave_session_status": status_name,
        "dave_protocol_version": protocol_version,
        "dave_epoch": epoch,
        "mapped_ssrcs": mapped_ssrcs,
        "reader_listening": listening,
        "reader_error": str(getattr(voice_client, "_dank_caption_reader_error", "") or "")[:240],
    }


def _safe_reader_error(error: Optional[BaseException]) -> str:
    if error is None:
        return ""
    name = type(error).__name__
    message = str(error).strip()
    if message:
        return f"{name}: {message}"[:240]
    return name[:240]


def _install_voice_recv_router_survival_patch() -> bool:
    """Keep one corrupt Opus packet from killing the entire receive reader.

    Upstream issue #43 and PR #57 document PacketRouter's fail-stop behavior:
    decoder.pop_data() can raise OpusError for a single malformed/corrupt frame,
    and PacketRouter.run() then tears down listening for the whole voice session.
    We catch only OpusError here. Unexpected exceptions retain upstream fail-stop
    behavior so real implementation bugs are still visible.
    """

    if voice_recv is None:
        return False

    try:
        from discord.ext.voice_recv.router import PacketRouter
        from discord.opus import OpusError
    except Exception:
        return False

    if bool(getattr(PacketRouter, "_dank_opus_survival_patch", False)):
        return True

    def _do_run(self: Any) -> None:
        while not self._end_thread.is_set():
            self.waiter.wait()
            with self._lock:
                for decoder in self.waiter.items:
                    try:
                        data = decoder.pop_data()
                    except OpusError as exc:
                        bridge = getattr(self.sink, "bridge", None)
                        if bridge is not None:
                            try:
                                bridge._increment("opus_decode_drops")
                            except Exception:
                                pass
                        log.debug(
                            "Live Captions dropped corrupt Opus packet ssrc=%s error=%s",
                            getattr(decoder, "ssrc", "?"),
                            exc,
                        )
                        continue
                    if data is not None:
                        self.sink.write(data.source, data)

    PacketRouter._do_run = _do_run
    setattr(PacketRouter, "_dank_opus_survival_patch", True)
    return True


def _install_raw_udp_probe(voice_client: Any, bridge: "PerSpeakerFrameBridge") -> None:
    connection = getattr(voice_client, "_connection", None)
    add_listener = getattr(connection, "add_socket_listener", None)
    remove_listener = getattr(connection, "remove_socket_listener", None)
    if not callable(add_listener):
        return

    previous = getattr(voice_client, "_dank_caption_udp_probe", None)
    if previous is not None and callable(remove_listener):
        try:
            remove_listener(previous)
        except Exception:
            log.debug("Live Captions could not remove previous UDP probe", exc_info=True)

    def _probe(_packet_data: bytes) -> None:
        bridge._increment("raw_udp_packets")

    add_listener(_probe)
    setattr(voice_client, "_dank_caption_udp_probe", _probe)


class PerSpeakerFrameBridge:
    """Thread-safe consent and frame boundary between voice_recv and asyncio.

    The voice receive package invokes sinks from its packet-router thread.
    Captioning lives on the bot event loop. This bridge performs identity and
    consent checks before copying PCM into the asyncio side.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        callback: Callable[[SpeakerPCMFrame], None],
    ) -> None:
        self.loop = loop
        self.callback = callback
        self.health = VoiceReceiveHealth()
        self._allowed_user_ids: set[int] = set()
        self._consent_generation: dict[int, int] = {}
        self._lock = threading.RLock()

    def _advance_consent_generation(self, user_id: int) -> int:
        uid = int(user_id)
        current = int(self._consent_generation.get(uid, 0)) + 1
        self._consent_generation[uid] = current
        return current

    def opt_in(self, user_id: int) -> None:
        with self._lock:
            uid = int(user_id)
            self._advance_consent_generation(uid)
            self._allowed_user_ids.add(uid)

    def opt_out(self, user_id: int) -> None:
        with self._lock:
            uid = int(user_id)
            self._advance_consent_generation(uid)
            self._allowed_user_ids.discard(uid)

    def clear_consent(self) -> None:
        with self._lock:
            for uid in tuple(self._allowed_user_ids):
                self._advance_consent_generation(uid)
            self._allowed_user_ids.clear()

    def is_opted_in(self, user_id: int) -> bool:
        with self._lock:
            return int(user_id) in self._allowed_user_ids

    def opted_in_user_ids(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(sorted(self._allowed_user_ids))

    def _increment(self, field: str) -> None:
        with self._lock:
            setattr(self.health, field, int(getattr(self.health, field)) + 1)

    def accept(
        self,
        *,
        source_user_id: Optional[int],
        mapped_user_id: Optional[int],
        ssrc: int,
        sequence: int,
        rtp_timestamp: int,
        pcm: bytes,
    ) -> bool:
        self._increment("frames_seen")

        if not source_user_id or not mapped_user_id:
            self._increment("frames_unknown_source")
            return False
        if int(source_user_id) != int(mapped_user_id):
            self._increment("frames_source_mismatch")
            return False
        uid = int(source_user_id)
        with self._lock:
            if uid not in self._allowed_user_ids:
                self._increment("frames_not_consented")
                return False
            consent_generation = int(self._consent_generation.get(uid, 0))
        if not pcm:
            self._increment("frames_empty")
            return False
        if len(pcm) % PCM_FRAME_ALIGNMENT:
            self._increment("frames_malformed_pcm")
            return False

        frame = SpeakerPCMFrame(
            user_id=int(source_user_id),
            ssrc=int(ssrc),
            sequence=int(sequence),
            rtp_timestamp=int(rtp_timestamp),
            pcm=bytes(pcm),
            received_at=time.monotonic(),
        )

        def _deliver() -> None:
            with self._lock:
                consent_still_valid = (
                    frame.user_id in self._allowed_user_ids
                    and int(self._consent_generation.get(frame.user_id, 0))
                    == consent_generation
                )
            if not consent_still_valid:
                self._increment("frames_not_consented")
                return
            try:
                self.callback(frame)
                self._increment("frames_routed")
            except asyncio.QueueFull:
                self._increment("queue_overflow")
            except Exception:
                self._increment("callback_failures")
                log.exception(
                    "Live Captions frame callback failed user=%s ssrc=%s",
                    frame.user_id,
                    frame.ssrc,
                )

        try:
            self.loop.call_soon_threadsafe(_deliver)
        except RuntimeError:
            self._increment("callback_failures")
            return False

        return True


if voice_recv is not None:

    class HardenedPerSpeakerSink(voice_recv.AudioSink):
        """PCM sink that rejects ambiguous speaker identity before captioning."""

        def __init__(self, bridge: PerSpeakerFrameBridge) -> None:
            super().__init__()
            self.bridge = bridge

        def wants_opus(self) -> bool:
            return False

        def write(self, user: Any, data: Any) -> None:
            packet = getattr(data, "packet", None)
            voice_client = self.voice_client
            if packet is None or voice_client is None:
                self.bridge._increment("frames_unknown_source")
                return

            ssrc = int(getattr(packet, "ssrc", 0) or 0)
            mapped_user_id: Optional[int] = None
            try:
                mapped_user_id = voice_client._get_id_from_ssrc(ssrc)
            except Exception:
                mapped_user_id = None

            source_user_id = int(getattr(user, "id", 0) or 0) or None
            self.bridge.accept(
                source_user_id=source_user_id,
                mapped_user_id=mapped_user_id,
                ssrc=ssrc,
                sequence=int(getattr(packet, "sequence", 0) or 0),
                rtp_timestamp=int(getattr(packet, "timestamp", 0) or 0),
                pcm=bytes(getattr(data, "pcm", b"") or b""),
            )

        def cleanup(self) -> None:
            self.bridge.clear_consent()

else:

    class HardenedPerSpeakerSink:  # pragma: no cover - only used without dependency
        def __init__(self, bridge: PerSpeakerFrameBridge) -> None:
            self.bridge = bridge
            raise VoiceReceiveUnavailable(voice_receive_capability().reason)


async def connect_receive_client(
    channel: discord.VoiceChannel,
    bridge: PerSpeakerFrameBridge,
) -> Any:
    capability = voice_receive_capability()
    if not capability.available or voice_recv is None:
        raise VoiceReceiveUnavailable(capability.reason)

    existing = channel.guild.voice_client
    if existing is not None:
        if int(getattr(getattr(existing, "channel", None), "id", 0) or 0) != int(channel.id):
            raise VoiceReceiveUnavailable(
                "Dank Shield is already connected to a different voice channel in this server."
            )
        if not isinstance(existing, voice_recv.VoiceRecvClient):
            raise VoiceReceiveUnavailable(
                "The existing Discord voice connection cannot receive Live Captions."
            )
        voice_client = existing
    else:
        voice_client = await channel.connect(
            cls=voice_recv.VoiceRecvClient,
            self_deaf=False,
            self_mute=True,
        )

    if voice_client.is_listening():
        raise VoiceReceiveUnavailable(
            "Dank Shield is already receiving audio for another caption session in this server."
        )

    if not _install_voice_recv_router_survival_patch():
        raise VoiceReceiveUnavailable(
            "voice receive router survival patch could not be installed"
        )

    _install_raw_udp_probe(voice_client, bridge)

    def _after_reader(error: Optional[Exception]) -> None:
        if error is None:
            return
        bridge._increment("reader_failures")
        safe_error = _safe_reader_error(error)
        setattr(voice_client, "_dank_caption_reader_error", safe_error)
        log.error("Live Captions receive reader stopped: %s", safe_error)

    setattr(voice_client, "_dank_caption_reader_error", "")
    voice_client.listen(HardenedPerSpeakerSink(bridge), after=_after_reader)
    return voice_client


def disconnect_receive_client(voice_client: Any) -> None:
    connection = getattr(voice_client, "_connection", None)
    probe = getattr(voice_client, "_dank_caption_udp_probe", None)
    remove_listener = getattr(connection, "remove_socket_listener", None)
    if probe is not None and callable(remove_listener):
        try:
            remove_listener(probe)
        except Exception:
            log.debug("Live Captions failed to remove UDP probe cleanly", exc_info=True)
        try:
            delattr(voice_client, "_dank_caption_udp_probe")
        except Exception:
            pass

    try:
        if getattr(voice_client, "is_listening", lambda: False)():
            voice_client.stop_listening()
    except Exception:
        log.exception("Live Captions failed to stop voice receive cleanly")


__all__ = [
    "BUNDLED_OPUS_DISTRIBUTION",
    "HardenedPerSpeakerSink",
    "PCM_CHANNELS",
    "PCM_SAMPLE_RATE",
    "PCM_SAMPLE_WIDTH",
    "PerSpeakerFrameBridge",
    "SpeakerPCMFrame",
    "VOICE_RECV_DAVE_COMMIT",
    "VOICE_RECV_DAVE_SOURCE",
    "VoiceReceiveCapability",
    "VoiceReceiveHealth",
    "VoiceReceiveUnavailable",
    "bundled_opus_library_path",
    "connect_receive_client",
    "disconnect_receive_client",
    "ensure_opus_loaded",
    "voice_receive_capability",
    "voice_receive_connection_diagnostics",
]
