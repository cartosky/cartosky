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
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .. import config
from . import nws as nws_service
from . import sampling
from .run_ids import parse_run_id_datetime

logger = logging.getLogger(__name__)

OPEN_METEO_GEOCODING_BASE = "https://geocoding-api.open-meteo.com/v1"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
GOOGLE_POLLEN_URL = "https://pollen.googleapis.com/v1/forecast:lookup"
ACIS_STATION_META_URL = "https://data.rcc-acis.org/StnMeta"
ACIS_STATION_DATA_URL = "https://data.rcc-acis.org/StnData"
NWS_API_BASE = nws_service.NWS_API_BASE
GEOCODE_COUNTRY_CODES = ["US", "CA"]
GEOCODE_SEARCH_CACHE_NAMESPACE = "geocode-search-v2"

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
AIR_QUALITY_CACHE_TTL = 30 * 60
POLLEN_CACHE_TTL = 3 * 60 * 60
ACIS_CACHE_TTL = 6 * 60 * 60
ACIS_STATION_RESOLUTION_TTL = 30 * 24 * 60 * 60
ACIS_MAX_STATION_CANDIDATES = 5
ACIS_MAX_STATION_ATTEMPTS = 3
ACIS_MIN_USABLE_ROW_RATIO = 0.5
ALERTS_CACHE_TTL = 60
FORECAST_PAGE_CACHE_TTL = 10 * 60
# A degraded NWS result (some product unavailable) is still cached, but briefly,
# so reloads of a flaky location don't re-run the whole slow NWS chain every time
# while still retrying upstream within a minute.
FORECAST_PAGE_DEGRADED_CACHE_TTL = 60

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
class LocationHint:
    display_name: str | None = None
    timezone: str | None = None
    country_code: str | None = None
    admin1: str | None = None
    country: str | None = None


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


def _purge_cache_entry(namespace: str, key: str) -> None:
    _memory_cache(namespace)._store.pop(key, None)
    try:
        _cache_file_path(namespace, key).unlink(missing_ok=True)
    except OSError:
        pass


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


async def _post_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    payload: dict[str, Any],
    retries: int = MAX_RETRIES,
) -> Any:
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        if attempt > 0:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS)
        try:
            response = await client.post(url, json=payload)
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


def _env_first(*names: str) -> str | None:
    for name in names:
        value = _coerce_str(os.getenv(name))
        if value:
            return value
    return None


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


def _date_iso(year: Any, month: Any, day: Any) -> str | None:
    try:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except (TypeError, ValueError):
        return None


