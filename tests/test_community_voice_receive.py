from __future__ import annotations

import threading
from types import SimpleNamespace

from discord.opus import OpusError

import stoney_verify.community_voice_receive as voice_receive
from stoney_verify.community_voice_receive import (
    PCM_FRAME_ALIGNMENT,
    PerSpeakerFrameBridge,
    VOICE_RECV_DAVE_COMMIT,
    VOICE_RECV_DAVE_SOURCE,
    _install_voice_recv_router_survival_patch,
    ensure_opus_loaded,
    voice_receive_capability,
    voice_receive_connection_diagnostics,
)


class _ImmediateLoop:
    def call_soon_threadsafe(self, callback):
        callback()


class _QueuedLoop:
    def __init__(self) -> None:
        self.callbacks = []

    def call_soon_threadsafe(self, callback):
        self.callbacks.append(callback)

    def run_all(self) -> None:
        callbacks = list(self.callbacks)
        self.callbacks.clear()
        for callback in callbacks:
            callback()


def test_voice_dependencies_are_pinned_for_dave_receive(monkeypatch) -> None:
    monkeypatch.setattr(voice_receive, "ensure_opus_loaded", lambda: (True, "test-opus"))
    capability = voice_receive_capability()
    assert capability.discord_py_version == "2.7.1"
    assert capability.dave_available is True
    assert capability.receive_extension_available is True
    assert capability.inbound_dave_decrypt_available is True
    assert capability.opus_available is True
    assert capability.opus_library == "test-opus"
    assert capability.available is True
    assert VOICE_RECV_DAVE_COMMIT == "03dd1e2dafe85522cc458441cd5b143b136ac836"
    assert VOICE_RECV_DAVE_SOURCE == "imayhaveborkedit/discord-ext-voice-recv#58"


def test_opus_loader_uses_discovered_native_library(monkeypatch) -> None:
    state = {"loaded": False, "attempts": []}

    monkeypatch.delenv("DANK_OPUS_LIBRARY", raising=False)
    monkeypatch.setattr(voice_receive.discord.opus, "is_loaded", lambda: state["loaded"])
    monkeypatch.setattr(
        voice_receive.ctypes.util,
        "find_library",
        lambda name: "libopus.so.0" if name == "opus" else None,
    )

    def fake_load(name: str) -> None:
        state["attempts"].append(name)
        if name == "libopus.so.0":
            state["loaded"] = True

    monkeypatch.setattr(voice_receive.discord.opus, "load_opus", fake_load)

    ok, library = ensure_opus_loaded()

    assert ok is True
    assert library == "libopus.so.0"
    assert state["attempts"] == ["libopus.so.0"]


def test_opus_loader_fails_closed_when_native_library_is_absent(monkeypatch) -> None:
    monkeypatch.delenv("DANK_OPUS_LIBRARY", raising=False)
    monkeypatch.setattr(voice_receive.discord.opus, "is_loaded", lambda: False)
    monkeypatch.setattr(voice_receive.ctypes.util, "find_library", lambda _name: None)
    monkeypatch.setattr(
        voice_receive.discord.opus,
        "load_opus",
        lambda _name: (_ for _ in ()).throw(OSError("missing")),
    )

    ok, library = ensure_opus_loaded()

    assert ok is False
    assert library == "not-found"


def test_voice_receive_connection_diagnostics_report_dave_and_ssrc_state() -> None:
    class _Status:
        name = "active"

    session = type(
        "Session",
        (),
        {"ready": True, "status": _Status(), "epoch": 7},
    )()
    connection = type(
        "Connection",
        (),
        {"dave_session": session, "dave_protocol_version": 1},
    )()
    voice_client = type(
        "VoiceClient",
        (),
        {
            "_connection": connection,
            "_ssrc_to_id": {100: 10, 200: 20},
            "is_listening": lambda self: True,
        },
    )()

    result = voice_receive_connection_diagnostics(voice_client)
    assert result == {
        "dave_session_present": True,
        "dave_session_ready": True,
        "dave_session_status": "active",
        "dave_protocol_version": 1,
        "dave_epoch": 7,
        "mapped_ssrcs": 2,
        "reader_listening": True,
        "reader_error": "",
    }


