import os
import sys
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("TWF_BASE", "https://example.com")
os.environ.setdefault("TWF_CLIENT_ID", "client-id")
os.environ.setdefault("TWF_CLIENT_SECRET", "client-secret")
os.environ.setdefault("TWF_REDIRECT_URI", "https://example.com/callback")
os.environ.setdefault("FRONTEND_RETURN", "https://example.com/app")
os.environ.setdefault("TOKEN_DB_PATH", "/tmp/twf_test_tokens.sqlite3")
os.environ.setdefault("TOKEN_ENC_KEY", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

from app import main as main_module
from app.services import forecast_page as forecast_page_service
from app.services import nws as nws_service

pytestmark = pytest.mark.anyio


def _freeze_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    frozen = datetime(2026, 4, 18, 17, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(forecast_page_service, "_utcnow", lambda: frozen)
    return frozen


def _mock_async_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    def build_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=10.0)

    monkeypatch.setattr(forecast_page_service, "_build_client", build_client)


def _open_meteo_payload(*, timezone_name: str) -> dict:
    return {
        "latitude": 43.55,
        "longitude": -96.73,
        "elevation": 438.0,
        "timezone": timezone_name,
        "current_units": {
            "visibility": "m",
        },
        "current": {
            "time": "2026-04-18T12:15",
            "temperature_2m": 58,
            "dew_point_2m": 41,
            "relative_humidity_2m": 53,
            "wind_speed_10m": 13,
            "wind_gusts_10m": 21,
            "wind_direction_10m": 305,
            "pressure_msl": 1014.2,
            "visibility": 16093.0,
            "weather_code": 2,
            "is_day": 1,
        },
        "hourly_units": {
            "precipitation": "inch",
            "snowfall": "inch",
        },
        "hourly": {
            "time": ["2026-04-18T13:00", "2026-04-18T14:00"],
            "temperature_2m": [60, 62],
            "dew_point_2m": [40, 41],
            "wind_speed_10m": [16, 18],
            "wind_gusts_10m": [24, 26],
            "wind_direction_10m": [315, 320],
            "precipitation_probability": [5, 10],
            "precipitation": [0.0, 0.01],
            "snowfall": [0.0, 0.0],
            "weather_code": [1, 2],
            "is_day": [1, 0],
        },
        "daily_units": {
            "precipitation_sum": "inch",
            "snowfall_sum": "inch",
        },
        "daily": {
            "time": ["2026-04-18", "2026-04-19"],
            "weather_code": [2, 61],
            "temperature_2m_max": [64, 61],
            "temperature_2m_min": [42, 44],
            "precipitation_probability_max": [15, 55],
            "precipitation_sum": [0.02, 0.18],
            "snowfall_sum": [0.0, 0.0],
            "wind_speed_10m_max": [19, 28],
            "wind_gusts_10m_max": [28, 35],
            "sunrise": ["2026-04-18T06:41", "2026-04-19T06:39"],
            "sunset": ["2026-04-18T20:11", "2026-04-19T20:12"],
        },
    }


def _open_meteo_air_quality_payload(*, timezone_name: str) -> dict:
    return {
        "latitude": 43.55,
        "longitude": -96.73,
        "timezone": timezone_name,
        "current_units": {
            "pm2_5": "μg/m³",
            "pm10": "μg/m³",
            "ozone": "μg/m³",
            "nitrogen_dioxide": "μg/m³",
        },
        "current": {
            "time": "2026-04-18T12:00",
            "us_aqi": 42,
            "us_aqi_pm2_5": 42,
            "us_aqi_pm10": 19,
            "us_aqi_ozone": 14,
            "us_aqi_nitrogen_dioxide": 8,
            "pm2_5": 11.2,
            "pm10": 18.7,
            "ozone": 31.4,
            "nitrogen_dioxide": 7.8,
        },
    }


def _google_pollen_payload() -> dict:
    return {
        "regionCode": "US",
        "dailyInfo": [
            {
                "date": {"year": 2026, "month": 4, "day": 18},
                "pollenTypeInfo": [
                    {
                        "code": "TREE",
                        "displayName": "Tree",
                        "inSeason": True,
                        "indexInfo": {
                            "code": "UPI",
                            "displayName": "Universal Pollen Index",
                            "value": 4,
                            "category": "High",
                            "color": {"red": 1.0, "green": 0.72, "blue": 0.1},
                        },
                        "healthRecommendations": ["High tree pollen may trigger symptoms."],
                    },
                    {
                        "code": "GRASS",
                        "displayName": "Grass",
                        "inSeason": True,
                        "indexInfo": {
                            "code": "UPI",
                            "displayName": "Universal Pollen Index",
                            "value": 3,
                            "category": "Moderate",
                            "color": {"red": 1.0, "green": 0.88, "blue": 0.1},
                        },
                    },
                    {
                        "code": "WEED",
                        "displayName": "Weed",
                        "inSeason": False,
                        "indexInfo": {
                            "code": "UPI",
                            "displayName": "Universal Pollen Index",
                            "value": 1,
                            "category": "Very Low",
                            "color": {"green": 0.62, "blue": 0.22},
                        },
                    },
                ],
                "plantInfo": [
                    {
                        "code": "OAK",
                        "displayName": "Oak",
                        "inSeason": True,
                        "indexInfo": {
                            "code": "UPI",
                            "displayName": "Universal Pollen Index",
                            "value": 4,
                            "category": "High",
                        },
                    },
                    {
                        "code": "GRAMINALES",
                        "displayName": "Grasses",
                        "inSeason": True,
                        "indexInfo": {
                            "code": "UPI",
                            "displayName": "Universal Pollen Index",
                            "value": 3,
                            "category": "Moderate",
                        },
                    },
                    {
                        "code": "RAGWEED",
                        "displayName": "Ragweed",
                        "inSeason": False,
                    },
                ],
            }
        ],
    }


def _google_pollen_empty_payload() -> dict:
    return {
        "regionCode": "US",
        "dailyInfo": [
            {
                "date": {"year": 2026, "month": 4, "day": 18},
                "pollenTypeInfo": [],
                "plantInfo": [],
            }
        ],
    }


def _nws_points_payload() -> dict:
    return {
        "properties": {
            "cwa": "FSD",
            "gridId": "FSD",
            "gridX": 97,
            "gridY": 70,
            "forecast": "https://api.weather.gov/gridpoints/FSD/97,70/forecast",
            "forecastHourly": "https://api.weather.gov/gridpoints/FSD/97,70/forecast/hourly",
            "forecastZone": "https://api.weather.gov/zones/forecast/SDZ050",
            "county": "https://api.weather.gov/zones/county/SDC099",
            "fireWeatherZone": "https://api.weather.gov/zones/fire/SDZ050",
            "observationStations": "https://api.weather.gov/gridpoints/FSD/97,70/stations",
        }
    }


def _nws_forecast_payload() -> dict:
    return {
        "properties": {
            "generatedAt": "2026-04-18T16:00:00+00:00",
            "periods": [
                {
                    "name": "Tonight",
                    "startTime": "2026-04-18T18:00:00-05:00",
                    "endTime": "2026-04-19T06:00:00-05:00",
                    "isDaytime": False,
                    "temperature": 44,
                    "windSpeed": "NW 10 to 15 mph",
                    "windDirection": "NW",
                    "icon": "https://api.weather.gov/icons/land/night/few?size=medium",
                    "shortForecast": "Mostly Clear",
                    "detailedForecast": "Mostly clear, with a low around 44.",
                }
            ],
        }
    }


def _nws_hourly_payload() -> dict:
    return {
        "properties": {
            "generatedAt": "2026-04-18T16:00:00+00:00",
            "periods": [
                {
                    "startTime": "2026-04-18T13:00:00-05:00",
                    "isDaytime": True,
                    "temperature": 60,
                    "windSpeed": "16 mph",
                    "windDirection": "NW",
                    "shortForecast": "Mostly Sunny",
                    "probabilityOfPrecipitation": {"value": 5},
                },
                {
                    "startTime": "2026-04-18T14:00:00-05:00",
                    "isDaytime": False,
                    "temperature": 62,
                    "windSpeed": "18 mph",
                    "windDirection": "NW",
                    "shortForecast": "Rain Showers",
                    "probabilityOfPrecipitation": {"value": 10},
                },
            ],
        }
    }


def _nws_station_collection() -> dict:
    return {
        "features": [
            {
                "id": "https://api.weather.gov/stations/KFSD",
                "geometry": {"coordinates": [-96.741, 43.582]},
                "properties": {
                    "stationIdentifier": "KFSD",
                    "name": "Sioux Falls Regional Airport",
                    "elevation": {"value": 435.0},
                    "stationType": "ASOS",
                },
            },
            {
                "id": "https://api.weather.gov/stations/K9V9",
                "geometry": {"coordinates": [-96.728, 43.545]},
                "properties": {
                    "stationIdentifier": "K9V9",
                    "name": "Tea Municipal",
                    "elevation": {"value": 437.0},
                    "stationType": "AWOS",
                },
            },
        ]
    }


def _nws_observation(*, timestamp: str, description: str, wind_speed_kmh: float = 22.5) -> dict:
    return {
        "properties": {
            "timestamp": timestamp,
            "temperature": {"value": 14.4},
            "dewpoint": {"value": 5.0},
            "relativeHumidity": {"value": 53.0},
            "windSpeed": {"value": wind_speed_kmh},
            "windGust": {"value": 35.4},
            "windDirection": {"value": 310.0},
            "barometricPressure": {"value": 101420.0},
            "visibility": {"value": 16093.0},
            "textDescription": description,
        }
    }


@pytest.fixture(autouse=True)
def isolate_forecast_page(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    forecast_page_service.configure_data_root(data_root)
    nws_service.configure_data_root(data_root)
    forecast_page_service.clear_all_caches()
    nws_service.clear_all_caches()


async def test_get_forecast_page_by_query_builds_us_hybrid_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_now(monkeypatch)
    monkeypatch.setenv("CARTOSKY_GOOGLE_POLLEN_API_KEY", "test-google-pollen-key")

    async def fake_fetch_observed_precip_mrms(lat: float, lon: float) -> dict[str, float | None] | None:
        assert (lat, lon) == (43.55, -96.73)
        return {
            "last_6h_in": 0.31,
            "last_24h_in": 0.87,
            "last_72h_in": 1.62,
        }

    async def fake_fetch_acis_precip_summary(lat: float, lon: float) -> dict[str, object] | None:
        assert (lat, lon) == (43.55, -96.73)
        return {
            "ytd": {
                "actual_in": 8.42,
                "normal_in": 9.25,
                "percent_of_normal": 91,
                "departure_in": -0.83,
                "station_name": "Sioux Falls Foss Field",
            },
            "days_since_rain": {
                "days": 14,
                "at_cap": True,
                "station_name": "Sioux Falls Foss Field",
            },
        }

    monkeypatch.setattr(forecast_page_service, "_fetch_observed_precip_mrms", fake_fetch_observed_precip_mrms)
    monkeypatch.setattr(forecast_page_service, "_fetch_acis_precip_summary", fake_fetch_acis_precip_summary)

    async def fake_get_afd_by_office(office: str) -> nws_service.AfdResult:
        return nws_service.AfdResult(
            wfo=office,
            office_name="FSD",
            issued_at="2026-04-18T16:42:00-05:00",
            product_text="Area forecast discussion text.",
            product_id="AFDFSD",
        )

    monkeypatch.setattr(forecast_page_service.nws_service, "get_afd_by_office", fake_get_afd_by_office)

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "geocoding-api.open-meteo.com" and path == "/v1/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "Sioux Falls",
                            "latitude": 43.55,
                            "longitude": -96.73,
                            "elevation": 438.0,
                            "timezone": "America/Chicago",
                            "country_code": "US",
                            "country": "United States",
                            "admin1": "South Dakota",
                            "postcodes": ["57104"],
                            "population": 202078,
                            "feature_code": "PPL",
                        }
                    ]
                },
            )
        if host == "api.open-meteo.com" and path == "/v1/forecast":
            return httpx.Response(200, json=_open_meteo_payload(timezone_name="America/Chicago"))
        if host == "air-quality-api.open-meteo.com" and path == "/v1/air-quality":
            return httpx.Response(200, json=_open_meteo_air_quality_payload(timezone_name="America/Chicago"))
        if host == "pollen.googleapis.com" and path == "/v1/forecast:lookup":
            return httpx.Response(200, json=_google_pollen_payload())
        if host == "api.weather.gov" and path == "/points/43.5500,-96.7300":
            return httpx.Response(200, json=_nws_points_payload())
        if host == "api.weather.gov" and path == "/gridpoints/FSD/97,70/forecast":
            return httpx.Response(200, json=_nws_forecast_payload())
        if host == "api.weather.gov" and path == "/gridpoints/FSD/97,70/forecast/hourly":
            return httpx.Response(200, json=_nws_hourly_payload())
        if host == "api.weather.gov" and path == "/gridpoints/FSD/97,70/stations":
            return httpx.Response(200, json=_nws_station_collection())
        if host == "api.weather.gov" and path == "/stations/KFSD/observations/latest":
            return httpx.Response(200, json=_nws_observation(timestamp="2026-04-18T14:00:00+00:00", description="Mostly Cloudy"))
        if host == "api.weather.gov" and path == "/stations/K9V9/observations/latest":
            return httpx.Response(200, json=_nws_observation(timestamp="2026-04-18T16:15:00+00:00", description="Partly Cloudy"))
        if host == "api.weather.gov" and path == "/alerts/active":
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "id": "urn:oid:alert-1",
                            "properties": {
                                "event": "Wind Advisory",
                                "severity": "Moderate",
                                "urgency": "Expected",
                                "certainty": "Likely",
                                "effective": "2026-04-18T13:00:00-05:00",
                                "expires": "2026-04-18T22:00:00-05:00",
                                "headline": "Wind Advisory in effect until 10 PM CDT",
                                "areaDesc": "Minnehaha; Lincoln",
                                "description": "Strong winds expected.",
                                "instruction": "Use caution while driving.",
                            },
                        }
                    ]
                },
            )
        raise AssertionError(f"Unhandled request: {request.method} {request.url}")

    _mock_async_client(monkeypatch, handler)

    payload = await forecast_page_service.get_forecast_page_by_query("57104")

    assert payload["location"]["display_name"] == "Sioux Falls, SD"
    assert payload["source_status"]["primary_region_mode"] == "us_hybrid"
    assert payload["source_status"]["nws"] == "ok"
    assert payload["current"]["source"] == "nws"
    assert payload["current"]["station"]["id"] == "K9V9"
    assert payload["hourly"][0]["source"] == "nws"
    assert payload["hourly"][1]["weather_code"] == "rain-night"
    assert payload["daily"][0]["source"] == "open_meteo"
    assert payload["air_quality"]["us_aqi"] == 42
    assert payload["air_quality"]["driver"]["code"] == "pm2_5"
    assert payload["pollen"]["index"] == 4
    assert payload["pollen"]["types"][0]["code"] == "TREE"
    assert payload["observed_precip"] == {
        "last_6h_in": 0.31,
        "last_24h_in": 0.87,
        "last_72h_in": 1.62,
        "ytd": {
            "actual_in": 8.42,
            "normal_in": 9.25,
            "percent_of_normal": 91,
            "departure_in": -0.83,
            "station_name": "Sioux Falls Foss Field",
        },
        "days_since_rain": {
            "days": 14,
            "at_cap": True,
            "station_name": "Sioux Falls Foss Field",
        },
    }
    assert payload["official_text_forecast"]["periods"][0]["name"] == "Tonight"
    assert payload["afd"]["product_id"] == "AFDFSD"
    assert payload["alerts"][0]["event"] == "Wind Advisory"
    assert payload["attribution"] == {
        "current": "NWS",
        "hourly": "NWS",
        "daily": "Open-Meteo",
        "air_quality": "Open-Meteo",
        "pollen": "Google Pollen API",
        "observed_precip": "MRMS · ACIS",
        "afd": "NWS",
        "alerts": "NWS",
    }


