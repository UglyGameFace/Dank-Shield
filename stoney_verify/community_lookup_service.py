from __future__ import annotations

"""Bounded no-key network utilities used by the Dank Shield Community Tools center."""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional, TypeVar
from urllib.parse import unquote, urlparse

import aiohttp

HTTP_TIMEOUT_SECONDS = 8
MAX_CONCURRENT_LOOKUPS = 8
CACHE_TTL_SECONDS = 300
USER_AGENT = "DankShield/CommunityTools DiscordBot"

_LOOKUP_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)
_CACHE: dict[str, tuple[float, Any]] = {}
_T = TypeVar("_T")


class CommunityLookupError(RuntimeError):
    """Raised when an external lookup cannot return a trustworthy result."""


@dataclass(frozen=True)
class WeatherResult:
    location: str
    temperature_c: float
    apparent_c: float
    humidity: int
    wind_kmh: float
    weather_code: int
    high_c: Optional[float] = None
    low_c: Optional[float] = None
    precipitation_probability: Optional[int] = None

    @property
    def temperature_f(self) -> float:
        return self.temperature_c * 9.0 / 5.0 + 32.0

    @property
    def apparent_f(self) -> float:
        return self.apparent_c * 9.0 / 5.0 + 32.0

    @property
    def wind_mph(self) -> float:
        return self.wind_kmh * 0.621371

    @property
    def high_f(self) -> Optional[float]:
        return None if self.high_c is None else self.high_c * 9.0 / 5.0 + 32.0

    @property
    def low_f(self) -> Optional[float]:
        return None if self.low_c is None else self.low_c * 9.0 / 5.0 + 32.0


@dataclass(frozen=True)
class ArticleResult:
    title: str
    summary: str
    url: str


@dataclass(frozen=True)
class UrbanResult:
    word: str
    definition: str
    example: str
    permalink: str
    thumbs_up: int
    thumbs_down: int


WEATHER_LABELS: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Heavy freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Heavy freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light rain showers",
    81: "Rain showers",
    82: "Heavy rain showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Severe thunderstorm with hail",
}


def _cached(key: str) -> Optional[Any]:
    row = _CACHE.get(key)
    if row is None:
        return None
    expires_at, value = row
    if expires_at <= time.monotonic():
        _CACHE.pop(key, None)
        return None
    return value


def _cache(key: str, value: _T, *, ttl: int = CACHE_TTL_SECONDS) -> _T:
    _CACHE[key] = (time.monotonic() + max(1, int(ttl)), value)
    if len(_CACHE) > 512:
        now = time.monotonic()
        for cache_key, (expires_at, _) in list(_CACHE.items()):
            if expires_at <= now:
                _CACHE.pop(cache_key, None)
        while len(_CACHE) > 512:
            _CACHE.pop(next(iter(_CACHE)))
    return value


def _require_mapping(value: Any, message: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CommunityLookupError(message)
    return value


def _number(value: Any, *, name: str) -> float:
    try:
        if value is None or isinstance(value, bool):
            raise ValueError
        return float(value)
    except (TypeError, ValueError) as exc:
        raise CommunityLookupError(f"The lookup provider returned invalid {name} data.") from exc


def _integer(value: Any, *, name: str) -> int:
    try:
        if value is None or isinstance(value, bool):
            raise ValueError
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CommunityLookupError(f"The lookup provider returned invalid {name} data.") from exc


def _optional_first_number(value: Any) -> Optional[float]:
    if not isinstance(value, list) or not value:
        return None
    try:
        return float(value[0])
    except (TypeError, ValueError):
        return None


def _optional_first_int(value: Any) -> Optional[int]:
    if not isinstance(value, list) or not value:
        return None
    try:
        return int(value[0])
    except (TypeError, ValueError):
        return None


async def _json_get(url: str, *, params: dict[str, Any], session: Optional[aiohttp.ClientSession] = None) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Language": "en-US,en;q=0.8"}

    async def request(active: aiohttp.ClientSession) -> Any:
        try:
            async with active.get(url, params=params, allow_redirects=True) as response:
                if response.status != 200:
                    raise CommunityLookupError(f"Lookup service returned HTTP {response.status}.")
                try:
                    return await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError) as exc:
                    raise CommunityLookupError("The lookup provider returned malformed data.") from exc
        except CommunityLookupError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            raise CommunityLookupError("The lookup service did not respond in time.") from exc

    async with _LOOKUP_SEMAPHORE:
        if session is not None:
            return await request(session)
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as active:
            return await request(active)


