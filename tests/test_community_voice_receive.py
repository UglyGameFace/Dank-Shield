from __future__ import annotations

from stoney_verify.community_voice_receive import (
    PCM_FRAME_ALIGNMENT,
    PerSpeakerFrameBridge,
    VOICE_RECV_DAVE_COMMIT,
    VOICE_RECV_DAVE_SOURCE,
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


def test_voice_dependencies_are_pinned_for_dave_receive() -> None:
    capability = voice_receive_capability()
    assert capability.discord_py_version == "2.7.1"
    assert capability.dave_available is True
    assert capability.receive_extension_available is True
    assert capability.inbound_dave_decrypt_available is True
    assert capability.available is True
    assert VOICE_RECV_DAVE_COMMIT == "78fcb434a3484f2abf54cf89e80e86b651e5c28d"
    assert VOICE_RECV_DAVE_SOURCE == "jstewart0788/discord-ext-voice-recv-dave"


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
    }


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
