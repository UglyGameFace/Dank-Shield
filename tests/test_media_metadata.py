from __future__ import annotations

import asyncio

from stoney_verify.media_metadata import (
    format_duration,
    parse_ffprobe_payload,
    parse_release_name,
    probe_media_file,
)


def test_release_name_parses_web_dl_title_year_resolution_audio_and_group() -> None:
    meta = parse_release_name(
        "The.Matrix.1999.1080p.WEB-DL.DDP5.1.Atmos.H.264-NTb.mkv"
    )

    assert meta["origin"] == "release_name"
    assert meta["title"] == "The Matrix"
    assert meta["year"] == 1999
    assert meta["resolution"] == "1080p"
    assert meta["source"] == "WEB-DL"
    assert "WEB-DL" in meta["source_tags"]
    assert "H.264" in meta["video_tags"]
    assert "DDP 5.1" in meta["audio_tags"]
    assert "Atmos" in meta["audio_tags"]
    assert meta["release_group"] == "NTb"


def test_release_name_distinguishes_cam_webrip_dvdrip_and_remux_claims() -> None:
    cam = parse_release_name("Movie.Name.2026.1080p.CAM.x264-GRP.mkv")
    webrip = parse_release_name("Movie.Name.2026.1080p.WEBRip.x265-GRP.mkv")
    dvd = parse_release_name("Movie.Name.2008.DVDRip.XviD-GRP.avi")
    remux = parse_release_name(
        "Movie.Name.2026.2160p.BluRay.REMUX.DV.HDR10.HEVC-GRP.mkv"
    )

    assert cam["source"] == "CAM"
    assert webrip["source"] == "WEBRip"
    assert dvd["source"] == "DVDRip"
    assert "BluRay" in remux["source_tags"]
    assert "REMUX" in remux["source_tags"]
    assert "Dolby Vision" in remux["hdr_tags"]
    assert "HDR10" in remux["hdr_tags"]
    assert "HEVC" in remux["video_tags"]


def test_release_name_parses_season_episode() -> None:
    meta = parse_release_name(
        "Example.Show.S02E05.2160p.WEB-DL.DDP5.1.HEVC-GROUP.mkv"
    )

    assert meta["title"] == "Example Show"
    assert meta["season"] == 2
    assert meta["episode"] == 5
    assert meta["resolution"] == "2160p"


def test_verified_ffprobe_payload_extracts_real_stream_metadata() -> None:
    payload = {
        "format": {
            "format_name": "matroska,webm",
            "format_long_name": "Matroska / WebM",
            "duration": "7265.125",
            "bit_rate": "5400000",
            "tags": {
                "title": "Verified Movie Title",
                "date": "2025",
            },
        },
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "codec_long_name": "H.265 / HEVC",
                "profile": "Main 10",
                "width": 3840,
                "height": 2160,
                "avg_frame_rate": "24000/1001",
                "pix_fmt": "yuv420p10le",
                "color_space": "bt2020nc",
                "color_transfer": "smpte2084",
                "color_primaries": "bt2020",
                "field_order": "progressive",
                "side_data_list": [
                    {
                        "side_data_type": "DOVI configuration record",
                        "dv_profile": 8,
                    }
                ],
            },
            {
                "codec_type": "audio",
                "codec_name": "truehd",
                "profile": "",
                "channels": 8,
                "channel_layout": "7.1",
                "sample_rate": "48000",
                "tags": {
                    "language": "eng",
                    "title": "TrueHD Atmos",
                },
            },
            {
                "codec_type": "audio",
                "codec_name": "eac3",
                "channels": 6,
                "channel_layout": "5.1(side)",
                "sample_rate": "48000",
                "tags": {"language": "spa"},
            },
            {
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {
                    "language": "eng",
                    "title": "English",
                },
            },
        ],
        "chapters": [{}, {}, {}],
    }

    meta = parse_ffprobe_payload(
        payload,
        filename="Verified.Movie.2025.2160p.mkv",
        file_size=7_500_000_000,
    )

    assert meta["origin"] == "verified_file"
    assert meta["duration_seconds"] == 7265.125
    assert meta["duration"] == "2:01:05"
    assert meta["bitrate"] == 5_400_000
    assert meta["embedded_title"] == "Verified Movie Title"
    assert meta["embedded_year"] == 2025
    assert meta["container"] == "matroska,webm"
    assert meta["file_size"] == 7_500_000_000

    video = meta["video"]
    assert video["codec"] == "hevc"
    assert video["profile"] == "Main 10"
    assert video["resolution"] == "3840x2160"
    assert video["fps"] == 23.976
    assert video["bit_depth"] == 10
    assert video["color_transfer"] == "smpte2084"
    assert "HDR10/PQ" in video["hdr"]
    assert "Dolby Vision" in video["hdr"]

    assert meta["audio_tracks"][0]["codec"] == "truehd"
    assert meta["audio_tracks"][0]["channels"] == 8
    assert meta["audio_tracks"][0]["layout"] == "7.1"
    assert meta["audio_languages"] == ["eng", "spa"]
    assert meta["subtitle_languages"] == ["eng"]
    assert meta["chapters"] == 3


def test_verified_and_release_metadata_origins_stay_distinct() -> None:
    release = parse_release_name("Fake.Name.2025.1080p.CAM.x264-GRP.mkv")
    verified = parse_ffprobe_payload(
        {
            "format": {"duration": "60.0", "format_name": "matroska"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "24/1",
                }
            ],
        },
        filename="Fake.Name.2025.1080p.CAM.x264-GRP.mkv",
        file_size=1000,
    )

    assert release["origin"] == "release_name"
    assert release["source"] == "CAM"
    assert verified["origin"] == "verified_file"
    assert "source" not in verified
    assert verified["video"]["codec"] == "hevc"


def test_format_duration_handles_hour_and_short_media() -> None:
    assert format_duration(65) == "1:05"
    assert format_duration(3661) == "1:01:01"


def test_probe_reports_unavailable_when_ffprobe_missing(monkeypatch, tmp_path) -> None:
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"not-real-media")
    monkeypatch.setattr("stoney_verify.media_metadata.shutil.which", lambda _name: None)

    result = asyncio.run(
        probe_media_file(
            media,
            filename=media.name,
            timeout_seconds=2,
        )
    )

    assert result["available"] is False
    assert result["reason"] == "ffprobe_not_installed"
