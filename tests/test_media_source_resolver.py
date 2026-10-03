from __future__ import annotations

import asyncio
from pathlib import Path

from stoney_verify import media_source_resolver as resolver
from stoney_verify.media_source_registry import CustomMediaSource, MediaSourceRegistry


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



def test_internet_archive_builtin_search_is_scoped_to_feature_films() -> None:
    url = resolver._internet_archive_search_url("Night of the Living Dead")
    assert url.startswith("https://archive.org/advancedsearch.php?")
    assert "collection%3Afeature_films" in url
    assert "Night+of+the+Living+Dead" in url
    assert "output=json" in url
    assert "rows=12" in url


def test_internet_archive_query_cannot_escape_feature_films_scope() -> None:
    url = resolver._internet_archive_search_url(
        'Movie") OR collection:opensource_movies OR title:("Other'
    )
    assert "collection%3Afeature_films" in url
    assert "%5C%22" in url


def test_internet_archive_doc_becomes_torrent_variant() -> None:
    variant = resolver._archive_variant_from_doc(
        {
            "identifier": "example_feature_film",
            "title": "Example Feature Film",
            "date": "1940",
            "downloads": 1234,
        }
    )
    assert variant is not None
    assert variant.source_id == resolver.INTERNET_ARCHIVE_SOURCE_ID
    assert variant.source_label == resolver.INTERNET_ARCHIVE_SOURCE_LABEL
    assert variant.source_ref == (
        "https://archive.org/download/example_feature_film/"
        "example_feature_film_archive.torrent"
    )
    assert variant.metadata["source_reported"]["archive_downloads"] == 1234


def test_internet_archive_doc_rejects_unsafe_identifier() -> None:
    for identifier in ("../not-safe", ".", ".."):
        assert resolver._archive_variant_from_doc(
            {"identifier": identifier, "title": "Bad"}
        ) is None



def test_aggregate_search_keeps_builtin_results_without_custom_sources(monkeypatch) -> None:
    builtin = resolver.ResolvedMediaVariant(
        title="Public Domain Movie",
        source_id=resolver.INTERNET_ARCHIVE_SOURCE_ID,
        source_label=resolver.INTERNET_ARCHIVE_SOURCE_LABEL,
        source_ref=(
            "https://archive.org/download/public_domain_movie/"
            "public_domain_movie_archive.torrent"
        ),
        file_size=0,
        seeds=0,
        leechers=0,
        peers=0,
        metadata={},
    )

    async def fake_builtin(query: str):
        assert query == "Public Domain Movie"
        return [builtin], ""

    async def fake_custom(guild_id: int, query: str):
        assert guild_id == 123
        assert query == "Public Domain Movie"
        return resolver.MediaSourceSearchOutcome(
            variants=(),
            errors=("No structured custom sources are enabled.",),
        )

    monkeypatch.setattr(resolver, "_search_builtin_internet_archive", fake_builtin)
    monkeypatch.setattr(resolver, "search_custom_media_sources", fake_custom)

    outcome = asyncio.run(
        resolver.search_movie_sources(123, "Public Domain Movie")
    )
    assert outcome.variants == (builtin,)
    assert outcome.errors == ()



def test_extract_items_accepts_common_torrent_api_containers() -> None:
    row = {"title": "Example", "magnet": "magnet:?xt=urn:btih:ABC"}
    for payload in (
        {"torrents": [row]},
        {"entries": [row]},
        {"movies": [row]},
        {"data": [row]},
        {"data": {"results": [row]}},
        {"data": {"torrents": [row]}},
    ):
        assert resolver._extract_items(payload) == [row]


