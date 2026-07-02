from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.models.cpc import CPC_MODEL
from app.services.publish_utils import promote_run, write_json_atomic, write_latest_pointer, write_run_manifest
from app.services.run_ids import format_run_id
from app.services.vector_simplify import simplify_vector_features

logger = logging.getLogger(__name__)

CPC_MODEL_ID = "cpc"
CPC_REGION_ID = "conus"
CPC_SOURCE_NAME = "NOAA CPC"
CPC_610_BASE_URL = "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/cpc_6_10_day_outlk/MapServer"
CPC_814_BASE_URL = "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/cpc_8_14_day_outlk/MapServer"
CPC_1M_TEMP_BASE_URL = "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/cpc_mthly_temp_outlk/MapServer"
CPC_1M_PRECIP_BASE_URL = "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/cpc_mthly_precip_outlk/MapServer"
CPC_3M_TEMP_BASE_URL = "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/cpc_sea_temp_outlk/MapServer"
CPC_3M_PRECIP_BASE_URL = "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/cpc_sea_precip_outlk/MapServer"
CPC_W34_TEMP_ZIP_URL = "https://ftp.cpc.ncep.noaa.gov/GIS/us_tempprcpfcst/wk34temp_latest.zip"
CPC_W34_PRECIP_ZIP_URL = "https://ftp.cpc.ncep.noaa.gov/GIS/us_tempprcpfcst/wk34prcp_latest.zip"


class CPCOutlookError(RuntimeError):
    pass


@dataclass(frozen=True)
class CPCProductConfig:
    var_id: str
    display_name: str
    period: str
    variable: str
    base_url: str
    layer_id: int
    legend_title: str
    style_key: str


@dataclass(frozen=True)
class CPCOutlookPayload:
    product: CPCProductConfig
    issued_at: datetime | None
    valid_start: datetime | None
    valid_end: datetime | None
    valid_seas: str | None
    features: list[dict]


@dataclass(frozen=True)
class CPCPublishResult:
    run_id: str
    published_run_dir: Path
    manifest_path: Path
    frame_count: int
    variable_ids: list[str]


