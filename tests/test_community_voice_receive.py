from __future__ import annotations

from stoney_verify.community_voice_receive import (
    PCM_FRAME_ALIGNMENT,
    PerSpeakerFrameBridge,
    VOICE_RECV_DAVE_COMMIT,
    voice_receive_capability,
)


class _ImmediateLoop:
    def call_soon_threadsafe(self, callback):
        callback()


def test_voice_dependencies_are_pinned_for_dave_receive() -> None:
    capability = voice_receive_capability()
    assert capability.discord_py_version == "2.7.1"
    assert capability.dave_available is True
    assert capability.receive_extension_available is True
    assert capability.inbound_dave_decrypt_available is True
    assert capability.available is True
    assert VOICE_RECV_DAVE_COMMIT == "bec048127f4148fd147afa3182c3771b6955dc08"


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