def test_generic_torrent_api_aliases_become_playable_variant() -> None:
    variant = resolver._variant_from_item(
        _source(),
        {
            "display_name": "Example Movie 2026",
            "file_name": "Example.Movie.2026.1080p.WEB-DL.x264-GROUP",
            "magnet_uri": "magnet:?xt=urn:btih:ABCDEF123456",
            "length": 3_500_000_000,
            "seed": 88,
            "leech": 12,
            "total_peers": 105,
        },
    )
    assert variant is not None
    assert variant.title == "Example Movie 2026"
    assert variant.source_ref == "magnet:?xt=urn:btih:ABCDEF123456"
    assert variant.file_size == 3_500_000_000
    assert variant.seeds == 88
    assert variant.leechers == 12
    assert variant.peers == 105
    assert variant.metadata["release_name"]["source"] == "WEB-DL"


def test_generic_torrent_api_accepts_https_torrent_alias() -> None:
    variant = resolver._variant_from_item(
        _source(),
        {
            "name": "Public Domain Feature",
            "torrent_url": "https://downloads.example.org/releases/movie.torrent",
            "size": 123_456_789,
            "seed_count": 12,
            "leech_count": 3,
        },
    )
    assert variant is not None
    assert variant.source_ref.endswith("/movie.torrent")
    assert variant.seeds == 12
    assert variant.leechers == 3
    assert variant.peers == 15


def test_generic_provider_ignores_rows_without_playable_ref() -> None:
    for item in (
        {"title": "Only a title"},
        {"title": "Web page", "url": "file:///tmp/not-allowed"},
        {"title": "Private", "download_url": "https://127.0.0.1/movie.torrent"},
    ):
        assert resolver._variant_from_item(_source(), item) is None


def test_structured_provider_accepts_nested_data_results() -> None:
    payload = {
        "data": {
            "results": [
                {
                    "title": "Nested Movie",
                    "magnet": "magnet:?xt=urn:btih:NESTED",
                    "seed": 33,
                    "leech": 4,
                    "peer": 40,
                    "filesize": 123456789,
                    "quality": "1080p",
                }
            ]
        }
    }
    items = resolver._expand_provider_items(resolver._extract_items(payload))
    assert len(items) == 1
    variant = resolver._variant_from_item(_source(), items[0])
    assert variant is not None
    assert variant.title == "Nested Movie"
    assert variant.seeds == 33
    assert variant.leechers == 4
    assert variant.peers == 40
    assert variant.file_size == 123456789
    assert variant.metadata["source_reported"]["quality"] == "1080p"


def test_structured_provider_flattens_nested_torrent_quality_map() -> None:
    payload = {
        "movies": [
            {
                "title": "Example Movie",
                "year": 2026,
                "torrents": {
                    "en": {
                        "1080p": {
                            "url": "magnet:?xt=urn:btih:QUALITY1080",
                            "seed": 120,
                            "peer": 150,
                            "filesize": 4_000_000_000,
                            "codec": "x265",
                        },
                        "720p": {
                            "url": "magnet:?xt=urn:btih:QUALITY720",
                            "seed": 60,
                            "peer": 80,
                            "filesize": 2_000_000_000,
                        },
                    }
                },
            }
        ]
    }
    items = resolver._expand_provider_items(resolver._extract_items(payload))
    assert len(items) == 2

    variants = [
        resolver._variant_from_item(_source(), item)
        for item in items
    ]
    variants = [item for item in variants if item is not None]
    assert len(variants) == 2
    refs = {item.source_ref for item in variants}
    assert refs == {
        "magnet:?xt=urn:btih:QUALITY1080",
        "magnet:?xt=urn:btih:QUALITY720",
    }

    high = next(
        item for item in variants
        if item.source_ref.endswith("QUALITY1080")
    )
    assert high.seeds == 120
    assert high.peers == 150
    assert high.metadata["source_reported"]["variant_path"] == "en/1080p"
    assert high.metadata["source_reported"]["codec"] == "x265"
    assert high.metadata["source_reported"]["year"] == 2026


def test_structured_provider_accepts_torrent_url_alias() -> None:
    variant = resolver._variant_from_item(
        _source(),
        {
            "movie": "Torrent URL Movie",
            "torrent_url": "https://cdn.example.com/file.torrent",
            "seeders": 10,
        },
    )
    assert variant is not None
    assert variant.source_ref == "https://cdn.example.com/file.torrent"
    assert variant.seeds == 10


