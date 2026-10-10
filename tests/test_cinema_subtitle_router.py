"""Three-source subtitle routing, quota and credential isolation regressions."""
from __future__ import annotations

import asyncio
import io
import json
import time
import zipfile
from types import SimpleNamespace

import pytest

from stoney_verify import cinema_subtitle_router as router


@pytest.fixture(autouse=True)
def clear_subtitle_router():
    router._OPTIONS.clear()
    router._RESULTS_CACHE.clear()
    router._VTT_CACHE.clear()
    router._WINDOWS.clear()
    router._COOLDOWN.clear()
    yield
    router._OPTIONS.clear()
    router._RESULTS_CACHE.clear()
    router._VTT_CACHE.clear()
    router._WINDOWS.clear()
    router._COOLDOWN.clear()


def _settings(monkeypatch):
    entries = [
        {"provider": "subsource", "name": "lobby", "key": "subsource-server-secret",
         "guild_ids": ["111"]},
        {"provider": "wyzie", "name": "other", "key": "wyzie-other-server-secret",
         "guild_ids": ["222"]},
        {"provider": "wyzie", "name": "lobby", "key": "wyzie-server-secret",
         "guild_ids": ["111"]},
        {"provider": "subdl", "name": "global", "key": "subdl-pro-server-secret",
         "scope": "operator", "licensed_commercial": True},
    ]
    monkeypatch.setenv("DANK_CINEMA_SUBTITLE_ACCOUNTS_JSON", json.dumps(entries))
    monkeypatch.delenv("DANK_CINEMA_SUBDL_API_KEY", raising=False)
    monkeypatch.delenv("DANK_CINEMA_SUBDL_COMMERCIAL_OK", raising=False)


def test_accounts_isolate_guilds_and_gated_subdl_entitlement(monkeypatch):
    _settings(monkeypatch)
    assert router.account_for(111, "wyzie").name == "lobby"
    assert router.account_for(222, "wyzie").name == "other"
    assert router.account_for(333, "wyzie") is None
    assert router.account_for(333, "subsource") is None
    assert router.account_for(333, "subdl").name == "global"
    assert router.provider_names(111) == ["subsource", "wyzie", "subdl"]
    assert router.provider_names(222) == ["wyzie", "subdl"]
    assert router.provider_configured(111)
    assert router.provider_configured(333)
    monkeypatch.setenv("DANK_CINEMA_SUBTITLE_ACCOUNTS_JSON", json.dumps([
        {"provider": "subdl", "name": "unlicensed", "key": "secret-unlicensed",
         "guild_ids": ["111"], "licensed_commercial": False},
    ]))
    assert router.account_for(111, "subdl") is None


def test_same_provider_never_hops_to_next_credential_when_limit_hit(monkeypatch):
    monkeypatch.setenv("DANK_CINEMA_SUBTITLE_ACCOUNTS_JSON", json.dumps([
        {"provider": "wyzie", "name": "account-a", "key": "first-guild-secret",
         "guild_ids": ["111"]},
        {"provider": "wyzie", "name": "account-b", "key": "second-guild-secret",
         "guild_ids": ["111"]},
    ]))
    pinned = router.account_for(111, "wyzie")
    assert pinned is not None and pinned.name == "account-a"
    router._COOLDOWN[pinned.fingerprint] = time.time() + 3600
    with pytest.raises(router.SubtitleProviderUnavailable, match="temporarily unavailable"):
        router._budget(router.account_for(111, "wyzie"))
    assert router.account_for(111, "wyzie").name == "account-a"


