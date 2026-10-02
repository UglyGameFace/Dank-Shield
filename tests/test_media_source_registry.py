from __future__ import annotations

from stoney_verify.media_source_registry import (
    MEDIA_SOURCE_REGISTRY_KEY,
    MediaSourceRegistry,
    add_custom_source,
    enabled_custom_sources,
    parse_media_source_registry,
    remove_custom_source,
    set_custom_source_enabled,
)


def test_custom_media_source_round_trip() -> None:
    registry = add_custom_source(
        MediaSourceRegistry(),
        source_id="my-media",
        label="My Media Feed",
        endpoint_url="https://media.example.com/catalog/search?q={query}",
        added_by=123,
    )
    payload = {MEDIA_SOURCE_REGISTRY_KEY: registry.to_payload()}
    parsed = parse_media_source_registry(payload)

    assert parsed.revision == 1
    assert len(parsed.sources) == 1
    source = parsed.sources[0]
    assert source.source_id == "my-media"
    assert source.label == "My Media Feed"
    assert source.endpoint_url.startswith("https://media.example.com/")
    assert source.added_by == 123
    assert source.enabled


def test_custom_source_can_be_disabled_and_removed() -> None:
    registry = add_custom_source(
        MediaSourceRegistry(),
        source_id="family-library",
        label="Family Library",
        endpoint_url="https://library.example.org/feed",
        added_by=55,
    )
    registry = set_custom_source_enabled(registry, "family-library", False)
    assert enabled_custom_sources(registry) == ()

    registry = set_custom_source_enabled(registry, "family-library", True)
    assert len(enabled_custom_sources(registry)) == 1

    registry = remove_custom_source(registry, "family-library")
    assert registry.sources == ()


def test_custom_source_rejects_private_or_credential_urls() -> None:
    invalid = (
        "http://media.example.com/feed",
        "https://localhost/feed",
        "https://127.0.0.1/feed",
        "https://10.0.0.8/feed",
        "https://user:pass@media.example.com/feed",
    )
    for url in invalid:
        try:
            add_custom_source(
                MediaSourceRegistry(),
                source_id="bad",
                label="Bad",
                endpoint_url=url,
                added_by=1,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe custom source URL accepted: {url}")


def test_custom_source_update_keeps_identity_and_revision_moves_forward() -> None:
    first = add_custom_source(
        MediaSourceRegistry(),
        source_id="archive",
        label="Archive One",
        endpoint_url="https://archive.example.com/v1",
        added_by=1,
    )
    second = add_custom_source(
        first,
        source_id="archive",
        label="Archive Two",
        endpoint_url="https://archive.example.com/v2",
        added_by=2,
    )

    assert len(second.sources) == 1
    assert second.revision == first.revision + 1
    assert second.sources[0].source_id == "archive"
    assert second.sources[0].label == "Archive Two"
    assert second.sources[0].endpoint_url.endswith("/v2")


def test_registry_uses_atomic_guild_config_compare_and_swap() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "stoney_verify"
        / "media_source_registry.py"
    ).read_text(encoding="utf-8")

    assert "compare_and_swap_guild_config_key" in source
    assert "MEDIA_SOURCE_REGISTRY_KEY" in source
    assert 'source="movie_night_media_source_registry"' in source
