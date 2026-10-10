from __future__ import annotations

"""Automatic Cinema source selection must account for release risks before probe."""

import asyncio

from stoney_verify import cinema_playback_service as playback
from stoney_verify.media_metadata import parse_release_name
from stoney_verify.movie_night import MovieSourceVariant


def _variant(name: str, provider: str = "source-a", *, verified=None) -> MovieSourceVariant:
    metadata = {
        "release_name": parse_release_name(name),
        "source_reported_verified": False,
    }
    if verified is not None:
        metadata["verified"] = verified
    return MovieSourceVariant(
        variant_id=name,
        source_ref="magnet:?xt=urn:btih:test",
        created_at=100.0,
        source_id=provider,
        source_label=provider,
        seeds=9,
        peers=9,
        metadata=metadata,
    )


def test_unsupported_video_clues_are_risky_before_torrent_metadata_download() -> None:
    hevc = _variant("Mad.Max.Fury.Road.2015.1080p.HEVC.DDP5.1.mkv")
    x265 = _variant("Mad.Max.Fury.Road.2015.1080p.x265.mp4")
    mkv = _variant("Mad.Max.Fury.Road.2015.1080p.H264.mkv")
    h264 = _variant("Mad.Max.Fury.Road.2015.1080p.x264.mp4")
    unknown = _variant("Mad.Max.Fury.Road.2015.1080p.BluRay")
    assert hevc.browser_video_risk_key() == 2
    assert x265.browser_video_risk_key() == 2
    assert mkv.browser_video_risk_key() == 2
    assert h264.browser_video_risk_key() == 1  # labels are NEVER verified safe
    assert unknown.browser_video_risk_key() == 1


def test_untrusted_provider_hints_can_rule_out_obvious_risks_not_prove_safety() -> None:
    variant = _variant("Mad.Max.Fury.Road.2015.1080p.WEBRip")
    variant.metadata["source_reported"] = {"codec": "hevc", "filename": "release.mp4"}
    assert variant.browser_video_risk_key() == 2
    variant.metadata["source_reported"] = {"video_codec": "h264", "filename": "release.mp4"}
    assert variant.browser_video_risk_key() == 1
    variant.metadata["source_reported"] = {"filename": "release.avi"}
    assert variant.browser_video_risk_key() == 2
    variant.metadata["verified"] = {
        "available": True,
        "filename": "release.mp4",
        "container": "mov,mp4,m4a,3gp,3g2,mj2",
        "video": {"codec": "h264", "bit_depth": 8},
    }
    assert variant.browser_video_risk_key() == 0


def test_auto_selection_bypasses_risky_preferred_provider(monkeypatch) -> None:
    risky = _variant("Mad.Max.Fury.Road.2015.HEVC.mkv", provider="preferred")
    fallback = _variant("Mad.Max.Fury.Road.2015.H264.mp4", provider="other")

    async def fake_profile(_user_id):
        return {"preferences": {"preferred_source": "preferred"}}

    monkeypatch.setattr(playback, "get_cinema_user", fake_profile)
    result = asyncio.run(playback.select_preferred_variant(42, [risky, fallback]))
    assert result is fallback


def test_auto_selection_refuses_only_explicitly_risky_releases(monkeypatch) -> None:
    risky = _variant("Mad.Max.Fury.Road.2015.x265.mkv")

    async def fake_profile(_user_id):
        return {"preferences": {"preferred_source": "source-a"}}

    monkeypatch.setattr(playback, "get_cinema_user", fake_profile)
    assert asyncio.run(playback.select_preferred_variant(42, [risky])) is None
    # Unknown is not equivalent to verified compatible and may still need
    # post-download verification. Keep existing admission for unknown.
    unknown = _variant("Mad.Max.Fury.Road.2015.1080p")
    assert asyncio.run(playback.select_preferred_variant(42, [risky, unknown])) is unknown
