from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_discloud_profile_is_site_capable_and_uses_current_ram_allocation() -> None:
    config = (ROOT / "discloud.config").read_text(encoding="utf-8")
    assert "TYPE=site" in config
    assert "MAIN=main.py" in config
    assert "RAM=1495" in config


def test_env_example_has_movie_night_dynamic_capacity_contract() -> None:
    env = (ROOT / ".env.example").read_text(encoding="utf-8")

    required = (
        "DANK_MEDIA_SERVER_ENABLED=true",
        "DANK_MEDIA_BIND_HOST=0.0.0.0",
        "DANK_MEDIA_PORT=8080",
        "DANK_PROCESS_MEMORY_LIMIT_MB=1495",
        "DANK_MOVIE_NIGHT_MEMORY_RESERVE_MB=350",
        "DANK_TORRENT_ESTIMATED_SESSION_MB=96",
        "DANK_TORRENT_SOFT_SESSION_LIMIT=2",
        "DANK_TORRENT_MAX_SESSIONS=4",
        "DANK_TORRENT_ALLOW_BURST=false",
        "DANK_TORRENT_MAX_FILE_BYTES=26843545600",
        "DANK_TORRENT_MAX_TOTAL_BYTES=53687091200",
        "DANK_TORRENT_DISK_RESERVE_BYTES=68719476736",
    )
    for line in required:
        assert line in env


def test_setup_surface_exposes_capacity_and_shared_session_telemetry() -> None:
    source = (
        ROOT / "stoney_verify" / "commands_ext" / "public_movie_night.py"
    ).read_text(encoding="utf-8")

    assert 'name="5 • Media capacity"' in source
    assert "current_rss_mb" in source
    assert "protected_reserve_mb" in source
    assert "active_unique_sessions" in source
    assert "total_leases" in source
    assert "shared_sessions" in source
    assert "session_slots_available" in source
    assert "disk_reserve_bytes" in source
    assert "committed_file_bytes" in source


def test_shared_torrent_architecture_keeps_one_canonical_runtime() -> None:
    source = (
        ROOT / "stoney_verify" / "torrent_streaming.py"
    ).read_text(encoding="utf-8")

    assert "_identity_index" in source
    assert "async def release_lease(" in source
    assert "async def _reuse_session(" in source
    assert "source_identity" in source
    assert "DANK_TORRENT_SOFT_SESSION_LIMIT" in source
    assert "DANK_MOVIE_NIGHT_MEMORY_RESERVE_MB" in source
    assert "DANK_TORRENT_DISK_RESERVE_BYTES" in source



def test_site_listener_can_start_before_movie_night_signing_is_configured() -> None:
    source = (
        ROOT / "stoney_verify" / "torrent_media_server.py"
    ).read_text(encoding="utf-8")

    assert 'DANK_MEDIA_SERVER_ENABLED' in source
    assert '"0.0.0.0"' in source
    assert "health remains available but media/watch access stays fail-closed" in source
    assert "Torrent media server refused to start" not in source
