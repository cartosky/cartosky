"""Forecast page aggregation service.

This module resolves user-entered locations, routes requests through the
appropriate upstream providers, and returns a normalized response contract for
the forecast page UI.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import nws as nws_service

logger = logging.getLogger(__name__)

OPEN_METEO_GEOCODING_BASE = "https://geocoding-api.open-meteo.com/v1"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
NWS_API_BASE = nws_service.NWS_API_BASE

REQUEST_TIMEOUT_SECONDS = 12.0
MAX_RETRIES = 1
RETRY_BACKOFF_SECONDS = 1.0
RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})

GEOCODE_CACHE_TTL = 30 * 24 * 60 * 60
REVERSE_GEOCODE_CACHE_TTL = 30 * 24 * 60 * 60
POINTS_CACHE_TTL = 24 * 60 * 60
OBSERVATION_CACHE_TTL = 10 * 60
FORECAST_CACHE_TTL = 20 * 60
AFD_CACHE_TTL = 30 * 60
OPEN_METEO_CACHE_TTL = 30 * 60
ALERTS_CACHE_TTL = 60
FORECAST_PAGE_CACHE_TTL = 10 * 60

MAX_STATION_CANDIDATES = 8
OBSERVATION_SCORE_THRESHOLD = 25.0

US_STATE_ABBR: dict[str, str] = {
    "alabama": "AL",
    "alaska": "AK",
    "american samoa": "AS",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "guam": "GU",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "northern mariana islands": "MP",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "puerto rico": "PR",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virgin islands": "VI",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}
US_STATE_LOOKUP: dict[str, str] = {
    **US_STATE_ABBR,
    **{value.lower(): value for value in US_STATE_ABBR.values()},
}

COMPASS_TO_DEGREES = {
    "N": 0,
    "NNE": 22,
    "NE": 45,
    "ENE": 68,
    "E": 90,
    "ESE": 112,
    "SE": 135,
    "SSE": 158,
    "S": 180,
    "SSW": 202,
    "SW": 225,
    "WSW": 248,
    "W": 270,
    "WNW": 292,
    "NW": 315,
    "NNW": 338,
}


class ForecastPageError(Exception):
    def __init__(self, code: str, message: str, upstream_status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.upstream_status = upstream_status


class LocationNotFoundError(ForecastPageError):
    def __init__(self, query: str) -> None:
        super().__init__("LOCATION_NOT_FOUND", f"No location results found for '{query}'.")


class UpstreamServiceError(ForecastPageError):
    def __init__(
        self,
        code: str = "FORECAST_UPSTREAM_ERROR",
        message: str = "Forecast upstream service temporarily unavailable.",
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(code=code, message=message, upstream_status=upstream_status)


@dataclass(frozen=True)
class ResolvedLocation:
    query: str | None
    display_name: str
    latitude: float
    longitude: float
    timezone: str | None
    country_code: str | None
    admin1: str | None
    country: str | None
    resolved_by: str
    elevation_m: float | None = None
    postcodes: tuple[str, ...] = ()


@dataclass(frozen=True)
class StationInfo:
    station_id: str
    name: str | None
    latitude: float | None
    longitude: float | None
    elevation_m: float | None
    station_type: str | None


@dataclass(frozen=True)
class ObservationCandidate:
    payload: dict[str, Any]
    score: float


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class _TtlCache:
    def __init__(self) -> None:
        self._store: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.time() > entry.expires_at:
            self._store.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl: float) -> None:
        self._store[key] = _CacheEntry(value=value, expires_at=time.time() + ttl)

    def clear(self) -> None:
        self._store.clear()


_configured_data_root: Path | None = None
_memory_caches: dict[str, _TtlCache] = {}


def configure_data_root(data_root: Path) -> None:
    global _configured_data_root
    _configured_data_root = data_root


def clear_all_caches() -> None:
    for cache in _memory_caches.values():
        cache.clear()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_data_root() -> Path:
    if _configured_data_root is not None:
        return _configured_data_root
    return Path(
        os.environ.get("CARTOSKY_DATA_ROOT")
        or os.environ.get("CARTOSKY_V3_DATA_ROOT")
        or os.environ.get("TWF_V3_DATA_ROOT")
        or "./data"
    )


def _cache_root() -> Path:
    return _resolve_data_root() / "forecast_page_cache"


def _memory_cache(namespace: str) -> _TtlCache:
    cache = _memory_caches.get(namespace)
    if cache is None:
        cache = _TtlCache()
        _memory_caches[namespace] = cache
    return cache


def _cache_file_path(namespace: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return _cache_root() / namespace / f"{digest}.json"


def _cache_get(namespace: str, key: str) -> Any | None:
    mem_value = _memory_cache(namespace).get(key)
    if mem_value is not None:
        return mem_value

    path = _cache_file_path(namespace, key)
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return None

    expires_at = raw.get("expires_at")
    if not isinstance(expires_at, (int, float)) or time.time() > float(expires_at):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    value = raw.get("value")
    _memory_cache(namespace).set(key, value, max(float(expires_at) - time.time(), 1.0))
    return value


def _cache_set(namespace: str, key: str, value: Any, ttl: float) -> None:
    expires_at = time.time() + ttl
    _memory_cache(namespace).set(key, value, ttl)

    path = _cache_file_path(namespace, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"expires_at": expires_at, "value": value}
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload))
    temp_path.replace(path)


def _build_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={
            "User-Agent": nws_service.NWS_USER_AGENT,
            "Accept": "application/json",
        },
    )


async def _request_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    accept: str | None = None,
    retries: int = MAX_RETRIES,
) -> Any:
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        headers = None
        if accept:
            headers = {"Accept": accept}
        if attempt > 0:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS)
        try:
            response = await client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            last_error = exc
            continue
        except httpx.RequestError as exc:
            raise UpstreamServiceError(message=f"Request failed for {url}: {exc}") from exc

        if response.status_code == 200:
            try:
                return response.json()
            except json.JSONDecodeError as exc:
                raise UpstreamServiceError(message=f"Invalid JSON returned from {url}.") from exc

        if response.status_code in RETRYABLE_STATUS_CODES:
            last_error = UpstreamServiceError(upstream_status=response.status_code)
            continue

        raise UpstreamServiceError(
            message=f"Upstream request failed for {url} with HTTP {response.status_code}.",
            upstream_status=response.status_code,
        )

    if isinstance(last_error, UpstreamServiceError):
        raise last_error
    raise UpstreamServiceError(message=f"Request to {url} timed out after retries.")


def _safe_float(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _safe_int(value: Any) -> int | None:
    parsed = _safe_float(value)
    if parsed is None:
        return None
    if parsed.is_integer():
        return int(parsed)
    return round(parsed)


def _coerce_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _fahrenheit(celsius: float | None) -> int | None:
    if celsius is None:
        return None
    return round(celsius * 9.0 / 5.0 + 32.0)


def _mph(kmh: float | None) -> int | None:
    if kmh is None:
        return None
    return round(kmh * 0.621371)


def _miles(meters: float | None) -> float | None:
    if meters is None:
        return None
    return round(meters * 0.000621371, 1)


def _convert_distance_to_miles(value: float | None, unit: str | None) -> float | None:
    if value is None:
        return None
    normalized = (unit or "").strip().lower()
    if normalized in {"m", "meter", "meters"}:
        return _miles(value)
    if normalized in {"km", "kilometer", "kilometers"}:
        return round(value * 0.621371, 1)
    if normalized in {"mi", "mile", "miles"}:
        return round(value, 1)
    return _miles(value)


def _pressure_mb_from_pa(pascals: float | None) -> float | None:
    if pascals is None:
        return None
    return round(pascals / 100.0, 1)


def _convert_precip_to_inches(value: float | None, unit: str | None) -> float | None:
    if value is None:
        return None
    normalized = (unit or "").strip().lower()
    if normalized in {"inch", "in", "inches"}:
        return round(value, 2)
    if normalized in {"mm", "millimeter", "millimeters"}:
        return round(value * 0.0393701, 2)
    if normalized in {"cm", "centimeter", "centimeters"}:
        return round(value * 0.393701, 2)
    return round(value, 2)


def _isoformat(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _minutes_between(older: datetime | None, newer: datetime | None) -> float | None:
    if older is None or newer is None:
        return None
    return max((newer - older).total_seconds() / 60.0, 0.0)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2.0) ** 2
    )
    return radius_km * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _wind_dir_degrees(value: Any) -> int | None:
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in COMPASS_TO_DEGREES:
            return COMPASS_TO_DEGREES[normalized]
    parsed = _safe_float(value)
    if parsed is None:
        return None
    return round(parsed) % 360


def _extract_numeric_prefix(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"(\d+)", text)
    if match is None:
        return None
    return int(match.group(1))


def _weather_text_from_wmo(code: int | None, *, is_day: bool = True) -> str | None:
    if code is None:
        return None
    if code == 0:
        return "Clear" if not is_day else "Sunny"
    if code == 1:
        return "Mostly Sunny" if is_day else "Mostly Clear"
    if code == 2:
        return "Partly Cloudy"
    if code == 3:
        return "Cloudy"
    if code in {45, 48}:
        return "Foggy"
    if code in {51, 53, 55, 56, 57}:
        return "Drizzle"
    if code in {61, 63, 65, 66, 67, 80, 81, 82}:
        return "Rain"
    if code in {71, 73, 75, 77, 85, 86}:
        return "Snow"
    if code in {95, 96, 99}:
        return "Thunderstorms"
    return "Unsettled"


def _icon_from_wmo(code: int | None, *, is_day: bool = True) -> str:
    if code == 0:
        return "clear-day" if is_day else "clear-night"
    if code in {1, 2}:
        return "partly-cloudy-day" if is_day else "partly-cloudy-night"
    if code == 3:
        return "cloudy"
    if code in {45, 48}:
        return "fog"
    if code in {51, 53, 55, 56, 57}:
        return "drizzle"
    if code in {61, 63, 65, 66, 67, 80, 81, 82}:
        return "rain"
    if code in {71, 73, 75, 77, 85, 86}:
        return "snow"
    if code in {95, 96, 99}:
        return "thunderstorm"
    return "cloudy"


def _icon_from_text(text: str | None, *, is_day: bool = True) -> str:
    normalized = (text or "").lower()
    if "thunder" in normalized:
        return "thunderstorm"
    if "snow" in normalized:
        return "snow"
    if "sleet" in normalized or "ice" in normalized:
        return "sleet"
    if "rain" in normalized or "shower" in normalized:
        return "rain"
    if "fog" in normalized or "haze" in normalized:
        return "fog"
    if "wind" in normalized or "breezy" in normalized:
        return "wind"
    if "cloud" in normalized or "overcast" in normalized:
        if "partly" in normalized or "mostly" in normalized:
            return "partly-cloudy-day" if is_day else "partly-cloudy-night"
        return "cloudy"
    if "clear" in normalized or "sun" in normalized:
        return "clear-day" if is_day else "clear-night"
    return "partly-cloudy-day" if is_day else "partly-cloudy-night"


def _extract_zone_code(url: str | None) -> str | None:
    cleaned = _coerce_str(url)
    if cleaned is None:
        return None
    parts = cleaned.rstrip("/").split("/")
    return parts[-1] if parts else None


def _freshness_state_for_current(observed_at: str | None) -> tuple[str, int | None]:
    observed_dt = _parse_iso_datetime(observed_at)
    age_minutes = _minutes_between(observed_dt, _utcnow())
    if age_minutes is None:
        return "unknown", None
    rounded_age = round(age_minutes)
    if age_minutes <= 35:
        return "fresh", rounded_age
    if age_minutes <= 90:
        return "aging", rounded_age
    return "stale", rounded_age


def _freshness_state_for_afd(issued_at: str | None) -> tuple[str, float | None]:
    issued_dt = _parse_iso_datetime(issued_at)
    if issued_dt is None:
        return "unknown", None
    age_hours = max((_utcnow() - issued_dt).total_seconds() / 3600.0, 0.0)
    if age_hours <= 12.0:
        return "fresh", round(age_hours, 1)
    if age_hours <= 24.0:
        return "aging", round(age_hours, 1)
    return "stale", round(age_hours, 1)


def _normalize_location_display_name(result: dict[str, Any]) -> str:
    name = _coerce_str(result.get("name")) or "Unknown"
    country_code = (_coerce_str(result.get("country_code")) or "").upper()
    admin1 = _coerce_str(result.get("admin1"))
    country = _coerce_str(result.get("country"))

    if country_code == "US" and admin1:
        state_abbr = US_STATE_LOOKUP.get(admin1.lower(), admin1)
        return f"{name}, {state_abbr}"
    if admin1 and admin1.lower() != name.lower():
        return f"{name}, {admin1}"
    if country and country.lower() != name.lower():
        return f"{name}, {country}"
    return name


def _location_result_payload(result: dict[str, Any], *, query: str) -> dict[str, Any]:
    return {
        "query": query,
        "display_name": _normalize_location_display_name(result),
        "latitude": _safe_float(result.get("latitude")),
        "longitude": _safe_float(result.get("longitude")),
        "timezone": _coerce_str(result.get("timezone")),
        "country_code": _coerce_str(result.get("country_code")),
        "admin1": _coerce_str(result.get("admin1")),
        "country": _coerce_str(result.get("country")),
        "postcodes": result.get("postcodes") or [],
        "resolved_by": "open_meteo_geocoding",
    }


def _state_token_from_query(query: str) -> str | None:
    match = re.search(r",\s*([A-Za-z]{2,})\s*$", query.strip())
    if match is None:
        return None
    raw_token = match.group(1).strip().lower()
    return US_STATE_LOOKUP.get(raw_token)


def _city_token_from_query(query: str) -> str | None:
    normalized = query.strip()
    if not normalized:
        return None
    state_token = _state_token_from_query(normalized)
    if state_token is None or "," not in normalized:
        return None
    city_token = normalized.rsplit(",", 1)[0].strip()
    return city_token or None


def _score_geocode_result(result: dict[str, Any], query: str) -> float:
    score = 0.0
    normalized_query = query.strip().lower()
    result_name = (_coerce_str(result.get("name")) or "").lower()
    admin1 = (_coerce_str(result.get("admin1")) or "").lower()
    country_code = (_coerce_str(result.get("country_code")) or "").upper()
    feature_code = (_coerce_str(result.get("feature_code")) or "").upper()

    if result_name == normalized_query:
        score += 80.0
    elif normalized_query in result_name:
        score += 35.0

    city_token = _city_token_from_query(query)
    if city_token:
        normalized_city = city_token.lower()
        if result_name == normalized_city:
            score += 75.0
        elif normalized_city in result_name:
            score += 30.0

    state_token = _state_token_from_query(query)
    if state_token and country_code == "US":
        admin1_abbr = US_STATE_LOOKUP.get(admin1)
        if admin1_abbr == state_token:
            score += 60.0

    if re.fullmatch(r"\d{5}", normalized_query):
        if country_code == "US":
            score += 50.0
        postcodes = result.get("postcodes") or []
        if normalized_query in postcodes:
            score += 200.0

    if feature_code in {"PPLC", "PPLA", "PPL"}:
        score += 15.0

    population = _safe_float(result.get("population"))
    if population is not None and population > 0:
        score += min(math.log10(population), 7.0)

    return score


async def _search_open_meteo_geocode(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    normalized_query = query.strip()
    cache_key = normalized_query.lower()
    cached = _cache_get("geocode-search", cache_key)
    if cached is not None:
        return cached

    search_attempts: list[dict[str, Any]] = [{
        "name": normalized_query,
        "count": 10,
        "language": "en",
        "format": "json",
    }]
    if re.fullmatch(r"\d{5}", normalized_query):
        search_attempts[0]["countryCode"] = "US"

    city_token = _city_token_from_query(normalized_query)
    state_token = _state_token_from_query(normalized_query)
    if city_token and state_token:
        search_attempts.append({
            "name": city_token,
            "count": 10,
            "language": "en",
            "format": "json",
            "countryCode": "US",
        })

    results: list[dict[str, Any]] = []
    for params in search_attempts:
        payload = await _request_json(client, f"{OPEN_METEO_GEOCODING_BASE}/search", params=params)
        candidate_results = payload.get("results") or []
        if not isinstance(candidate_results, list):
            candidate_results = []
        candidate_results = [item for item in candidate_results if isinstance(item, dict)]
        if candidate_results:
            results = sorted(
                candidate_results,
                key=lambda item: _score_geocode_result(item, normalized_query),
                reverse=True,
            )
            break

    _cache_set("geocode-search", cache_key, results, GEOCODE_CACHE_TTL)
    return results


async def _reverse_open_meteo_geocode(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
) -> dict[str, Any] | None:
    cache_key = f"{lat:.4f},{lon:.4f}"
    cached = _cache_get("reverse-geocode", cache_key)
    if cached is not None:
        return cached

    try:
        payload = await _request_json(
            client,
            f"{OPEN_METEO_GEOCODING_BASE}/reverse",
            params={
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                "count": 1,
                "language": "en",
                "format": "json",
            },
        )
    except ForecastPageError:
        return None

    results = payload.get("results") or []
    if not isinstance(results, list) or not results:
        return None
    first = results[0] if isinstance(results[0], dict) else None
    if first is not None:
        _cache_set("reverse-geocode", cache_key, first, REVERSE_GEOCODE_CACHE_TTL)
    return first


def _resolved_location_from_result(result: dict[str, Any], *, query: str | None, resolved_by: str) -> ResolvedLocation:
    return ResolvedLocation(
        query=query,
        display_name=_normalize_location_display_name(result),
        latitude=_safe_float(result.get("latitude")) or 0.0,
        longitude=_safe_float(result.get("longitude")) or 0.0,
        timezone=_coerce_str(result.get("timezone")),
        country_code=_coerce_str(result.get("country_code")),
        admin1=_coerce_str(result.get("admin1")),
        country=_coerce_str(result.get("country")),
        resolved_by=resolved_by,
        elevation_m=_safe_float(result.get("elevation")),
        postcodes=tuple(result.get("postcodes") or []),
    )


async def search_locations(query: str) -> dict[str, Any]:
    normalized_query = query.strip()
    if len(normalized_query) < 2:
        raise ForecastPageError("INVALID_QUERY", "Location search query must be at least 2 characters long.")

    async with _build_client() as client:
        results = await _search_open_meteo_geocode(client, normalized_query)

    return {
        "query": normalized_query,
        "results": [_location_result_payload(result, query=normalized_query) for result in results],
    }


async def _resolve_location_by_query(client: httpx.AsyncClient, query: str) -> ResolvedLocation:
    results = await _search_open_meteo_geocode(client, query)
    if not results:
        raise LocationNotFoundError(query)
    return _resolved_location_from_result(results[0], query=query, resolved_by="open_meteo_geocoding")


async def _resolve_location_by_coordinates(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
) -> ResolvedLocation:
    result = await _reverse_open_meteo_geocode(client, lat, lon)
    if result is None:
        return ResolvedLocation(
            query=f"{lat:.4f},{lon:.4f}",
            display_name=f"{lat:.4f}, {lon:.4f}",
            latitude=lat,
            longitude=lon,
            timezone=None,
            country_code=None,
            admin1=None,
            country=None,
            resolved_by="coordinate_input",
        )
    resolved = _resolved_location_from_result(result, query=f"{lat:.4f},{lon:.4f}", resolved_by="open_meteo_reverse_geocoding")
    return ResolvedLocation(
        query=resolved.query,
        display_name=resolved.display_name,
        latitude=lat,
        longitude=lon,
        timezone=resolved.timezone,
        country_code=resolved.country_code,
        admin1=resolved.admin1,
        country=resolved.country,
        resolved_by=resolved.resolved_by,
        elevation_m=resolved.elevation_m,
        postcodes=resolved.postcodes,
    )


def _forecast_location_cache_key(location: ResolvedLocation) -> str:
    country = (location.country_code or "XX").upper()
    return f"{country}:{location.latitude:.3f}:{location.longitude:.3f}"


async def _fetch_open_meteo_forecast(client: httpx.AsyncClient, location: ResolvedLocation) -> dict[str, Any]:
    timezone_name = location.timezone or "auto"
    cache_key = f"{location.latitude:.3f}:{location.longitude:.3f}:{timezone_name}"
    cached = _cache_get("open-meteo-forecast", cache_key)
    if cached is not None:
        return cached

    payload = await _request_json(
        client,
        OPEN_METEO_FORECAST_URL,
        params={
            "latitude": round(location.latitude, 4),
            "longitude": round(location.longitude, 4),
            "timezone": timezone_name,
            "forecast_days": 16,
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "current": ",".join(
                [
                    "temperature_2m",
                    "dew_point_2m",
                    "relative_humidity_2m",
                    "wind_speed_10m",
                    "wind_gusts_10m",
                    "wind_direction_10m",
                    "pressure_msl",
                    "visibility",
                    "weather_code",
                    "is_day",
                ]
            ),
            "hourly": ",".join(
                [
                    "temperature_2m",
                    "dew_point_2m",
                    "wind_speed_10m",
                    "wind_gusts_10m",
                    "wind_direction_10m",
                    "precipitation_probability",
                    "precipitation",
                    "snowfall",
                    "weather_code",
                ]
            ),
            "daily": ",".join(
                [
                    "weather_code",
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_probability_max",
                    "precipitation_sum",
                    "snowfall_sum",
                    "wind_speed_10m_max",
                    "wind_gusts_10m_max",
                    "sunrise",
                    "sunset",
                ]
            ),
        },
    )
    _cache_set("open-meteo-forecast", cache_key, payload, OPEN_METEO_CACHE_TTL)
    return payload


async def _fetch_nws_points(client: httpx.AsyncClient, lat: float, lon: float) -> dict[str, Any]:
    cache_key = f"{lat:.4f},{lon:.4f}"
    cached = _cache_get("nws-points", cache_key)
    if cached is not None:
        return cached

    payload = await _request_json(
        client,
        f"{NWS_API_BASE}/points/{lat:.4f},{lon:.4f}",
        accept="application/geo+json",
    )
    _cache_set("nws-points", cache_key, payload, POINTS_CACHE_TTL)
    return payload


async def _fetch_cached_nws_payload(client: httpx.AsyncClient, namespace: str, cache_key: str, url: str) -> dict[str, Any]:
    cached = _cache_get(namespace, cache_key)
    if cached is not None:
        return cached
    payload = await _request_json(client, url, accept="application/geo+json")
    ttl = FORECAST_CACHE_TTL if namespace != "nws-alerts" else ALERTS_CACHE_TTL
    _cache_set(namespace, cache_key, payload, ttl)
    return payload


def _station_info_from_feature(feature: dict[str, Any]) -> StationInfo | None:
    props = feature.get("properties") or {}
    station_id = _coerce_str(props.get("stationIdentifier"))
    if station_id is None:
        feature_id = _coerce_str(feature.get("id")) or _coerce_str(props.get("@id"))
        if feature_id:
            station_id = feature_id.rstrip("/").split("/")[-1]
    if station_id is None:
        return None

    coords = feature.get("geometry", {}).get("coordinates") or []
    longitude = _safe_float(coords[0]) if len(coords) >= 2 else None
    latitude = _safe_float(coords[1]) if len(coords) >= 2 else None
    elevation_m = _safe_float(props.get("elevation"))
    return StationInfo(
        station_id=station_id,
        name=_coerce_str(props.get("name")),
        latitude=latitude,
        longitude=longitude,
        elevation_m=elevation_m,
        station_type=_coerce_str(props.get("stationType")),
    )


async def _fetch_station_candidates(client: httpx.AsyncClient, stations_url: str) -> list[StationInfo]:
    cache_key = stations_url
    cached = _cache_get("nws-stations", cache_key)
    if cached is not None:
        return [StationInfo(**item) for item in cached]

    payload = await _request_json(client, stations_url, accept="application/geo+json")
    features = payload.get("features") or payload.get("observationStations") or []
    stations: list[StationInfo] = []
    if isinstance(features, list):
        for item in features[:MAX_STATION_CANDIDATES]:
            if isinstance(item, dict):
                station = _station_info_from_feature(item)
                if station is not None:
                    stations.append(station)
            elif isinstance(item, str):
                stations.append(StationInfo(station_id=item.rstrip("/").split("/")[-1], name=None, latitude=None, longitude=None, elevation_m=None, station_type=None))

    _cache_set("nws-stations", cache_key, [station.__dict__ for station in stations], FORECAST_CACHE_TTL)
    return stations


async def _fetch_station_observation(client: httpx.AsyncClient, station_id: str) -> dict[str, Any]:
    cache_key = station_id
    cached = _cache_get("nws-observation", cache_key)
    if cached is not None:
        return cached
    payload = await _request_json(
        client,
        f"{NWS_API_BASE}/stations/{station_id}/observations/latest",
        accept="application/geo+json",
    )
    _cache_set("nws-observation", cache_key, payload, OBSERVATION_CACHE_TTL)
    return payload


def _normalize_nws_observation_payload(
    raw: dict[str, Any],
    station: StationInfo,
    *,
    target_lat: float,
    target_lon: float,
) -> dict[str, Any]:
    props = raw.get("properties") or {}
    observed_at = _coerce_str(props.get("timestamp"))
    distance_km = None
    if station.latitude is not None and station.longitude is not None:
        distance_km = round(_haversine_km(target_lat, target_lon, station.latitude, station.longitude), 1)

    temp_f = _fahrenheit(_safe_float(props.get("temperature")))
    dewpoint_f = _fahrenheit(_safe_float(props.get("dewpoint")))
    humidity = _safe_int(props.get("relativeHumidity"))
    wind_speed_mph = _mph(_safe_float(props.get("windSpeed")))
    wind_gust_mph = _mph(_safe_float(props.get("windGust")))
    wind_dir_deg = _safe_int(props.get("windDirection"))
    pressure_mb = _pressure_mb_from_pa(_safe_float(props.get("barometricPressure")) or _safe_float(props.get("seaLevelPressure")))
    visibility_mi = _miles(_safe_float(props.get("visibility")))
    short_text = _coerce_str(props.get("textDescription"))
    freshness_state, age_minutes = _freshness_state_for_current(observed_at)
    icon = _icon_from_text(short_text, is_day=True)

    return {
        "source": "nws",
        "observed_at": observed_at,
        "station": {
            "id": station.station_id,
            "name": station.name or station.station_id,
            "distance_km": distance_km,
        },
        "temperature_f": temp_f,
        "dewpoint_f": dewpoint_f,
        "humidity_pct": humidity,
        "wind_dir_deg": wind_dir_deg,
        "wind_speed_mph": wind_speed_mph,
        "wind_gust_mph": wind_gust_mph,
        "pressure_mb": pressure_mb,
        "visibility_mi": visibility_mi,
        "icon": icon,
        "short_text": short_text,
        "quality": {
            "is_fallback": False,
            "is_stale": freshness_state == "stale",
            "freshness": freshness_state,
            "age_minutes": age_minutes,
        },
    }


def _score_station_observation(
    payload: dict[str, Any],
    station: StationInfo,
    *,
    target_elevation_m: float | None,
) -> float:
    score = 100.0
    station_distance = payload.get("station", {}).get("distance_km")
    if isinstance(station_distance, (int, float)):
        score -= 2.0 * float(station_distance)

    age_minutes = payload.get("quality", {}).get("age_minutes")
    if isinstance(age_minutes, (int, float)):
        score -= float(age_minutes)
        if age_minutes > 45:
            score -= 20.0

    null_fields = 0
    for field_name in ("temperature_f", "dewpoint_f", "wind_speed_mph", "visibility_mi"):
        if payload.get(field_name) is None:
            null_fields += 1
    score -= 15.0 * null_fields

    if target_elevation_m is not None and station.elevation_m is not None:
        if abs(target_elevation_m - station.elevation_m) >= 450.0:
            score -= 15.0

    station_type = (station.station_type or "").upper()
    if station_type in {"ASOS", "AWOS"}:
        score += 5.0

    return score


async def _select_best_nws_current(
    client: httpx.AsyncClient,
    stations_url: str,
    *,
    target_lat: float,
    target_lon: float,
    target_elevation_m: float | None,
) -> dict[str, Any] | None:
    stations = await _fetch_station_candidates(client, stations_url)
    if not stations:
        return None

    tasks = [asyncio.create_task(_fetch_station_observation(client, station.station_id)) for station in stations]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    candidates: list[ObservationCandidate] = []
    for station, raw_result in zip(stations, raw_results, strict=False):
        if isinstance(raw_result, Exception):
            continue
        normalized = _normalize_nws_observation_payload(
            raw_result,
            station,
            target_lat=target_lat,
            target_lon=target_lon,
        )
        score = _score_station_observation(normalized, station, target_elevation_m=target_elevation_m)
        candidates.append(ObservationCandidate(payload=normalized, score=score))

    if not candidates:
        return None

    best = max(candidates, key=lambda item: item.score)
    if best.score < OBSERVATION_SCORE_THRESHOLD:
        return None
    return best.payload


def _normalize_open_meteo_current(payload: dict[str, Any]) -> dict[str, Any]:
    current = payload.get("current") or {}
    current_units = payload.get("current_units") or {}
    weather_code = _safe_int(current.get("weather_code"))
    is_day = bool(_safe_int(current.get("is_day")) or 0)
    visibility_unit = _coerce_str(current_units.get("visibility"))
    pressure_value = _safe_float(current.get("pressure_msl"))
    return {
        "source": "open_meteo",
        "observed_at": _coerce_str(current.get("time")),
        "station": None,
        "temperature_f": _safe_int(current.get("temperature_2m")),
        "dewpoint_f": _safe_int(current.get("dew_point_2m")),
        "humidity_pct": _safe_int(current.get("relative_humidity_2m")),
        "wind_dir_deg": _safe_int(current.get("wind_direction_10m")),
        "wind_speed_mph": _safe_int(current.get("wind_speed_10m")),
        "wind_gust_mph": _safe_int(current.get("wind_gusts_10m")),
        "pressure_mb": round(pressure_value, 1) if pressure_value is not None else None,
        "visibility_mi": _convert_distance_to_miles(_safe_float(current.get("visibility")), visibility_unit),
        "icon": _icon_from_wmo(weather_code, is_day=is_day),
        "short_text": _weather_text_from_wmo(weather_code, is_day=is_day),
        "quality": {
            "is_fallback": True,
            "is_stale": False,
            "freshness": "modeled",
            "age_minutes": None,
        },
    }


def _build_daily_summary(weather_code: int | None, pop_pct: int | None, wind_speed_mph: int | None) -> str | None:
    parts: list[str] = []
    if wind_speed_mph is not None:
        if wind_speed_mph >= 30:
            parts.append("Windy")
        elif wind_speed_mph >= 18:
            parts.append("Breezy")
    if pop_pct is not None and pop_pct >= 20:
        base = _weather_text_from_wmo(weather_code, is_day=True)
        if base in {"Snow", "Rain", "Thunderstorms", "Drizzle"}:
            parts.append(f"Chance of {base}")
    if not parts:
        base = _weather_text_from_wmo(weather_code, is_day=True)
        if base:
            parts.append(base)
    return ", ".join(parts[:2]) if parts else None


def _normalize_open_meteo_hourly(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    hourly = payload.get("hourly") or {}
    hourly_units = payload.get("hourly_units") or {}
    times = hourly.get("time") or []
    entries: list[dict[str, Any]] = []
    for index, time_value in enumerate(times):
        weather_code = _safe_int((hourly.get("weather_code") or [None])[index])
        entries.append(
            {
                "time": time_value,
                "source": "open_meteo",
                "temperature_f": _safe_int((hourly.get("temperature_2m") or [None])[index]),
                "dewpoint_f": _safe_int((hourly.get("dew_point_2m") or [None])[index]),
                "wind_speed_mph": _safe_int((hourly.get("wind_speed_10m") or [None])[index]),
                "wind_gust_mph": _safe_int((hourly.get("wind_gusts_10m") or [None])[index]),
                "wind_dir_deg": _safe_int((hourly.get("wind_direction_10m") or [None])[index]),
                "pop_pct": _safe_int((hourly.get("precipitation_probability") or [None])[index]),
                "qpf_in": _convert_precip_to_inches(
                    _safe_float((hourly.get("precipitation") or [None])[index]),
                    _coerce_str(hourly_units.get("precipitation")),
                ),
                "snow_in": _convert_precip_to_inches(
                    _safe_float((hourly.get("snowfall") or [None])[index]),
                    _coerce_str(hourly_units.get("snowfall")),
                ),
                "weather_code": _icon_from_wmo(weather_code, is_day=True),
                "short_text": _weather_text_from_wmo(weather_code, is_day=True),
            }
        )
    freshness = {
        "source": "open_meteo",
        "generated_at": _isoformat(_utcnow()),
        "state": "fresh",
    }
    return entries, freshness


def _normalize_open_meteo_daily(payload: dict[str, Any]) -> list[dict[str, Any]]:
    daily = payload.get("daily") or {}
    daily_units = payload.get("daily_units") or {}
    dates = daily.get("time") or []
    entries: list[dict[str, Any]] = []
    for index, date_value in enumerate(dates):
        weather_code = _safe_int((daily.get("weather_code") or [None])[index])
        pop_pct = _safe_int((daily.get("precipitation_probability_max") or [None])[index])
        wind_speed_mph = _safe_int((daily.get("wind_speed_10m_max") or [None])[index])
        entries.append(
            {
                "date": date_value,
                "source": "open_meteo",
                "high_f": _safe_int((daily.get("temperature_2m_max") or [None])[index]),
                "low_f": _safe_int((daily.get("temperature_2m_min") or [None])[index]),
                "pop_pct": pop_pct,
                "qpf_in": _convert_precip_to_inches(
                    _safe_float((daily.get("precipitation_sum") or [None])[index]),
                    _coerce_str(daily_units.get("precipitation_sum")),
                ),
                "snow_in": _convert_precip_to_inches(
                    _safe_float((daily.get("snowfall_sum") or [None])[index]),
                    _coerce_str(daily_units.get("snowfall_sum")),
                ),
                "wind_speed_mph": wind_speed_mph,
                "wind_gust_mph": _safe_int((daily.get("wind_gusts_10m_max") or [None])[index]),
                "sunrise": _coerce_str((daily.get("sunrise") or [None])[index]),
                "sunset": _coerce_str((daily.get("sunset") or [None])[index]),
                "icon": _icon_from_wmo(weather_code, is_day=True),
                "short_text": _build_daily_summary(weather_code, pop_pct, wind_speed_mph),
            }
        )
    return entries


def _qpf_inches_from_nws_quantity(quantity: Any) -> float | None:
    if not isinstance(quantity, dict):
        return None
    value = _safe_float(quantity.get("value"))
    if value is None:
        return None
    unit_code = _coerce_str(quantity.get("unitCode"))
    if unit_code and unit_code.lower().endswith("mm"):
        return round(value * 0.0393701, 2)
    return round(value, 2)


def _normalize_nws_hourly(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    props = payload.get("properties") or {}
    periods = props.get("periods") or []
    entries: list[dict[str, Any]] = []
    for period in periods:
        if not isinstance(period, dict):
            continue
        short_text = _coerce_str(period.get("shortForecast"))
        is_daytime = bool(period.get("isDaytime", True))
        entries.append(
            {
                "time": _coerce_str(period.get("startTime")),
                "source": "nws",
                "temperature_f": _safe_int(period.get("temperature")),
                "dewpoint_f": None,
                "wind_speed_mph": _extract_numeric_prefix(_coerce_str(period.get("windSpeed"))),
                "wind_gust_mph": None,
                "wind_dir_deg": _wind_dir_degrees(period.get("windDirection")),
                "pop_pct": _safe_int((period.get("probabilityOfPrecipitation") or {}).get("value")),
                "qpf_in": _qpf_inches_from_nws_quantity(period.get("quantitativePrecipitation")),
                "snow_in": _qpf_inches_from_nws_quantity(period.get("snowfallAmount")),
                "weather_code": _icon_from_text(short_text, is_day=is_daytime),
                "short_text": short_text,
            }
        )
    freshness = {
        "source": "nws",
        "generated_at": _coerce_str(props.get("generatedAt")) or _coerce_str(props.get("updated")),
        "state": "fresh",
    }
    return entries, freshness


def _normalize_nws_text_forecast(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    props = payload.get("properties") or {}
    periods = props.get("periods") or []
    normalized_periods: list[dict[str, Any]] = []
    for period in periods:
        if not isinstance(period, dict):
            continue
        normalized_periods.append(
            {
                "name": _coerce_str(period.get("name")),
                "start": _coerce_str(period.get("startTime")),
                "end": _coerce_str(period.get("endTime")),
                "is_daytime": bool(period.get("isDaytime", True)),
                "temperature_f": _safe_int(period.get("temperature")),
                "wind_text": " ".join(
                    [
                        item
                        for item in [
                            _coerce_str(period.get("windDirection")),
                            _coerce_str(period.get("windSpeed")),
                        ]
                        if item
                    ]
                )
                or None,
                "icon_url": _coerce_str(period.get("icon")),
                "short_text": _coerce_str(period.get("shortForecast")),
                "detailed_text": _coerce_str(period.get("detailedForecast")),
            }
        )
    if not normalized_periods:
        return None, {"source": "nws", "generated_at": None, "state": "unknown"}
    return (
        {
            "source": "nws",
            "generated_at": _coerce_str(props.get("generatedAt")) or _coerce_str(props.get("updated")),
            "periods": normalized_periods,
        },
        {
            "source": "nws",
            "generated_at": _coerce_str(props.get("generatedAt")) or _coerce_str(props.get("updated")),
            "state": "fresh",
        },
    )


async def _fetch_nws_alerts(
    client: httpx.AsyncClient,
    *,
    lat: float,
    lon: float,
    zone_codes: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    point_key = f"point:{lat:.3f},{lon:.3f}"
    try:
        payload = await _fetch_cached_nws_payload(
            client,
            "nws-alerts",
            point_key,
            f"{NWS_API_BASE}/alerts/active?point={lat:.4f},{lon:.4f}",
        )
    except ForecastPageError:
        payload = None

    features = [] if payload is None else payload.get("features") or []
    if not features and zone_codes:
        for zone_code in zone_codes:
            try:
                zone_payload = await _fetch_cached_nws_payload(
                    client,
                    "nws-alerts",
                    f"zone:{zone_code}",
                    f"{NWS_API_BASE}/alerts/active?zone={zone_code}",
                )
            except ForecastPageError:
                continue
            zone_features = zone_payload.get("features") or []
            if zone_features:
                features = zone_features
                break

    normalized_alerts: list[dict[str, Any]] = []
    if isinstance(features, list):
        for feature in features:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties") or {}
            area_desc = _coerce_str(props.get("areaDesc")) or ""
            areas = [item.strip() for item in area_desc.split(";") if item.strip()]
            normalized_alerts.append(
                {
                    "id": _coerce_str(props.get("id")) or _coerce_str(feature.get("id")),
                    "source": "nws",
                    "event": _coerce_str(props.get("event")),
                    "severity": _coerce_str(props.get("severity")),
                    "urgency": _coerce_str(props.get("urgency")),
                    "certainty": _coerce_str(props.get("certainty")),
                    "effective": _coerce_str(props.get("effective")),
                    "expires": _coerce_str(props.get("expires")),
                    "headline": _coerce_str(props.get("headline")),
                    "areas": areas,
                    "description": _coerce_str(props.get("description")),
                    "instruction": _coerce_str(props.get("instruction")),
                }
            )

    freshness = {
        "source": "nws",
        "checked_at": _isoformat(_utcnow()),
        "state": "live",
    }
    return normalized_alerts, freshness


def _location_payload(location: ResolvedLocation, om_payload: dict[str, Any]) -> dict[str, Any]:
    timezone_name = location.timezone or _coerce_str(om_payload.get("timezone"))
    country_code = (location.country_code or "").upper() or None
    return {
        "query": location.query,
        "display_name": location.display_name,
        "latitude": round(location.latitude, 4),
        "longitude": round(location.longitude, 4),
        "timezone": timezone_name,
        "country_code": country_code,
        "admin1": location.admin1,
        "resolved_by": location.resolved_by,
    }


def _build_source_status(*, region_mode: str, nws_status: str, om_status: str) -> dict[str, Any]:
    return {
        "primary_region_mode": region_mode,
        "nws": nws_status,
        "open_meteo": om_status,
        "generated_at": _isoformat(_utcnow()),
    }


def _build_freshness_payload(
    *,
    current_payload: dict[str, Any] | None,
    hourly_freshness: dict[str, Any] | None,
    afd_payload: dict[str, Any] | None,
    alerts_freshness: dict[str, Any] | None,
) -> dict[str, Any]:
    current_quality = (current_payload or {}).get("quality") or {}
    afd_state, afd_age_hours = _freshness_state_for_afd((afd_payload or {}).get("issued_at")) if afd_payload else ("unknown", None)
    return {
        "current": {
            "state": current_quality.get("freshness"),
            "observed_at": (current_payload or {}).get("observed_at"),
            "age_minutes": current_quality.get("age_minutes"),
        },
        "hourly": hourly_freshness,
        "afd": {
            "state": afd_state,
            "issued_at": (afd_payload or {}).get("issued_at") if afd_payload else None,
            "age_hours": afd_age_hours,
        },
        "alerts": alerts_freshness,
    }


async def _build_forecast_page_payload(client: httpx.AsyncClient, location: ResolvedLocation) -> dict[str, Any]:
    cache_key = _forecast_location_cache_key(location)
    cached_payload = _cache_get("forecast-page", cache_key)
    if cached_payload is not None:
        payload = copy.deepcopy(cached_payload)
        payload["location"]["query"] = location.query
        if payload.get("source_status", {}).get("primary_region_mode") == "us_hybrid":
            alerts, alerts_freshness = await _fetch_nws_alerts(
                client,
                lat=location.latitude,
                lon=location.longitude,
                zone_codes=[],
            )
            payload["alerts"] = alerts
            payload["freshness"]["alerts"] = alerts_freshness
        return payload

    om_task = asyncio.create_task(_fetch_open_meteo_forecast(client, location))

    declared_us_region = (location.country_code or "").upper() == "US"
    should_probe_nws = declared_us_region or location.resolved_by in {"coordinate_input", "open_meteo_reverse_geocoding"}

    nws_status = "not_applicable"
    om_status = "ok"
    current_payload: dict[str, Any] | None = None
    hourly_payload: list[dict[str, Any]] = []
    hourly_freshness: dict[str, Any] | None = None
    official_text_forecast: dict[str, Any] | None = None
    official_freshness: dict[str, Any] | None = None
    afd_payload: dict[str, Any] | None = None
    alerts_payload: list[dict[str, Any]] = []
    alerts_freshness: dict[str, Any] | None = None
    attribution = {
        "current": None,
        "hourly": None,
        "daily": "Open-Meteo",
        "afd": None,
        "alerts": None,
    }

    points_payload: dict[str, Any] | None = None
    zone_codes: list[str] = []
    office_code: str | None = None

    if should_probe_nws:
        try:
            points_payload = await _fetch_nws_points(client, location.latitude, location.longitude)
            nws_status = "ok"
        except ForecastPageError:
            nws_status = "unavailable"
            points_payload = None

    try:
        om_payload = await om_task
    except ForecastPageError:
        om_payload = {}
        om_status = "unavailable"

    if points_payload is not None:
        props = points_payload.get("properties") or {}
        office_code = _coerce_str(props.get("cwa")) or _coerce_str(props.get("gridId"))
        forecast_url = _coerce_str(props.get("forecast"))
        hourly_url = _coerce_str(props.get("forecastHourly"))
        stations_url = _coerce_str(props.get("observationStations"))
        zone_codes = [
            code
            for code in [
                _extract_zone_code(props.get("forecastZone")),
                _extract_zone_code(props.get("county")),
                _extract_zone_code(props.get("fireWeatherZone")),
            ]
            if code
        ]

        tasks: dict[str, asyncio.Task[Any]] = {}
        if forecast_url:
            tasks["forecast"] = asyncio.create_task(_fetch_cached_nws_payload(client, "nws-forecast", forecast_url, forecast_url))
        if hourly_url:
            tasks["hourly"] = asyncio.create_task(_fetch_cached_nws_payload(client, "nws-hourly", hourly_url, hourly_url))
        if stations_url:
            tasks["current"] = asyncio.create_task(
                _select_best_nws_current(
                    client,
                    stations_url,
                    target_lat=location.latitude,
                    target_lon=location.longitude,
                    target_elevation_m=location.elevation_m,
                )
            )
        tasks["alerts"] = asyncio.create_task(
            _fetch_nws_alerts(client, lat=location.latitude, lon=location.longitude, zone_codes=zone_codes)
        )
        if office_code:
            tasks["afd"] = asyncio.create_task(nws_service.get_afd_by_office(office_code))

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        keyed_results = dict(zip(tasks.keys(), results, strict=False))

        forecast_result = keyed_results.get("forecast")
        if isinstance(forecast_result, dict):
            official_text_forecast, official_freshness = _normalize_nws_text_forecast(forecast_result)
            attribution["hourly"] = attribution["hourly"] or "NWS"

        hourly_result = keyed_results.get("hourly")
        if isinstance(hourly_result, dict):
            hourly_payload, hourly_freshness = _normalize_nws_hourly(hourly_result)
            attribution["hourly"] = "NWS"

        current_result = keyed_results.get("current")
        if isinstance(current_result, dict):
            current_payload = current_result
            attribution["current"] = "NWS"

        alerts_result = keyed_results.get("alerts")
        if isinstance(alerts_result, tuple):
            alerts_payload, alerts_freshness = alerts_result
            attribution["alerts"] = "NWS"

        afd_result = keyed_results.get("afd")
        if isinstance(afd_result, nws_service.AfdResult):
            afd_payload = {
                "source": "nws",
                "office": afd_result.wfo,
                "issued_at": afd_result.issued_at,
                "product_id": afd_result.product_id,
                "headline": "Area Forecast Discussion",
                "text": afd_result.product_text,
            }
            attribution["afd"] = "NWS"

        if any(isinstance(keyed_results.get(name), Exception) for name in keyed_results):
            nws_status = "degraded"

    if om_payload:
        if current_payload is None:
            current_payload = _normalize_open_meteo_current(om_payload)
            attribution["current"] = "Open-Meteo"
            if nws_status == "ok":
                nws_status = "degraded"
        if not hourly_payload:
            hourly_payload, hourly_freshness = _normalize_open_meteo_hourly(om_payload)
            attribution["hourly"] = "Open-Meteo"
            if should_probe_nws and nws_status == "ok":
                nws_status = "degraded"
        daily_payload = _normalize_open_meteo_daily(om_payload)
    else:
        daily_payload = []

    if current_payload is None:
        raise ForecastPageError("FORECAST_PAGE_EMPTY", "No forecast data could be assembled for this location.")

    region_mode = "us_hybrid" if declared_us_region or points_payload is not None else "open_meteo_beta"
    payload = {
        "location": _location_payload(location, om_payload),
        "source_status": _build_source_status(region_mode=region_mode, nws_status=nws_status, om_status=om_status),
        "current": current_payload,
        "hourly": hourly_payload,
        "daily": daily_payload,
        "official_text_forecast": official_text_forecast,
        "afd": afd_payload,
        "alerts": alerts_payload,
        "attribution": attribution,
        "freshness": _build_freshness_payload(
            current_payload=current_payload,
            hourly_freshness=hourly_freshness or official_freshness,
            afd_payload=afd_payload,
            alerts_freshness=alerts_freshness,
        ),
    }
    _cache_set("forecast-page", cache_key, payload, FORECAST_PAGE_CACHE_TTL)
    return payload


async def get_forecast_page_by_query(query: str) -> dict[str, Any]:
    normalized_query = query.strip()
    if len(normalized_query) < 2:
        raise ForecastPageError("INVALID_QUERY", "Forecast page query must be at least 2 characters long.")

    async with _build_client() as client:
        location = await _resolve_location_by_query(client, normalized_query)
        return await _build_forecast_page_payload(client, location)


async def get_forecast_page(lat: float, lon: float) -> dict[str, Any]:
    async with _build_client() as client:
        location = await _resolve_location_by_coordinates(client, lat, lon)
        return await _build_forecast_page_payload(client, location)


async def get_forecast_discussion(office: str) -> dict[str, Any] | None:
    normalized_office = office.strip().upper()
    if not normalized_office:
        raise ForecastPageError("INVALID_OFFICE", "Forecast discussion office code is required.")

    cache_key = normalized_office
    cached = _cache_get("forecast-discussion", cache_key)
    if cached is not None:
        return cached

    afd = await nws_service.get_afd_by_office(normalized_office)
    if afd is None:
        return None

    payload = {
        "source": "nws",
        "office": afd.wfo,
        "issued_at": afd.issued_at,
        "product_id": afd.product_id,
        "headline": "Area Forecast Discussion",
        "text": afd.product_text,
    }
    _cache_set("forecast-discussion", cache_key, payload, AFD_CACHE_TTL)
    return payload


async def get_model_guidance_placeholder(lat: float, lon: float) -> dict[str, Any]:
    return {
        "status": "placeholder",
        "message": "Model and ensemble guidance will be added in a follow-up backend pass.",
        "location": {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
        },
        "sections": [
            {
                "id": "ensemble_charts",
                "title": "Ensemble Charts",
                "status": "planned",
                "description": "Reserved for plumes, spread, and ensemble trend visualizations.",
            },
            {
                "id": "guidance_summary",
                "title": "Guidance Summary",
                "status": "planned",
                "description": "Reserved for model notes, confidence text, and comparison summaries.",
            },
        ],
    }