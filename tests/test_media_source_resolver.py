from __future__ import annotations

from pathlib import Path

from stoney_verify import media_source_resolver as resolver
from stoney_verify.media_source_registry import CustomMediaSource


def _source() -> CustomMediaSource:
    return CustomMediaSource(
        source_id="family-library",
        label="Family Library",
        endpoint_url="https://media.example.com/search?q={query}",
        enabled=True,
        added_by=1,
        created_at="2026-10-01T00:00:00+00:00",
    )


def test_search_url_supports_placeholder_and_query_parameter_modes() -> None:
    assert resolver._search_url(
        "https://media.example.com/search?q={query}",
        "Blade Runner",
    ) == "https://media.example.com/search?q=Blade+Runner"

    url = resolver._search_url(
        "https://media.example.com/search?type=movie",
        "Alien",
    )
    assert "type=movie" in url
    assert "q=Alien" in url


def test_source_result_keeps_swarm_health_and_unverified_source_metadata() -> None:
    variant = resolver._variant_from_item(
        _source(),
        {
            "title": "Example Movie",
            "release_name": "Example.Movie.2026.1080p.WEB-DL.x265.DDP5.1-GROUP",
            "magnet": "magnet:?xt=urn:btih:ABCDEF",
            "size_bytes": 4_000_000_000,
            "seeders": 120,
            "leechers": 18,
            "peers": 145,
            "metadata": {
                "claimed_resolution": "1080p",
                "claimed_codec": "HEVC",
            },
        },
    )
    assert variant is not None
    assert variant.source_id == "family-library"
    assert variant.source_label == "Family Library"
    assert variant.file_size == 4_000_000_000
    assert variant.seeds == 120
    assert variant.leechers == 18
    assert variant.peers == 145
    assert variant.metadata["source_reported_verified"] is False
    assert variant.metadata["source_reported"]["claimed_codec"] == "HEVC"
    assert variant.metadata["release_name"]["source"] == "WEB-DL"


def test_private_and_local_source_refs_are_rejected() -> None:
    for value in (
        "https://127.0.0.1/file.torrent",
        "https://10.0.0.2/file.torrent",
        "https://localhost/file.torrent",
        "file:///tmp/movie.torrent",
    ):
        assert resolver._safe_source_ref(value) == ""


def test_public_ip_guard_rejects_private_ranges() -> None:
    assert resolver._public_ip("8.8.8.8")
    assert not resolver._public_ip("127.0.0.1")
    assert not resolver._public_ip("10.10.10.10")
    assert not resolver._public_ip("169.254.1.1")


def test_torrent_metadata_fetch_reuses_public_only_resolver_and_byte_cap() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "stoney_verify"
        / "media_source_resolver.py"
    ).read_text(encoding="utf-8")

    assert "async def fetch_torrent_metadata(" in source
    assert "PublicOnlyResolver()" in source
    assert "allow_redirects=False" in source
    assert "_validate_request_url(urljoin(current, location))" in source
    assert "torrent metadata exceeds the configured limit" in source
    assert "use_dns_cache=False" in source
