from __future__ import annotations

"""Hardened per-speaker Discord voice receive boundary for Community Hub.

The external voice-receive package owns Discord UDP/RTP transport plumbing.
Dank Shield owns the safety boundary after DAVE decryption: every PCM frame
must still resolve to exactly one Discord user/SSRC pair before it can enter
captioning. Unknown, mismatched, malformed, or non-consented audio is dropped.

No audio is written to disk by this module.
"""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
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

# Exact reviewed receive implementation. This is PR #62's head, pinned in
# requirements.txt so upstream branch movement cannot silently change runtime.
VOICE_RECV_DAVE_COMMIT = "bec048127f4148fd147afa3182c3771b6955dc08"


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


def voice_receive_capability() -> VoiceReceiveCapability:
    receive_ok = voice_recv is not None
    inbound_dave = False
    if receive_ok:
        try:
            from discord.ext.voice_recv.reader import AudioReader

            inbound_dave = callable(getattr(AudioReader, "_dave_decrypt", None))
        except Exception:
            inbound_dave = False

    dave_ok = davey is not None
    available = bool(receive_ok and dave_ok and inbound_dave)
    if not receive_ok:
        reason = "discord-ext-voice-recv is unavailable"
    elif not dave_ok:
        reason = "davey is unavailable"
    elif not inbound_dave:
        reason = "voice receive dependency does not expose inbound DAVE decryption"
    else:
        reason = "ready"

    return VoiceReceiveCapability(
        available=available,
        reason=reason,
        discord_py_version=str(getattr(discord, "__version__", "unknown")),
        dave_available=dave_ok,
        receive_extension_available=receive_ok,
        inbound_dave_decrypt_available=inbound_dave,
    )


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
        self._lock = threading.RLock()

    def opt_in(self, user_id: int) -> None:
        with self._lock:
            self._allowed_user_ids.add(int(user_id))

    def opt_out(self, user_id: int) -> None:
        with self._lock:
            self._allowed_user_ids.discard(int(user_id))

    def clear_consent(self) -> None:
        with self._lock:
            self._allowed_user_ids.clear()

    def is_opted_in(self, user_id: int) -> bool:
        with self._lock:
            return int(user_id) in self._allowed_user_ids

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
        if not self.is_opted_in(int(source_user_id)):
            self._increment("frames_not_consented")
            return False
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
            try:
                self.callback(frame)
            except asyncio.QueueFull:
                self._increment("queue_overflow")
            except Exception:
                self._increment("callback_failures")
                log.exception(
                    "Community Hub caption frame callback failed user=%s ssrc=%s",
                    frame.user_id,
                    frame.ssrc,
                )

        try:
            self.loop.call_soon_threadsafe(_deliver)
        except RuntimeError:
            self._increment("callback_failures")
            return False

        self._increment("frames_routed")
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
                "The existing Discord voice connection cannot receive Community Hub captions."
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

    voice_client.listen(HardenedPerSpeakerSink(bridge))
    return voice_client


def disconnect_receive_client(voice_client: Any) -> None:
    try:
        if getattr(voice_client, "is_listening", lambda: False)():
            voice_client.stop_listening()
    except Exception:
        log.exception("Community Hub failed to stop voice receive cleanly")


__all__ = [
    "HardenedPerSpeakerSink",
    "PCM_CHANNELS",
    "PCM_SAMPLE_RATE",
    "PCM_SAMPLE_WIDTH",
    "PerSpeakerFrameBridge",
    "SpeakerPCMFrame",
    "VOICE_RECV_DAVE_COMMIT",
    "VoiceReceiveCapability",
    "VoiceReceiveHealth",
    "VoiceReceiveUnavailable",
    "connect_receive_client",
    "disconnect_receive_client",
    "voice_receive_capability",
]
