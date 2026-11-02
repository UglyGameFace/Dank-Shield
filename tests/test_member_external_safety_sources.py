"""External safety sources are advisory-only, bounded and identity checked."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stoney_verify import member_external_safety_sources as external
from stoney_verify.commands_ext import member_command_center as center

USER = "123456789012345678"


def test_invalid_id_does_not_call_external_providers():
    with pytest.raises(ValueError):
        asyncio.run(external.inspect_external_sources("invalid"))


def test_protector_checks_exact_id_and_boolean_without_enforcement(monkeypatch):
    monkeypatch.setenv("DANK_PROTECTOR_API_KEY", "dummy")
    external._account_cache.clear()
    monkeypatch.setattr(
        external, "_get_json",
        AsyncMock(return_value={"user_id": USER, "blacklisted": True}),
    )
    report = asyncio.run(external._protector(object(), USER))
    assert report.status == "reported"
    assert "no incident proof" in report.detail.lower()

    external._account_cache.clear()
    monkeypatch.setattr(
        external, "_get_json",
        AsyncMock(return_value={"user_id": "999999999999999999", "blacklisted": True}),
    )
    report = asyncio.run(external._protector(object(), USER))
    assert report.status == "unavailable"


def test_protector_without_key_does_not_query(monkeypatch):
    monkeypatch.delenv("DANK_PROTECTOR_API_KEY", raising=False)
    request = AsyncMock(side_effect=AssertionError("no credentials"))
    monkeypatch.setattr(external, "_get_json", request)
    result = asyncio.run(external._protector(object(), USER))
    assert result.status == "unavailable"
    request.assert_not_awaited()


def test_discord_reviews_requires_exact_id_and_current_username(monkeypatch):
    monkeypatch.setenv("DANK_DISCORDREVIEWS_API_KEY", "dummy")
    external._account_cache.clear()
    monkeypatch.setattr(external, "_get_json", AsyncMock(return_value={
        "success": True, "data": {
            "discord_id": "987654321012345678",
            "reputation": {"negative": 8},
        },
    }))
    mismatch = asyncio.run(external._discord_reviews(object(), USER, "someuser"))
    assert mismatch.status == "unavailable"
    missing = asyncio.run(external._discord_reviews(object(), USER, None))
    assert missing.status == "unavailable"

    external._account_cache.clear()
    monkeypatch.setattr(external, "_get_json", AsyncMock(return_value={
        "success": True, "data": {
            "discord_id": USER, "reputation": {"negative": 2},
        },
    }))
    good = asyncio.run(external._discord_reviews(object(), USER, "someuser"))
    assert good.status == "reported"
    assert "opinions" in good.detail.lower()


def test_cheaterstats_paid_requests_explicitly_disabled_by_default(monkeypatch):
    monkeypatch.setenv("DANK_CHEATERSTATS_API_KEY", "dummy")
    monkeypatch.delenv("DANK_CHEATERSTATS_PAID_LOOKUPS", raising=False)
    request = AsyncMock(side_effect=AssertionError("must not bill credits"))
    monkeypatch.setattr(external, "_get_json", request)
    r = asyncio.run(external._cheaterstats_bans(object(), USER))
    assert r.status == "unavailable"
    request.assert_not_awaited()


def test_cheaterstats_csbans_only_and_exact_subject(monkeypatch):
    monkeypatch.setenv("DANK_CHEATERSTATS_API_KEY", "dummy")
    monkeypatch.setenv("DANK_CHEATERSTATS_PAID_LOOKUPS", "1")
    external._account_cache.clear()
    request = AsyncMock(return_value={
        "success": True, "user": USER, "total": 1,
    })
    monkeypatch.setattr(external, "_get_json", request)
    result = asyncio.run(external._cheaterstats_bans(object(), USER))
    assert result.status == "reported"
    assert request.await_args.args[1] == "https://api.cheaterstats.cc/v2/csbans"
    assert request.await_args.kwargs["params"] == {"discordId": USER}
    assert "reported game ban" in result.detail.lower()


def test_domain_exact_host_and_subdomains_not_substrings(monkeypatch):
    monkeypatch.setattr(external, "_domain_cache", (
        time.monotonic(), frozenset({"malicious.example", "bad.test"}),
    ))
    positive = asyncio.run(external.check_historical_scam_domain(
        "https://sub.malicious.example/any",
    ))
    assert positive.status == "reported"
    assert "malicious.example" in positive.detail
    negative = asyncio.run(external.check_historical_scam_domain(
        "https://notmalicious.example/path",
    ))
    assert negative.status == "none_reported"
    assert "NOT establish" in negative.detail
    with pytest.raises(ValueError):
        external.normalize_host("ftp://malicious.example")


def test_other_sources_are_not_falsely_queried_or_described_as_verified(monkeypatch):
    monkeypatch.setattr(external, "_protector", AsyncMock(return_value=
                        external.SourceFinding("Protector", "unavailable", "disabled")))
    monkeypatch.setattr(external, "_discord_reviews", AsyncMock(return_value=
                        external.SourceFinding("Discord Reviews", "unavailable", "disabled")))
    monkeypatch.setattr(external, "_cheaterstats_bans", AsyncMock(return_value=
                        external.SourceFinding("CheaterStats (game bans)", "unavailable", "disabled")))
    results = asyncio.run(external.inspect_external_sources(USER))
    assert len(results) == 6
    assert [x.source for x in results[3:]] == [
        "Trustify", "Project Redacted", "Discord AntiScam",
    ]
    assert all(x.status == "manual_review" for x in results[3:])


def test_non_owner_cannot_fetch_external_account_data(monkeypatch):
    target = SimpleNamespace(id=int(USER))
    caller = SimpleNamespace(
        guild=SimpleNamespace(id=123456789012345678, owner_id=111111111111111111),
        user=SimpleNamespace(id=222222222222222222),
        response=SimpleNamespace(defer=AsyncMock()),
    )
    fetched = AsyncMock(side_effect=AssertionError("owner check must run first"))
    reply = AsyncMock()
    monkeypatch.setattr(external, "inspect_external_sources", fetched)
    monkeypatch.setattr(center, "reply_ephemeral", reply)
    asyncio.run(center._show_external_sources(caller, target.id))
    caller.response.defer.assert_not_awaited()
    fetched.assert_not_awaited()
    reply.assert_awaited_once()


def test_manual_search_buttons_use_only_official_provider_pages():
    import discord

    view = center.ExternalSourcesView(123456789012345678)
    links = {
        child.label: child.url for child in view.children
        if child.style == discord.ButtonStyle.link
    }
    assert links == {
        "Open Trustify": "https://trustify.fun/scammers",
        "Open Redacted": "https://project-redacted.org/",
    }
    assert not any("api/" in url for url in links.values())


def test_documented_key_registration_links_and_disabled_paid_default():
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    doc = (repo / "docs/EXTERNAL_SAFETY_PROVIDER_SETUP.md").read_text()
    assert "https://protector-bot.com/developers" in doc
    assert "https://discordreviews.com/settings" in doc
    assert "https://cheaterstats.cc/v2/api" in doc
    assert "DANK_CHEATERSTATS_PAID_LOOKUPS=0" in doc
    assert "DANK_CHEATERSTATS_PAID_LOOKUPS=1" in doc
    assert "Do not" in doc


def test_provider_cache_eviction_preserves_recent_results_and_marks_age(monkeypatch):
    external._account_cache.clear()
    clock = [100.0]
    monkeypatch.setattr(external.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(external, "_CACHE_LIMIT", 2)
    a = external.SourceFinding("Protector", "reported", "Reported by owner")
    b = external.SourceFinding("Protector", "none_reported", "No blacklist flag")
    external._cache_set("Protector", "a", "", a)
    clock[0] += 5
    external._cache_set("Protector", "b", "", b)
    clock[0] += 8
    cached = external._cache_get("Protector", "a")
    assert cached.status == "reported"
    assert "Cached 13s ago" in cached.detail
    assert "not a fresh provider check" in cached.detail
    assert "Cached" not in external._account_cache[("Protector", "a", "")][1].detail

    external._cache_set("Protector", "c", "", b)
    assert ("Protector", "b", "") not in external._account_cache
    assert ("Protector", "a", "") in external._account_cache
    assert ("Protector", "c", "") in external._account_cache
    external._account_cache.clear()


def test_provider_cache_expiry_is_not_a_clearance(monkeypatch):
    external._account_cache.clear()
    clock = [30.0]
    monkeypatch.setattr(external.time, "monotonic", lambda: clock[0])
    external._cache_set(
        "Protector", USER, "",
        external.SourceFinding("Protector", "none_reported", "No blacklist flag; not a clearance."),
    )
    clock[0] += external._ACCOUNT_TTL_SECONDS
    assert external._cache_get("Protector", USER) is None
    assert ("Protector", USER, "") not in external._account_cache