CPC_PRODUCT_CONFIGS: dict[str, CPCProductConfig] = {
    "cpc_610_temp": CPCProductConfig(
        var_id="cpc_610_temp",
        display_name="CPC 6-10 Day Temperature Outlook",
        period="6-10",
        variable="temperature",
        base_url=CPC_610_BASE_URL,
        layer_id=0,
        legend_title="CPC Temperature Outlook",
        style_key="cpc_temperature_outlook",
    ),
    "cpc_610_precip": CPCProductConfig(
        var_id="cpc_610_precip",
        display_name="CPC 6-10 Day Precipitation Outlook",
        period="6-10",
        variable="precipitation",
        base_url=CPC_610_BASE_URL,
        layer_id=1,
        legend_title="CPC Precipitation Outlook",
        style_key="cpc_precipitation_outlook",
    ),
    "cpc_814_temp": CPCProductConfig(
        var_id="cpc_814_temp",
        display_name="CPC 8-14 Day Temperature Outlook",
        period="8-14",
        variable="temperature",
        base_url=CPC_814_BASE_URL,
        layer_id=0,
        legend_title="CPC Temperature Outlook",
        style_key="cpc_temperature_outlook",
    ),
    "cpc_814_precip": CPCProductConfig(
        var_id="cpc_814_precip",
        display_name="CPC 8-14 Day Precipitation Outlook",
        period="8-14",
        variable="precipitation",
        base_url=CPC_814_BASE_URL,
        layer_id=1,
        legend_title="CPC Precipitation Outlook",
        style_key="cpc_precipitation_outlook",
    ),
    "cpc_w34_temp": CPCProductConfig(
        var_id="cpc_w34_temp",
        display_name="CPC Week 3-4 Temperature Outlook",
        period="w34",
        variable="temperature",
        base_url=CPC_W34_TEMP_ZIP_URL,
        layer_id=0,
        legend_title="CPC Temperature Outlook",
        style_key="cpc_temperature_outlook",
    ),
    "cpc_w34_precip": CPCProductConfig(
        var_id="cpc_w34_precip",
        display_name="CPC Week 3-4 Precipitation Outlook",
        period="w34",
        variable="precipitation",
        base_url=CPC_W34_PRECIP_ZIP_URL,
        layer_id=0,
        legend_title="CPC Precipitation Outlook",
        style_key="cpc_precipitation_outlook",
    ),
    "cpc_1m_temp": CPCProductConfig(
        var_id="cpc_1m_temp",
        display_name="CPC 1-Month Temperature Outlook",
        period="1m",
        variable="temperature",
        base_url=CPC_1M_TEMP_BASE_URL,
        layer_id=0,
        legend_title="CPC Temperature Outlook",
        style_key="cpc_temperature_outlook",
    ),
    "cpc_1m_precip": CPCProductConfig(
        var_id="cpc_1m_precip",
        display_name="CPC 1-Month Precipitation Outlook",
        period="1m",
        variable="precipitation",
        base_url=CPC_1M_PRECIP_BASE_URL,
        layer_id=0,
        legend_title="CPC Precipitation Outlook",
        style_key="cpc_precipitation_outlook",
    ),
    "cpc_3m_temp": CPCProductConfig(
        var_id="cpc_3m_temp",
        display_name="CPC 3-Month Temperature Outlook",
        period="3m",
        variable="temperature",
        base_url=CPC_3M_TEMP_BASE_URL,
        layer_id=0,
        legend_title="CPC Temperature Outlook",
        style_key="cpc_temperature_outlook",
    ),
    "cpc_3m_precip": CPCProductConfig(
        var_id="cpc_3m_precip",
        display_name="CPC 3-Month Precipitation Outlook",
        period="3m",
        variable="precipitation",
        base_url=CPC_3M_PRECIP_BASE_URL,
        layer_id=0,
        legend_title="CPC Precipitation Outlook",
        style_key="cpc_precipitation_outlook",
    ),
}

TEMP_COLORS: dict[str, dict[int, str] | str] = {
    "above": {
        33: "#edbe7b",
        40: "#ea9d5d",
        50: "#e36d3f",
        60: "#d55021",
        70: "#c24100",
        80: "#a43700",
        90: "#842f00",
    },
    "near": "#b0afb0",
    "below": {
        33: "#cad5e9",
        40: "#afcce5",
        50: "#88c2e8",
        60: "#43afe3",
        70: "#0072b1",
        80: "#3d3282",
        90: "#2e2565",
    },
}

PRECIP_COLORS: dict[str, dict[int, str] | str] = {
    "above": {
        33: "#bfdfb9",
        40: "#a4d591",
        50: "#55bd3e",
        60: "#00a32a",
        70: "#008819",
        80: "#327108",
        90: "#336400",
    },
    "near": "#b1b0b1",
    "below": {
        33: "#f4dba4",
        40: "#e0b561",
        50: "#c98142",
        60: "#ad6440",
        70: "#a55a49",
        80: "#945200",
        90: "#623f3d",
    },
}


def fetch_cpc_outlook(
    period: str,
    variable: str,
    *,
    timeout_seconds: float = 30.0,
) -> dict:
    config = _config_for(period=period, variable=variable)
    return fetch_cpc_layer_geojson(config, timeout_seconds=timeout_seconds)


