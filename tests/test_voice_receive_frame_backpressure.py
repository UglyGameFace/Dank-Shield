"""Reader-thread backpressure and consent regression tests for voice PCM."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import stoney_verify.community_voice_receive as receive


class _QueuedLoop:
    def __init__(self) -> None:
        self.callbacks = []

    def call_soon_threadsafe(self, callback, *args) -> None:
        self.callbacks.append((callback, args))

    def run_all(self) -> None:
        callbacks = self.callbacks[:]
        self.callbacks.clear()
        for callback, args in callbacks:
            callback(*args)


class _ImmediateLoop:
    def call_soon_threadsafe(self, callback, *args) -> None:
        callback(*args)


def _submit(bridge: receive.PerSpeakerFrameBridge, sequence: int, uid: int = 10) -> bool:
    return bridge.accept(
        source_user_id=uid,
        mapped_user_id=uid,
        ssrc=uid * 10,
        sequence=sequence,
        rtp_timestamp=sequence * 960,
        pcm=b"\x00" * (receive.PCM_FRAME_ALIGNMENT * 480),
    )


def test_reader_backpressure_caps_unrun_callbacks_and_recovers(monkeypatch) -> None:
    monkeypatch.setattr(receive, "_MAX_PENDING_FRAME_DISPATCHES", 3)
    loop = _QueuedLoop()
    delivered = []
    bridge = receive.PerSpeakerFrameBridge(loop, delivered.append)
    bridge.opt_in(10)

    # A stalled asyncio loop cannot grow its callback queue indefinitely.
    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda i: _submit(bridge, i), range(40)))

    assert outcomes.count(True) == 3
    assert outcomes.count(False) == 37
    assert len(loop.callbacks) == 3
    status = bridge.health.snapshot()
    assert status["dispatch_pending"] == 3
    assert status["dispatch_pending_peak"] == 3
    assert status["dispatch_backpressure_drops"] == 37
    assert delivered == []

    loop.run_all()
    assert len(delivered) == 3
    assert bridge.health.dispatch_pending == 0
    assert _submit(bridge, 41)
    loop.run_all()
    assert len(delivered) == 4


def test_queued_revoked_pcm_is_dropped_and_capacity_released(monkeypatch) -> None:
    monkeypatch.setattr(receive, "_MAX_PENDING_FRAME_DISPATCHES", 2)
    loop = _QueuedLoop()
    delivered = []
    bridge = receive.PerSpeakerFrameBridge(loop, delivered.append)
    bridge.opt_in(10)
    bridge.opt_in(20)

    assert _submit(bridge, 1, 10)
    assert _submit(bridge, 2, 20)
    assert not _submit(bridge, 3, 20)

    # An old queued frame must never be delivered after opt-out and re-opt-in.
    bridge.opt_out(10)
    bridge.opt_in(10)
    loop.run_all()
    assert [frame.user_id for frame in delivered] == [20]
    assert bridge.health.frames_not_consented == 1
    assert bridge.health.dispatch_pending == 0
    assert _submit(bridge, 4, 10)
    loop.run_all()
    assert [frame.user_id for frame in delivered] == [20, 10]


def test_downstream_queue_overflow_does_not_leak_dispatch_slots(monkeypatch) -> None:
    monkeypatch.setattr(receive, "_MAX_PENDING_FRAME_DISPATCHES", 1)

    def full(_frame) -> None:
        raise asyncio.QueueFull

    bridge = receive.PerSpeakerFrameBridge(_ImmediateLoop(), full)
    bridge.opt_in(10)
    for sequence in range(5):
        assert _submit(bridge, sequence)
    assert bridge.health.queue_overflow == 5
    assert bridge.health.dispatch_pending == 0
    assert bridge.health.dispatch_backpressure_drops == 0

    delivered = []
    bridge.callback = delivered.append
    assert _submit(bridge, 6)
    assert len(delivered) == 1


def test_closed_loop_releases_reserved_slot(monkeypatch) -> None:
    monkeypatch.setattr(receive, "_MAX_PENDING_FRAME_DISPATCHES", 1)

    class _ClosedLoop:
        def call_soon_threadsafe(self, callback, *args) -> None:
            raise RuntimeError("Event loop is closed")

    bridge = receive.PerSpeakerFrameBridge(_ClosedLoop(), lambda frame: None)
    bridge.opt_in(10)
    assert not _submit(bridge, 1)
    assert bridge.health.callback_failures == 1
    assert bridge.health.dispatch_pending == 0

    delivered = []
    bridge.loop = _ImmediateLoop()
    bridge.callback = delivered.append
    assert _submit(bridge, 2)
    assert len(delivered) == 1
    assert bridge.health.dispatch_pending == 0