def test_expand_provider_items_ignores_nested_entries_without_playable_ref() -> None:
    payload = {
        "results": [
            {
                "title": "Metadata Only",
                "torrents": {
                    "1080p": {"quality": "1080p", "seed": 100}
                },
            }
        ]
    }
    assert resolver._expand_provider_items(
        resolver._extract_items(payload)
    ) == []


def test_apibay_style_info_hash_result_becomes_playable_magnet() -> None:
    payload = [
        {
            "id": "12345678",
            "name": "Example Movie 2026 1080p",
            "info_hash": "0123456789ABCDEF0123456789ABCDEF01234567",
            "leechers": "12",
            "seeders": "88",
            "size": "3500000000",
            "category": "207",
        }
    ]

    items = resolver._expand_provider_items(resolver._extract_items(payload))
    assert len(items) == 1

    variant = resolver._variant_from_item(_source(), items[0])
    assert variant is not None
    assert variant.title == "Example Movie 2026 1080p"
    assert variant.source_ref == (
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
    )
    assert variant.file_size == 3_500_000_000
    assert variant.seeds == 88
    assert variant.leechers == 12
    assert variant.peers == 100


def test_info_hash_aliases_support_hex_and_base32_btih() -> None:
    hex_item = {
        "name": "Hex",
        "infohash": "ABCDEF0123456789ABCDEF0123456789ABCDEF01",
    }
    assert resolver._item_source_ref(hex_item) == (
        "magnet:?xt=urn:btih:abcdef0123456789abcdef0123456789abcdef01"
    )

    base32_item = {
        "name": "Base32",
        "hash": "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567",
    }
    assert resolver._item_source_ref(base32_item) == (
        "magnet:?xt=urn:btih:ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    )


def test_invalid_info_hash_does_not_become_playable() -> None:
    for value in (
        "",
        "not-a-torrent-hash",
        "1234",
        "g" * 40,
        "0" * 40,
        "A" * 31,
        "A" * 33,
    ):
        item = {"name": "Invalid Hash", "info_hash": value}
        assert resolver._item_source_ref(item) == ""
        assert resolver._variant_from_item(_source(), item) is None


def test_explicit_playable_ref_wins_over_info_hash_fallback() -> None:
    item = {
        "name": "Explicit",
        "magnet": "magnet:?xt=urn:btih:EXPLICIT",
        "info_hash": "0123456789abcdef0123456789abcdef01234567",
    }
    assert resolver._item_source_ref(item) == "magnet:?xt=urn:btih:EXPLICIT"


def test_static_xml_feed_endpoint_is_not_rewritten_with_query_parameter() -> None:
    endpoint = "https://fosstorrents.com/feed/torrents.xml"
    assert resolver._search_url(endpoint, "Blender") == endpoint


def test_rss_torrent_feed_enclosure_becomes_playable_release() -> None:
    payload = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>FOSS Torrents - RSS Feed for Torrent Files</title>
        <item>
          <title>Blender 4.5 Linux x64</title>
          <link>https://example.org/projects/blender</link>
          <enclosure
            url="https://downloads.example.org/blender-4.5-linux-x64.torrent"
            length="123456"
            type="application/x-bittorrent" />
          <category>Software</category>
        </item>
        <item>
          <title>Unrelated Project</title>
          <enclosure
            url="https://downloads.example.org/unrelated.torrent"
            type="application/x-bittorrent" />
        </item>
      </channel>
    </rss>
    """

    items = resolver._extract_feed_items(payload, "Blender")
    assert len(items) == 1
    variant = resolver._variant_from_item(_source(), items[0])
    assert variant is not None
    assert variant.title == "Blender 4.5 Linux x64"
    assert variant.source_ref.endswith("blender-4.5-linux-x64.torrent")
    assert variant.file_size == 123456
    assert variant.metadata["source_reported"]["category"] == "Software"


def test_atom_enclosure_feed_is_supported_without_treating_page_link_as_media() -> None:
    payload = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Open Movie 2026</title>
        <link rel="alternate" href="https://example.org/open-movie" />
        <link rel="enclosure"
              type="application/x-bittorrent"
              href="https://downloads.example.org/open-movie.torrent" />
      </entry>
    </feed>
    """

    items = resolver._extract_feed_items(payload, "Open Movie")
    assert len(items) == 1
    assert items[0]["source_ref"] == "https://downloads.example.org/open-movie.torrent"


def test_rss_torrent_extension_info_hash_becomes_magnet() -> None:
    payload = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss xmlns:torrent="http://xmlns.ezrss.it/0.1/">
      <channel>
        <item>
          <title>Public Domain Movie 2026</title>
          <torrent:infoHash>0123456789ABCDEF0123456789ABCDEF01234567</torrent:infoHash>
        </item>
      </channel>
    </rss>
    """

    items = resolver._extract_feed_items(payload, "Public Domain Movie")
    assert len(items) == 1
    variant = resolver._variant_from_item(_source(), items[0])
    assert variant is not None
    assert variant.source_ref == (
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
    )


def test_rss_feed_rejects_doctype_and_entity_declarations() -> None:
    payload = b"""<?xml version="1.0"?>
    <!DOCTYPE rss [<!ENTITY x "unsafe">]>
    <rss><channel><item><title>&x;</title></item></channel></rss>
    """
    try:
        resolver._extract_feed_items(payload, "unsafe")
    except ValueError as exc:
        assert "declarations are not allowed" in str(exc)
    else:
        raise AssertionError("unsafe XML declaration should be rejected")


def test_static_feed_directory_endpoint_is_not_rewritten() -> None:
    endpoint = "https://example.org/feed/"
    assert resolver._search_url(endpoint, "Open Movie") == endpoint


def test_single_object_and_keyed_object_json_results_are_supported() -> None:
    single = {
        "name": "Single Movie",
        "info_hash": "0123456789abcdef0123456789abcdef01234567",
    }
    assert resolver._extract_items(single) == [single]

    first = {
        "name": "First Movie",
        "magnet": "magnet:?xt=urn:btih:FIRST",
    }
    second = {
        "name": "Second Movie",
        "torrent_url": "https://downloads.example.org/second.torrent",
    }
    payload = {"123": first, "456": second, "status": {"ok": True}}
    assert resolver._extract_items(payload) == [first, second]


def test_torznab_attrs_become_swarm_metadata_and_info_hash_magnet() -> None:
    payload = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss xmlns:torznab="http://torznab.com/schemas/2015/feed">
      <channel>
        <item>
          <title>Open Movie 2026 1080p WEB-DL</title>
          <guid>https://example.org/details/9001</guid>
          <torznab:attr name="infohash"
                        value="0123456789ABCDEF0123456789ABCDEF01234567" />
          <torznab:attr name="seeders" value="42" />
          <torznab:attr name="peers" value="13" />
          <torznab:attr name="size" value="4567890000" />
          <torznab:attr name="tmdbid" value="12345" />
        </item>
      </channel>
    </rss>
    """

    items = resolver._extract_feed_items(payload, "Open Movie")
    assert len(items) == 1
    variant = resolver._variant_from_item(_source(), items[0])
    assert variant is not None
    assert variant.source_ref == (
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
    )
    assert variant.seeds == 42
    assert variant.peers == 55
    assert variant.file_size == 4_567_890_000
    assert variant.metadata["source_reported"]["tmdbid"] == "12345"


def test_torrent_namespace_swarm_and_magnet_fields_are_supported() -> None:
    payload = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss xmlns:torrent="http://xmlns.ezrss.it/0.1/">
      <channel>
        <item>
          <title>Public Domain Feature 1080p</title>
          <torrent:magnetURI>magnet:?xt=urn:btih:ABCDEF</torrent:magnetURI>
          <torrent:contentLength>987654321</torrent:contentLength>
          <torrent:seeders>25</torrent:seeders>
          <torrent:leechers>5</torrent:leechers>
          <torrent:peers>35</torrent:peers>
        </item>
      </channel>
    </rss>
    """

    items = resolver._extract_feed_items(payload, "Public Domain Feature")
    assert len(items) == 1
    variant = resolver._variant_from_item(_source(), items[0])
    assert variant is not None
    assert variant.source_ref == "magnet:?xt=urn:btih:ABCDEF"
    assert variant.file_size == 987_654_321
    assert variant.seeds == 25
    assert variant.leechers == 5
    assert variant.peers == 35


def test_html_provider_body_is_rejected_not_scraped() -> None:
    payload = b"<html><body><item><title>Fake Movie</title></item></body></html>"
    try:
        resolver._extract_feed_items(payload, "Fake Movie")
    except ValueError as exc:
        assert "returned HTML" in str(exc)
    else:
        raise AssertionError("HTML provider body should not be treated as a feed")


def test_v2_info_hash_alias_becomes_btmh_magnet() -> None:
    sha256 = "0123456789abcdef" * 4
    item = {
        "name": "Hybrid Movie",
        "info_hash_v2": sha256,
    }
    assert resolver._item_source_ref(item) == (
        "magnet:?xt=urn:btmh:1220" + sha256
    )

    item = {
        "name": "Hybrid Movie",
        "btmh": "1220" + sha256.upper(),
    }
    assert resolver._item_source_ref(item) == (
        "magnet:?xt=urn:btmh:1220" + sha256
    )


def test_zero_v2_info_hash_is_rejected() -> None:
    item = {"name": "Invalid V2", "info_hash_v2": "0" * 64}
    assert resolver._item_source_ref(item) == ""


def test_custom_provider_zero_results_are_reported_as_diagnostic(monkeypatch) -> None:
    source = _source()

    async def fake_load(guild_id: int, refresh: bool = False):
        assert guild_id == 123
        assert refresh is True
        return {}, MediaSourceRegistry(sources=(source,))

    async def fake_search_one(current, query):
        assert current is source
        assert query == "Missing Movie"
        return [], ""

    monkeypatch.setattr(resolver, "load_media_source_registry", fake_load)
    monkeypatch.setattr(resolver, "_search_one", fake_search_one)

    outcome = asyncio.run(
        resolver.search_custom_media_sources(123, "Missing Movie")
    )
    assert outcome.variants == ()
    assert outcome.errors == (
        "Family Library: no playable results for this search.",
    )


def test_common_camel_case_provider_aliases_are_supported() -> None:
    payload = {
        "searchResults": [
            {
                "displayName": "Camel Movie 2026",
                "releaseName": "Camel.Movie.2026.1080p.WEB-DL.x265",
                "infoHash": "0123456789ABCDEF0123456789ABCDEF01234567",
                "sizeBytes": 2_500_000_000,
                "seedCount": 70,
                "leechCount": 10,
                "totalPeers": 95,
                "videoCodec": "x265",
            }
        ]
    }

    items = resolver._expand_provider_items(resolver._extract_items(payload))
    assert len(items) == 1
    variant = resolver._variant_from_item(_source(), items[0])
    assert variant is not None
    assert variant.title == "Camel Movie 2026"
    assert variant.file_size == 2_500_000_000
    assert variant.seeds == 70
    assert variant.leechers == 10
    assert variant.peers == 95
    assert variant.metadata["source_reported"]["videoCodec"] == "x265"


def test_provider_hash_wins_over_generic_detail_url() -> None:
    item = {
        "name": "Example Movie",
        "url": "https://catalog.example.org/details/123",
        "hash": "0123456789ABCDEF0123456789ABCDEF01234567",
    }
    assert resolver._item_source_ref(item) == (
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
    )


def test_provider_media_ref_rejects_embedded_credentials() -> None:
    item = {
        "name": "Credential Leak",
        "torrent_url": "https://user:secret@downloads.example.org/movie.torrent",
    }
    assert resolver._item_source_ref(item) == ""
    assert resolver._variant_from_item(_source(), item) is None