def fetch_cpc_layer_geojson(config: CPCProductConfig, *, timeout_seconds: float = 30.0) -> dict:
    query = urlencode({"where": "1=1", "outFields": "*", "f": "geojson"})
    url = f"{config.base_url.rstrip('/')}/{config.layer_id}/query?{query}"
    request = Request(
        url,
        headers={
            "User-Agent": "CartoSky-CPC/1.0",
            "Accept": "application/geo+json,application/json;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=float(timeout_seconds)) as response:
        return json.loads(response.read().decode("utf-8"))


def _config_for(*, period: str, variable: str) -> CPCProductConfig:
    normalized_period = str(period).strip().lower().replace("day", "").replace(" ", "")
    normalized_variable = str(variable).strip().lower()
    for config in CPC_PRODUCT_CONFIGS.values():
        if config.period.replace("-", "") == normalized_period.replace("-", "") and config.variable == normalized_variable:
            return config
    raise CPCOutlookError(f"Unknown CPC outlook period={period!r} variable={variable!r}")


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 1e12:
            numeric /= 1000.0
        try:
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M", "%Y%m%d_%H%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _first_datetime(props: dict, *keys: str) -> datetime | None:
    for key in keys:
        parsed = _coerce_datetime(props.get(key))
        if parsed is not None:
            return parsed
        parsed = _coerce_datetime(props.get(key.lower()))
        if parsed is not None:
            return parsed
    return None


def _first_text(props: dict, *keys: str) -> str | None:
    for key in keys:
        for candidate in (props.get(key), props.get(key.lower())):
            text = str(candidate or "").strip()
            if text:
                return text
    return None


def _category(value: object) -> tuple[str, str] | None:
    text = str(value or "").strip().lower()
    if text in {"above", "a", "above normal"}:
        return "above", "Above Normal"
    if text in {"below", "b", "below normal"}:
        return "below", "Below Normal"
    if text in {"normal", "near", "n", "near normal", "ec", "equal chances", "equal chance"}:
        return "near", "Near Normal"
    return None


def _probability(value: object) -> int | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric <= 0:
        return None
    return int(round(numeric))


def _probability_bucket(probability: int | None) -> int:
    if probability is None:
        return 33
    for bucket in (90, 80, 70, 60, 50, 40, 33):
        if probability >= bucket:
            return bucket
    return 33


def _style_for(variable: str, category: str, probability: int | None) -> tuple[str, int]:
    palette = TEMP_COLORS if variable == "temperature" else PRECIP_COLORS
    if category == "near":
        return str(palette["near"]), 0
    bucket = _probability_bucket(probability)
    category_palette = palette.get(category)
    if isinstance(category_palette, dict):
        return category_palette.get(bucket) or category_palette[33], bucket
    return "#888888", bucket


def _format_iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def _hover_label(
    *,
    category_label: str,
    probability: int | None,
) -> str:
    display_category = category_label[:1].upper() + category_label[1:].lower() if category_label else category_label
    parts = [f"Category: {display_category}"]
    if probability is not None:
        parts.append(f"Probability: {probability}%")
    return " · ".join(parts)


def normalize_cpc_features(raw_data: dict, *, config: CPCProductConfig) -> CPCOutlookPayload:
    features_raw = raw_data.get("features")
    if not isinstance(features_raw, list):
        raise CPCOutlookError("CPC payload is missing features")

    normalized_features: list[dict] = []
    issued_at: datetime | None = None
    valid_start: datetime | None = None
    valid_end: datetime | None = None
    valid_seas: str | None = None
    forecast_date: datetime | None = None

    for feature in features_raw:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        props = feature.get("properties")
        if not isinstance(props, dict):
            props = {}

        category = _category(props.get("cat") or props.get("CAT") or props.get("category"))
        if category is None:
            continue
        category_key, category_label = category
        probability = _probability(props.get("prob") or props.get("PROB") or props.get("probability"))
        fill, bucket = _style_for(config.variable, category_key, probability)
        display_label = category_label if category_key == "near" else f"{probability or bucket}% {category_label}"

        feature_valid_start = _first_datetime(props, "start_date", "START_DATE")
        feature_valid_end = _first_datetime(props, "end_date", "END_DATE")
        feature_valid_seas = _first_text(props, "valid_seas", "VALID_SEAS")
        feature_issued_at = _first_datetime(props, "idp_filedate", "idp_FILEDATE", "fcst_date", "FCST_DATE")
        forecast_date = forecast_date or _first_datetime(props, "fcst_date", "FCST_DATE")
        issued_at = issued_at or feature_issued_at
        valid_start = valid_start or feature_valid_start
        valid_end = valid_end or feature_valid_end
        valid_seas = valid_seas or feature_valid_seas

        normalized_features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "source": CPC_SOURCE_NAME,
                    "outlook_type": config.variable,
                    "period": config.period,
                    "category": category_key,
                    "label": category_label,
                    "probability": probability,
                    "probability_bucket": bucket if category_key != "near" else None,
                    "displayLabel": display_label,
                    "risk_label": display_label,
                    "hover_label": _hover_label(
                        category_label=category_label,
                        probability=probability,
                    ),
                    "valid_start": _format_iso(feature_valid_start),
                    "valid_end": _format_iso(feature_valid_end),
                    "valid_seas": feature_valid_seas,
                    "issued_at": _format_iso(feature_issued_at),
                    "fill": fill,
                    "fill_opacity": 0.66 if category_key != "near" else 0.42,
                    "stroke": "#30343b",
                    "stroke_width": 0.75,
                    "sort_rank": _sort_rank(category_key, probability),
                },
            }
        )

    if not normalized_features:
        raise CPCOutlookError(f"{config.display_name} payload had no recognized CPC outlook polygons")

    normalized_features.sort(key=lambda item: int(item["properties"].get("sort_rank") or 0))
    return CPCOutlookPayload(
        product=config,
        issued_at=issued_at or forecast_date,
        valid_start=valid_start,
        valid_end=valid_end,
        valid_seas=valid_seas,
        features=normalized_features,
    )