def _hex_from_rgb_color(color: Any) -> str | None:
    if not isinstance(color, dict):
        return None
    red = _safe_float(color.get("red")) or 0.0
    green = _safe_float(color.get("green")) or 0.0
    blue = _safe_float(color.get("blue")) or 0.0
    if red == 0.0 and green == 0.0 and blue == 0.0:
        return None
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def _us_aqi_category(value: int | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if value <= 50:
        return "Good", "#3ecf6a"
    if value <= 100:
        return "Moderate", "#f6c84c"
    if value <= 150:
        return "Unhealthy for Sensitive Groups", "#f59e0b"
    if value <= 200:
        return "Unhealthy", "#ef4444"
    if value <= 300:
        return "Very Unhealthy", "#8b5cf6"
    return "Hazardous", "#7f1d1d"


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


def _localize_datetime(value: str | None, timezone_name: str | None) -> datetime | None:
    dt = _parse_iso_datetime(value)
    if dt is None or not timezone_name:
        return dt
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return dt
    if dt.tzinfo is None:
        return dt.replace(tzinfo=zone)
    return dt.astimezone(zone)


def _infer_is_day_for_current(
    observed_at: str | None,
    *,
    timezone_name: str | None,
    om_payload: dict[str, Any] | None,
) -> bool | None:
    if not om_payload:
        return None

    observed_local = _localize_datetime(observed_at, timezone_name)
    daily = om_payload.get("daily") or {}
    current = om_payload.get("current") or {}

    if observed_local is not None:
        target_date = observed_local.date().isoformat()
        dates = daily.get("time") or []
        sunrises = daily.get("sunrise") or []
        sunsets = daily.get("sunset") or []
        for index, date_value in enumerate(dates):
            if _coerce_str(date_value) != target_date:
                continue
            sunrise_local = _localize_datetime((sunrises or [None])[index], timezone_name)
            sunset_local = _localize_datetime((sunsets or [None])[index], timezone_name)
            if sunrise_local is not None and sunset_local is not None:
                return sunrise_local <= observed_local < sunset_local
            break

    explicit_is_day = _safe_int(current.get("is_day"))
    if explicit_is_day is None:
        return None

    current_local = _localize_datetime(_coerce_str(current.get("time")), timezone_name)
    if observed_local is not None and current_local is not None:
        if observed_local.date() == current_local.date() and abs((observed_local - current_local).total_seconds()) <= 12 * 3600:
            return bool(explicit_is_day)
        return None
    return bool(explicit_is_day)


def _apply_current_icon_day_night(
    current_payload: dict[str, Any] | None,
    *,
    timezone_name: str | None,
    om_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not current_payload or _coerce_str(current_payload.get("source")) != "nws":
        return current_payload
    short_text = _coerce_str(current_payload.get("short_text"))
    if short_text is None:
        return current_payload
    is_day = _infer_is_day_for_current(
        _coerce_str(current_payload.get("observed_at")),
        timezone_name=timezone_name,
        om_payload=om_payload,
    )
    if is_day is None:
        return current_payload
    updated_payload = dict(current_payload)
    updated_payload["icon"] = _icon_from_text(short_text, is_day=is_day)
    return updated_payload


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
        return "fog-day" if is_day else "fog-night"
    if code in {51, 53, 55, 56, 57}:
        return "drizzle-day" if is_day else "drizzle-night"
    if code in {61, 63, 65, 66, 67, 80, 81, 82}:
        return "rain-day" if is_day else "rain-night"
    if code in {71, 73, 75, 77, 85, 86}:
        return "snow-day" if is_day else "snow-night"
    if code in {95, 96, 99}:
        return "thunderstorm-day" if is_day else "thunderstorm-night"
    return "cloudy"


def _icon_from_text(text: str | None, *, is_day: bool = True) -> str:
    normalized = (text or "").lower()
    if "thunder" in normalized:
        return "thunderstorm-day" if is_day else "thunderstorm-night"
    if "snow" in normalized:
        return "snow-day" if is_day else "snow-night"
    if "sleet" in normalized or "ice" in normalized:
        return "sleet-day" if is_day else "sleet-night"
    if "rain" in normalized or "shower" in normalized:
        return "rain-day" if is_day else "rain-night"
    if "fog" in normalized or "haze" in normalized:
        return "fog-day" if is_day else "fog-night"
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
    cached = _cache_get(GEOCODE_SEARCH_CACHE_NAMESPACE, cache_key)
    if cached:
        return cached
    if cached is not None:
        _purge_cache_entry(GEOCODE_SEARCH_CACHE_NAMESPACE, cache_key)

    search_attempts: list[dict[str, Any]] = [{
        "name": normalized_query,
        "count": 10,
        "language": "en",
        "format": "json",
    }]

    city_token = _city_token_from_query(normalized_query)
    state_token = _state_token_from_query(normalized_query)
    if city_token and state_token:
        search_attempts.append({
            "name": city_token,
            "count": 10,
            "language": "en",
            "format": "json",
        })

    results: list[dict[str, Any]] = []
    for params in search_attempts:
        payloads = await asyncio.gather(
            *[
                _request_json(
                    client,
                    f"{OPEN_METEO_GEOCODING_BASE}/search",
                    params={**params, "countryCode": country_code},
                )
                for country_code in GEOCODE_COUNTRY_CODES
            ]
        )
        candidate_results: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for payload in payloads:
            attempt_results = payload.get("results") or []
            if not isinstance(attempt_results, list):
                attempt_results = []
            for item in attempt_results:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                if item_id is not None:
                    if item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)
                candidate_results.append(item)
        if candidate_results:
            results = sorted(
                candidate_results,
                key=lambda item: _score_geocode_result(item, normalized_query),
                reverse=True,
            )
            break

    if results:
        _cache_set(GEOCODE_SEARCH_CACHE_NAMESPACE, cache_key, results, GEOCODE_CACHE_TTL)
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


def _resolved_location_from_hint(
    *,
    lat: float,
    lon: float,
    hint: LocationHint,
) -> ResolvedLocation:
    country_code = (hint.country_code or "").strip().upper() or None
    display_name = (hint.display_name or "").strip() or f"{lat:.4f}, {lon:.4f}"
    return ResolvedLocation(
        query=display_name,
        display_name=display_name,
        latitude=lat,
        longitude=lon,
        timezone=(hint.timezone or "").strip() or None,
        country_code=country_code,
        admin1=(hint.admin1 or "").strip() or None,
        country=(hint.country or "").strip() or None,
        resolved_by="frontend_location_hint",
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


async def reverse_location(lat: float, lon: float) -> dict[str, Any]:
    async with _build_client() as client:
        result = await _reverse_open_meteo_geocode(client, lat, lon)

    return {
        "location": _location_result_payload(result, query=f"{lat:.4f},{lon:.4f}") if result else None,
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
                    "is_day",
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


async def _fetch_open_meteo_air_quality(client: httpx.AsyncClient, location: ResolvedLocation) -> dict[str, Any] | None:
    timezone_name = location.timezone or "auto"
    cache_key = f"{location.latitude:.3f}:{location.longitude:.3f}:{timezone_name}"
    cached = _cache_get("open-meteo-air-quality", cache_key)
    if cached is not None:
        return cached

    payload = await _request_json(
        client,
        OPEN_METEO_AIR_QUALITY_URL,
        params={
            "latitude": round(location.latitude, 4),
            "longitude": round(location.longitude, 4),
            "timezone": timezone_name,
            "current": ",".join(
                [
                    "us_aqi",
                    "us_aqi_pm2_5",
                    "us_aqi_pm10",
                    "us_aqi_ozone",
                    "us_aqi_nitrogen_dioxide",
                    "pm2_5",
                    "pm10",
                    "ozone",
                    "nitrogen_dioxide",
                ]
            ),
        },
    )
    _cache_set("open-meteo-air-quality", cache_key, payload, AIR_QUALITY_CACHE_TTL)
    return payload


async def _fetch_google_pollen(client: httpx.AsyncClient, location: ResolvedLocation) -> dict[str, Any] | None:
    api_key = _env_first("CARTOSKY_GOOGLE_POLLEN_API_KEY", "GOOGLE_POLLEN_API_KEY")
    if api_key is None:
        return None

    local_date_key = _utcnow().date().isoformat()
    if location.timezone:
        try:
            local_date_key = _utcnow().astimezone(ZoneInfo(location.timezone)).date().isoformat()
        except ZoneInfoNotFoundError:
            pass

    cache_key = f"{location.latitude:.3f}:{location.longitude:.3f}:{local_date_key}"
    cached = _cache_get("google-pollen", cache_key)
    if cached is not None:
        return cached

    payload = await _request_json(
        client,
        GOOGLE_POLLEN_URL,
        params={
            "key": api_key,
            "location.latitude": round(location.latitude, 4),
            "location.longitude": round(location.longitude, 4),
            "days": 1,
            "pageSize": 1,
            "plantsDescription": "false",
        },
    )
    _cache_set("google-pollen", cache_key, payload, POLLEN_CACHE_TTL)
    return payload


def _parse_acis_precip_amount(value: Any) -> float | None:
    return _parse_acis_numeric_value(value, trace_is_zero=True)


def _parse_acis_numeric_value(value: Any, *, trace_is_zero: bool = False) -> float | None:
    text = _coerce_str(value)
    if text is None:
        return None
    upper = text.upper()
    if trace_is_zero and upper == "T":
        return 0.0
    if upper in {"M", "S"}:
        return None
    return _safe_float(text)


def _acis_row_quality(rows: Any, value_index: int) -> tuple[int, int]:
    total_rows = 0
    usable_rows = 0
    if not isinstance(rows, list):
        return usable_rows, total_rows
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        total_rows += 1
        if len(row) > value_index and _parse_acis_precip_amount(row[value_index]) is not None:
            usable_rows += 1
    return usable_rows, total_rows


def _preferred_acis_sid(values: Any) -> str | None:
    if not isinstance(values, list):
        return None
    type_priority = {
        "6": 0,
        "32": 1,
        "2": 2,
        "1": 3,
        "7": 4,
        "5": 5,
        "4": 6,
        "3": 7,
        "10": 8,
    }
    candidates: list[tuple[int, int, str]] = []
    for raw in values:
        sid = _coerce_str(raw)
        if sid is None:
            continue
        sid_type = sid.rsplit(" ", 1)[-1]
        candidates.append((type_priority.get(sid_type, 99), -len(sid), sid))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def _build_acis_ytd_summary(
    *,
    station_name: str,
    rows: list[Any],
) -> dict[str, Any] | None:
    if not rows:
        return None
    last_row = rows[-1]
    if not isinstance(last_row, list) or len(last_row) < 3:
        return None

    actual_raw = _parse_acis_precip_amount(last_row[1])
    departure_raw = _parse_acis_precip_amount(last_row[2])
    if actual_raw is None and departure_raw is None:
        return None

    actual_in = round(actual_raw, 2) if actual_raw is not None else None
    departure_in = round(departure_raw, 2) if departure_raw is not None else None
    normal_in = None
    if actual_in is not None and departure_in is not None:
        normal_in = round(actual_in - departure_in, 2)
    percent_of_normal = None
    if actual_in is not None and normal_in not in {None, 0}:
        percent_of_normal = round((actual_in / normal_in) * 100)

    return {
        "actual_in": actual_in,
        "normal_in": normal_in,
        "percent_of_normal": percent_of_normal,
        "departure_in": departure_in,
        "station_name": station_name,
    }


def _compose_temperature_history_payload(
    *,
    location: ResolvedLocation,
    daily_payload: list[dict[str, Any]],
    fetched_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    today_daily = daily_payload[0] if daily_payload else None
    today_high_f = _safe_int((today_daily or {}).get("high_f"))
    today_low_f = _safe_int((today_daily or {}).get("low_f"))
    normal_high_f = _safe_int((fetched_payload or {}).get("normal_high_f"))
    normal_low_f = _safe_int((fetched_payload or {}).get("normal_low_f"))
    records_high = (fetched_payload or {}).get("records_high") if isinstance(fetched_payload, dict) else None
    records_low = (fetched_payload or {}).get("records_low") if isinstance(fetched_payload, dict) else None
    station_name = _coerce_str((fetched_payload or {}).get("station_name")) if isinstance(fetched_payload, dict) else None

    if all(
        value is None
        for value in (today_high_f, today_low_f, normal_high_f, normal_low_f, records_high, records_low, station_name)
    ):
        return None

    timezone_name = location.timezone
    local_today = _utcnow().date().isoformat()
    if timezone_name:
        try:
            local_today = _utcnow().astimezone(ZoneInfo(timezone_name)).date().isoformat()
        except ZoneInfoNotFoundError:
            pass
    forecast_date = _coerce_str((today_daily or {}).get("date"))
    high_is_final = forecast_date is not None and forecast_date != local_today

    departure_f = None
    if today_high_f is not None and normal_high_f is not None:
        departure_f = today_high_f - normal_high_f

    return {
        "today_high_f": today_high_f,
        "normal_high_f": normal_high_f,
        "today_low_f": today_low_f,
        "normal_low_f": normal_low_f,
        "departure_f": departure_f,
        "high_is_final": high_is_final,
        "records_high": records_high,
        "records_low": records_low,
        "station_name": station_name,
    }


async def _fetch_observed_precip_mrms(lat: float, lon: float) -> dict[str, Any] | None:
    variables = ["mrms_recent_precip_6h", "mrms_recent_precip_24h", "mrms_recent_precip_72h"]
    try:
        run_id = await asyncio.to_thread(
            sampling.resolve_latest_complete_run,
            "mrms",
            variables,
            region="conus",
        )
    except Exception:
        logger.exception("MRMS observed precip run resolution failed for lat=%.4f lon=%.4f", lat, lon)
        return None
    if run_id is None:
        return None

    results: dict[str, float | None] = {}
    for var, key in (
        ("mrms_recent_precip_6h", "last_6h_in"),
        ("mrms_recent_precip_24h", "last_24h_in"),
        ("mrms_recent_precip_72h", "last_72h_in"),
    ):
        present, value = await asyncio.to_thread(
            sampling.sample_value,
            "mrms",
            run_id,
            var,
            0,
            lat=lat,
            lon=lon,
            region="conus",
        )
        results[key] = value if present else None
    return results


async def _fetch_acis_precip_summary_with_client(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
) -> dict[str, Any] | None:
    today = _utcnow().date()
    cache_key = f"{lat:.3f}:{lon:.3f}:{today.isoformat()}"
    cached = _cache_get("acis-precip-summary", cache_key)
    if cached is not None:
        return cached

    resolved_station = await _resolve_acis_station(client, lat, lon, quality_check_elem="pcpn")
    if resolved_station is None:
        return None
    station_sid, station_name = resolved_station

    try:
        ytd_payload = await _post_json(
            client,
            ACIS_STATION_DATA_URL,
            payload={
                "sid": station_sid,
                "sdate": f"{today.year}-01-01",
                "edate": today.isoformat(),
                "elems": [
                    {"name": "pcpn", "duration": "ytd", "reduce": "sum"},
                    {"name": "pcpn", "duration": "ytd", "reduce": "sum", "normal": "departure"},
                ],
            },
        )
    except ForecastPageError as exc:
        logger.warning(
            "ACIS reduced YTD fetch failed for lat=%.4f lon=%.4f station=%s (%s): %s",
            lat,
            lon,
            station_name,
            station_sid,
            exc.message,
        )
        ytd_payload = None

    ytd_rows = ytd_payload.get("data") if isinstance(ytd_payload, dict) else None
    ytd_row_list = ytd_rows if isinstance(ytd_rows, list) else []
    ytd_summary = _build_acis_ytd_summary(
        station_name=station_name,
        rows=ytd_row_list,
    )
    summary = {
        "ytd": ytd_summary,
    }

    logger.info(
        "ACIS precip summary for lat=%.4f lon=%.4f station=%s: ytd_row_count=%d ytd=%s",
        lat,
        lon,
        station_name,
        len(ytd_row_list),
        summary.get("ytd"),
    )
    if summary.get("ytd") is None:
        return None

    _cache_set("acis-precip-summary", cache_key, summary, ACIS_CACHE_TTL)
    return summary


async def _fetch_acis_precip_summary(lat: float, lon: float) -> dict[str, Any] | None:
    async with _build_client() as client:
        return await _fetch_acis_precip_summary_with_client(client, lat, lon)


async def _resolve_acis_station(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
    *,
    quality_check_elem: str = "pcpn",
) -> tuple[str, str] | None:
    cache_key = f"{lat:.3f}:{lon:.3f}:{quality_check_elem}"
    cached = _cache_get("acis-station-resolution", cache_key)
    if isinstance(cached, dict):
        cached_sid = _coerce_str(cached.get("sid"))
        cached_name = _coerce_str(cached.get("name"))
        if cached_sid is not None and cached_name is not None:
            return cached_sid, cached_name

    today = _utcnow().date()
    attempted_sids: set[str] = set()
    attempt_count = 0

    async def _fetch_candidate_payload(candidate_sid: str) -> dict[str, Any]:
        return await _post_json(
            client,
            ACIS_STATION_DATA_URL,
            payload={
                "sid": candidate_sid,
                "sdate": f"{today.year}-01-01",
                "edate": today.isoformat(),
                "meta": "name,sids",
                "elems": [{"name": quality_check_elem}, {"name": quality_check_elem, "normal": 1}],
            },
        )

    async def _select_from_candidates(
        candidates: list[tuple[str, str]],
        *,
        source_label: str,
    ) -> tuple[str, str] | None:
        nonlocal attempt_count
        partial_candidate: tuple[str, str] | None = None
        for candidate_sid, candidate_name in candidates:
            if source_label == "acis_bbox" and attempt_count >= ACIS_MAX_STATION_ATTEMPTS:
                break
            if candidate_sid in attempted_sids:
                continue
            attempted_sids.add(candidate_sid)
            if source_label == "acis_bbox":
                attempt_count += 1

            candidate_payload = await _fetch_candidate_payload(candidate_sid)
            candidate_rows = candidate_payload.get("data") if isinstance(candidate_payload, dict) else None
            actual_usable_rows, total_rows = _acis_row_quality(candidate_rows, 1)
            normal_usable_rows, _normal_total_rows = _acis_row_quality(candidate_rows, 2)
            actual_ratio = (actual_usable_rows / total_rows) if total_rows > 0 else 0.0
            normal_ratio = (normal_usable_rows / total_rows) if total_rows > 0 else 0.0
            actual_missing_ratio = 1.0 - actual_ratio if total_rows > 0 else 1.0
            normal_missing_ratio = 1.0 - normal_ratio if total_rows > 0 else 1.0

            if total_rows == 0 or actual_ratio < ACIS_MIN_USABLE_ROW_RATIO:
                logger.info(
                    "Rejecting ACIS station %s (%s) for lat=%.4f lon=%.4f: actual_rows=%d/%d actual_missing_fraction=%.2f normal_missing_fraction=%.2f",
                    candidate_name,
                    candidate_sid,
                    lat,
                    lon,
                    actual_usable_rows,
                    total_rows,
                    actual_missing_ratio,
                    normal_missing_ratio,
                )
                continue

            if normal_ratio < ACIS_MIN_USABLE_ROW_RATIO:
                logger.info(
                    "ACIS candidate %s (%s) has usable actual data but insufficient normal-period record: actual_rows=%d/%d normal_rows=%d/%d normal_missing_fraction=%.2f",
                    candidate_name,
                    candidate_sid,
                    actual_usable_rows,
                    total_rows,
                    normal_usable_rows,
                    total_rows,
                    normal_missing_ratio,
                )
                if partial_candidate is None:
                    partial_candidate = (candidate_sid, candidate_name)
                continue

            if source_label == "nws":
                logger.info(
                    "ACIS station resolved via NWS-anchored station %s (%s) for lat=%.4f lon=%.4f",
                    candidate_name,
                    candidate_sid,
                    lat,
                    lon,
                )
            else:
                logger.info(
                    "ACIS station resolved via ACIS bbox search station %s (%s) for lat=%.4f lon=%.4f",
                    candidate_name,
                    candidate_sid,
                    lat,
                    lon,
                )
            return candidate_sid, candidate_name

        if partial_candidate is not None:
            if source_label == "nws":
                logger.info(
                    "ACIS station resolved via NWS-anchored station %s (%s) for lat=%.4f lon=%.4f",
                    partial_candidate[1],
                    partial_candidate[0],
                    lat,
                    lon,
                )
            else:
                logger.info(
                    "ACIS station resolved via ACIS bbox search station %s (%s) for lat=%.4f lon=%.4f",
                    partial_candidate[1],
                    partial_candidate[0],
                    lat,
                    lon,
                )
            return partial_candidate
        return None

    try:
        nws_candidates = await _fetch_nws_station_candidates_for_acis(client, lat, lon)
        if nws_candidates:
            resolved = await _select_from_candidates(
                [(f"{station.station_id} 5", station.name or station.station_id) for station in nws_candidates],
                source_label="nws",
            )
            if resolved is not None:
                _cache_set(
                    "acis-station-resolution",
                    cache_key,
                    {"sid": resolved[0], "name": resolved[1]},
                    ACIS_STATION_RESOLUTION_TTL,
                )
                return resolved
            logger.info(
                "Falling back to ACIS bbox search for lat=%.4f lon=%.4f because all NWS candidates failed quality check",
                lat,
                lon,
            )
        else:
            logger.info(
                "Falling back to ACIS bbox search for lat=%.4f lon=%.4f because NWS station list was empty or unavailable",
                lat,
                lon,
            )

        for span in (0.35, 0.75, 1.5, 3.0):
            if attempt_count >= ACIS_MAX_STATION_ATTEMPTS:
                break
            bbox = f"{lon - span:.3f},{lat - span:.3f},{lon + span:.3f},{lat + span:.3f}"
            meta_payload = await _post_json(
                client,
                ACIS_STATION_META_URL,
                payload={"bbox": bbox, "meta": "name,sids,ll,elev"},
            )
            stations = meta_payload.get("meta") if isinstance(meta_payload, dict) else None
            if not isinstance(stations, list):
                continue

            span_candidates: list[tuple[float, str, str]] = []
            for station in stations:
                if not isinstance(station, dict):
                    continue
                coords = station.get("ll")
                if not isinstance(coords, list) or len(coords) < 2:
                    continue
                station_lon = _safe_float(coords[0])
                station_lat = _safe_float(coords[1])
                sid = _preferred_acis_sid(station.get("sids"))
                name = _coerce_str(station.get("name"))
                if station_lon is None or station_lat is None or sid is None or name is None:
                    continue
                distance_km = _haversine_km(lat, lon, station_lat, station_lon)
                span_candidates.append((distance_km, sid, name))

            span_candidates.sort(key=lambda item: item[0])
            resolved = await _select_from_candidates(
                [(sid, name) for _distance_km, sid, name in span_candidates[:ACIS_MAX_STATION_CANDIDATES]],
                source_label="acis_bbox",
            )
            if resolved is not None:
                _cache_set(
                    "acis-station-resolution",
                    cache_key,
                    {"sid": resolved[0], "name": resolved[1]},
                    ACIS_STATION_RESOLUTION_TTL,
                )
                return resolved
    except ForecastPageError as exc:
        logger.warning(
            "ACIS station resolution failed for lat=%.4f lon=%.4f (%s): %s",
            lat,
            lon,
            quality_check_elem,
            exc.message,
        )
        return None

    logger.warning(
        "ACIS station lookup found no candidates for lat=%.4f lon=%.4f across all bbox spans",
        lat,
        lon,
    )
    return None


async def _fetch_temperature_history(location: ResolvedLocation) -> dict[str, Any] | None:
    today = _utcnow().date().isoformat()
    async with _build_client() as client:
        resolved_station = await _resolve_acis_station(client, location.latitude, location.longitude, quality_check_elem="maxt")
        if resolved_station is None:
            return None
        station_sid, station_name = resolved_station
        try:
            payload = await _post_json(
                client,
                ACIS_STATION_DATA_URL,
                payload={
                    "sid": station_sid,
                    "sdate": today,
                    "edate": today,
                    "elems": [{"name": "maxt", "normal": 1}, {"name": "mint", "normal": 1}],
                },
            )
        except ForecastPageError as exc:
            logger.warning(
                "ACIS temperature history fetch failed for lat=%.4f lon=%.4f station=%s (%s): %s",
                location.latitude,
                location.longitude,
                station_name,
                station_sid,
                exc.message,
            )
            return None

    rows = payload.get("data") if isinstance(payload, dict) else None
    row_list = rows if isinstance(rows, list) else []
    last_row = row_list[-1] if row_list else None
    normal_high_f = None
    normal_low_f = None
    if isinstance(last_row, list):
        if len(last_row) > 1:
            normal_high_f = _safe_int(_parse_acis_numeric_value(last_row[1]))
        if len(last_row) > 2:
            normal_low_f = _safe_int(_parse_acis_numeric_value(last_row[2]))

    return {
        "station_name": station_name,
        "normal_high_f": normal_high_f,
        "normal_low_f": normal_low_f,
        "records_high": None,
        "records_low": None,
    }


def _observed_precip_attribution(observed_precip: dict[str, Any] | None) -> str | None:
    if observed_precip is None:
        return None
    has_mrms = any(observed_precip.get(key) is not None for key in ("last_6h_in", "last_24h_in", "last_72h_in"))
    has_acis = observed_precip.get("ytd") is not None
    if has_mrms and has_acis:
        return "MRMS · ACIS"
    if has_mrms:
        return "MRMS"
    if has_acis:
        return "ACIS"
    return None


def _observed_precip_needs_refetch(observed_precip: dict[str, Any] | None) -> bool:
    if observed_precip is None:
        return True
    ytd = observed_precip.get("ytd")
    if isinstance(ytd, dict) and ytd.get("percent_of_normal") is None:
        return True
    if any(observed_precip.get(key) is None for key in ("last_6h_in", "last_24h_in", "last_72h_in")):
        return True
    return False


async def _fetch_observed_precip(location: ResolvedLocation) -> dict[str, Any] | None:
    mrms_task = asyncio.create_task(_fetch_observed_precip_mrms(location.latitude, location.longitude))
    acis_task = asyncio.create_task(_fetch_acis_precip_summary(location.latitude, location.longitude))
    mrms_payload, acis_payload = await asyncio.gather(mrms_task, acis_task)

    observed_precip = {
        "last_6h_in": None,
        "last_24h_in": None,
        "last_72h_in": None,
        "ytd": None,
    }
    if isinstance(mrms_payload, dict):
        for key in ("last_6h_in", "last_24h_in", "last_72h_in"):
            observed_precip[key] = mrms_payload.get(key)
    if isinstance(acis_payload, dict):
        observed_precip["ytd"] = acis_payload.get("ytd")

    if _observed_precip_attribution(observed_precip) is None:
        return None
    return observed_precip


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


async def _fetch_nws_station_candidates_for_acis(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
) -> list[StationInfo]:
    try:
        points_payload = await _fetch_nws_points(client, lat, lon)
    except ForecastPageError:
        return []

    props = points_payload.get("properties") or {}
    stations_url = _coerce_str(props.get("observationStations"))
    if stations_url is None:
        return []

    try:
        stations = await _fetch_station_candidates(client, stations_url)
    except ForecastPageError:
        return []
    return stations[:4]


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
        if not isinstance(raw_result, dict):
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


def _normalize_open_meteo_air_quality(payload: dict[str, Any]) -> dict[str, Any] | None:
    current = payload.get("current") or {}
    current_units = payload.get("current_units") or {}
    observed_at = _coerce_str(current.get("time"))
    us_aqi = _safe_int(current.get("us_aqi"))
    category, color = _us_aqi_category(us_aqi)

    driver_candidates = [
        ("pm2_5", "PM2.5", "us_aqi_pm2_5", "pm2_5"),
        ("pm10", "PM10", "us_aqi_pm10", "pm10"),
        ("ozone", "Ozone", "us_aqi_ozone", "ozone"),
        ("nitrogen_dioxide", "NO2", "us_aqi_nitrogen_dioxide", "nitrogen_dioxide"),
    ]
    driver = None
    for code, label, aqi_key, value_key in driver_candidates:
        candidate_aqi = _safe_int(current.get(aqi_key))
        if candidate_aqi is None:
            continue
        candidate = {
            "code": code,
            "label": label,
            "value": _safe_float(current.get(value_key)),
            "unit": _coerce_str(current_units.get(value_key)),
            "aqi": candidate_aqi,
        }
        if driver is None or candidate_aqi > driver["aqi"]:
            driver = candidate

    if us_aqi is None and driver is None:
        return None

    display_aqi = us_aqi if us_aqi is not None else (driver["aqi"] if driver is not None else None)
    if category is None or color is None:
        category, color = _us_aqi_category(display_aqi)

    return {
        "source": "open_meteo",
        "observed_at": observed_at,
        "us_aqi": us_aqi,
        "category": category,
        "color": color,
        "driver": driver,
        "pollutants": {
            "pm2_5": _safe_float(current.get("pm2_5")),
            "pm10": _safe_float(current.get("pm10")),
            "ozone": _safe_float(current.get("ozone")),
            "nitrogen_dioxide": _safe_float(current.get("nitrogen_dioxide")),
        },
    }


def _normalize_google_pollen(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    daily_info = payload.get("dailyInfo") or []
    if not daily_info or not isinstance(daily_info[0], dict):
        return None

    first_day = daily_info[0]
    date_payload = first_day.get("date") or {}
    date_value = _date_iso(date_payload.get("year"), date_payload.get("month"), date_payload.get("day"))

    type_entries: list[dict[str, Any]] = []
    for item in first_day.get("pollenTypeInfo") or []:
        if not isinstance(item, dict):
            continue
        index_info = item.get("indexInfo") or {}
        type_entries.append(
            {
                "code": _coerce_str(item.get("code")),
                "label": _coerce_str(item.get("displayName")) or _coerce_str(item.get("code")),
                "category": _coerce_str(index_info.get("category")),
                "index": _safe_int(index_info.get("value")),
                "color": _hex_from_rgb_color(index_info.get("color")),
                "in_season": bool(item.get("inSeason", False)),
            }
        )

    type_entries = [entry for entry in type_entries if entry.get("code")]
    type_entries.sort(key=lambda entry: ((entry.get("index") or -1), bool(entry.get("in_season"))), reverse=True)
    dominant_type = type_entries[0] if type_entries else None

    plant_entries: list[dict[str, Any]] = []
    for item in first_day.get("plantInfo") or []:
        if not isinstance(item, dict):
            continue
        index_info = item.get("indexInfo") or {}
        plant_entries.append(
            {
                "code": _coerce_str(item.get("code")),
                "label": _coerce_str(item.get("displayName")) or _coerce_str(item.get("code")),
                "category": _coerce_str(index_info.get("category")),
                "index": _safe_int(index_info.get("value")),
                "in_season": bool(item.get("inSeason", False)),
            }
        )

    plant_entries = [entry for entry in plant_entries if entry.get("code")]
    plant_entries.sort(key=lambda entry: ((entry.get("index") or -1), bool(entry.get("in_season"))), reverse=True)
    dominant_plant = plant_entries[0] if plant_entries else None

    if dominant_type is None:
        return {
            "source": "google_pollen",
            "date": date_value,
            "index": 0,
            "category": "None",
            "color": "#9ca3af",
            "dominant_type": None,
            "dominant_plant": dominant_plant.get("label") if dominant_plant else None,
            "summary": "No significant pollen types are affecting this location today.",
            "types": [],
        }

    summary_parts: list[str] = []
    for entry in type_entries[:2]:
        category = _coerce_str(entry.get("category"))
        label = _coerce_str(entry.get("label"))
        if category and label:
            summary_parts.append(f"{category} {label.lower()} pollen")

    return {
        "source": "google_pollen",
        "date": date_value,
        "index": dominant_type.get("index"),
        "category": dominant_type.get("category"),
        "color": dominant_type.get("color"),
        "dominant_type": dominant_type.get("label"),
        "dominant_plant": dominant_plant.get("label") if dominant_plant else None,
        "summary": ", ".join(summary_parts) + "." if summary_parts else None,
        "types": type_entries,
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
        is_day = bool(_safe_int((hourly.get("is_day") or [None])[index]) or 0)
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
                "weather_code": _icon_from_wmo(weather_code, is_day=is_day),
                "short_text": _weather_text_from_wmo(weather_code, is_day=is_day),
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


async def _timed(name: str, coro: Any, sink: dict[str, float]) -> Any:
    """Await ``coro``, recording its wall time (ms) into ``sink[name]``.

    Used to attribute a cold forecast-page build across its upstream calls so the
    route can emit a Server-Timing header (find the long pole without guessing).
    """
    started = time.perf_counter()
    try:
        return await coro
    finally:
        sink[name] = round((time.perf_counter() - started) * 1000.0, 1)


async def _build_core_payload(client: httpx.AsyncClient, location: ResolvedLocation) -> dict[str, Any]:
    """Fast forecast core (Open-Meteo forecast + non-NWS supplemental data).

    Renders instantly. NWS enrichment — the observation upgrade, the 7-day
    narrative, alerts, and the AFD — is fetched separately by the client via the
    full ``/forecast-page`` endpoint and merged in. ``source_status.nws ==
    "pending"`` signals the client that NWS enrichment is available for this
    (US) location; ``"not_applicable"`` means open-meteo is the only source.
    """
    timings: dict[str, float] = {}
    aq_task = asyncio.create_task(_timed("open_meteo_air_quality", _fetch_open_meteo_air_quality(client, location), timings))
    pollen_task = asyncio.create_task(_timed("google_pollen", _fetch_google_pollen(client, location), timings))
    observed_precip_task = asyncio.create_task(_timed("observed_precip", _fetch_observed_precip(location), timings))
    temperature_history_task = asyncio.create_task(_timed("temperature_history", _fetch_temperature_history(location), timings))
    try:
        om_payload = await _fetch_open_meteo_forecast(client, location)
        om_status = "ok"
    except ForecastPageError:
        om_payload = {}
        om_status = "unavailable"
    if not om_payload:
        raise ForecastPageError("FORECAST_PAGE_EMPTY", "No forecast data could be assembled for this location.")

    declared_us_region = (location.country_code or "").upper() == "US"
    should_probe_nws = declared_us_region or location.resolved_by in {"coordinate_input", "open_meteo_reverse_geocoding"}

    current_payload = _normalize_open_meteo_current(om_payload)
    hourly_payload, hourly_freshness = _normalize_open_meteo_hourly(om_payload)
    daily_payload = _normalize_open_meteo_daily(om_payload)
    try:
        air_quality_payload = _normalize_open_meteo_air_quality(await aq_task)
    except ForecastPageError:
        air_quality_payload = None
    try:
        pollen_payload = _normalize_google_pollen(await pollen_task)
    except ForecastPageError as exc:
        logger.warning(
            "Google pollen fetch failed for lat=%.4f lon=%.4f query=%s: %s",
            location.latitude,
            location.longitude,
            location.query,
            exc.message,
        )
        pollen_payload = None
    observed_precip_payload = await observed_precip_task
    fetched_temperature_history = await temperature_history_task
    temperature_history_payload = _compose_temperature_history_payload(
        location=location,
        daily_payload=daily_payload,
        fetched_payload=fetched_temperature_history,
    )
    timezone_name = location.timezone or _coerce_str(om_payload.get("timezone"))
    current_payload = _apply_current_icon_day_night(
        current_payload, timezone_name=timezone_name, om_payload=om_payload
    )

    return {
        "location": _location_payload(location, om_payload),
        "source_status": _build_source_status(
            region_mode="us_hybrid" if should_probe_nws else "open_meteo_beta",
            nws_status="pending" if should_probe_nws else "not_applicable",
            om_status=om_status,
        ),
        "current": current_payload,
        "hourly": hourly_payload,
        "daily": daily_payload,
        "air_quality": air_quality_payload,
        "pollen": pollen_payload,
        "temperature_history": temperature_history_payload,
        "observed_precip": observed_precip_payload,
        "official_text_forecast": None,
        "afd": None,
        "alerts": [],
        "attribution": {
            "current": "Open-Meteo",
            "hourly": "Open-Meteo",
            "daily": "Open-Meteo",
            "air_quality": "Open-Meteo" if air_quality_payload else None,
            "pollen": "Google Pollen API" if pollen_payload else None,
            "temperature_history": (
                "Open-Meteo · ACIS"
                if fetched_temperature_history is not None
                else ("Open-Meteo" if temperature_history_payload else None)
            ),
            "observed_precip": _observed_precip_attribution(observed_precip_payload),
            "afd": None,
            "alerts": None,
        },
        "freshness": _build_freshness_payload(
            current_payload=current_payload,
            hourly_freshness=hourly_freshness,
            afd_payload=None,
            alerts_freshness=None,
        ),
    }


async def _build_forecast_page_payload(client: httpx.AsyncClient, location: ResolvedLocation) -> dict[str, Any]:
    cache_key = _forecast_location_cache_key(location)
    cached_payload = _cache_get("forecast-page", cache_key)
    # Serve any unexpired cache entry. Degraded entries are cached with a short
    # TTL (below), so the entry's own expiry — not a per-read retry check — decides
    # when to re-hit NWS. This stops a flaky location from re-running the full NWS
    # chain on every reload.
    if cached_payload is not None:
        payload = copy.deepcopy(cached_payload)
        payload["location"]["query"] = location.query
        payload.setdefault("air_quality", None)
        payload.setdefault("pollen", None)
        payload.setdefault("temperature_history", None)
        payload.setdefault("observed_precip", None)
        attribution = payload.setdefault("attribution", {})
        attribution.setdefault("air_quality", None)
        attribution.setdefault("pollen", None)
        attribution.setdefault("temperature_history", None)
        attribution.setdefault("observed_precip", None)
        if payload.get("pollen") is None:
            try:
                pollen_payload = _normalize_google_pollen(await _fetch_google_pollen(client, location))
            except ForecastPageError as exc:
                logger.warning(
                    "Google pollen fetch failed for lat=%.4f lon=%.4f query=%s: %s",
                    location.latitude,
                    location.longitude,
                    location.query,
                    exc.message,
                )
                pollen_payload = None
            if pollen_payload is not None:
                payload["pollen"] = pollen_payload
                attribution["pollen"] = "Google Pollen API"
            else:
                logger.warning(
                    "Google pollen response normalized empty for lat=%.4f lon=%.4f query=%s",
                    location.latitude,
                    location.longitude,
                    location.query,
                )
        if _observed_precip_needs_refetch(payload.get("observed_precip")):
            observed_precip = await _fetch_observed_precip(location)
            payload["observed_precip"] = observed_precip
            attribution["observed_precip"] = _observed_precip_attribution(observed_precip)
        if payload.get("temperature_history") is None:
            fetched_temperature_history = await _fetch_temperature_history(location)
            payload["temperature_history"] = _compose_temperature_history_payload(
                location=location,
                daily_payload=payload.get("daily") or [],
                fetched_payload=fetched_temperature_history,
            )
            attribution["temperature_history"] = (
                "Open-Meteo · ACIS"
                if fetched_temperature_history is not None
                else ("Open-Meteo" if payload.get("temperature_history") else None)
            )
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

    timings: dict[str, float] = {}
    om_task = asyncio.create_task(_timed("open_meteo", _fetch_open_meteo_forecast(client, location), timings))
    aq_task = asyncio.create_task(_timed("open_meteo_air_quality", _fetch_open_meteo_air_quality(client, location), timings))
    pollen_task = asyncio.create_task(_timed("google_pollen", _fetch_google_pollen(client, location), timings))
    observed_precip_task = asyncio.create_task(_timed("observed_precip", _fetch_observed_precip(location), timings))
    temperature_history_task = asyncio.create_task(_timed("temperature_history", _fetch_temperature_history(location), timings))

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
    air_quality_payload: dict[str, Any] | None = None
    pollen_payload: dict[str, Any] | None = None
    temperature_history_payload: dict[str, Any] | None = None
    observed_precip_payload: dict[str, Any] | None = None
    attribution = {
        "current": None,
        "hourly": None,
        "daily": "Open-Meteo",
        "air_quality": None,
        "pollen": None,
        "temperature_history": None,
        "observed_precip": None,
        "afd": None,
        "alerts": None,
    }

    points_payload: dict[str, Any] | None = None
    zone_codes: list[str] = []
    office_code: str | None = None

    if should_probe_nws:
        try:
            points_payload = await _timed(
                "nws_points", _fetch_nws_points(client, location.latitude, location.longitude), timings
            )
            nws_status = "ok"
        except ForecastPageError:
            nws_status = "unavailable"
            points_payload = None

    try:
        om_payload = await om_task
    except ForecastPageError:
        om_payload = {}
        om_status = "unavailable"

    try:
        air_quality_payload = _normalize_open_meteo_air_quality(await aq_task)
        if air_quality_payload is not None:
            attribution["air_quality"] = "Open-Meteo"
    except ForecastPageError:
        air_quality_payload = None

    try:
        pollen_payload = _normalize_google_pollen(await pollen_task)
        if pollen_payload is not None:
            attribution["pollen"] = "Google Pollen API"
        else:
            logger.warning(
                "Google pollen response normalized empty for lat=%.4f lon=%.4f query=%s",
                location.latitude,
                location.longitude,
                location.query,
            )
    except ForecastPageError as exc:
        logger.warning(
            "Google pollen fetch failed for lat=%.4f lon=%.4f query=%s: %s",
            location.latitude,
            location.longitude,
            location.query,
            exc.message,
        )
        pollen_payload = None

    observed_precip_payload = await observed_precip_task
    attribution["observed_precip"] = _observed_precip_attribution(observed_precip_payload)

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
            tasks["forecast"] = asyncio.create_task(
                _timed("nws_forecast", _fetch_cached_nws_payload(client, "nws-forecast", forecast_url, forecast_url), timings)
            )
        if hourly_url:
            tasks["hourly"] = asyncio.create_task(
                _timed("nws_hourly", _fetch_cached_nws_payload(client, "nws-hourly", hourly_url, hourly_url), timings)
            )
        if stations_url:
            tasks["current"] = asyncio.create_task(
                _timed(
                    "nws_current",
                    _select_best_nws_current(
                        client,
                        stations_url,
                        target_lat=location.latitude,
                        target_lon=location.longitude,
                        target_elevation_m=location.elevation_m,
                    ),
                    timings,
                )
            )
        tasks["alerts"] = asyncio.create_task(
            _timed(
                "nws_alerts",
                _fetch_nws_alerts(client, lat=location.latitude, lon=location.longitude, zone_codes=zone_codes),
                timings,
            )
        )
        if office_code:
            tasks["afd"] = asyncio.create_task(_timed("nws_afd", nws_service.get_afd_by_office(office_code), timings))

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

    fetched_temperature_history = await temperature_history_task
    temperature_history_payload = _compose_temperature_history_payload(
        location=location,
        daily_payload=daily_payload,
        fetched_payload=fetched_temperature_history,
    )
    attribution["temperature_history"] = (
        "Open-Meteo · ACIS"
        if fetched_temperature_history is not None
        else ("Open-Meteo" if temperature_history_payload else None)
    )

    timezone_name = location.timezone or _coerce_str(om_payload.get("timezone"))
    current_payload = _apply_current_icon_day_night(
        current_payload,
        timezone_name=timezone_name,
        om_payload=om_payload,
    )

    if current_payload is None:
        raise ForecastPageError("FORECAST_PAGE_EMPTY", "No forecast data could be assembled for this location.")

    region_mode = "us_hybrid" if declared_us_region or points_payload is not None else "open_meteo_beta"
    payload = {
        "location": _location_payload(location, om_payload),
        "source_status": _build_source_status(region_mode=region_mode, nws_status=nws_status, om_status=om_status),
        "current": current_payload,
        "hourly": hourly_payload,
        "daily": daily_payload,
        "air_quality": air_quality_payload,
        "pollen": pollen_payload,
        "temperature_history": temperature_history_payload,
        "observed_precip": observed_precip_payload,
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
    # Cache policy by NWS state:
    #  - "unavailable" (points lookup failed → NWS skipped, fast open-meteo
    #    fallback): likely transient, so DON'T cache — the next load retries NWS.
    #  - "degraded" (points OK but the slow chain returned partial data): cache
    #    briefly so rapid reloads of a flaky location don't re-run the chain,
    #    while still retrying within ~a minute.
    #  - "ok"/"not_applicable": full TTL.
    nws_state = (payload.get("source_status") or {}).get("nws")
    if nws_state != "unavailable":
        _cache_set(
            "forecast-page",
            cache_key,
            payload,
            FORECAST_PAGE_DEGRADED_CACHE_TTL if nws_state == "degraded" else FORECAST_PAGE_CACHE_TTL,
        )
    # Attach per-upstream timings to the RESPONSE only (a fresh dict), never the
    # cached copy — the route turns these into a Server-Timing header.
    return {**payload, "_server_timing": timings}


async def get_forecast_page_by_query(query: str) -> dict[str, Any]:
    normalized_query = query.strip()
    if len(normalized_query) < 2:
        raise ForecastPageError("INVALID_QUERY", "Forecast page query must be at least 2 characters long.")

    async with _build_client() as client:
        location = await _resolve_location_by_query(client, normalized_query)
        return await _build_forecast_page_payload(client, location)


async def get_forecast_page_by_query_core(query: str) -> dict[str, Any]:
    """Open-Meteo-only core for a free-text query (geocode + open-meteo). The
    client enriches with NWS afterward using the resolved coords in the payload."""
    normalized_query = query.strip()
    if len(normalized_query) < 2:
        raise ForecastPageError("INVALID_QUERY", "Forecast page query must be at least 2 characters long.")

    async with _build_client() as client:
        location = await _resolve_location_by_query(client, normalized_query)
        return await _build_core_payload(client, location)


async def get_forecast_page(lat: float, lon: float, location_hint: LocationHint | None = None) -> dict[str, Any]:
    async with _build_client() as client:
        if location_hint is not None and location_hint.display_name:
            location = _resolved_location_from_hint(lat=lat, lon=lon, hint=location_hint)
        else:
            location = await _resolve_location_by_coordinates(client, lat, lon)
        return await _build_forecast_page_payload(client, location)


async def get_forecast_page_core(lat: float, lon: float, location_hint: LocationHint | None = None) -> dict[str, Any]:
    """Open-Meteo-only forecast core for an instant first paint. The client then
    fetches the full ``/forecast-page`` for NWS enrichment and merges it in."""
    async with _build_client() as client:
        if location_hint is not None and location_hint.display_name:
            location = _resolved_location_from_hint(lat=lat, lon=lon, hint=location_hint)
        else:
            location = await _resolve_location_by_coordinates(client, lat, lon)
        return await _build_core_payload(client, location)


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


# ── Model Guidance meteogram ──────────────────────────────────────────────


class MeteogramRequestError(ValueError):
    """Raised for a structurally valid request the service refuses (HTTP 400)."""


# In-process origin cache (mirrors main._sample_cache). The CDN layer
# (Cache-Control) is the primary cache; this absorbs repeat origin fan-outs
# within the 5-minute window. Key includes the resolved run_id per model, so a
# new cycle publish is a (correct) cache miss rather than stale data.
METEOGRAM_CACHE_TTL_SECONDS = 300.0
_meteogram_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_meteogram_cache_lock = threading.Lock()


def _meteogram_cache_key(
    *,
    lat: float,
    lon: float,
    models: list[str],
    variables: list[str],
    policy_type: str,
    include_members: bool,
    run_ids: dict[str, str | None],
    entitled: dict[str, bool],
    sampling_source: str,
) -> str:
    def _hash(parts: list[str]) -> str:
        return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:12]

    models_hash = _hash(sorted(models))
    vars_hash = _hash(sorted(variables))
    policy_hash = _hash([policy_type])
    run_ids_hash = _hash([f"{m}:{run_ids.get(m) or '-'}" for m in sorted(models)])
    # Folded in (beyond the plan's key spec) so differing entitlements never
    # share a cached payload at the origin.
    entitled_hash = _hash([f"{m}:{int(bool(entitled.get(m, True)))}" for m in sorted(models)])
    # sampling_source ("cog" | "binary") keeps a substrate change from ever
    # serving a payload cached under the other substrate. Required (no default)
    # so the caller can never silently omit it once the substrate can vary.
    return (
        f"meteogram:v1:{round(lat, 3)}:{round(lon, 3)}:"
        f"{models_hash}:{vars_hash}:{policy_hash}:{int(include_members)}:{run_ids_hash}:{entitled_hash}:"
        f"{sampling_source}"
    )


def _variable_units(model: str, var: str, sidecar_units: str | None) -> str:
    if sidecar_units:
        return sidecar_units
    try:
        from ..models.registry import get_model

        plugin = get_model(model)
        canonical = plugin.normalize_var_id(var) if hasattr(plugin, "normalize_var_id") else var
        capability = (
            plugin.get_var_capability(canonical) if hasattr(plugin, "get_var_capability") else None
        )
        units = getattr(capability, "units", None) if capability is not None else None
        if units:
            return str(units)
    except Exception:
        pass
    return ""


# Whether this module's payload builder can emit per-member series. Flipped
# to True with the Phase 5 backend (member series sampled from member grid
# binaries via the seek sampler); kept as an explicit kill switch for the
# member payload path independent of member publishing.
_MEMBER_SERIES_PAYLOAD_SUPPORTED = True


def _model_supports_members(model: str) -> bool:
    """Does this model publish per-member data the meteogram can serve?

    Requires the ensemble.members descriptor on the model's capability
    catalog (member pipeline Phase 2 design R7/D1 — supported_views
    intentionally stays ["mean"] and is not consulted) AND the model on the
    binary-sampling allowlist: member frames exist only as grid binaries, so
    a model on the COG path has no substrate to serve them from.
    """
    if not _MEMBER_SERIES_PAYLOAD_SUPPORTED:
        return False
    normalized = str(model or "").strip().lower()
    if normalized not in config.binary_sampling_models():
        return False
    try:
        from ..models.base import ensemble_member_descriptors
        from ..models.registry import get_model

        plugin = get_model(normalized)
        return bool(ensemble_member_descriptors(plugin))
    except Exception:
        return False


def _member_series_for_model_var(
    model: str,
    run_id: str,
    var: str,
    *,
    lat: float,
    lon: float,
    region: str | None,
    mean_result: dict[str, Any],
) -> dict[str, Any] | None:
    """``variables[var]["members"]`` block per the Model Guidance Section 7
    contract: ``{"mean": {...}, "control": {...}, "m01": {...}, ...}``.

    Returns None when the variable carries no member descriptor (the caller
    omits the key — e.g. a requested variable without member publishing).
    The "mean" entry reuses the already-sampled main series. Member series
    are sampled from the member grid binaries via the seek sampler (one-pixel
    reads — the fan-out is members × fhs frames per variable), using the MEAN
    series' forecast hours as the candidate frame list: member vars are not
    registered in the run manifest (they are metadata under the canonical var
    — design R7), members share the mean's schedule, and an individually
    absent member frame simply drops out via ``present=False``. Member
    ``valid_time`` reuses the mean point's (identical by construction), with
    run_id + fh as the fallback.
    """
    mean_points = mean_result.get("points") or []
    if not mean_points:
        return None
    try:
        from ..models.base import ensemble_member_descriptors, ensemble_member_ids
        from ..models.registry import get_model

        plugin = get_model(model)
        canonical = plugin.normalize_var_id(var)
        descriptor = ensemble_member_descriptors(plugin).get(canonical)
    except Exception:
        logger.exception("Member descriptor resolution failed: %s/%s", model, var)
        return None
    if not descriptor:
        return None

    run_dt = parse_run_id_datetime(run_id)
    candidate_frames: list[tuple[int, str | None]] = [
        (int(point["fh"]), point.get("valid_time")) for point in mean_points
    ]
    members_block: dict[str, Any] = {"mean": {"points": mean_result.get("points")}}
    for member in ensemble_member_ids(descriptor):
        member_var = f"{canonical}__{member}"
        points: list[dict[str, Any]] = []
        for fh, valid_time in candidate_frames:
            present, value = sampling.sample_binary_value_seek(
                model, run_id, member_var, fh, lat=lat, lon=lon, region=region,
            )
            if not present:
                continue
            if valid_time is None and run_dt is not None:
                valid_time = _isoformat(run_dt + timedelta(hours=int(fh)))
            points.append({"fh": fh, "valid_time": valid_time, "value": value})
        members_block[member] = {"points": points or None}
    return members_block


def get_forecast_meteogram(
    *,
    lat: float,
    lon: float,
    models: list[str],
    variables: list[str],
    run_policy: dict[str, Any] | None = None,
    pinned_runs: dict[str, str] | None = None,
    include_members: bool = False,
    region: str | None = None,
    entitled: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Fan out point samples across models/variables and return one payload.

    Reads only already-published artifacts via :mod:`app.services.sampling`
    (same COG read path as ``/api/v4/sample``). Never raises for missing data:
    per-model and per-variable status fields carry the outcome. Raises
    :class:`MeteogramRequestError` only for refused requests (HTTP 400).
    """
    resolved_policy = run_policy or {"type": "latest_per_model"}
    policy_type = str(resolved_policy.get("type") or "latest_per_model")
    if policy_type != "latest_per_model":
        raise MeteogramRequestError(f"Unsupported run_policy: {policy_type}")

    entitled = entitled or {}
    norm_models: list[str] = []
    for model in models:
        normalized = str(model or "").strip().lower()
        if normalized and normalized not in norm_models:
            norm_models.append(normalized)
    norm_vars: list[str] = []
    for var in variables:
        normalized = str(var or "").strip().lower()
        if normalized and normalized not in norm_vars:
            norm_vars.append(normalized)

    if include_members:
        unsupported = [m for m in norm_models if not _model_supports_members(m)]
        if unsupported:
            raise MeteogramRequestError(
                "include_members requires per-member data; unsupported for: "
                + ", ".join(unsupported)
            )

    # Normalize any explicitly pinned runs (model id -> run id), keyed lowercase
    # to match norm_models.
    pinned = {
        str(k or "").strip().lower(): str(v or "").strip()
        for k, v in (pinned_runs or {}).items()
        if str(k or "").strip() and str(v or "").strip()
    }

    # Resolve the run per (entitled) model. An explicitly pinned run is honored
    # only when it exists and is complete for the requested variables; otherwise
    # we fall back to the latest *complete* run — not merely the latest discovered
    # run, which may still be publishing frames (a building run would otherwise
    # produce truncated lines near "Now"). Run ids are part of the cache key so a
    # cycle publish (or a different pin) correctly invalidates the cached payload.
    run_ids: dict[str, str | None] = {}
    for model in norm_models:
        if entitled.get(model) is False:
            continue
        try:
            resolved: str | None = None
            pinned_run = pinned.get(model)
            if pinned_run:
                concrete = sampling.resolve_run(model, pinned_run, region=region)
                if concrete and sampling.run_complete_for_variables(
                    model, concrete, norm_vars, region=region
                ):
                    resolved = concrete
            if resolved is None:
                resolved = sampling.resolve_latest_complete_run(model, norm_vars, region=region)
            run_ids[model] = resolved
        except Exception:
            logger.exception("Meteogram run resolution failed for %s", model)
            run_ids[model] = None

    # Binary-sampling allowlist (migration plan Phase F): allowlisted models
    # sample grid binaries via _sample_variable_series_binary; everything else
    # keeps the value-COG fan-out. The substrate split is folded into the cache
    # key ("cog" when no requested model is allowlisted — byte-identical to the
    # pre-allowlist key — otherwise "binary:<models>") so a substrate flip can
    # never serve a payload cached under the other substrate.
    binary_models = config.binary_sampling_models()
    active_binary = sorted(m for m in norm_models if m in binary_models)
    sampling_source = "binary:" + ",".join(active_binary) if active_binary else "cog"

    cache_key = _meteogram_cache_key(
        lat=lat,
        lon=lon,
        models=norm_models,
        variables=norm_vars,
        policy_type=policy_type,
        include_members=include_members,
        run_ids=run_ids,
        entitled=entitled,
        sampling_source=sampling_source,
    )
    now = time.monotonic()
    with _meteogram_cache_lock:
        cached = _meteogram_cache.get(cache_key)
        if cached is not None:
            expires_at, payload = cached
            if expires_at > now:
                return payload
            _meteogram_cache.pop(cache_key, None)

    # Build the per-(model, variable) plan from manifests (one cached read each):
    # forecast hours + their valid_times + units. Sampling then reads only the COG
    # value per frame — valid_time/units come from the manifest, not a per-frame
    # sidecar — and every frame across all models/variables is sampled in one pass.
    series: dict[str, Any] = {}
    plan: list[tuple[str, str, str, list[tuple[int, str | None]], str | None]] = []
    sample_tasks: list[tuple[str, str, str, int]] = []
    var_results_by_model: dict[str, dict[str, Any]] = {}
    vars_with_values_by_model: dict[str, int] = {}
    for model in norm_models:
        if entitled.get(model) is False:
            series[model] = {"status": "not_entitled"}
            continue
        run_id = run_ids.get(model)
        if not run_id:
            series[model] = {"status": "unavailable", "run_id": None}
            continue
        if model in binary_models:
            # Allowlisted: sample this model's grid binaries; the result shape
            # matches the COG assembly below, so status/series handling is
            # shared. Non-allowlisted models in the same request still take the
            # COG fan-out.
            attach_members = include_members and _model_supports_members(model)
            for var in norm_vars:
                result = _sample_variable_series_binary(
                    model, run_id, var, lat=lat, lon=lon, region=region
                )
                points = result.get("points")
                if points and any(p["value"] is not None for p in points):
                    vars_with_values_by_model[model] = (
                        vars_with_values_by_model.get(model, 0) + 1
                    )
                if attach_members and points:
                    members_block = _member_series_for_model_var(
                        model, run_id, var,
                        lat=lat, lon=lon, region=region, mean_result=result,
                    )
                    if members_block is not None:
                        result["members"] = members_block
                var_results_by_model.setdefault(model, {})[var] = result
            continue
        for var in norm_vars:
            frames, units = sampling.manifest_frame_entries(model, run_id, var, region=region)
            plan.append((model, run_id, var, frames, units))
            for fh, _vt in frames:
                sample_tasks.append((model, run_id, var, fh))

    sampled = sampling.sample_values_parallel(sample_tasks, lat=lat, lon=lon, region=region)
    value_by_key: dict[tuple[str, str, int], tuple[bool, float | None]] = {}
    for task, res in zip(sample_tasks, sampled):
        value_by_key[(task[0], task[2], task[3])] = res

    # Frames whose manifest omits valid_time fall back to the canonical sidecar.
    # Normally empty (the publish pipeline writes valid_time into the manifest),
    # so this adds no reads on the hot path.
    vt_fallback_tasks = [
        (model, run_id, var, fh)
        for model, run_id, var, frames, _units in plan
        for fh, vt in frames
        if vt is None and value_by_key.get((model, var, fh), (False, None))[0]
    ]
    vt_by_key: dict[tuple[str, str, int], str | None] = {}
    if vt_fallback_tasks:
        for task, vt in zip(
            vt_fallback_tasks, sampling.read_frame_valid_times(vt_fallback_tasks, region=region)
        ):
            vt_by_key[(task[0], task[2], task[3])] = vt

    for model, run_id, var, frames, units in plan:
        points: list[dict[str, Any]] = []
        for fh, valid_time in frames:
            present, value = value_by_key.get((model, var, fh), (False, None))
            if not present:
                continue
            if valid_time is None:
                valid_time = vt_by_key.get((model, var, fh))
            points.append({"fh": fh, "valid_time": valid_time, "value": value})
        resolved_units = _variable_units(model, var, units)
        if points:
            points.sort(key=lambda item: item["fh"])
            result: dict[str, Any] = {"units": resolved_units, "points": points}
            if any(p["value"] is not None for p in points):
                vars_with_values_by_model[model] = vars_with_values_by_model.get(model, 0) + 1
        else:
            result = {"units": resolved_units, "points": None, "error": "artifact_not_found"}
        var_results_by_model.setdefault(model, {})[var] = result

    for model in norm_models:
        if model in series:  # already set: not_entitled / unavailable
            continue
        run_id = run_ids.get(model)
        run_dt = parse_run_id_datetime(run_id)
        run_time = _isoformat(run_dt) if run_dt is not None else None
        vwv = vars_with_values_by_model.get(model, 0)
        status = "ok" if (vwv == len(norm_vars) and vwv > 0) else "partial"
        series[model] = {
            "run_id": run_id,
            "run_time": run_time,
            "status": status,
            "variables": var_results_by_model.get(model, {}),
        }

    payload = {
        "location": {"lat": lat, "lon": lon},
        "generated_at": _isoformat(_utcnow()),
        "run_policy": {"type": "latest_per_model"},
        "series": series,
    }
    with _meteogram_cache_lock:
        _meteogram_cache[cache_key] = (time.monotonic() + METEOGRAM_CACHE_TTL_SECONDS, payload)
    return payload


def _sample_variable_series_binary(
    model: str,
    run_id: str,
    var: str,
    *,
    lat: float,
    lon: float,
    region: str | None = None,
) -> dict[str, Any]:
    """Grid-binary counterpart of the per-variable series assembly inside
    :func:`get_forecast_meteogram`.

    Performs the same loop that function runs per ``(model, run, var)`` —
    manifest frames from :func:`sampling.manifest_frame_entries`, one sample per
    forecast hour, frames whose artifact is absent (``present=False``) omitted,
    sidecar ``valid_time`` fallback for frames the manifest leaves blank — but
    reads the packed grid binaries via :func:`sampling.sample_binary_value`
    instead of the value COGs via :func:`sampling.sample_value`. Result shape
    matches the per-variable entry in the meteogram payload:
    ``{"units": ..., "points": [{"fh", "valid_time", "value"}, ...]}`` or
    ``{"units": ..., "points": None, "error": "artifact_not_found"}``.

    Called from ``get_forecast_meteogram`` only for models on the
    ``CARTOSKY_BINARY_SAMPLING_MODELS`` allowlist (empty by default — no model
    takes this path until the migration plan's cutover); also exercised
    directly by the Phase E COG-vs-binary comparison test.
    """
    frames, units = sampling.manifest_frame_entries(model, run_id, var, region=region)
    value_by_fh: dict[int, float | None] = {}
    for fh, _vt in frames:
        present, value = sampling.sample_binary_value(
            model, run_id, var, fh, lat=lat, lon=lon, region=region
        )
        if present:
            value_by_fh[fh] = value

    vt_fallback_tasks = [
        (model, run_id, var, fh) for fh, vt in frames if vt is None and fh in value_by_fh
    ]
    vt_by_fh: dict[int, str | None] = {}
    if vt_fallback_tasks:
        for task, vt in zip(
            vt_fallback_tasks, sampling.read_frame_valid_times(vt_fallback_tasks, region=region)
        ):
            vt_by_fh[task[3]] = vt

    points: list[dict[str, Any]] = []
    for fh, valid_time in frames:
        if fh not in value_by_fh:
            continue
        if valid_time is None:
            valid_time = vt_by_fh.get(fh)
        points.append({"fh": fh, "valid_time": valid_time, "value": value_by_fh[fh]})

    resolved_units = _variable_units(model, var, units)
    if points:
        points.sort(key=lambda item: item["fh"])
        return {"units": resolved_units, "points": points}
    return {"units": resolved_units, "points": None, "error": "artifact_not_found"}
