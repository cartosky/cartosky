import sys
from pathlib import Path

from starlette.responses import JSONResponse

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services import nws


def test_parse_observation_treats_nan_values_as_missing() -> None:
    raw = {
        "properties": {
            "timestamp": "2026-04-03T12:00:00+00:00",
            "temperature": {"value": float("nan")},
            "dewpoint": {"value": 5.0},
            "relativeHumidity": {"value": float("nan")},
            "windSpeed": {"value": 10.0},
            "windGust": {"value": float("inf")},
            "visibility": {"value": float("-inf")},
            "textDescription": "Clear",
        }
    }

    obs = nws._parse_observation(raw, "KXYZ")

    assert obs.station_id == "KXYZ"
    assert obs.temp_f is None
    assert obs.relative_humidity is None
    assert obs.wind_gust_mph is None
    assert obs.visibility_mi is None
    assert obs.dewpoint_f == 41
    assert obs.wind_speed_mph == 6
    assert obs.text_description == "Clear"


def test_serialize_weather_bundle_remains_json_safe_with_missing_numeric_values() -> None:
    obs = nws._parse_observation(
        {
            "properties": {
                "timestamp": "2026-04-03T12:00:00+00:00",
                "temperature": {"value": float("nan")},
                "relativeHumidity": {"value": float("nan")},
                "textDescription": "Clear",
            }
        },
        "KXYZ",
    )
    forecast = nws._parse_forecast(
        {
            "properties": {
                "generatedAt": "2026-04-03T12:00:00+00:00",
                "periods": [
                    {
                        "number": 1,
                        "name": "Today",
                        "isDaytime": True,
                        "temperature": float("nan"),
                        "windSpeed": "5 mph",
                        "windDirection": "NW",
                        "shortForecast": "Sunny",
                        "detailedForecast": "Sunny through the day.",
                        "probabilityOfPrecipitation": {"value": float("nan")},
                    }
                ],
            }
        }
    )
    bundle = nws.WeatherBundle(
        anchor=nws.AnchorInfo(
            anchor_id="test-anchor",
            city="Test City",
            state="Test State",
            st="TS",
            lat=0.0,
            lon=0.0,
            wfo=None,
            grid_x=None,
            grid_y=None,
        ),
        observation=obs,
        forecast=forecast,
    )

    payload = nws.serialize_weather_bundle(bundle)

    assert payload["observation"]["tempF"] is None
    assert payload["observation"]["relativeHumidity"] is None
    assert payload["forecast"]["periods"][0]["tempF"] is None
    assert payload["forecast"]["periods"][0]["precipProbability"] is None
    JSONResponse(payload)