def fetch_and_normalize_w34_shapefile(
    config: CPCProductConfig,
    *,
    timeout_seconds: float = 30.0,
) -> CPCOutlookPayload:
    import datetime as dt_mod
    import io
    import zipfile

    import shapefile

    request = Request(
        config.base_url,
        headers={"User-Agent": "CartoSky-CPC/1.0"},
    )
    with urlopen(request, timeout=float(timeout_seconds)) as response:
        zip_bytes = response.read()

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        shp_name = next(name for name in zf.namelist() if name.endswith(".shp"))
        stem = shp_name[:-4]
        sf = shapefile.Reader(
            shp=io.BytesIO(zf.read(stem + ".shp")),
            dbf=io.BytesIO(zf.read(stem + ".dbf")),
            shx=io.BytesIO(zf.read(stem + ".shx")),
        )

        normalized_features: list[dict] = []
        issued_at: datetime | None = None
        valid_start: datetime | None = None
        valid_end: datetime | None = None

        def _date_to_dt(value: object) -> datetime | None:
            if isinstance(value, dt_mod.date) and not isinstance(value, datetime):
                return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
            if isinstance(value, datetime):
                return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
            return _coerce_datetime(value)

        for shape_rec in sf.shapeRecords():
            rec = shape_rec.record.as_dict()
            geom = shape_rec.shape.__geo_interface__

            category = _category(rec.get("Cat"))
            if category is None:
                continue
            category_key, category_label = category

            probability = _probability(rec.get("Prob"))
            fill, bucket = _style_for(config.variable, category_key, probability)
            display_label = category_label if category_key == "near" else f"{probability or bucket}% {category_label}"

            feature_fcst_date = _date_to_dt(rec.get("Fcst_Date"))
            feature_valid_start = _date_to_dt(rec.get("Start_Date"))
            feature_valid_end = _date_to_dt(rec.get("End_Date"))

            issued_at = issued_at or feature_fcst_date
            valid_start = valid_start or feature_valid_start
            valid_end = valid_end or feature_valid_end

            normalized_features.append(
                {
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {
                        "source": CPC_SOURCE_NAME,
                        "outlook_type": config.variable,
                        "period": config.period,
                        "category": category_key,
                        "label": category_label,
                        "probability": probability,
                        "probability_bucket": bucket if category_key != "near" else None,
                        "displayLabel": display_label,
                        "risk_label": display_label,
                        "hover_label": _hover_label(
                            category_label=category_label,
                            probability=probability,
                        ),
                        "valid_start": _format_iso(feature_valid_start),
                        "valid_end": _format_iso(feature_valid_end),
                        "issued_at": _format_iso(feature_fcst_date),
                        "fill": fill,
                        "fill_opacity": 0.66 if category_key != "near" else 0.42,
                        "stroke": "#30343b",
                        "stroke_width": 0.75,
                        "sort_rank": _sort_rank(category_key, probability),
                    },
                }
            )

    if not normalized_features:
        raise CPCOutlookError(f"{config.display_name} shapefile had no recognized polygons")

    normalized_features.sort(key=lambda item: int(item["properties"].get("sort_rank") or 0))
    return CPCOutlookPayload(
        product=config,
        issued_at=issued_at,
        valid_start=valid_start,
        valid_end=valid_end,
        valid_seas=None,
        features=normalized_features,
    )