async def test_get_forecast_page_by_query_falls_back_to_open_meteo_current_when_nws_obs_are_poor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_now(monkeypatch)

    async def fake_get_afd_by_office(office: str) -> nws_service.AfdResult | None:
        return None

    monkeypatch.setattr(forecast_page_service.nws_service, "get_afd_by_office", fake_get_afd_by_office)

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "geocoding-api.open-meteo.com" and path == "/v1/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "Sioux Falls",
                            "latitude": 43.55,
                            "longitude": -96.73,
                            "elevation": 438.0,
                            "timezone": "America/Chicago",
                            "country_code": "US",
                            "country": "United States",
                            "admin1": "South Dakota",
                            "postcodes": ["57104"],
                            "population": 202078,
                            "feature_code": "PPL",
                        }
                    ]
                },
            )
        if host == "api.open-meteo.com" and path == "/v1/forecast":
            return httpx.Response(200, json=_open_meteo_payload(timezone_name="America/Chicago"))
        if host == "air-quality-api.open-meteo.com" and path == "/v1/air-quality":
            return httpx.Response(200, json=_open_meteo_air_quality_payload(timezone_name="America/Chicago"))
        if host == "api.weather.gov" and path == "/points/43.5500,-96.7300":
            return httpx.Response(200, json=_nws_points_payload())
        if host == "api.weather.gov" and path == "/gridpoints/FSD/97,70/forecast":
            return httpx.Response(200, json=_nws_forecast_payload())
        if host == "api.weather.gov" and path == "/gridpoints/FSD/97,70/forecast/hourly":
            return httpx.Response(200, json=_nws_hourly_payload())
        if host == "api.weather.gov" and path == "/gridpoints/FSD/97,70/stations":
            return httpx.Response(200, json=_nws_station_collection())
        if host == "api.weather.gov" and path.startswith("/stations/"):
            return httpx.Response(
                200,
                json={
                    "properties": {
                        "timestamp": "2026-04-18T12:00:00+00:00",
                        "temperature": {"value": None},
                        "dewpoint": {"value": None},
                        "relativeHumidity": {"value": None},
                        "windSpeed": {"value": None},
                        "windGust": {"value": None},
                        "windDirection": {"value": None},
                        "barometricPressure": {"value": None},
                        "visibility": {"value": None},
                        "textDescription": None,
                    }
                },
            )
        if host == "api.weather.gov" and path == "/alerts/active":
            return httpx.Response(200, json={"features": []})
        raise AssertionError(f"Unhandled request: {request.method} {request.url}")

    _mock_async_client(monkeypatch, handler)

    payload = await forecast_page_service.get_forecast_page_by_query("57104")

    assert payload["current"]["source"] == "open_meteo"
    assert payload["current"]["quality"]["is_fallback"] is True
    assert payload["source_status"]["nws"] == "degraded"
    assert payload["current"]["short_text"] == "Partly Cloudy"