def test_router_drops_corrupt_opus_packet_without_stopping_reader() -> None:
    from discord.ext.voice_recv.router import PacketRouter

    assert _install_voice_recv_router_survival_patch() is True

    delivered = []
    bridge = PerSpeakerFrameBridge(_ImmediateLoop(), delivered.append)

    class _OnePassEnd:
        def __init__(self) -> None:
            self.calls = 0

        def is_set(self) -> bool:
            self.calls += 1
            return self.calls > 1

    class _Waiter:
        def __init__(self, items) -> None:
            self.items = items

        def wait(self) -> None:
            return None

    class _BadDecoder:
        ssrc = 100

        def pop_data(self):
            exc = OpusError.__new__(OpusError)
            Exception.__init__(exc, "corrupted stream")
            exc.code = -4
            raise exc

    class _GoodDecoder:
        ssrc = 200

        def pop_data(self):
            return SimpleNamespace(source=SimpleNamespace(id=20))

    class _Sink:
        def __init__(self) -> None:
            self.bridge = bridge
            self.writes = []

        def write(self, source, data) -> None:
            self.writes.append((source, data))

    sink = _Sink()
    fake_router = SimpleNamespace(
        _end_thread=_OnePassEnd(),
        waiter=_Waiter([_BadDecoder(), _GoodDecoder()]),
        _lock=threading.RLock(),
        sink=sink,
    )

    PacketRouter._do_run(fake_router)

    assert bridge.health.opus_decode_drops == 1
    assert len(sink.writes) == 1
    assert sink.writes[0][0].id == 20


def test_non_consented_audio_never_crosses_caption_boundary() -> None:
    delivered = []
    bridge = PerSpeakerFrameBridge(_ImmediateLoop(), delivered.append)

    ok = bridge.accept(
        source_user_id=10,
        mapped_user_id=10,
        ssrc=100,
        sequence=1,
        rtp_timestamp=960,
        pcm=b"\\x00" * (PCM_FRAME_ALIGNMENT * 10),
    )
    assert ok is False
    assert delivered == []
    assert bridge.health.frames_not_consented == 1


def test_ssrc_user_mismatch_is_dropped_not_mixed() -> None:
    delivered = []
    bridge = PerSpeakerFrameBridge(_ImmediateLoop(), delivered.append)
    bridge.opt_in(10)
    bridge.opt_in(20)

    assert bridge.accept(
        source_user_id=10,
        mapped_user_id=20,
        ssrc=100,
        sequence=1,
        rtp_timestamp=960,
        pcm=b"\\x01" * (PCM_FRAME_ALIGNMENT * 10),
    ) is False
    assert delivered == []
    assert bridge.health.frames_source_mismatch == 1


def test_interleaved_speakers_remain_separate_frames() -> None:
    delivered = []
    bridge = PerSpeakerFrameBridge(_ImmediateLoop(), delivered.append)
    bridge.opt_in(10)
    bridge.opt_in(20)

    assert bridge.accept(
        source_user_id=10,
        mapped_user_id=10,
        ssrc=100,
        sequence=1,
        rtp_timestamp=960,
        pcm=b"\\x01" * (PCM_FRAME_ALIGNMENT * 10),
    )
    assert bridge.accept(
        source_user_id=20,
        mapped_user_id=20,
        ssrc=200,
        sequence=1,
        rtp_timestamp=960,
        pcm=b"\\x02" * (PCM_FRAME_ALIGNMENT * 10),
    )

    assert [frame.user_id for frame in delivered] == [10, 20]
    assert [frame.ssrc for frame in delivered] == [100, 200]
    assert delivered[0].pcm != delivered[1].pcm
    assert bridge.health.frames_routed == 2


def test_malformed_pcm_is_dropped_before_transcription() -> None:
    delivered = []
    bridge = PerSpeakerFrameBridge(_ImmediateLoop(), delivered.append)
    bridge.opt_in(10)

    assert bridge.accept(
        source_user_id=10,
        mapped_user_id=10,
        ssrc=100,
        sequence=1,
        rtp_timestamp=960,
        pcm=b"abc",
    ) is False
    assert delivered == []
    assert bridge.health.frames_malformed_pcm == 1


def test_stale_scheduled_frame_cannot_survive_opt_out_and_reopt_in() -> None:
    delivered = []
    loop = _QueuedLoop()
    bridge = PerSpeakerFrameBridge(loop, delivered.append)
    bridge.opt_in(10)

    assert bridge.accept(
        source_user_id=10,
        mapped_user_id=10,
        ssrc=100,
        sequence=1,
        rtp_timestamp=960,
        pcm=b"\x01" * (PCM_FRAME_ALIGNMENT * 10),
    ) is True
    assert delivered == []

    # Revoke and later re-grant consent before the already-scheduled callback
    # runs. The old frame belongs to the old consent generation and must die.
    bridge.opt_out(10)
    bridge.opt_in(10)
    loop.run_all()

    assert delivered == []
    assert bridge.health.frames_routed == 0
    assert bridge.health.frames_not_consented == 1

    # A new frame created under the new generation is allowed.
    assert bridge.accept(
        source_user_id=10,
        mapped_user_id=10,
        ssrc=100,
        sequence=2,
        rtp_timestamp=1920,
        pcm=b"\x02" * (PCM_FRAME_ALIGNMENT * 10),
    ) is True
    loop.run_all()

    assert len(delivered) == 1
    assert delivered[0].sequence == 2
    assert bridge.health.frames_routed == 1
