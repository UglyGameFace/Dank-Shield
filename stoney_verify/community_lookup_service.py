from __future__ import annotations

"""No-key network utilities used by the Dank Shield Community Tools center."""

from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

import aiohttp

HTTP_TIMEOUT_SECONDS = 8
USER_AGENT = "DankShield/CommunityTools DiscordBot"


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

    @property
    def temperature_f(self) -> float:
        return self.temperature_c * 9.0 / 5.0 + 32.0

    @property
    def apparent_f(self) -> float:
        return self.apparent_c * 9.0 / 5.0 + 32.0

    @property
    def wind_mph(self) -> float:
        return self.wind_kmh * 0.621371


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


async def _json_get(url: str, *, params: dict[str, Any]) -> Any:
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, params=params, allow_redirects=True) as response:
                if response.status != 200:
                    raise CommunityLookupError(f"Lookup service returned HTTP {response.status}.")
                return await response.json(content_type=None)
    except CommunityLookupError:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        raise CommunityLookupError("The lookup service did not respond in time.") from exc


async def weather_lookup(location: str) -> WeatherResult:
    query = " ".join(str(location or "").split()).strip()
    if len(query) < 2 or len(query) > 120:
        raise CommunityLookupError("Enter a city, region, or postal code.")

    geocoding = await _json_get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": query, "count": 1, "language": "en", "format": "json"},
    )
    results = geocoding.get("results") if isinstance(geocoding, dict) else None
    if not results:
        raise CommunityLookupError("I could not find that location.")
    place = results[0]
    latitude = float(place["latitude"])
    longitude = float(place["longitude"])

    current_payload = await _json_get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "timezone": "auto",
        },
    )
    current = current_payload.get("current") if isinstance(current_payload, dict) else None
    if not isinstance(current, dict):
        raise CommunityLookupError("Weather data is temporarily unavailable.")

    parts = [str(place.get("name") or query)]
    admin = str(place.get("admin1") or "").strip()
    country = str(place.get("country") or "").strip()
    if admin and admin.casefold() != parts[0].casefold():
        parts.append(admin)
    if country:
        parts.append(country)

    return WeatherResult(
        location=", ".join(parts),
        temperature_c=float(current.get("temperature_2m", 0.0)),
        apparent_c=float(current.get("apparent_temperature", current.get("temperature_2m", 0.0))),
        humidity=int(current.get("relative_humidity_2m", 0)),
        wind_kmh=float(current.get("wind_speed_10m", 0.0)),
        weather_code=int(current.get("weather_code", -1)),
    )


async def wikipedia_lookup(query: str) -> ArticleResult:
    term = " ".join(str(query or "").split()).strip()
    if len(term) < 1 or len(term) > 200:
        raise CommunityLookupError("Enter a Wikipedia topic.")
    data = await _json_get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "opensearch",
            "search": term,
            "limit": 1,
            "namespace": 0,
            "format": "json",
        },
    )
    if not isinstance(data, list) or len(data) < 4 or not data[1]:
        raise CommunityLookupError("Wikipedia did not return a matching article.")
    return ArticleResult(
        title=str(data[1][0]),
        summary=str(data[2][0] or "No summary was returned.")[:1800],
        url=str(data[3][0]),
    )


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
    pages = ((data or {}).get("query") or {}).get("pages") if isinstance(data, dict) else None
    if not pages:
        raise CommunityLookupError("Wikipedia random article is temporarily unavailable.")
    page = pages[0]
    return ArticleResult(
        title=str(page.get("title") or "Random Wikipedia article"),
        summary=str(page.get("extract") or "No summary was returned.")[:1800],
        url=str(page.get("fullurl") or "https://en.wikipedia.org/"),
    )


async def random_wikihow() -> ArticleResult:
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
    headers = {"User-Agent": USER_AGENT}
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get("https://www.wikihow.com/Special:Randomizer", allow_redirects=True) as response:
                if response.status != 200:
                    raise CommunityLookupError(f"WikiHow returned HTTP {response.status}.")
                final_url = str(response.url)
    except CommunityLookupError:
        raise
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise CommunityLookupError("WikiHow did not respond in time.") from exc

    path = unquote(urlparse(final_url).path).strip("/")
    title = path.replace("-", " ").strip() or "Random WikiHow"
    return ArticleResult(title=title, summary="Open this randomly selected WikiHow article.", url=final_url)


async def urban_dictionary_lookup(term: str) -> UrbanResult:
    word = " ".join(str(term or "").split()).strip()
    if len(word) < 1 or len(word) > 100:
        raise CommunityLookupError("Enter a word or phrase.")
    data = await _json_get(
        "https://api.urbandictionary.com/v0/define",
        params={"term": word},
    )
    entries = data.get("list") if isinstance(data, dict) else None
    if not entries:
        raise CommunityLookupError("Urban Dictionary did not return a definition.")
    entry = max(
        entries,
        key=lambda item: int(item.get("thumbs_up", 0)) - int(item.get("thumbs_down", 0)),
    )
    clean = lambda value: str(value or "").replace("[", "").replace("]", "").strip()
    return UrbanResult(
        word=str(entry.get("word") or word),
        definition=clean(entry.get("definition"))[:1700],
        example=clean(entry.get("example"))[:700],
        permalink=str(entry.get("permalink") or ""),
        thumbs_up=int(entry.get("thumbs_up", 0)),
        thumbs_down=int(entry.get("thumbs_down", 0)),
    )


__all__ = [
    "ArticleResult",
    "CommunityLookupError",
    "UrbanResult",
    "WEATHER_LABELS",
    "WeatherResult",
    "random_wikihow",
    "random_wikipedia",
    "urban_dictionary_lookup",
    "weather_lookup",
    "wikipedia_lookup",
]