async def weather_lookup(location: str) -> WeatherResult:
    query = " ".join(str(location or "").split()).strip()
    if len(query) < 2 or len(query) > 120:
        raise CommunityLookupError("Enter a city, region, or postal code.")
    cache_key = f"weather:{query.casefold()}"
    cached = _cached(cache_key)
    if isinstance(cached, WeatherResult):
        return cached

    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Language": "en-US,en;q=0.8"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        geocoding = await _json_get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": query, "count": 1, "language": "en", "format": "json"},
            session=session,
        )
        geocoding_map = _require_mapping(geocoding, "Location lookup returned malformed data.")
        results = geocoding_map.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], Mapping):
            raise CommunityLookupError("I could not find that location.")
        place = results[0]
        latitude = _number(place.get("latitude"), name="latitude")
        longitude = _number(place.get("longitude"), name="longitude")

        payload = await _json_get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "forecast_days": 1,
                "timezone": "auto",
            },
            session=session,
        )
    payload_map = _require_mapping(payload, "Weather data is temporarily unavailable.")
    current = _require_mapping(payload_map.get("current"), "Weather data is temporarily unavailable.")
    daily = payload_map.get("daily") if isinstance(payload_map.get("daily"), Mapping) else {}

    parts = [str(place.get("name") or query)]
    admin = str(place.get("admin1") or "").strip()
    country = str(place.get("country") or "").strip()
    if admin and admin.casefold() != parts[0].casefold():
        parts.append(admin)
    if country:
        parts.append(country)

    result = WeatherResult(
        location=", ".join(parts),
        temperature_c=_number(current.get("temperature_2m"), name="temperature"),
        apparent_c=_number(current.get("apparent_temperature"), name="apparent temperature"),
        humidity=_integer(current.get("relative_humidity_2m"), name="humidity"),
        wind_kmh=_number(current.get("wind_speed_10m"), name="wind speed"),
        weather_code=_integer(current.get("weather_code"), name="weather code"),
        high_c=_optional_first_number(daily.get("temperature_2m_max")),
        low_c=_optional_first_number(daily.get("temperature_2m_min")),
        precipitation_probability=_optional_first_int(daily.get("precipitation_probability_max")),
    )
    return _cache(cache_key, result, ttl=120)


async def wikipedia_lookup(query: str) -> ArticleResult:
    term = " ".join(str(query or "").split()).strip()
    if len(term) < 1 or len(term) > 200:
        raise CommunityLookupError("Enter a Wikipedia topic.")
    cache_key = f"wiki:{term.casefold()}"
    cached = _cached(cache_key)
    if isinstance(cached, ArticleResult):
        return cached

    data = await _json_get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": term,
            "gsrnamespace": 0,
            "gsrlimit": 1,
            "prop": "extracts|info",
            "exintro": 1,
            "explaintext": 1,
            "inprop": "url",
            "format": "json",
            "formatversion": 2,
        },
    )
    data_map = _require_mapping(data, "Wikipedia returned malformed data.")
    query_map = data_map.get("query")
    pages = query_map.get("pages") if isinstance(query_map, Mapping) else None
    if not isinstance(pages, list) or not pages or not isinstance(pages[0], Mapping):
        raise CommunityLookupError("Wikipedia did not return a matching article.")
    page = pages[0]
    title = str(page.get("title") or "").strip()
    url = str(page.get("fullurl") or "").strip()
    if not title or not url.startswith("https://"):
        raise CommunityLookupError("Wikipedia returned incomplete article data.")
    summary = str(page.get("extract") or "No introduction was returned.").strip()[:1800]
    return _cache(cache_key, ArticleResult(title=title, summary=summary, url=url), ttl=600)