async def test_get_forecast_page_by_query_uses_night_icon_for_nws_current(monkeypatch: pytest.MonkeyPatch) -> None:
    frozen = datetime(2026, 4, 19, 3, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(forecast_page_service, "_utcnow", lambda: frozen)

    async def fake_get_afd_by_office(office: str) -> nws_service.AfdResult:
        return nws_service.AfdResult(
            wfo=office,
            office_name="FSD",
            issued_at="2026-04-18T16:42:00-05:00",
            product_text="Area forecast discussion text.",
            product_id="AFDFSD",
        )

    monkeypatch.setattr(forecast_page_service.nws_service, "get_afd_by_office", fake_get_afd_by_office)

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "geocoding-api.open-meteo.com" and path == "/v1/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "Sioux Falls",
                            "latitude": 43.55,
                            "longitude": -96.73,
                            "elevation": 438.0,
                            "timezone": "America/Chicago",
                            "country_code": "US",
                            "country": "United States",
                            "admin1": "South Dakota",
                            "postcodes": ["57104"],
                            "population": 202078,
                            "feature_code": "PPL",
                        }
                    ]
                },
            )
        if host == "api.open-meteo.com" and path == "/v1/forecast":
            return httpx.Response(200, json=_open_meteo_payload(timezone_name="America/Chicago"))
        if host == "air-quality-api.open-meteo.com" and path == "/v1/air-quality":
            return httpx.Response(200, json=_open_meteo_air_quality_payload(timezone_name="America/Chicago"))
        if host == "api.weather.gov" and path == "/points/43.5500,-96.7300":
            return httpx.Response(200, json=_nws_points_payload())
        if host == "api.weather.gov" and path == "/gridpoints/FSD/97,70/forecast":
            return httpx.Response(200, json=_nws_forecast_payload())
        if host == "api.weather.gov" and path == "/gridpoints/FSD/97,70/forecast/hourly":
            return httpx.Response(200, json=_nws_hourly_payload())
        if host == "api.weather.gov" and path == "/gridpoints/FSD/97,70/stations":
            return httpx.Response(200, json=_nws_station_collection())
        if host == "api.weather.gov" and path == "/stations/KFSD/observations/latest":
            return httpx.Response(200, json=_nws_observation(timestamp="2026-04-19T00:30:00+00:00", description="Mostly Cloudy"))
        if host == "api.weather.gov" and path == "/stations/K9V9/observations/latest":
            return httpx.Response(200, json=_nws_observation(timestamp="2026-04-19T02:15:00+00:00", description="Partly Cloudy"))
        if host == "api.weather.gov" and path == "/alerts/active":
            return httpx.Response(200, json={"features": []})
        raise AssertionError(f"Unhandled request: {request.method} {request.url}")

    _mock_async_client(monkeypatch, handler)

    payload = await forecast_page_service.get_forecast_page_by_query("57104")

    assert payload["current"]["source"] == "nws"
    assert payload["current"]["icon"] == "partly-cloudy-night"


