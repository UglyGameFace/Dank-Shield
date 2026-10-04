from __future__ import annotations

from stoney_verify import movie_catalog


def test_tmdb_catalog_ready_uses_bot_owner_token(monkeypatch) -> None:
    monkeypatch.delenv("DANK_TMDB_READ_TOKEN", raising=False)
    assert not movie_catalog.tmdb_catalog_ready()

    monkeypatch.setenv("DANK_TMDB_READ_TOKEN", "token-value")
    assert movie_catalog.tmdb_catalog_ready()


def test_tmdb_movie_parser_keeps_exact_identity_and_safe_poster() -> None:
    movie = movie_catalog._tmdb_movie_from_item(
        {
            "id": 157336,
            "title": "Interstellar",
            "original_title": "Interstellar",
            "release_date": "2014-11-05",
            "overview": "A space movie.",
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "popularity": 42.5,
            "adult": True,
        }
    )
    assert movie is not None
    assert movie.provider == "tmdb"
    assert movie.provider_id == "157336"
    assert movie.title == "Interstellar"
    assert movie.year == 2014
    assert movie.poster_url == "https://image.tmdb.org/t/p/w342/poster.jpg"
    assert movie.backdrop_url == "https://image.tmdb.org/t/p/w780/backdrop.jpg"
    assert movie.to_metadata()["backdrop_url"] == "https://image.tmdb.org/t/p/w780/backdrop.jpg"
    assert movie.adult is True
    assert movie.to_metadata()["adult"] is True


def test_watch_region_is_two_letter_operator_default(monkeypatch) -> None:
    monkeypatch.setenv("DANK_TMDB_WATCH_REGION", "gb")
    assert movie_catalog.tmdb_watch_region() == "GB"

    monkeypatch.setenv("DANK_TMDB_WATCH_REGION", "not-a-region")
    assert movie_catalog.tmdb_watch_region() == "US"


def test_watch_availability_normalizes_and_deduplicates_provider_names() -> None:
    watch = movie_catalog._watch_availability_from_payload(
        {
            "results": {
                "US": {
                    "link": "https://www.themoviedb.org/movie/123/watch",
                    "free": [
                        {"provider_name": "Tubi"},
                        {"provider_name": "Tubi"},
                    ],
                    "ads": [{"provider_name": "Pluto TV"}],
                    "flatrate": [{"provider_name": "Plex"}],
                    "rent": [{"provider_name": "Prime Video"}],
                    "buy": [{"provider_name": "Apple TV"}],
                }
            }
        },
        region="US",
    )

    assert watch.free == ("Tubi",)
    assert watch.ads == ("Pluto TV",)
    assert watch.flatrate == ("Plex",)
    assert watch.rent == ("Prime Video",)
    assert watch.buy == ("Apple TV",)
    assert watch.link == "https://www.themoviedb.org/movie/123/watch"
    assert watch.to_metadata()["attribution"] == "JustWatch via TMDB"


def test_watch_availability_rejects_untrusted_detail_link() -> None:
    watch = movie_catalog._watch_availability_from_payload(
        {
            "results": {
                "US": {
                    "link": "https://evil.example/movie/123",
                    "free": [{"provider_name": "Example"}],
                }
            }
        },
        region="US",
    )
    assert watch.link == ""
    assert watch.free == ("Example",)


def test_tmdb_search_has_explicit_adult_toggle_contract() -> None:
    import inspect

    source = inspect.getsource(movie_catalog.search_tmdb_movies)
    assert "include_adult: bool = False" in source
    assert '"include_adult": "true" if include_adult else "false"' in source