def _sort_rank(category: str, probability: int | None) -> int:
    base = {"near": 0, "below": 100, "above": 200}.get(category, 300)
    return base + int(probability or 0)


def _latest_published_run_id(data_root: Path) -> str | None:
    latest_path = data_root / "published" / CPC_MODEL_ID / "LATEST.json"
    try:
        latest_payload = json.loads(latest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    run_id = str(latest_payload.get("run_id") or "").strip()
    return run_id or None


def _preservation_source_run_id(data_root: Path, run_id: str) -> str:
    current_manifest = data_root / "manifests" / CPC_MODEL_ID / f"{run_id}.json"
    if current_manifest.is_file():
        return run_id
    latest_run_id = _latest_published_run_id(data_root)
    if not latest_run_id:
        return run_id
    latest_run = data_root / "published" / CPC_MODEL_ID / latest_run_id
    latest_manifest = data_root / "manifests" / CPC_MODEL_ID / f"{latest_run_id}.json"
    if latest_run.is_dir() and latest_manifest.is_file():
        return latest_run_id
    return run_id


def _link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    try:
        target.hardlink_to(source)
    except OSError:
        shutil.copy2(source, target)


def _copy_preserved_run_artifact(source: Path, target: Path, *, run_id: str, source_run_id: str) -> None:
    if source.suffix.lower() == ".geojson":
        # Re-simplify preserved vectors so pre-existing unsimplified payloads
        # (published before geometry reduction landed) shrink on the next
        # publish cycle instead of waiting for their product to be reissued.
        try:
            payload = json.loads(source.read_text())
        except (OSError, json.JSONDecodeError):
            _link_or_copy(source, target)
            return
        features = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(features, list):
            _link_or_copy(source, target)
            return
        payload["features"] = simplify_vector_features(features)
        write_json_atomic(target, payload, compact=True)
        return
    if source_run_id == run_id or source.suffix.lower() != ".json":
        _link_or_copy(source, target)
        return
    try:
        payload = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError):
        _link_or_copy(source, target)
        return
    if not isinstance(payload, dict):
        _link_or_copy(source, target)
        return
    if payload.get("run") == source_run_id:
        payload["run"] = run_id
    write_json_atomic(target, payload)


def _seed_preserved_cpc_variables(
    *,
    data_root: Path,
    run_id: str,
    source_run_id: str,
    refreshed_var_ids: set[str],
) -> list[str]:
    published_run = data_root / "published" / CPC_MODEL_ID / source_run_id
    if not published_run.is_dir():
        return []
    stage_run = data_root / "staging" / CPC_MODEL_ID / run_id
    preserved: list[str] = []
    for var_dir in sorted(published_run.iterdir()):
        if not var_dir.is_dir():
            continue
        var_id = var_dir.name
        if var_id in refreshed_var_ids or var_id not in CPC_PRODUCT_CONFIGS:
            continue
        target_var_dir = stage_run / var_id
        target_var_dir.mkdir(parents=True, exist_ok=True)
        for src_file in var_dir.rglob("*"):
            if not src_file.is_file():
                continue
            rel = src_file.relative_to(var_dir)
            dst_file = target_var_dir / rel
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            _copy_preserved_run_artifact(src_file, dst_file, run_id=run_id, source_run_id=source_run_id)
        preserved.append(var_id)
    return preserved