async def test_degraded_us_hybrid_payload_does_not_poison_forecast_page_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_now(monkeypatch)
    points_calls = 0

    async def fake_get_afd_by_office(office: str) -> nws_service.AfdResult:
        return nws_service.AfdResult(
            wfo=office,
            office_name="FSD",
            issued_at="2026-04-18T16:42:00-05:00",
            product_text="Area forecast discussion text.",
            product_id="AFDFSD",
        )

    monkeypatch.setattr(forecast_page_service.nws_service, "get_afd_by_office", fake_get_afd_by_office)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal points_calls
        host = request.url.host
        path = request.url.path
        if host == "api.open-meteo.com" and path == "/v1/forecast":
            return httpx.Response(200, json=_open_meteo_payload(timezone_name="America/Chicago"))
        if host == "air-quality-api.open-meteo.com" and path == "/v1/air-quality":
            return httpx.Response(200, json=_open_meteo_air_quality_payload(timezone_name="America/Chicago"))
        if host == "api.weather.gov" and path == "/points/43.5500,-96.7300":
            points_calls += 1
            if points_calls == 1:
                return httpx.Response(404, json={"title": "temporary points miss"})
            return httpx.Response(200, json=_nws_points_payload())
        if host == "api.weather.gov" and path == "/gridpoints/FSD/97,70/forecast":
            return httpx.Response(200, json=_nws_forecast_payload())
        if host == "api.weather.gov" and path == "/gridpoints/FSD/97,70/forecast/hourly":
            return httpx.Response(200, json=_nws_hourly_payload())
        if host == "api.weather.gov" and path == "/gridpoints/FSD/97,70/stations":
            return httpx.Response(200, json=_nws_station_collection())
        if host == "api.weather.gov" and path == "/stations/KFSD/observations/latest":
            return httpx.Response(200, json=_nws_observation(timestamp="2026-04-18T16:15:00+00:00", description="Partly Cloudy"))
        if host == "api.weather.gov" and path == "/stations/K9V9/observations/latest":
            return httpx.Response(200, json=_nws_observation(timestamp="2026-04-18T16:20:00+00:00", description="Mostly Sunny"))
        if host == "api.weather.gov" and path == "/alerts/active":
            return httpx.Response(200, json={"features": []})
        raise AssertionError(f"Unhandled request: {request.method} {request.url}")

    _mock_async_client(monkeypatch, handler)

    location_hint = forecast_page_service.LocationHint(
        display_name="Sioux Falls, SD",
        timezone="America/Chicago",
        country_code="US",
        admin1="South Dakota",
        country="United States",
    )

    first_payload = await forecast_page_service.get_forecast_page(43.55, -96.73, location_hint=location_hint)
    second_payload = await forecast_page_service.get_forecast_page(43.55, -96.73, location_hint=location_hint)

    assert first_payload["source_status"]["primary_region_mode"] == "us_hybrid"
    assert first_payload["source_status"]["nws"] == "unavailable"
    assert first_payload["current"]["source"] == "open_meteo"
    assert second_payload["source_status"]["nws"] == "ok"
    assert second_payload["current"]["source"] == "nws"
    assert second_payload["hourly"][0]["source"] == "nws"
    assert points_calls == 2


