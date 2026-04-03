"""NWS (National Weather Service) service layer.

Provides current observations, 7-day forecast, and Area Forecast Discussion
for anchor cities. All NWS API calls are proxied through this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NWS_API_BASE = "https://api.weather.gov"
NWS_USER_AGENT = "(CartoSky, contact@cartosky.com)"
NWS_REQUEST_TIMEOUT = 10.0  # seconds per request
NWS_RETRY_BACKOFF = 1.0  # seconds before retry
NWS_MAX_RETRIES = 1
_RETRYABLE_STATUS_CODES = frozenset({502, 503, 504})

# Cache TTLs (seconds)
POINTS_CACHE_TTL = 86400  # 24 hours
STATION_LIST_CACHE_TTL = 3600  # 1 hour
OBSERVATION_CACHE_TTL = 180  # 3 minutes
FORECAST_CACHE_TTL = 900  # 15 minutes
AFD_CACHE_TTL = 1800  # 30 minutes

# Station fallback
MAX_STATION_ATTEMPTS = 3
OBSERVATION_STALENESS_THRESHOLD_SECONDS = 90 * 60  # 90 minutes


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PointMetadata:
    """Resolved NWS grid point metadata for an anchor city."""
    wfo: str
    grid_x: int
    grid_y: int


@dataclass(frozen=True)
class AnchorInfo:
    """Static anchor city information from the backend index."""
    anchor_id: str
    city: str
    state: str
    st: str
    lat: float
    lon: float
    wfo: str | None
    grid_x: int | None
    grid_y: int | None


@dataclass
class ObservationResult:
    """Current observation data, converted to US customary units."""
    station_name: str | None
    station_id: str | None
    observed_at: str | None
    temp_f: float | None
    dewpoint_f: float | None
    relative_humidity: float | None
    wind_direction: str | None
    wind_speed_mph: float | None
    wind_gust_mph: float | None
    wind_chill_f: float | None
    heat_index_f: float | None
    pressure_inhg: float | None
    visibility_mi: float | None
    text_description: str | None
    precip_last_hour_in: float | None
    degraded: bool = False
    stations_attempted: int = 1
    fallback_used: bool = False


@dataclass
class ForecastPeriod:
    """A single forecast period (day or night)."""
    number: int
    name: str
    is_daytime: bool
    temp_f: int | None
    wind_speed: str | None
    wind_direction: str | None
    short_forecast: str | None
    detailed_forecast: str | None
    precip_probability: int | None


@dataclass
class ForecastResult:
    """7-day forecast data."""
    generated_at: str | None
    periods: list[ForecastPeriod]


@dataclass
class AfdResult:
    """Area Forecast Discussion."""
    wfo: str
    office_name: str | None
    issued_at: str | None
    product_text: str | None
    product_id: str | None


@dataclass
class WeatherBundle:
    """Combined observation + forecast result for a single anchor."""
    anchor: AnchorInfo
    observation: ObservationResult | None
    forecast: ForecastResult | None
    resolved_from_cache: bool = False


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class NwsServiceError(Exception):
    """Base error for NWS service failures."""

    def __init__(
        self,
        code: str,
        message: str,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.upstream_status = upstream_status


class AnchorNotFoundError(NwsServiceError):
    """Raised when an anchor ID is not in the index."""

    def __init__(self, anchor_id: str) -> None:
        super().__init__(
            code="ANCHOR_NOT_FOUND",
            message=f"Anchor '{anchor_id}' not found.",
        )


class NwsUpstreamError(NwsServiceError):
    """Raised when the NWS API returns a non-retryable error."""

    def __init__(
        self,
        code: str = "NWS_UPSTREAM_ERROR",
        message: str = "NWS API temporarily unavailable.",
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(code=code, message=message, upstream_status=upstream_status)


# ---------------------------------------------------------------------------
# TTL Cache
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class _TtlCache:
    """Simple in-memory TTL cache. Not thread-safe (async single-thread is fine)."""

    def __init__(self) -> None:
        self._store: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl: float) -> None:
        self._store[key] = _CacheEntry(value=value, expires_at=time.monotonic() + ttl)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# Anchor Index
# ---------------------------------------------------------------------------

_anchor_index: dict[str, AnchorInfo] | None = None
_configured_data_root: Path | None = None


def configure_data_root(data_root: Path) -> None:
    """Set the data root directory. Called once from main.py at import time."""
    global _configured_data_root
    _configured_data_root = data_root


def _resolve_data_root() -> Path:
    if _configured_data_root is not None:
        return _configured_data_root
    # Fallback: derive from env vars (same logic as main.py DATA_ROOT)
    return Path(
        os.environ.get("CARTOSKY_DATA_ROOT")
        or os.environ.get("CARTOSKY_V3_DATA_ROOT")
        or os.environ.get("TWF_V3_DATA_ROOT")
        or "./data"
    )


def load_anchor_index(path: Path | None = None) -> dict[str, AnchorInfo]:
    """Load the anchor index JSON from disk. Called once on first access."""
    global _anchor_index
    if path is None:
        path = _resolve_data_root() / "anchor_index.json"

    try:
        with open(path, "r") as f:
            raw = json.load(f)
    except FileNotFoundError:
        logger.error("NWS anchor index not found at %s", path)
        raise NwsServiceError(
            code="ANCHOR_INDEX_MISSING",
            message=f"Anchor index file not found: {path}",
        )
    except json.JSONDecodeError as exc:
        logger.error("NWS anchor index is not valid JSON: %s", exc)
        raise NwsServiceError(
            code="ANCHOR_INDEX_INVALID",
            message=f"Anchor index file is not valid JSON: {exc}",
        )

    anchors: dict[str, AnchorInfo] = {}
    for anchor_id, data in raw.get("anchors", {}).items():
        anchors[anchor_id] = AnchorInfo(
            anchor_id=anchor_id,
            city=data["city"],
            state=data["state"],
            st=data["st"],
            lat=data["lat"],
            lon=data["lon"],
            wfo=data.get("wfo"),
            grid_x=data.get("gridX"),
            grid_y=data.get("gridY"),
        )

    _anchor_index = anchors
    logger.info("Loaded NWS anchor index: %d anchors from %s", len(anchors), path)
    return anchors


def get_anchor_index() -> dict[str, AnchorInfo]:
    """Return the loaded anchor index, loading from disk if necessary."""
    global _anchor_index
    if _anchor_index is None:
        load_anchor_index()
    assert _anchor_index is not None
    return _anchor_index


def get_anchor(anchor_id: str) -> AnchorInfo:
    """Look up an anchor by ID. Raises AnchorNotFoundError if not found."""
    index = get_anchor_index()
    info = index.get(anchor_id)
    if info is None:
        raise AnchorNotFoundError(anchor_id)
    return info


# ---------------------------------------------------------------------------
# HTTP client with retry
# ---------------------------------------------------------------------------

async def _nws_request(
    client: httpx.AsyncClient,
    url: str,
    *,
    retries: int = NWS_MAX_RETRIES,
) -> dict[str, Any]:
    """Make a GET request to the NWS API with retry on transient failures.

    Returns the parsed JSON response. Raises NwsUpstreamError on failure.
    """
    last_error: Exception | None = None

    for attempt in range(1 + retries):
        if attempt > 0:
            logger.info("NWS retry attempt %d for %s", attempt, url)
            await asyncio.sleep(NWS_RETRY_BACKOFF)

        try:
            response = await client.get(url)
        except httpx.TimeoutException as exc:
            logger.warning("NWS request timeout: %s (attempt %d)", url, attempt + 1)
            last_error = exc
            continue
        except httpx.RequestError as exc:
            logger.warning("NWS request error: %s — %s", url, exc)
            raise NwsUpstreamError(
                message=f"NWS API request failed: {exc}",
                upstream_status=None,
            ) from exc

        if response.status_code == 200:
            return response.json()

        status = response.status_code
        logger.warning("NWS HTTP %d from %s (attempt %d)", status, url, attempt + 1)

        if status in _RETRYABLE_STATUS_CODES:
            last_error = NwsUpstreamError(upstream_status=status)
            continue

        # Non-retryable error
        raise NwsUpstreamError(
            code="NWS_UPSTREAM_ERROR",
            message=f"NWS API returned HTTP {status}.",
            upstream_status=status,
        )

    # All retries exhausted
    if isinstance(last_error, NwsUpstreamError):
        raise last_error
    raise NwsUpstreamError(
        message="NWS API request timed out after retries.",
        upstream_status=None,
    )


def _build_client() -> httpx.AsyncClient:
    """Create an httpx AsyncClient configured for NWS API calls."""
    return httpx.AsyncClient(
        timeout=NWS_REQUEST_TIMEOUT,
        headers={
            "User-Agent": NWS_USER_AGENT,
            "Accept": "application/geo+json",
        },
    )


# ---------------------------------------------------------------------------
# Module-level caches
# ---------------------------------------------------------------------------

_points_cache = _TtlCache()
_station_list_cache = _TtlCache()
_observation_cache = _TtlCache()
_forecast_cache = _TtlCache()
_afd_cache = _TtlCache()


def clear_all_caches() -> None:
    """Clear all NWS caches. Useful for testing."""
    _points_cache.clear()
    _station_list_cache.clear()
    _observation_cache.clear()
    _forecast_cache.clear()
    _afd_cache.clear()


# ---------------------------------------------------------------------------
# Unit conversion helpers
# ---------------------------------------------------------------------------

def _c_to_f(celsius: float | None) -> float | None:
    """Convert Celsius to Fahrenheit, rounded to nearest integer."""
    if celsius is None:
        return None
    return round(celsius * 9 / 5 + 32)


def _kmh_to_mph(kmh: float | None) -> float | None:
    """Convert km/h to mph, rounded to nearest integer."""
    if kmh is None:
        return None
    return round(kmh * 0.621371)


def _pa_to_inhg(pascals: float | None) -> float | None:
    """Convert Pascals to inches of mercury, rounded to 2 decimal places."""
    if pascals is None:
        return None
    return round(pascals * 0.00029530, 2)


def _m_to_mi(meters: float | None) -> float | None:
    """Convert meters to miles, rounded to 1 decimal place."""
    if meters is None:
        return None
    return round(meters * 0.000621371, 1)


def _mm_to_in(mm: float | None) -> float | None:
    """Convert millimeters to inches, rounded to 2 decimal places."""
    if mm is None:
        return None
    return round(mm * 0.0393701, 2)


def _safe_float(obj: dict[str, Any] | None) -> float | None:
    """Extract the numeric value from an NWS quantity object like {"value": 12.3, "unitCode": "..."}."""
    if obj is None:
        return None
    val = obj.get("value")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _wind_direction_label(degrees: float | None) -> str | None:
    """Convert wind direction in degrees to a compass label."""
    if degrees is None:
        return None
    directions = [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    ]
    index = round(degrees / 22.5) % 16
    return directions[index]


# ---------------------------------------------------------------------------
# Point metadata resolution
# ---------------------------------------------------------------------------

async def _fetch_points(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
) -> PointMetadata:
    """Fetch /points/{lat},{lon} from NWS and extract grid metadata."""
    url = f"{NWS_API_BASE}/points/{lat:.4f},{lon:.4f}"
    data = await _nws_request(client, url)
    props = data.get("properties", {})

    wfo = props.get("cwa") or props.get("gridId")
    grid_x = props.get("gridX")
    grid_y = props.get("gridY")

    if not wfo or grid_x is None or grid_y is None:
        raise NwsUpstreamError(
            code="NWS_INVALID_POINTS_RESPONSE",
            message=f"NWS /points response missing required fields for ({lat}, {lon}).",
        )

    return PointMetadata(wfo=wfo, grid_x=int(grid_x), grid_y=int(grid_y))


async def resolve_point_metadata(
    client: httpx.AsyncClient,
    anchor: AnchorInfo,
    *,
    force_refresh: bool = False,
) -> tuple[PointMetadata, bool]:
    """Resolve NWS grid metadata for an anchor, using cache and fallback.

    Returns (metadata, resolved_from_cache).
    """
    cache_key = anchor.anchor_id

    if not force_refresh:
        cached = _points_cache.get(cache_key)
        if cached is not None:
            return cached, True

    # Try live /points fetch
    try:
        meta = await _fetch_points(client, anchor.lat, anchor.lon)
        _points_cache.set(cache_key, meta, POINTS_CACHE_TTL)
        return meta, False
    except NwsServiceError:
        # Fall back to pre-computed values from the anchor index
        if anchor.wfo and anchor.grid_x is not None and anchor.grid_y is not None:
            meta = PointMetadata(wfo=anchor.wfo, grid_x=anchor.grid_x, grid_y=anchor.grid_y)
            _points_cache.set(cache_key, meta, POINTS_CACHE_TTL)
            logger.warning(
                "NWS /points failed for %s; using pre-computed metadata (wfo=%s, grid=%d,%d)",
                anchor.anchor_id, anchor.wfo, anchor.grid_x, anchor.grid_y,
            )
            return meta, False
        raise


# ---------------------------------------------------------------------------
# Station list
# ---------------------------------------------------------------------------

async def _fetch_station_list(
    client: httpx.AsyncClient,
    wfo: str,
    grid_x: int,
    grid_y: int,
) -> list[str]:
    """Fetch the list of observation stations for a grid point, ordered by proximity."""
    url = f"{NWS_API_BASE}/gridpoints/{wfo}/{grid_x},{grid_y}/stations"
    data = await _nws_request(client, url)

    features = data.get("features") or data.get("observationStations") or []
    station_ids: list[str] = []

    if isinstance(features, list):
        for feat in features:
            if isinstance(feat, dict):
                props = feat.get("properties", {})
                sid = props.get("stationIdentifier")
                if sid:
                    station_ids.append(str(sid))
            elif isinstance(feat, str):
                # Sometimes NWS returns station URLs instead of feature objects
                parts = feat.rstrip("/").split("/")
                if parts:
                    station_ids.append(parts[-1])

    return station_ids


async def get_station_list(
    client: httpx.AsyncClient,
    wfo: str,
    grid_x: int,
    grid_y: int,
) -> list[str]:
    """Get station list with caching."""
    cache_key = f"{wfo}/{grid_x},{grid_y}"
    cached = _station_list_cache.get(cache_key)
    if cached is not None:
        return cached

    stations = await _fetch_station_list(client, wfo, grid_x, grid_y)
    if stations:
        _station_list_cache.set(cache_key, stations, STATION_LIST_CACHE_TTL)
    return stations


# ---------------------------------------------------------------------------
# Observations with station fallback
# ---------------------------------------------------------------------------

def _parse_observation(raw: dict[str, Any], station_id: str) -> ObservationResult:
    """Parse a raw NWS observation response into an ObservationResult with unit conversion."""
    props = raw.get("properties", {})

    # Station name: try the station metadata first
    station_name = None
    station_meta = props.get("station")
    if isinstance(station_meta, str):
        # Station is a URL reference; extract ID
        pass
    station_name = station_name or station_id

    observed_at = props.get("timestamp")

    # Temperature and related fields (NWS returns Celsius)
    temp_c = _safe_float(props.get("temperature"))
    dewpoint_c = _safe_float(props.get("dewpoint"))
    wind_chill_c = _safe_float(props.get("windChill"))
    heat_index_c = _safe_float(props.get("heatIndex"))

    # Humidity
    humidity_obj = props.get("relativeHumidity")
    humidity = _safe_float(humidity_obj)
    if humidity is not None:
        humidity = round(humidity)

    # Wind (NWS returns km/h)
    wind_speed_kmh = _safe_float(props.get("windSpeed"))
    wind_gust_kmh = _safe_float(props.get("windGust"))
    wind_direction_deg = _safe_float(props.get("windDirection"))

    # Pressure (NWS returns Pascals)
    # Try barometricPressure first, fall back to seaLevelPressure
    pressure_pa = _safe_float(props.get("barometricPressure"))
    if pressure_pa is None:
        pressure_pa = _safe_float(props.get("seaLevelPressure"))

    # Visibility (meters)
    visibility_m = _safe_float(props.get("visibility"))

    # Precipitation last hour (mm in the raw NWS data, though unitCode may differ)
    precip_mm = _safe_float(props.get("precipitationLastHour"))

    # Text description
    text_description = props.get("textDescription")
    if isinstance(text_description, str):
        text_description = text_description.strip() or None
    else:
        text_description = None

    return ObservationResult(
        station_name=station_name,
        station_id=station_id,
        observed_at=observed_at,
        temp_f=_c_to_f(temp_c),
        dewpoint_f=_c_to_f(dewpoint_c),
        relative_humidity=humidity,
        wind_direction=_wind_direction_label(wind_direction_deg),
        wind_speed_mph=_kmh_to_mph(wind_speed_kmh),
        wind_gust_mph=_kmh_to_mph(wind_gust_kmh),
        wind_chill_f=_c_to_f(wind_chill_c),
        heat_index_f=_c_to_f(heat_index_c),
        pressure_inhg=_pa_to_inhg(pressure_pa),
        visibility_mi=_m_to_mi(visibility_m),
        text_description=text_description,
        precip_last_hour_in=_mm_to_in(precip_mm),
    )


def _observation_is_acceptable(obs: ObservationResult) -> bool:
    """Check if an observation meets the acceptance criteria.

    Accept if observation is < 90 minutes old and has temp or textDescription.
    """
    if obs.observed_at is None:
        return False

    # Check staleness
    try:
        from datetime import datetime, timezone
        observed_dt = datetime.fromisoformat(obs.observed_at.replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - observed_dt).total_seconds()
        if age_seconds > OBSERVATION_STALENESS_THRESHOLD_SECONDS:
            return False
    except (ValueError, TypeError):
        return False

    # Check data quality — at least one of temp or description must be present
    return obs.temp_f is not None or obs.text_description is not None


def _pick_best_observation(candidates: list[ObservationResult]) -> ObservationResult:
    """Pick the best observation from a list of candidates.

    Prefers: most recent timestamp, non-null temperature.
    """
    if len(candidates) == 1:
        return candidates[0]

    def score(obs: ObservationResult) -> tuple[int, str]:
        has_temp = 1 if obs.temp_f is not None else 0
        timestamp = obs.observed_at or ""
        return (has_temp, timestamp)

    best = max(candidates, key=score)
    best.degraded = True
    return best


async def get_observation_with_fallback(
    client: httpx.AsyncClient,
    station_list: list[str],
) -> ObservationResult:
    """Fetch the latest observation, trying up to MAX_STATION_ATTEMPTS stations.

    Implements the station fallback rules from the plan.
    """
    candidates: list[ObservationResult] = []

    for i, station_id in enumerate(station_list[:MAX_STATION_ATTEMPTS]):
        url = f"{NWS_API_BASE}/stations/{station_id}/observations/latest"
        try:
            data = await _nws_request(client, url)
        except NwsServiceError:
            logger.warning("NWS observation fetch failed for station %s", station_id)
            continue

        obs = _parse_observation(data, station_id)
        obs.stations_attempted = i + 1
        candidates.append(obs)

        if _observation_is_acceptable(obs):
            obs.fallback_used = i > 0
            return obs

    if not candidates:
        # All stations failed — return an empty-ish observation
        return ObservationResult(
            station_name=None,
            station_id=None,
            observed_at=None,
            temp_f=None,
            dewpoint_f=None,
            relative_humidity=None,
            wind_direction=None,
            wind_speed_mph=None,
            wind_gust_mph=None,
            wind_chill_f=None,
            heat_index_f=None,
            pressure_inhg=None,
            visibility_mi=None,
            text_description=None,
            precip_last_hour_in=None,
            degraded=True,
            stations_attempted=min(len(station_list), MAX_STATION_ATTEMPTS),
            fallback_used=True,
        )

    best = _pick_best_observation(candidates)
    best.fallback_used = True
    best.stations_attempted = len(candidates)
    return best


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------

def _parse_forecast(raw: dict[str, Any]) -> ForecastResult:
    """Parse a raw NWS forecast response into a ForecastResult."""
    props = raw.get("properties", {})
    generated_at = props.get("generatedAt")

    periods: list[ForecastPeriod] = []
    for p in props.get("periods", []):
        # Extract precipitation probability
        precip_prob = None
        prob_of_precip = p.get("probabilityOfPrecipitation")
        if isinstance(prob_of_precip, dict):
            val = prob_of_precip.get("value")
            if val is not None:
                try:
                    precip_prob = int(val)
                except (TypeError, ValueError):
                    pass

        periods.append(ForecastPeriod(
            number=p.get("number", 0),
            name=p.get("name", ""),
            is_daytime=p.get("isDaytime", True),
            temp_f=p.get("temperature"),
            wind_speed=p.get("windSpeed"),
            wind_direction=p.get("windDirection"),
            short_forecast=p.get("shortForecast"),
            detailed_forecast=p.get("detailedForecast"),
            precip_probability=precip_prob,
        ))

    return ForecastResult(generated_at=generated_at, periods=periods)


async def _fetch_forecast(
    client: httpx.AsyncClient,
    wfo: str,
    grid_x: int,
    grid_y: int,
) -> ForecastResult:
    """Fetch the 7-day forecast for a grid point."""
    url = f"{NWS_API_BASE}/gridpoints/{wfo}/{grid_x},{grid_y}/forecast"
    data = await _nws_request(client, url)
    return _parse_forecast(data)


# ---------------------------------------------------------------------------
# AFD (Area Forecast Discussion)
# ---------------------------------------------------------------------------

async def _fetch_latest_afd(
    client: httpx.AsyncClient,
    wfo: str,
) -> AfdResult | None:
    """Fetch the latest Area Forecast Discussion for a WFO."""
    # Step 1: Get the list of AFD products for this WFO
    list_url = f"{NWS_API_BASE}/products/types/AFD/locations/{wfo}"
    data = await _nws_request(client, list_url)

    graph = data.get("@graph", [])
    if not graph:
        return None

    # Take the first (most recent) product
    latest = graph[0]
    product_id = latest.get("id")
    if not product_id:
        return None

    # Step 2: Fetch the full product
    product_url = f"{NWS_API_BASE}/products/{product_id}"
    try:
        product_data = await _nws_request(client, product_url)
    except NwsUpstreamError as exc:
        if exc.upstream_status == 404:
            logger.warning("NWS AFD product %s not found (404)", product_id)
            return None
        raise

    product_text = product_data.get("productText")
    issued_at = product_data.get("issuanceTime")
    issuing_office = product_data.get("issuingOffice")

    return AfdResult(
        wfo=wfo,
        office_name=issuing_office,
        issued_at=issued_at,
        product_text=product_text,
        product_id=product_id,
    )


# ---------------------------------------------------------------------------
# Public API: get_weather_bundle
# ---------------------------------------------------------------------------

async def get_weather_bundle(anchor_id: str) -> WeatherBundle:
    """Fetch current observations and 7-day forecast for an anchor city.

    This is the main entry point for the /api/v4/anchors/{anchor_id}/weather endpoint.
    Uses caching for observations (3 min) and forecast (15 min).
    """
    anchor = get_anchor(anchor_id)

    # Check observation and forecast caches
    obs_cache_key = f"obs:{anchor_id}"
    fcast_cache_key = f"fcast:{anchor_id}"

    cached_obs: ObservationResult | None = _observation_cache.get(obs_cache_key)
    cached_fcast: ForecastResult | None = _forecast_cache.get(fcast_cache_key)

    if cached_obs is not None and cached_fcast is not None:
        return WeatherBundle(
            anchor=anchor,
            observation=cached_obs,
            forecast=cached_fcast,
            resolved_from_cache=True,
        )

    async with _build_client() as client:
        # Resolve point metadata
        meta, points_from_cache = await resolve_point_metadata(client, anchor)

        observation = cached_obs
        forecast = cached_fcast

        # Fetch observations if not cached
        if observation is None:
            try:
                stations = await get_station_list(client, meta.wfo, meta.grid_x, meta.grid_y)
                if stations:
                    observation = await get_observation_with_fallback(client, stations)
                    _observation_cache.set(obs_cache_key, observation, OBSERVATION_CACHE_TTL)
                else:
                    logger.warning("No stations found for %s (wfo=%s, grid=%d,%d)",
                                   anchor_id, meta.wfo, meta.grid_x, meta.grid_y)
            except NwsServiceError as exc:
                # On grid 404, try refreshing /points metadata
                if isinstance(exc, NwsUpstreamError) and exc.upstream_status == 404 and not points_from_cache:
                    logger.info("Grid 404 for %s; forcing /points refresh", anchor_id)
                    try:
                        meta, _ = await resolve_point_metadata(client, anchor, force_refresh=True)
                        stations = await get_station_list(client, meta.wfo, meta.grid_x, meta.grid_y)
                        if stations:
                            observation = await get_observation_with_fallback(client, stations)
                            _observation_cache.set(obs_cache_key, observation, OBSERVATION_CACHE_TTL)
                    except NwsServiceError:
                        logger.warning("NWS observation fetch failed after /points refresh for %s", anchor_id)
                else:
                    logger.warning("NWS observation fetch failed for %s: %s", anchor_id, exc)

        # Fetch forecast if not cached
        if forecast is None:
            try:
                forecast = await _fetch_forecast(client, meta.wfo, meta.grid_x, meta.grid_y)
                _forecast_cache.set(fcast_cache_key, forecast, FORECAST_CACHE_TTL)
            except NwsServiceError as exc:
                # On grid 404, try refreshing /points metadata (if not already done)
                if isinstance(exc, NwsUpstreamError) and exc.upstream_status == 404:
                    logger.info("Forecast grid 404 for %s; forcing /points refresh", anchor_id)
                    try:
                        meta, _ = await resolve_point_metadata(client, anchor, force_refresh=True)
                        forecast = await _fetch_forecast(client, meta.wfo, meta.grid_x, meta.grid_y)
                        _forecast_cache.set(fcast_cache_key, forecast, FORECAST_CACHE_TTL)
                    except NwsServiceError:
                        logger.warning("NWS forecast fetch failed after /points refresh for %s", anchor_id)
                else:
                    logger.warning("NWS forecast fetch failed for %s: %s", anchor_id, exc)

    return WeatherBundle(
        anchor=anchor,
        observation=observation,
        forecast=forecast,
        resolved_from_cache=points_from_cache,
    )


# ---------------------------------------------------------------------------
# Public API: get_afd
# ---------------------------------------------------------------------------

async def get_afd(anchor_id: str) -> AfdResult | None:
    """Fetch the latest Area Forecast Discussion for an anchor city.

    This is the main entry point for the /api/v4/anchors/{anchor_id}/afd endpoint.
    Uses caching with 30 min TTL.
    """
    anchor = get_anchor(anchor_id)

    # Check AFD cache
    afd_cache_key = f"afd:{anchor_id}"
    cached_afd: AfdResult | None = _afd_cache.get(afd_cache_key)
    if cached_afd is not None:
        return cached_afd

    async with _build_client() as client:
        # Resolve point metadata to get WFO
        meta, _ = await resolve_point_metadata(client, anchor)

        afd = await _fetch_latest_afd(client, meta.wfo)
        if afd is not None:
            _afd_cache.set(afd_cache_key, afd, AFD_CACHE_TTL)

    return afd


# ---------------------------------------------------------------------------
# Serialization helpers (for JSON responses)
# ---------------------------------------------------------------------------

def serialize_observation(obs: ObservationResult) -> dict[str, Any]:
    """Serialize an ObservationResult to the JSON response shape."""
    return {
        "stationName": obs.station_name,
        "stationId": obs.station_id,
        "observedAt": obs.observed_at,
        "tempF": obs.temp_f,
        "dewpointF": obs.dewpoint_f,
        "relativeHumidity": obs.relative_humidity,
        "windDirection": obs.wind_direction,
        "windSpeedMph": obs.wind_speed_mph,
        "windGustMph": obs.wind_gust_mph,
        "windChillF": obs.wind_chill_f,
        "heatIndexF": obs.heat_index_f,
        "pressureInHg": obs.pressure_inhg,
        "visibilityMi": obs.visibility_mi,
        "textDescription": obs.text_description,
        "precipLastHourIn": obs.precip_last_hour_in,
    }


def serialize_forecast(fcast: ForecastResult) -> dict[str, Any]:
    """Serialize a ForecastResult to the JSON response shape."""
    return {
        "generatedAt": fcast.generated_at,
        "periods": [
            {
                "number": p.number,
                "name": p.name,
                "isDaytime": p.is_daytime,
                "tempF": p.temp_f,
                "windSpeed": p.wind_speed,
                "windDirection": p.wind_direction,
                "shortForecast": p.short_forecast,
                "detailedForecast": p.detailed_forecast,
                "precipProbability": p.precip_probability,
            }
            for p in fcast.periods
        ],
    }


def serialize_weather_bundle(bundle: WeatherBundle) -> dict[str, Any]:
    """Serialize a WeatherBundle to the full JSON response shape."""
    result: dict[str, Any] = {
        "city": bundle.anchor.city,
        "state": bundle.anchor.state,
        "st": bundle.anchor.st,
        "observation": serialize_observation(bundle.observation) if bundle.observation else None,
        "forecast": serialize_forecast(bundle.forecast) if bundle.forecast else None,
        "meta": {
            "anchorId": bundle.anchor.anchor_id,
            "resolvedFromCache": bundle.resolved_from_cache,
            "observationDegraded": bundle.observation.degraded if bundle.observation else None,
            "observationStationFallbackUsed": bundle.observation.fallback_used if bundle.observation else None,
            "stationsAttempted": bundle.observation.stations_attempted if bundle.observation else 0,
        },
    }
    return result


def serialize_afd(afd: AfdResult, anchor_id: str) -> dict[str, Any]:
    """Serialize an AfdResult to the JSON response shape."""
    return {
        "wfo": afd.wfo,
        "officeName": afd.office_name,
        "issuedAt": afd.issued_at,
        "productText": afd.product_text,
        "meta": {
            "anchorId": anchor_id,
            "productId": afd.product_id,
        },
    }
