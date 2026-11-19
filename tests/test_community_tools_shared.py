"""Shared Community Tools helpers that survived the Sticky Messages removal.

``community_tools_service`` is now only the shared floor: the storage
exceptions and the SSRF-safe HTTPS URL normalizer that Quiet Server
Notices use for partner links. The URL test lived in the deleted
sticky service suite and was relocated here so the guard stays pinned.
"""
from __future__ import annotations

import pytest

from stoney_verify.community_tools_service import (
    InvalidCommunityToolValue,
    normalize_https_url,
)


def test_external_asset_urls_reject_local_private_targets_and_credentials() -> None:
    assert normalize_https_url("https://cdn.example.com/image.png") == "https://cdn.example.com/image.png"
    for bad in (
        "http://example.com/image.png",
        "https://localhost/image.png",
        "https://127.0.0.1/image.png",
        "https://10.0.0.1/image.png",
        "https://user:pass@example.com/image.png",
        "https://example.com:8443/image.png",
    ):
        with pytest.raises(InvalidCommunityToolValue):
            normalize_https_url(bad)