async def test_forecast_page_core_is_open_meteo_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_now(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "api.open-meteo.com" and path == "/v1/forecast":
            return httpx.Response(200, json=_open_meteo_payload(timezone_name="America/Chicago"))
        if host == "air-quality-api.open-meteo.com" and path == "/v1/air-quality":
            return httpx.Response(200, json=_open_meteo_air_quality_payload(timezone_name="America/Chicago"))
        if host == "api.weather.gov":
            raise AssertionError(f"core must not call NWS: {request.url}")
        if host == "pollen.googleapis.com":
            raise AssertionError(f"core must not call Google Pollen: {request.url}")
        raise AssertionError(f"Unhandled request: {request.method} {request.url}")

    _mock_async_client(monkeypatch, handler)

    location_hint = forecast_page_service.LocationHint(
        display_name="Sioux Falls, SD",
        timezone="America/Chicago",
        country_code="US",
        admin1="South Dakota",
        country="United States",
    )
    payload = await forecast_page_service.get_forecast_page_core(43.55, -96.73, location_hint=location_hint)

    assert payload["current"]["source"] == "open_meteo"
    assert payload["hourly"]
    assert payload["daily"]
    assert payload["air_quality"]["us_aqi"] == 42
    assert payload["pollen"] is None
    assert payload["observed_precip"] is None
    assert payload["official_text_forecast"] is None
    assert payload["afd"] is None
    assert payload["alerts"] == []
    # US location → NWS enrichment available; the client should fetch it next.
    assert payload["source_status"]["nws"] == "pending"


def test_normalize_google_pollen_empty_day_returns_none_category_payload() -> None:
    normalized = forecast_page_service._normalize_google_pollen(_google_pollen_empty_payload())

    assert normalized is not None
    assert normalized["index"] == 0
    assert normalized["category"] == "None"
    assert normalized["types"] == []


def test_summarize_acis_precip_summary_builds_ytd_and_days_since_rain() -> None:
    summary = forecast_page_service._summarize_acis_precip_summary(
        station_name="Sioux Falls Foss Field",
        rows=[
            ["2026-01-01", "0.10", "0.02"],
            ["2026-01-02", "0.25", "0.03"],
            ["2026-01-03", "T", "0.02"],
            ["2026-01-04", "M", "0.01"],
            ["2026-01-05", "0.00", "0.04"],
            ["2026-01-06", "0.00", "0.04"],
            ["2026-01-07", "0.12", "0.01"],
            ["2026-01-08", "0.00", "0.01"],
            ["2026-01-09", "0.05", "0.01"],
        ],
    )

    assert summary == {
        "ytd": {
            "actual_in": 0.52,
            "normal_in": 0.19,
            "percent_of_normal": 274,
            "departure_in": 0.33,
            "station_name": "Sioux Falls Foss Field",
        },
        "days_since_rain": {
            "days": 2,
            "at_cap": False,
            "station_name": "Sioux Falls Foss Field",
        },
    }


def test_summarize_acis_precip_summary_caps_days_since_rain_when_series_never_finds_wet_day() -> None:
    summary = forecast_page_service._summarize_acis_precip_summary(
        station_name="Dry Creek",
        rows=[
            ["2026-01-01", "0.00", "0.02"],
            ["2026-01-02", "T", "0.02"],
            ["2026-01-03", "0.10", "0.03"],
        ],
    )

    assert summary == {
        "ytd": {
            "actual_in": 0.1,
            "normal_in": 0.07,
            "percent_of_normal": 143,
            "departure_in": 0.03,
            "station_name": "Dry Creek",
        },
        "days_since_rain": {
            "days": 3,
            "at_cap": True,
            "station_name": "Dry Creek",
        },
    }


async def test_get_forecast_page_refreshes_missing_pollen_from_cached_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_now(monkeypatch)
    monkeypatch.setenv("CARTOSKY_GOOGLE_POLLEN_API_KEY", "test-google-pollen-key")

    location = forecast_page_service.ResolvedLocation(
        query="57104",
        display_name="Sioux Falls, SD",
        latitude=43.55,
        longitude=-96.73,
        timezone="America/Chicago",
        country_code="US",
        admin1="South Dakota",
        country="United States",
        resolved_by="frontend_location_hint",
    )
    cache_key = forecast_page_service._forecast_location_cache_key(location)
    forecast_page_service._cache_set(
        "forecast-page",
        cache_key,
        {
            "location": {
                "query": "57104",
                "display_name": "Sioux Falls, SD",
                "latitude": 43.55,
                "longitude": -96.73,
                "timezone": "America/Chicago",
                "country_code": "US",
                "admin1": "South Dakota",
                "resolved_by": "frontend_location_hint",
            },
            "source_status": {
                "primary_region_mode": "us_hybrid",
                "nws": "ok",
                "open_meteo": "ok",
                "generated_at": "2026-04-18T17:00:00Z",
            },
            "current": {"source": "nws"},
            "hourly": [],
            "daily": [],
            "air_quality": None,
            "pollen": None,
            "observed_precip": None,
            "official_text_forecast": None,
            "afd": None,
            "alerts": [],
            "attribution": {
                "current": "NWS",
                "hourly": "NWS",
                "daily": "Open-Meteo",
                "air_quality": None,
                "pollen": None,
                "observed_precip": None,
                "afd": None,
                "alerts": None,
            },
            "freshness": {
                "current": {"state": "fresh", "observed_at": "2026-04-18T16:15:00+00:00", "age_minutes": 45},
                "afd": {"state": "unknown", "issued_at": None, "age_hours": None},
            },
        },
        forecast_page_service.FORECAST_PAGE_CACHE_TTL,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "pollen.googleapis.com" and path == "/v1/forecast:lookup":
            return httpx.Response(200, json=_google_pollen_payload())
        if host == "api.weather.gov" and path == "/alerts/active":
            return httpx.Response(200, json={"features": []})
        raise AssertionError(f"Unhandled request: {request.method} {request.url}")

    _mock_async_client(monkeypatch, handler)

    payload = await forecast_page_service.get_forecast_page(
        43.55,
        -96.73,
        location_hint=forecast_page_service.LocationHint(
            display_name="Sioux Falls, SD",
            timezone="America/Chicago",
            country_code="US",
            admin1="South Dakota",
            country="United States",
        ),
    )

    assert payload["pollen"]["index"] == 4
    assert payload["attribution"]["pollen"] == "Google Pollen API"


async def test_get_forecast_page_by_query_non_us_uses_open_meteo_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_now(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "geocoding-api.open-meteo.com" and path == "/v1/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "Toronto",
                            "latitude": 43.6532,
                            "longitude": -79.3832,
                            "elevation": 86.0,
                            "timezone": "America/Toronto",
                            "country_code": "CA",
                            "country": "Canada",
                            "admin1": "Ontario",
                            "population": 2731571,
                            "feature_code": "PPLA",
                        }
                    ]
                },
            )
        if host == "api.open-meteo.com" and path == "/v1/forecast":
            payload = _open_meteo_payload(timezone_name="America/Toronto")
            payload["latitude"] = 43.6532
            payload["longitude"] = -79.3832
            return httpx.Response(200, json=payload)
        if host == "air-quality-api.open-meteo.com" and path == "/v1/air-quality":
            return httpx.Response(200, json=_open_meteo_air_quality_payload(timezone_name="America/Toronto"))
        raise AssertionError(f"Unhandled request: {request.method} {request.url}")

    _mock_async_client(monkeypatch, handler)

    payload = await forecast_page_service.get_forecast_page_by_query("Toronto")

    assert payload["location"]["display_name"] == "Toronto, Ontario"
    assert payload["source_status"]["primary_region_mode"] == "open_meteo_beta"
    assert payload["source_status"]["nws"] == "not_applicable"
    assert payload["official_text_forecast"] is None
    assert payload["afd"] is None
    assert payload["alerts"] == []
    assert payload["current"]["source"] == "open_meteo"
    assert payload["hourly"][1]["weather_code"] == "partly-cloudy-night"