def cache_cpc_outlook(data_root: Path, outlook: CPCOutlookPayload, *, run_id: str) -> None:
    var_root = data_root / "staging" / CPC_MODEL_ID / run_id / outlook.product.var_id
    vector_root = var_root / "vectors"
    vector_root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        vector_root / "fh000.geojson",
        {"type": "FeatureCollection", "features": simplify_vector_features(outlook.features)},
        compact=True,
    )
    write_json_atomic(var_root / "fh000.json", _build_frame_sidecar(run_id=run_id, outlook=outlook))


def _legend_entries(variable: str) -> list[dict[str, object]]:
    palette = TEMP_COLORS if variable == "temperature" else PRECIP_COLORS
    rows: list[dict[str, object]] = []
    for value, label, category in (
        (33, "33-40% Below Normal", "below"),
        (40, "40-50% Below Normal", "below"),
        (50, "50-60% Below Normal", "below"),
        (60, "60-70% Below Normal", "below"),
        (70, "70-80% Below Normal", "below"),
        (80, "80-90% Below Normal", "below"),
        (90, "90-100% Below Normal", "below"),
        (0, "Near Normal", "near"),
        (133, "33-40% Above Normal", "above"),
        (140, "40-50% Above Normal", "above"),
        (150, "50-60% Above Normal", "above"),
        (160, "60-70% Above Normal", "above"),
        (170, "70-80% Above Normal", "above"),
        (180, "80-90% Above Normal", "above"),
        (190, "90-100% Above Normal", "above"),
    ):
        if category == "near":
            color = str(palette["near"])
        else:
            bucket = value if value < 100 else value - 100
            category_palette = palette[category]
            color = str(category_palette[bucket]) if isinstance(category_palette, dict) else "#888888"
        rows.append({"value": value, "color": color, "label": label})
    return rows


def _build_frame_sidecar(*, run_id: str, outlook: CPCOutlookPayload) -> dict:
    valid_time = outlook.valid_start or outlook.issued_at or datetime.now(timezone.utc)
    payload = {
        "contract_version": "3.0",
        "model": CPC_MODEL_ID,
        "run": run_id,
        "var": outlook.product.var_id,
        "fh": 0,
        "region": CPC_REGION_ID,
        "valid_time": valid_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issue_time": _format_iso(outlook.issued_at),
        "issued_at": _format_iso(outlook.issued_at),
        "valid_start": _format_iso(outlook.valid_start),
        "valid_end": _format_iso(outlook.valid_end),
        "valid_seas": outlook.valid_seas,
        "source": CPC_SOURCE_NAME,
        "kind": "categorical",
        "legend_title": outlook.product.legend_title,
        "legend_note": "Probabilities of above, near, or below normal conditions; not deterministic temperatures or precipitation amounts.",
        "display_name": outlook.product.display_name,
        "period": outlook.product.period,
        "outlook_type": outlook.product.variable,
        "legend_entries": _legend_entries(outlook.product.variable),
        "vector_layers": {
            "primary": {
                "format": "geojson",
                "path": "vectors/fh000.geojson",
                "style_key": outlook.product.style_key,
            }
        },
    }
    return {key: value for key, value in payload.items() if value is not None}