def test_cross_source_discovery_falls_back_and_issues_opaque_viewer_choices(monkeypatch):
    _settings(monkeypatch)
    calls = []

    async def fake_discover(account, media, lang):
        calls.append((account.provider, account.name, lang))
        if account.provider == "subsource":
            raise router.SubtitleProviderUnavailable("429")
        if account.provider == "wyzie":
            return [{"source": "wyzie", "reference": "https://sub.wyzie.io/c/id/5?tok=foo",
                     "language": lang, "label": "Wyzie • English"}]
        return [{"source": "subdl", "reference": "subdl-123",
                 "language": lang, "label": "SubDL • English"}]

    monkeypatch.setattr(router, "_discover", fake_discover)
    media = {"media_type": "movie", "tmdb_id": 1234}
    first = asyncio.run(router.discover_subtitles(
        media, "en", guild_id=111, viewer_id=17, stream_token="film-token",
    ))
    assert [row["source"] for row in first] == ["wyzie", "subdl"]
    assert [provider for provider, _, _ in calls] == ["subsource", "wyzie", "subdl"]
    assert all(set(row) == {"source", "id", "language", "label"} for row in first)
    assert "secret" not in str(first) and "tok=foo" not in str(first)
    assert router.lookup_selection(first[0]["id"], guild_id=111,
                                   viewer_id=17, stream_token="film-token")
    for guild, uid, token in ((222, 17, "film-token"), (111, 18, "film-token"),
                              (111, 17, "other-film")):
        with pytest.raises(router.SubtitleProviderUnavailable):
            router.lookup_selection(first[0]["id"], guild_id=guild,
                                    viewer_id=uid, stream_token=token)


def test_wyzie_response_links_are_pinned_to_its_download_host():
    good = "https://sub.wyzie.io/c/abc/id/123?format=srt&tok=opaque123"
    assert router._safe_wyzie_url(good) == good
    for bad in (
        "https://evil.test/c/a?tok=x",
        "https://sub.wyzie.io.evil.test/c/a?tok=x",
        "http://sub.wyzie.io/c/a?tok=x",
        "https://sub.wyzie.io/c/a",
        "https://user@sub.wyzie.io/c/a?tok=x",
    ):
        with pytest.raises(router.SubtitleProviderUnavailable):
            router._safe_wyzie_url(bad)
    parsed = router._parse_wyzie([
        {"url": good, "language": "en", "release": "Movie.1080p"},
        {"url": good, "language": "ja", "release": "Wrong language"},
        {"url": "https://evil.test/c/a?tok=x", "language": "en"},
    ], language="en")
    assert len(parsed) == 1
    assert parsed[0]["source"] == "wyzie"
    assert parsed[0]["reference"] == good
    assert parsed[0]["label"] == "Wyzie • Movie.1080p"


def test_subsource_uses_verified_internal_title_id_and_exact_episode(monkeypatch):
    account = router.SubtitleAccount(
        provider="subsource", name="guild", key="my-provider-secret",
        guild_ids=frozenset({111}),
    )
    calls = []

    async def get(_session, _account, path, *, params=None):
        calls.append((path, dict(params or {})))
        if path == "/movies/search":
            return {"data": [
                {"title": "Example Show", "releaseYear": 2021, "movieId": 3322},
                {"title": "Other Show", "releaseYear": 2021, "movieId": 9988},
            ]}
        return {"data": [
            {"subtitleId": 4411, "releaseInfo": ["Example.Show.S03E09.WEB.1080p"]},
            {"subtitleId": 4422, "releaseInfo": ["Example.Show.S03E08.WEB.1080p"]},
        ]}

    monkeypatch.setattr(router, "_json_request", get)
    result = asyncio.run(router._search_subsource(
        None, account, {
            "media_type": "episode", "series_title": "Example Show",
            "series_id": 555, "season_number": 3, "episode_number": 9,
            "year": 2021,
        }, "en",
    ))
    assert calls[0] == ("/movies/search",
                        {"searchType": "text", "q": "example show"})
    assert calls[1][0] == "/subtitles"
    assert calls[1][1]["movieId"] == "3322"
    assert calls[1][1]["seasonNumber"] == "3"
    assert calls[1][1]["episodeNumber"] == "9"
    assert [row["reference"] for row in result] == ["4411"]


def test_subtitle_archive_selects_only_one_exact_episode():
    def archive(files):
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w", zipfile.ZIP_DEFLATED) as z:
            for name, text in files.items():
                z.writestr(name, text)
        return blob.getvalue()

    cue = "1\n00:00:01,000 --> 00:00:02,000\nHello\n"
    good = archive({"Show.S03E09.en.srt": cue, "Show.S03E08.en.srt": cue})
    vtt = router._zip_to_vtt(good, media=("tv", 444, 3, 9))
    assert vtt.startswith(b"WEBVTT")
    with pytest.raises(router.SubtitleProviderUnavailable):
        router._zip_to_vtt(archive({"Show.S03E08.en.srt": cue}),
                           media=("tv", 444, 3, 9))
    with pytest.raises(router.SubtitleProviderUnavailable):
        router._zip_to_vtt(archive({"Show.S03E09.en.srt": cue,
                                    "Show.S03E09.es.srt": cue}),
                           media=("tv", 444, 3, 9))