async def test_search_locations_city_state_query_falls_back_to_city_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "geocoding-api.open-meteo.com" and path == "/v1/search":
            name = request.url.params.get("name")
            country_code = request.url.params.get("countryCode")
            requests.append((name or "", country_code))
            if name == "Denver, CO":
                return httpx.Response(200, json={"results": []})
            if name == "Denver" and country_code == "CA":
                return httpx.Response(200, json={"results": []})
            if name == "Denver" and country_code == "US":
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "name": "Denver",
                                "latitude": 39.7392,
                                "longitude": -104.9903,
                                "elevation": 1609.0,
                                "timezone": "America/Denver",
                                "country_code": "US",
                                "country": "United States",
                                "admin1": "Colorado",
                                "population": 715522,
                                "feature_code": "PPLA",
                            }
                        ]
                    },
                )
        raise AssertionError(f"Unhandled request: {request.method} {request.url}")

    _mock_async_client(monkeypatch, handler)

    payload = await forecast_page_service.search_locations("Denver, CO")

    assert requests == [("Denver, CO", "US"), ("Denver, CO", "CA"), ("Denver", "US"), ("Denver", "CA")]
    assert payload["results"][0]["display_name"] == "Denver, CO"
    assert payload["results"][0]["latitude"] == pytest.approx(39.7392)


async def test_search_locations_ignores_stale_empty_geocode_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forecast_page_service._cache_set(
        forecast_page_service.GEOCODE_SEARCH_CACHE_NAMESPACE,
        "renton",
        [],
        forecast_page_service.GEOCODE_CACHE_TTL,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "geocoding-api.open-meteo.com" and path == "/v1/search":
            name = request.url.params.get("name")
            country_code = request.url.params.get("countryCode")
            if name == "Renton" and country_code == "US":
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "id": 5808189,
                                "name": "Renton",
                                "latitude": 47.48288,
                                "longitude": -122.21707,
                                "timezone": "America/Los_Angeles",
                                "country_code": "US",
                                "country": "United States",
                                "admin1": "Washington",
                                "feature_code": "PPL",
                            }
                        ]
                    },
                )
            return httpx.Response(200, json={"results": []})
        raise AssertionError(f"Unhandled request: {request.method} {request.url}")

    _mock_async_client(monkeypatch, handler)

    payload = await forecast_page_service.search_locations("Renton")

    assert len(payload["results"]) == 1
    assert payload["results"][0]["display_name"] == "Renton, WA"


