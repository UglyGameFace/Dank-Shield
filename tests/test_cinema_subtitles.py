import pytest

from stoney_verify.cinema_subtitles import srt_to_vtt


def test_srt_to_vtt_safe_and_timed():
    value = b"1\n00:00:01,000 --> 00:00:03,250\nHello <script>alert(1)</script> & friends\n\n2\n00:00:04,000 --> 00:00:05,000\nBonjour\n"
    result = srt_to_vtt(value)
    assert result.startswith("WEBVTT\n\n")
    assert "00:00:01.000 --> 00:00:03.250" in result
    assert "&lt;script&gt;" in result
    assert "<script>" not in result
    assert "Bonjour" in result


@pytest.mark.parametrize("data", [
    b"", b"1\n00:99:00,000 --> 00:00:01,000\nNope",
    b"1\n00:00:03,000 --> 00:00:01,000\nNope",
    b"1\n00:00:00,000 --> 00:00:01,000\n\x00",
])
def test_reject_invalid_subtitle(data):
    with pytest.raises(ValueError):
        srt_to_vtt(data)


def test_reject_oversized_subtitle():
    with pytest.raises(ValueError, match="too large"):
        srt_to_vtt(b"x" * 20, max_bytes=10)