def test_browser_tracks_use_opaque_ids_for_all_three_providers():
    from stoney_verify import movie_night_web

    html = movie_night_web._watch_html(
        "room", 1111, "uid=1111&exp=9999999999&sig=test",
    )
    assert 'option.value=row.source==="local"?"local"' in html
    assert '"provider:"+String(row.id)' in html
    assert '"/subtitles/provider?"' in html
    assert 'row.source==="subdl"?"subdl:"' not in html
    assert 'id="subtitleFile"' in html
    assert 'id="subtitleSize"' in html
    assert 'id="subtitleDelay"' in html



def test_twenty_viewers_share_single_upstream_search(monkeypatch):
    account = router.SubtitleAccount(
        provider="wyzie", name="shared", key="tenant-key-private",
        guild_ids=frozenset({111}),
    )
    calls = []

    async def fake_search(session, config, metadata, language):
        calls.append(language)
        await asyncio.sleep(0.01)
        return [{"source": "wyzie", "reference": "https://sub.wyzie.io/c/one?tok=ok",
                 "language": "en", "label": "English"}]

    monkeypatch.setattr(router, "_search_wyzie", fake_search)
    metadata = {"media_type": "movie", "tmdb_id": 1205}

    async def twenty():
        return await asyncio.gather(*[
            router._discover(account, metadata, "en") for _ in range(20)
        ])
    responses = asyncio.run(twenty())
    assert len(calls) == 1
    assert all(result == responses[0] for result in responses)


def test_twenty_viewers_share_download_but_cannot_use_each_others_selection(monkeypatch):
    account = router.SubtitleAccount(
        provider="wyzie", name="shared", key="tenant-key-private",
        guild_ids=frozenset({111}),
    )
    monkeypatch.setattr(router, "account_for", lambda guild, provider: account)
    now = time.monotonic()
    for user_id in range(20, 40):
        router._OPTIONS["view-" + str(user_id)] = (now + 900, {
            "account": account, "provider": "wyzie",
            "reference": "https://sub.wyzie.io/c/one?tok=sample",
            "stream_token": "film-token", "guild_id": 111,
            "viewer_id": user_id, "media": ("movie", 1205, 0, 0),
        })
    calls = []

    async def fake_download(selection_id, *, guild_id, viewer_id, stream_token):
        calls.append(viewer_id)
        await asyncio.sleep(0.02)
        result = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nTest\n"
        key = (account.fingerprint, ("movie", 1205, 0, 0), "wyzie",
               "https://sub.wyzie.io/c/one?tok=sample")
        router._VTT_CACHE[key] = (time.monotonic() + 500, result)
        return result

    monkeypatch.setattr(router, "_download_selection_uncached", fake_download)

    async def twenty():
        return await asyncio.gather(*[
            router.download_subtitle_selection(
                "view-" + str(uid), guild_id=111,
                viewer_id=uid, stream_token="film-token",
            )
            for uid in range(20, 40)
        ])

    results = asyncio.run(twenty())
    assert len(calls) == 1
    assert len(results) == 20
    assert all(value == results[0] for value in results)
    with pytest.raises(router.SubtitleProviderUnavailable):
        asyncio.run(router.download_subtitle_selection(
            "view-20", guild_id=111, viewer_id=21, stream_token="film-token",
        ))


def test_provider_429_cools_only_its_account_and_keeps_other_provider_usable(monkeypatch):
    _settings(monkeypatch)
    account = router.account_for(111, "wyzie")
    other = router.account_for(111, "subsource")
    monkeypatch.setattr(router.time, "time", lambda: 1000.0)
    with pytest.raises(router.SubtitleProviderUnavailable):
        router._handle_response(SimpleNamespace(
            status=429, headers={"Retry-After": "900"},
        ), account)
    assert router._COOLDOWN[account.fingerprint] == 1900.0
    with pytest.raises(router.SubtitleProviderUnavailable):
        router._budget(account)
    router._budget(other)