async def test_get_forecast_page_by_coordinates_probes_nws_when_reverse_geocode_lacks_country(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_now(monkeypatch)

    async def fake_get_afd_by_office(office: str) -> nws_service.AfdResult:
        return nws_service.AfdResult(
            wfo=office,
            office_name="BOX",
            issued_at="2026-04-18T16:42:00-04:00",
            product_text="Boston area forecast discussion.",
            product_id="AFDBOX",
        )

    monkeypatch.setattr(forecast_page_service.nws_service, "get_afd_by_office", fake_get_afd_by_office)

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "geocoding-api.open-meteo.com" and path == "/v1/reverse":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "Boston",
                            "latitude": 42.3601,
                            "longitude": -71.0589,
                            "timezone": "America/New_York",
                            "country": "United States",
                            "admin1": "Massachusetts",
                        }
                    ]
                },
            )
        if host == "api.open-meteo.com" and path == "/v1/forecast":
            payload = _open_meteo_payload(timezone_name="America/New_York")
            payload["latitude"] = 42.3601
            payload["longitude"] = -71.0589
            return httpx.Response(200, json=payload)
        if host == "air-quality-api.open-meteo.com" and path == "/v1/air-quality":
            return httpx.Response(200, json=_open_meteo_air_quality_payload(timezone_name="America/New_York"))
        if host == "api.weather.gov" and path == "/points/42.3601,-71.0589":
            return httpx.Response(
                200,
                json={
                    "properties": {
                        "cwa": "BOX",
                        "gridId": "BOX",
                        "gridX": 70,
                        "gridY": 76,
                        "forecast": "https://api.weather.gov/gridpoints/BOX/70,76/forecast",
                        "forecastHourly": "https://api.weather.gov/gridpoints/BOX/70,76/forecast/hourly",
                        "forecastZone": "https://api.weather.gov/zones/forecast/MAZ015",
                        "county": "https://api.weather.gov/zones/county/MAC025",
                        "fireWeatherZone": "https://api.weather.gov/zones/fire/MAZ015",
                        "observationStations": "https://api.weather.gov/gridpoints/BOX/70,76/stations",
                    }
                },
            )
        if host == "api.weather.gov" and path == "/gridpoints/BOX/70,76/forecast":
            return httpx.Response(200, json=_nws_forecast_payload())
        if host == "api.weather.gov" and path == "/gridpoints/BOX/70,76/forecast/hourly":
            return httpx.Response(200, json=_nws_hourly_payload())
        if host == "api.weather.gov" and path == "/gridpoints/BOX/70,76/stations":
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "id": "https://api.weather.gov/stations/KBOS",
                            "geometry": {"coordinates": [-71.0096, 42.3656]},
                            "properties": {
                                "stationIdentifier": "KBOS",
                                "name": "Boston Logan International Airport",
                                "elevation": {"value": 5.0},
                                "stationType": "ASOS",
                            },
                        }
                    ]
                },
            )
        if host == "api.weather.gov" and path == "/stations/KBOS/observations/latest":
            return httpx.Response(200, json=_nws_observation(timestamp="2026-04-18T16:15:00+00:00", description="Partly Cloudy"))
        if host == "api.weather.gov" and path == "/alerts/active":
            return httpx.Response(200, json={"features": []})
        raise AssertionError(f"Unhandled request: {request.method} {request.url}")

    _mock_async_client(monkeypatch, handler)

    payload = await forecast_page_service.get_forecast_page(42.3601, -71.0589)

    assert payload["location"]["display_name"] == "Boston, Massachusetts"
    assert payload["source_status"]["primary_region_mode"] == "us_hybrid"
    assert payload["source_status"]["nws"] == "ok"
    assert payload["current"]["source"] == "nws"
    assert payload["official_text_forecast"] is not None
    assert payload["afd"]["product_id"] == "AFDBOX"


async def test_get_forecast_page_with_location_hint_skips_reverse_geocode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_now(monkeypatch)

    async def fake_get_afd_by_office(office: str) -> nws_service.AfdResult:
        return nws_service.AfdResult(
            wfo=office,
            office_name="SEW",
            issued_at="2026-04-18T16:42:00-07:00",
            product_text="Seattle area forecast discussion.",
            product_id="AFDSEW",
        )

    monkeypatch.setattr(forecast_page_service.nws_service, "get_afd_by_office", fake_get_afd_by_office)

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "geocoding-api.open-meteo.com" and path == "/v1/reverse":
            raise AssertionError("reverse geocoding should be skipped when a location hint is provided")
        if host == "api.open-meteo.com" and path == "/v1/forecast":
            payload = _open_meteo_payload(timezone_name="America/Los_Angeles")
            payload["latitude"] = 47.6062
            payload["longitude"] = -122.3321
            return httpx.Response(200, json=payload)
        if host == "air-quality-api.open-meteo.com" and path == "/v1/air-quality":
            return httpx.Response(200, json=_open_meteo_air_quality_payload(timezone_name="America/Los_Angeles"))
        if host == "api.weather.gov" and path == "/points/47.6062,-122.3321":
            return httpx.Response(
                200,
                json={
                    "properties": {
                        "cwa": "SEW",
                        "gridId": "SEW",
                        "gridX": 124,
                        "gridY": 67,
                        "forecast": "https://api.weather.gov/gridpoints/SEW/124,67/forecast",
                        "forecastHourly": "https://api.weather.gov/gridpoints/SEW/124,67/forecast/hourly",
                        "forecastZone": "https://api.weather.gov/zones/forecast/WAZ509",
                        "county": "https://api.weather.gov/zones/county/WAC033",
                        "fireWeatherZone": "https://api.weather.gov/zones/fire/WAZ659",
                        "observationStations": "https://api.weather.gov/gridpoints/SEW/124,67/stations",
                    }
                },
            )
        if host == "api.weather.gov" and path == "/gridpoints/SEW/124,67/forecast":
            return httpx.Response(200, json=_nws_forecast_payload())
        if host == "api.weather.gov" and path == "/gridpoints/SEW/124,67/forecast/hourly":
            return httpx.Response(200, json=_nws_hourly_payload())
        if host == "api.weather.gov" and path == "/gridpoints/SEW/124,67/stations":
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "id": "https://api.weather.gov/stations/KSEA",
                            "geometry": {"coordinates": [-122.3094, 47.4490]},
                            "properties": {
                                "stationIdentifier": "KSEA",
                                "name": "Seattle-Tacoma International Airport",
                                "elevation": {"value": 132.0},
                                "stationType": "ASOS",
                            },
                        }
                    ]
                },
            )
        if host == "api.weather.gov" and path == "/stations/KSEA/observations/latest":
            return httpx.Response(200, json=_nws_observation(timestamp="2026-04-18T16:50:00+00:00", description="Partly Cloudy"))
        if host == "api.weather.gov" and path == "/alerts/active":
            return httpx.Response(200, json={"features": []})
        raise AssertionError(f"Unhandled request: {request.method} {request.url}")

    _mock_async_client(monkeypatch, handler)

    payload = await forecast_page_service.get_forecast_page(
        47.6062,
        -122.3321,
        location_hint=forecast_page_service.LocationHint(
            display_name="Seattle, WA",
            timezone="America/Los_Angeles",
            country_code="US",
            admin1="Washington",
            country="United States",
        ),
    )

    assert payload["location"]["display_name"] == "Seattle, WA"
    assert payload["location"]["resolved_by"] == "frontend_location_hint"
    assert payload["source_status"]["primary_region_mode"] == "us_hybrid"
    assert payload["current"]["source"] == "nws"


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr(main_module, "DATA_ROOT", Path("/tmp/test-forecast-data"))
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


