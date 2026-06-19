from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

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

from app.models.cpc import CPC_MODEL, CPC_VARIABLE_CATALOG
from app.services import cpc_outlook


def _feature(cat: str, prob: float) -> dict:
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-100.0, 40.0], [-99.0, 40.0], [-99.0, 41.0], [-100.0, 40.0]]],
        },
        "properties": {
            "fcst_date": 1779321600000,
            "start_date": 1779840000000,
            "end_date": 1780185600000,
            "prob": prob,
            "cat": cat,
            "idp_filedate": 1779390916000,
            "idp_source": "610temp_latest",
        },
    }


def test_normalize_cpc_temperature_categories_and_metadata() -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [_feature("Above", 40.0), _feature("Below", 70.0), _feature("Normal", 36.0)],
    }

    normalized = cpc_outlook.normalize_cpc_features(
        payload,
        config=cpc_outlook.CPC_PRODUCT_CONFIGS["cpc_610_temp"],
    )

    assert normalized.product.var_id == "cpc_610_temp"
    assert normalized.issued_at == datetime(2026, 5, 21, 19, 15, 16, tzinfo=timezone.utc)
    assert normalized.valid_start == datetime(2026, 5, 27, tzinfo=timezone.utc)
    assert normalized.valid_end == datetime(2026, 5, 31, tzinfo=timezone.utc)

    by_category = {feature["properties"]["category"]: feature["properties"] for feature in normalized.features}
    assert by_category["above"]["fill"] == "#ea9d5d"
    assert by_category["above"]["probability"] == 40
    assert by_category["above"]["displayLabel"] == "40% Above Normal"
    assert by_category["below"]["fill"] == "#0072b1"
    assert by_category["near"]["fill"] == "#b0afb0"
    assert by_category["near"]["label"] == "Near Normal"
    assert by_category["above"]["hover_label"] == "Category: Above normal · Probability: 40%"


def test_normalize_cpc_equal_chances_and_valid_seas_metadata() -> None:
    feature = _feature("EC", 33.0)
    feature["properties"]["valid_seas"] = "JAS 2026"
    payload = {"type": "FeatureCollection", "features": [feature]}

    normalized = cpc_outlook.normalize_cpc_features(
        payload,
        config=cpc_outlook.CPC_PRODUCT_CONFIGS["cpc_3m_temp"],
    )

    props = normalized.features[0]["properties"]
    assert normalized.valid_seas == "JAS 2026"
    assert props["category"] == "near"
    assert props["label"] == "Near Normal"
    assert props["valid_seas"] == "JAS 2026"


def test_normalize_cpc_precip_palette() -> None:
    payload = {"type": "FeatureCollection", "features": [_feature("Above", 60.0), _feature("Below", 50.0)]}

    normalized = cpc_outlook.normalize_cpc_features(
        payload,
        config=cpc_outlook.CPC_PRODUCT_CONFIGS["cpc_814_precip"],
    )

    by_category = {feature["properties"]["category"]: feature["properties"] for feature in normalized.features}
    assert by_category["above"]["fill"] == "#00a32a"
    assert by_category["below"]["fill"] == "#c98142"
    assert by_category["above"]["period"] == "8-14"
    assert by_category["above"]["outlook_type"] == "precipitation"


def test_publish_cpc_outlooks_writes_vector_bundle(tmp_path: Path) -> None:
    config = cpc_outlook.CPC_PRODUCT_CONFIGS["cpc_610_temp"]
    feature = _feature("Above", 40.0)
    feature["properties"]["VALID_SEAS"] = "Jul 2026"
    normalized = cpc_outlook.normalize_cpc_features(
        {"type": "FeatureCollection", "features": [feature]},
        config=config,
    )

    result = cpc_outlook.publish_cpc_outlooks(
        data_root=tmp_path,
        products={config.var_id: normalized},
        issued_at=normalized.issued_at or datetime(2026, 5, 21, tzinfo=timezone.utc),
    )

    latest = json.loads((tmp_path / "published" / "cpc" / "LATEST.json").read_text())
    assert latest["run_id"] == result.run_id

    sidecar = json.loads((tmp_path / "published" / "cpc" / result.run_id / config.var_id / "fh000.json").read_text())
    assert sidecar["vector_layers"]["primary"]["style_key"] == "cpc_temperature_outlook"
    assert sidecar["legend_note"].startswith("Probabilities of above")
    assert sidecar["valid_start"] == "2026-05-27T00:00:00Z"
    assert sidecar["valid_seas"] == "Jul 2026"

    vector = json.loads(
        (tmp_path / "published" / "cpc" / result.run_id / config.var_id / "vectors" / "fh000.geojson").read_text()
    )
    assert vector["features"][0]["properties"]["risk_label"] == "40% Above Normal"

    manifest = json.loads((tmp_path / "manifests" / "cpc" / f"{result.run_id}.json").read_text())
    assert manifest["metadata"]["time_axis_mode"] == "valid"
    assert manifest["variables"][config.var_id]["available_frames"] == 1


def test_cpc_catalog_includes_monthly_and_seasonal_products() -> None:
    assert CPC_VARIABLE_CATALOG["cpc_1m_temp"].order == 4
    assert CPC_VARIABLE_CATALOG["cpc_1m_precip"].order == 5
    assert CPC_VARIABLE_CATALOG["cpc_3m_temp"].order == 6
    assert CPC_VARIABLE_CATALOG["cpc_3m_precip"].order == 7
    assert CPC_MODEL.normalize_var_id("1m-temperature") == "cpc_1m_temp"
    assert CPC_MODEL.normalize_var_id("cpc_3m_precipitation") == "cpc_3m_precip"