async def random_wikipedia() -> ArticleResult:
    data = await _json_get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "generator": "random",
            "grnnamespace": 0,
            "grnlimit": 1,
            "prop": "extracts|info",
            "exintro": 1,
            "explaintext": 1,
            "inprop": "url",
            "format": "json",
            "formatversion": 2,
        },
    )
    data_map = _require_mapping(data, "Wikipedia random article returned malformed data.")
    query_map = data_map.get("query")
    pages = query_map.get("pages") if isinstance(query_map, Mapping) else None
    if not isinstance(pages, list) or not pages or not isinstance(pages[0], Mapping):
        raise CommunityLookupError("Wikipedia random article is temporarily unavailable.")
    page = pages[0]
    title = str(page.get("title") or "Random Wikipedia article").strip()
    url = str(page.get("fullurl") or "").strip()
    if not url.startswith("https://"):
        raise CommunityLookupError("Wikipedia returned an invalid article URL.")
    return ArticleResult(
        title=title,
        summary=str(page.get("extract") or "No introduction was returned.").strip()[:1800],
        url=url,
    )


async def random_wikihow() -> ArticleResult:
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
    headers = {"User-Agent": USER_AGENT}
    try:
        async with _LOOKUP_SEMAPHORE:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get("https://www.wikihow.com/Special:Randomizer", allow_redirects=True) as response:
                    if response.status != 200:
                        raise CommunityLookupError(f"WikiHow returned HTTP {response.status}.")
                    final_url = str(response.url)
    except CommunityLookupError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
        raise CommunityLookupError("WikiHow did not respond in time.") from exc

    parsed = urlparse(final_url)
    host = str(parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not (host == "wikihow.com" or host.endswith(".wikihow.com")):
        raise CommunityLookupError("WikiHow redirected to an unexpected destination, so Dank Shield refused it.")
    path = unquote(parsed.path).strip("/")
    title = path.replace("-", " ").strip() or "Random WikiHow"
    return ArticleResult(title=title, summary="Open this randomly selected WikiHow article.", url=final_url)


def _safe_vote_number(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def urban_dictionary_lookup(term: str) -> UrbanResult:
    word = " ".join(str(term or "").split()).strip()
    if len(word) < 1 or len(word) > 100:
        raise CommunityLookupError("Enter a word or phrase.")
    cache_key = f"urban:{word.casefold()}"
    cached = _cached(cache_key)
    if isinstance(cached, UrbanResult):
        return cached

    data = await _json_get("https://api.urbandictionary.com/v0/define", params={"term": word})
    data_map = _require_mapping(data, "Urban Dictionary returned malformed data.")
    entries = data_map.get("list")
    if not isinstance(entries, list):
        raise CommunityLookupError("Urban Dictionary returned malformed data.")
    valid_entries = [item for item in entries if isinstance(item, Mapping)]
    if not valid_entries:
        raise CommunityLookupError("Urban Dictionary did not return a definition.")
    entry = max(
        valid_entries,
        key=lambda item: _safe_vote_number(item.get("thumbs_up")) - _safe_vote_number(item.get("thumbs_down")),
    )

    def clean(value: Any) -> str:
        return str(value or "").replace("[", "").replace("]", "").strip()

    permalink = str(entry.get("permalink") or "").strip()
    parsed = urlparse(permalink) if permalink else None
    if parsed is not None:
        host = str(parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not (host == "urbandictionary.com" or host.endswith(".urbandictionary.com")):
            permalink = ""

    result = UrbanResult(
        word=str(entry.get("word") or word),
        definition=clean(entry.get("definition"))[:1700],
        example=clean(entry.get("example"))[:700],
        permalink=permalink,
        thumbs_up=_safe_vote_number(entry.get("thumbs_up")),
        thumbs_down=_safe_vote_number(entry.get("thumbs_down")),
    )
    return _cache(cache_key, result, ttl=600)


__all__ = [
    "ArticleResult",
    "CACHE_TTL_SECONDS",
    "CommunityLookupError",
    "MAX_CONCURRENT_LOOKUPS",
    "UrbanResult",
    "WEATHER_LABELS",
    "WeatherResult",
    "random_wikihow",
    "random_wikipedia",
    "urban_dictionary_lookup",
    "weather_lookup",
    "wikipedia_lookup",
]