def build_cpc_products_fingerprint(products: dict[str, CPCOutlookPayload]) -> str:
    """Content fingerprint over the refreshed products.

    The bundle ``run_id`` is derived from the *oldest* product issue time, so it stays
    pinned for weeks while the slowest-cadence outlook (1-month / 3-month) is unchanged.
    This fingerprint lets the poller detect when any individual product (e.g. the daily
    6-10 / 8-14 day outlooks) has fresh data and republish into the same ``run_id``.
    """
    parts: list[str] = []
    for var_id in sorted(products.keys()):
        outlook = products[var_id]
        issue_stamp = (
            outlook.issued_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if outlook.issued_at is not None
            else "none"
        )
        parts.append(f"{var_id}:{issue_stamp}:{len(outlook.features)}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def publish_cpc_outlooks(
    *,
    data_root: Path,
    products: dict[str, CPCOutlookPayload],
    issued_at: datetime,
) -> CPCPublishResult:
    if not products:
        raise CPCOutlookError("CPC publish requires at least one product")

    run_id = format_run_id(issued_at.astimezone(timezone.utc), include_minutes=True)
    preservation_source_run_id = _preservation_source_run_id(data_root, run_id)
    staging_run_root = data_root / "staging" / CPC_MODEL_ID / run_id
    if staging_run_root.exists():
        shutil.rmtree(staging_run_root, ignore_errors=True)
    staging_run_root.mkdir(parents=True, exist_ok=True)

    preserved_var_ids = _seed_preserved_cpc_variables(
        data_root=data_root,
        run_id=run_id,
        source_run_id=preservation_source_run_id,
        refreshed_var_ids=set(products.keys()),
    )

    targets: list[tuple[str, int]] = [(var_id, 0) for var_id in preserved_var_ids]
    latest_valid_time: datetime | None = None
    for var_id, outlook in products.items():
        cache_cpc_outlook(data_root, outlook, run_id=run_id)
        targets.append((var_id, 0))
        if outlook.valid_end is not None:
            latest_valid_time = outlook.valid_end if latest_valid_time is None else max(latest_valid_time, outlook.valid_end)

    published_variable_ids = sorted(set(products.keys()) | set(preserved_var_ids))

    promote_run(data_root=data_root, model=CPC_MODEL_ID, run_id=run_id)
    write_run_manifest(
        data_root=data_root,
        model=CPC_MODEL_ID,
        run_id=run_id,
        targets=targets,
        plugin=CPC_MODEL,
        metadata={
            "source": "NOAA CPC ArcGIS MapServer",
            "time_axis_mode": "valid",
            "target_frame_count": len(targets),
            "available_frame_count": len(targets),
            "latest_valid_time": (latest_valid_time or issued_at).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_fingerprint": build_cpc_products_fingerprint(products),
        },
    )
    write_latest_pointer(data_root=data_root, model=CPC_MODEL_ID, run_id=run_id, source="cpc_outlook")
    return CPCPublishResult(
        run_id=run_id,
        published_run_dir=data_root / "published" / CPC_MODEL_ID / run_id,
        manifest_path=data_root / "manifests" / CPC_MODEL_ID / f"{run_id}.json",
        frame_count=len(targets),
        variable_ids=published_variable_ids,
    )


def collect_latest_cpc_outlooks(*, timeout_seconds: float = 30.0) -> tuple[dict[str, CPCOutlookPayload], datetime]:
    products: dict[str, CPCOutlookPayload] = {}
    failures: list[str] = []
    for config in CPC_PRODUCT_CONFIGS.values():
        try:
            if config.period == "w34":
                products[config.var_id] = fetch_and_normalize_w34_shapefile(config, timeout_seconds=timeout_seconds)
            else:
                raw = fetch_cpc_layer_geojson(config, timeout_seconds=timeout_seconds)
                products[config.var_id] = normalize_cpc_features(raw, config=config)
        except Exception as exc:
            failures.append(f"{config.var_id}: {exc}")
            logger.warning("Skipping CPC product var=%s error=%s", config.var_id, exc)

    if not products:
        raise CPCOutlookError("CPC publish failed: no products available; " + "; ".join(failures))
    issue_time = min((outlook.issued_at for outlook in products.values() if outlook.issued_at is not None), default=None)
    return products, issue_time or datetime.now(timezone.utc)


def publish_latest_cpc_outlooks(*, data_root: Path, timeout_seconds: float = 30.0) -> CPCPublishResult:
    try:
        products, issued_at = collect_latest_cpc_outlooks(timeout_seconds=timeout_seconds)
    except CPCOutlookError:
        latest_pointer = data_root / "published" / CPC_MODEL_ID / "LATEST.json"
        if latest_pointer.exists():
            logger.warning("CPC refresh failed; preserving last known good CPC outlook bundle")
        raise
    return publish_cpc_outlooks(data_root=data_root, products=products, issued_at=issued_at)