async def test_forecast_page_routes_smoke(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_search_locations(query: str) -> dict:
        return {"query": query, "results": [{"display_name": "Sioux Falls, SD"}]}

    async def fake_forecast_page(lat: float, lon: float, location_hint=None) -> dict:
        return {
            "location": {
                "query": getattr(location_hint, "display_name", None),
                "display_name": getattr(location_hint, "display_name", None) or "Sioux Falls, SD",
                "latitude": lat,
                "longitude": lon,
                "resolved_by": "frontend_location_hint" if location_hint is not None else "open_meteo_reverse_geocoding",
            },
            "source_status": {"primary_region_mode": "us_hybrid", "nws": "ok", "open_meteo": "ok", "generated_at": "2026-04-18T17:00:00Z"},
            "current": {"source": "nws"},
            "hourly": [],
            "daily": [],
            "air_quality": None,
            "pollen": None,
            "observed_precip": None,
            "official_text_forecast": None,
            "afd": None,
            "alerts": [],
            "attribution": {"current": "NWS", "hourly": None, "daily": "Open-Meteo", "air_quality": None, "pollen": None, "observed_precip": None, "afd": None, "alerts": None},
            "freshness": {},
        }

    async def fake_forecast_page_by_query(query: str) -> dict:
        return {
            "location": {"query": query, "display_name": "Sioux Falls, SD"},
            "source_status": {"primary_region_mode": "us_hybrid", "nws": "ok", "open_meteo": "ok", "generated_at": "2026-04-18T17:00:00Z"},
            "current": {"source": "nws"},
            "hourly": [],
            "daily": [],
            "air_quality": None,
            "pollen": None,
            "observed_precip": None,
            "official_text_forecast": None,
            "afd": None,
            "alerts": [],
            "attribution": {"current": "NWS", "hourly": None, "daily": "Open-Meteo", "air_quality": None, "pollen": None, "observed_precip": None, "afd": None, "alerts": None},
            "freshness": {},
        }

    async def fake_forecast_discussion(office: str) -> dict:
        return {"source": "nws", "office": office, "headline": "Area Forecast Discussion", "text": "..."}

    async def fake_model_guidance(lat: float, lon: float) -> dict:
        return {"status": "placeholder", "location": {"latitude": lat, "longitude": lon}, "sections": []}

    monkeypatch.setattr(forecast_page_service, "search_locations", fake_search_locations)
    monkeypatch.setattr(forecast_page_service, "get_forecast_page", fake_forecast_page)
    monkeypatch.setattr(forecast_page_service, "get_forecast_page_by_query", fake_forecast_page_by_query)
    monkeypatch.setattr(forecast_page_service, "get_forecast_discussion", fake_forecast_discussion)
    monkeypatch.setattr(forecast_page_service, "get_model_guidance_placeholder", fake_model_guidance)

    search_response = await client.get("/api/v4/locations/search", params={"q": "57104"})
    forecast_coords_response = await client.get(
        "/api/v4/forecast-page",
        params={
            "lat": 43.55,
            "lon": -96.73,
            "display_name": "Sioux Falls, SD",
            "country_code": "US",
            "timezone": "America/Chicago",
            "admin1": "South Dakota",
            "country": "United States",
        },
    )
    forecast_response = await client.get("/api/v4/forecast-page/by-query", params={"q": "57104"})
    discussion_response = await client.get("/api/v4/forecast-discussion", params={"office": "FSD"})
    # Retired after Model Guidance Phase 1B — clients use POST /api/v4/forecast/meteogram.
    guidance_response = await client.get("/api/v4/model-guidance", params={"lat": 43.55, "lon": -96.73})

    assert search_response.status_code == 200
    assert search_response.json()["results"][0]["display_name"] == "Sioux Falls, SD"
    assert forecast_coords_response.status_code == 200
    assert forecast_coords_response.json()["location"]["display_name"] == "Sioux Falls, SD"
    assert forecast_response.status_code == 200
    assert forecast_response.json()["location"]["query"] == "57104"
    assert discussion_response.status_code == 200
    assert discussion_response.json()["office"] == "FSD"
    assert guidance_response.status_code == 410
    assert guidance_response.json()["error"] == "gone"
