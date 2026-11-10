"""Memory and byte-fidelity regressions for bulk Discord PCM operations."""

from __future__ import annotations

import random
import struct
import tracemalloc

from stoney_verify import community_voice_captions as captions


def _old_convert(pcm: bytes) -> bytes:
    """Reference for the pre-optimization FIR behavior; independent of arrays."""
    usable = len(pcm) - (len(pcm) % 4)
    if not usable:
        return b""
    samples = struct.unpack("<" + ("h" * (usable // 2)), pcm[:usable])
    mono = [
        int(round((int(samples[i]) + int(samples[i + 1])) / 2.0))
        for i in range(0, len(samples), 2)
    ]
    coeffs = captions._PCM48_TO_16_FIR
    half = len(coeffs) // 2
    last = len(mono) - 1
    out = []
    for output_index in range((len(mono) + 2) // 3):
        center = output_index * 3
        acc = 0.0
        for tap_index, coefficient in enumerate(coeffs):
            source = center + tap_index - half
            value = mono[0] if source < 0 else mono[last] if source > last else mono[source]
            acc += coefficient * float(value)
        out.append(max(-32768, min(32767, int(round(acc)))))
    return struct.pack("<" + ("h" * len(out)), *out) if out else b""


def _old_normalize(pcm: bytes, target_peak: int = 26000) -> bytes:
    if not pcm or len(pcm) % 2:
        return pcm
    count = len(pcm) // 2
    samples = struct.unpack("<" + ("h" * count), pcm)
    peak = max((abs(value) for value in samples), default=0)
    if peak <= 0 or peak >= target_peak:
        return pcm
    gain = min(4.0, float(target_peak) / float(peak))
    if gain <= 1.05:
        return pcm
    normalized = [
        max(-32768, min(32767, int(round(value * gain))))
        for value in samples
    ]
    return struct.pack("<" + ("h" * count), *normalized)


def _pcm(samples: int, seed: int = 23) -> bytes:
    r = random.Random(seed)
    return struct.pack("<" + ("h" * samples), *(r.randint(-32768, 32767) for _ in range(samples)))


def test_bulk_fir_pcm_output_matches_existing_sample_for_sample() -> None:
    for frames in (0, 1, 2, 3, 17, 480, 4800, 48000):
        pcm = _pcm(frames * 2)
        expected = _old_convert(pcm)
        assert captions.pcm48_stereo_to_pcm16_mono(pcm) == expected
        # Preserve existing truncation of one incomplete stereo sample.
        assert captions.pcm48_stereo_to_pcm16_mono(pcm + b"\x08") == expected


def test_bulk_amplitude_normalization_is_byte_exact() -> None:
    for frames in (0, 1, 17, 1920, 48000):
        pcm = _pcm(frames, seed=56)
        assert captions.normalize_pcm16_lossless_timing(pcm) == _old_normalize(pcm)
        # Force gain to exercise the compact normalization output path.
        quiet = struct.pack("<" + ("h" * frames), *([500] * frames))
        assert captions.normalize_pcm16_lossless_timing(quiet) == _old_normalize(quiet)
        assert captions.normalize_pcm16_lossless_timing(pcm + b"\x09") == pcm + b"\x09"


def test_bulk_pcm_conversion_limits_peak_temporary_python_allocations() -> None:
    # One second is representative of the per-segment allocations that scale
    # up to 8s and across concurrent Vosk transcription requests. Exclude
    # input allocation itself by starting tracing after constructing the bytes.
    pcm = _pcm(48000 * 2, seed=77)
    tracemalloc.start()
    try:
        result = captions.pcm48_stereo_to_pcm16_mono(pcm)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(result) == 32000
    assert peak < len(pcm) * 6, (
        f"whole-segment PCM converter allocated {peak} for {len(pcm)} input bytes"
    )
