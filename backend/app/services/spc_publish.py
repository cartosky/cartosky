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

from app.models.spc import SPC_MODEL
from app.services.publish_utils import promote_run, write_json_atomic, write_latest_pointer, write_run_manifest
from app.services.run_ids import format_run_id

logger = logging.getLogger(__name__)

SPC_MODEL_ID = "spc"
SPC_REGION_ID = "conus"
SPC_LAYER_BASE_URL = "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/SPC_wx_outlks/FeatureServer"

SPC_RISK_STYLE_BY_CODE: dict[int, dict[str, object]] = {
    1: {"risk_label": "T-Storms", "fill": "#808080", "sort_rank": 1},
    2: {"risk_label": "Marginal", "fill": "#008000", "sort_rank": 2},
    3: {"risk_label": "Slight", "fill": "#FFFF00", "sort_rank": 3},
    4: {"risk_label": "Enhanced", "fill": "#FFA500", "sort_rank": 4},
    5: {"risk_label": "Moderate", "fill": "#FF0000", "sort_rank": 5},
    6: {"risk_label": "High", "fill": "#FF00FF", "sort_rank": 6},
}

DN_PROBABILITY_STYLES: dict[int, tuple[int, str, str]] = {
    15: (15, "#FFEB7F", "#FF9600"),
    30: (30, "#FF9600", "#FF4500"),
    45: (45, "#FF4500", "#CC0000"),
    60: (60, "#FF0000", "#990000"),
}


@dataclass(frozen=True)
class SPCProductConfig:
    var_id: str
    display_name: str
    legend_title: str
    kind: str
    style_key: str
    day_layers: tuple[tuple[int, str], ...]
    probability_name: str | None = None


SPC_CONVECTIVE_PRODUCT = SPCProductConfig(
    var_id="convective",
    display_name="SPC Convective Outlook",
    legend_title="Categorical Chance",
    kind="categorical",
    style_key="spc_convective",
    day_layers=((1, "Day 1"), (9, "Day 2"), (17, "Day 3")),
)

SPC_TORNADO_PRODUCT = SPCProductConfig(
    var_id="tornado_prob",
    display_name="SPC Tornado Probability",
    legend_title="Tornado Probability",
    kind="categorical",
    style_key="spc_tornado_probability",
    day_layers=((3, "Day 1"), (11, "Day 2")),
    probability_name="Tornado",
)

SPC_WIND_PRODUCT = SPCProductConfig(
    var_id="wind_prob",
    display_name="SPC Wind Probability",
    legend_title="Wind Probability",
    kind="categorical",
    style_key="spc_wind_probability",
    day_layers=((7, "Day 1"), (15, "Day 2")),
    probability_name="Wind",
)

SPC_HAIL_PRODUCT = SPCProductConfig(
    var_id="hail_prob",
    display_name="SPC Hail Probability",
    legend_title="Hail Probability",
    kind="categorical",
    style_key="spc_hail_probability",
    day_layers=((5, "Day 1"), (13, "Day 2")),
    probability_name="Hail",
)

SPC_EXTENDED_PRODUCT = SPCProductConfig(
    var_id="extended",
    display_name="SPC Extended Outlook",
    legend_title="Severe Probability",
    kind="categorical",
    style_key="spc_extended_probability",
    day_layers=((21, "Day 4"), (22, "Day 5"), (23, "Day 6"), (24, "Day 7"), (25, "Day 8")),
    probability_name="Any Severe",
)

SPC_PRODUCT_CONFIGS: dict[str, SPCProductConfig] = {
    SPC_CONVECTIVE_PRODUCT.var_id: SPC_CONVECTIVE_PRODUCT,
    SPC_TORNADO_PRODUCT.var_id: SPC_TORNADO_PRODUCT,
    SPC_WIND_PRODUCT.var_id: SPC_WIND_PRODUCT,
    SPC_HAIL_PRODUCT.var_id: SPC_HAIL_PRODUCT,
    SPC_EXTENDED_PRODUCT.var_id: SPC_EXTENDED_PRODUCT,
}

SPC_VARIABLE_ID = SPC_CONVECTIVE_PRODUCT.var_id
SPC_DAY_LAYERS = SPC_CONVECTIVE_PRODUCT.day_layers


class SPCPublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class SPCFramePayload:
    fh: int
    day_label: str
    valid_time: datetime
    issue_time: datetime
    features: list[dict]


@dataclass(frozen=True)
class SPCPublishResult:
    run_id: str
    published_run_dir: Path
    manifest_path: Path
    frame_count: int
    variable_ids: list[str]


def fetch_spc_layer_geojson(
    layer_id: int,
    *,
    timeout_seconds: float = 30.0,
    base_url: str = SPC_LAYER_BASE_URL,
) -> dict:
    query = urlencode({"where": "1=1", "outFields": "*", "f": "geojson"})
    url = f"{base_url.rstrip('/')}/{int(layer_id)}/query?{query}"
    request = Request(
        url,
        headers={
            "User-Agent": "CartoSky-SPC/1.0",
            "Accept": "application/geo+json,application/json;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=float(timeout_seconds)) as response:
        return json.loads(response.read().decode("utf-8"))


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
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M", "%Y%m%d_%H%M", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_timestamp(props: dict, *candidate_keys: str) -> datetime | None:
    for key in candidate_keys:
        parsed = _coerce_datetime(props.get(key))
        if parsed is not None:
            return parsed
        lowered = props.get(str(key).lower())
        parsed = _coerce_datetime(lowered)
        if parsed is not None:
            return parsed
    return None


def _risk_code_from_properties(props: dict) -> int | None:
    label_text = str(
        props.get("label")
        or props.get("LABEL")
        or props.get("label2")
        or props.get("LABEL2")
        or props.get("CATEGORY")
        or ""
    ).strip().lower()
    label_map = {
        "tstm": 1,
        "t-storms": 1,
        "thunderstorms": 1,
        "general thunderstorms risk": 1,
        "mrgl": 2,
        "marginal": 2,
        "marginal risk": 2,
        "slgt": 3,
        "slight": 3,
        "slight risk": 3,
        "enh": 4,
        "enhanced": 4,
        "enhanced risk": 4,
        "mdt": 5,
        "moderate": 5,
        "moderate risk": 5,
        "high": 6,
        "high risk": 6,
    }
    if label_text in label_map:
        return label_map[label_text]

    dn_map = {1: 1, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6}
    for candidate in (props.get("dn"), props.get("DN"), props.get("RISK_CODE"), props.get("risk"), props.get("RISKLVL"), props.get("LEVEL")):
        try:
            code = int(candidate)
        except (TypeError, ValueError):
            continue
        mapped = dn_map.get(code)
        if mapped in SPC_RISK_STYLE_BY_CODE:
            return mapped
    return None


def _probability_percent_from_label(value: object) -> int | None:
    text = str(value or "").strip().lower()
    if not text or text.startswith("cig"):
        return None
    try:
        numeric = float(text)
    except ValueError:
        return None
    percent = int(round(numeric * 100.0))
    return percent if percent > 0 else None


def _format_probability_hover_label(*, probability_name: str, percent: int | None, significant: bool) -> str:
    if percent is None:
        return f"{probability_name} Probability"
    return f"{percent}% {probability_name} Probability"


def _non_empty_string_property(props: dict, *keys: str) -> str | None:
    for key in keys:
        value = props.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _probability_style_from_dn(props: dict) -> tuple[int, str, str] | None:
    for candidate in (props.get("dn"), props.get("DN")):
        try:
            dn = int(candidate)
        except (TypeError, ValueError):
            continue
        return DN_PROBABILITY_STYLES.get(dn)
    return None


def _is_no_meaningful_probability_feature(feature: object) -> bool:
    if not isinstance(feature, dict):
        return True
    props = feature.get("properties")
    if not isinstance(props, dict):
        return True

    label = props.get("label")
    if label is None and "label" not in props:
        label = props.get("LABEL")

    dn = props.get("dn")
    if dn is None and "dn" not in props:
        dn = props.get("DN")

    dn_is_zero = False
    try:
        dn_is_zero = float(dn) == 0.0
    except (TypeError, ValueError):
        pass

    return (label is None and _probability_style_from_dn(props) is None) or dn_is_zero


def _legend_entries_for_frame(frame: SPCFramePayload) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for feature in frame.features:
        props = feature.get("properties") or {}
        label = str(props.get("risk_label") or "").strip()
        color = str(props.get("fill") or "").strip()
        if not label or not color:
            continue
        key = (label, color)
        if key in seen:
            continue
        seen.add(key)
        entries.append({"value": int(props.get("sort_rank") or len(entries) + 1), "color": color, "label": label})
    entries.sort(key=lambda item: int(item["value"]))
    return entries


def normalize_spc_geojson(payload: dict, *, day_label: str, fh: int) -> SPCFramePayload:
    return _normalize_spc_geojson(payload, product=SPC_CONVECTIVE_PRODUCT, day_label=day_label, fh=fh)


def _normalize_convective_geojson(payload: dict, *, product: SPCProductConfig, day_label: str, fh: int) -> SPCFramePayload:
    features_raw = payload.get("features")
    if not isinstance(features_raw, list):
        raise SPCPublishError("SPC payload is missing features")

    normalized_features: list[dict] = []
    valid_time: datetime | None = None
    issue_time: datetime | None = None
    for feature in features_raw:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        props = feature.get("properties")
        if not isinstance(props, dict):
            props = {}
        risk_code = _risk_code_from_properties(props)
        if risk_code is None:
            continue
        style = SPC_RISK_STYLE_BY_CODE[risk_code]
        normalized_features.append(
            {
                "type": "Feature",
                "properties": {
                    "risk_code": risk_code,
                    "risk_label": str(style["risk_label"]),
                    "hover_label": str(style["risk_label"]),
                    "fill": style["fill"],
                    "fill_opacity": 0.65,
                    "stroke": "#000000",
                    "stroke_width": 1.25,
                    "sort_rank": int(style["sort_rank"]),
                    "day_label": day_label,
                },
                "geometry": geometry,
            }
        )
        valid_time = valid_time or _parse_timestamp(props, "valid", "VALID", "VALID2", "VALID_TIME", "VALIDTIME", "prodValid")
        issue_time = issue_time or _parse_timestamp(props, "issue", "ISSUE", "ISSUE2", "ISSUED", "ISSUE_TIME", "productIssued")

    if not normalized_features:
        raise SPCPublishError(f"SPC {product.display_name} {day_label} payload had no recognized features")
    if valid_time is None or issue_time is None:
        raise SPCPublishError(f"SPC {product.display_name} {day_label} payload is missing issue/valid timestamps")
    normalized_features.sort(key=lambda feature: int(feature["properties"]["sort_rank"]))
    return SPCFramePayload(
        fh=int(fh),
        day_label=day_label,
        valid_time=valid_time.astimezone(timezone.utc),
        issue_time=issue_time.astimezone(timezone.utc),
        features=normalized_features,
    )


def _normalize_probability_geojson(payload: dict, *, product: SPCProductConfig, day_label: str, fh: int) -> SPCFramePayload:
    features_raw = payload.get("features")
    if not isinstance(features_raw, list):
        raise SPCPublishError("SPC payload is missing features")
    if not features_raw or all(_is_no_meaningful_probability_feature(feature) for feature in features_raw):
        raise SPCPublishError("no meaningful features (predictability too low)")

    probability_name = str(product.probability_name or product.display_name).strip() or product.display_name
    normalized_features: list[dict] = []
    valid_time: datetime | None = None
    issue_time: datetime | None = None
    for feature in features_raw:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        props = feature.get("properties")
        if not isinstance(props, dict):
            props = {}

        raw_label = props.get("label")
        if raw_label is None and "label" not in props:
            raw_label = props.get("LABEL")
        label = str(raw_label or "").strip()
        label2 = _non_empty_string_property(props, "label2", "LABEL2")
        dn_style = _probability_style_from_dn(props) if not label else None
        percent = _probability_percent_from_label(label)
        if percent is None and dn_style is not None:
            percent = dn_style[0]
        is_significant = label.upper().startswith("CIG") or "conditional intensity group" in (label2 or "").lower()
        if percent is None and not is_significant:
            continue

        sort_rank = 1000 if is_significant else int(percent or 0)
        display_label = "SIG" if is_significant else f"{percent}%"
        hover_label = label2 or _format_probability_hover_label(
            probability_name=probability_name,
            percent=percent,
            significant=is_significant,
        )
        fill = str(props.get("fill") or props.get("FILL") or "#888888")
        stroke = str(props.get("stroke") or props.get("STROKE") or "#000000")
        if dn_style is not None:
            fill = dn_style[1]
            stroke = dn_style[2]
        normalized_features.append(
            {
                "type": "Feature",
                "properties": {
                    "risk_code": sort_rank,
                    "risk_label": display_label,
                    "hover_label": hover_label,
                    "fill": fill,
                    "fill_opacity": 0.35 if is_significant else 0.65,
                    "stroke": stroke,
                    "stroke_width": 2.0 if is_significant else 1.25,
                    "sort_rank": sort_rank,
                    "day_label": day_label,
                    "is_significant": is_significant,
                },
                "geometry": geometry,
            }
        )
        valid_time = valid_time or _parse_timestamp(props, "valid", "VALID", "VALID2", "VALID_TIME", "VALIDTIME", "prodValid")
        issue_time = issue_time or _parse_timestamp(props, "issue", "ISSUE", "ISSUE2", "ISSUED", "ISSUE_TIME", "productIssued")

    if issue_time is None:
        for feature in features_raw:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties")
            if not isinstance(props, dict):
                continue
            issue_time = _parse_timestamp(props, "idp_filedate", "IDP_FILEDATE")
            if issue_time is not None:
                break
    if valid_time is None:
        valid_time = issue_time

    if not normalized_features:
        raise SPCPublishError(f"SPC {product.display_name} {day_label} payload had no recognized features")
    if valid_time is None or issue_time is None:
        raise SPCPublishError(f"SPC {product.display_name} {day_label} payload is missing issue/valid timestamps")
    normalized_features.sort(key=lambda feature: int(feature["properties"]["sort_rank"]))
    return SPCFramePayload(
        fh=int(fh),
        day_label=day_label,
        valid_time=valid_time.astimezone(timezone.utc),
        issue_time=issue_time.astimezone(timezone.utc),
        features=normalized_features,
    )


def _normalize_spc_geojson(payload: dict, *, product: SPCProductConfig, day_label: str, fh: int) -> SPCFramePayload:
    if product.probability_name:
        return _normalize_probability_geojson(payload, product=product, day_label=day_label, fh=fh)
    return _normalize_convective_geojson(payload, product=product, day_label=day_label, fh=fh)


def _build_frame_sidecar(*, run_id: str, frame: SPCFramePayload) -> dict:
    return _build_product_frame_sidecar(run_id=run_id, product=SPC_CONVECTIVE_PRODUCT, frame=frame)


def _build_product_frame_sidecar(*, run_id: str, product: SPCProductConfig, frame: SPCFramePayload) -> dict:
    return {
        "contract_version": "3.0",
        "model": SPC_MODEL_ID,
        "run": run_id,
        "var": product.var_id,
        "fh": int(frame.fh),
        "region": SPC_REGION_ID,
        "valid_time": frame.valid_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issue_time": frame.issue_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": product.kind,
        "legend_title": product.legend_title,
        "display_name": product.display_name,
        "day_label": frame.day_label,
        "legend_entries": _legend_entries_for_frame(frame),
        "vector_layers": {
            "primary": {
                "format": "geojson",
                "path": f"vectors/fh{frame.fh:03d}.geojson",
                "style_key": product.style_key,
            }
        },
    }


def publish_spc_bundle(*, data_root: Path, frames: list[SPCFramePayload], issue_time: datetime) -> SPCPublishResult:
    return publish_spc_products_bundle(
        data_root=data_root,
        products={SPC_CONVECTIVE_PRODUCT.var_id: frames},
        issue_time=issue_time,
    )


def build_spc_products_fingerprint(products: dict[str, list[SPCFramePayload]]) -> str:
    parts: list[str] = []
    for var_id in sorted(products.keys()):
        for frame in sorted(products[var_id], key=lambda item: int(item.fh)):
            issue_stamp = frame.issue_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            parts.append(f"{var_id}:{frame.fh}:{issue_stamp}:{len(frame.features)}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def publish_spc_products_bundle(
    *,
    data_root: Path,
    products: dict[str, list[SPCFramePayload]],
    issue_time: datetime,
) -> SPCPublishResult:
    if not products:
        raise SPCPublishError("SPC publish requires at least one product")

    run_id = format_run_id(issue_time.astimezone(timezone.utc), include_minutes=True)
    staging_run_root = data_root / "staging" / SPC_MODEL_ID / run_id
    if staging_run_root.exists():
        shutil.rmtree(staging_run_root, ignore_errors=True)
    staging_run_root.mkdir(parents=True, exist_ok=True)

    targets: list[tuple[str, int]] = []
    total_frames = 0
    latest_valid_time: datetime | None = None
    for var_id, frames in products.items():
        product = SPC_PRODUCT_CONFIGS[var_id]
        var_root = staging_run_root / var_id
        vector_root = var_root / "vectors"
        vector_root.mkdir(parents=True, exist_ok=True)
        for frame in sorted(frames, key=lambda item: int(item.fh)):
            total_frames += 1
            latest_valid_time = frame.valid_time if latest_valid_time is None else max(latest_valid_time, frame.valid_time)
            write_json_atomic(vector_root / f"fh{frame.fh:03d}.geojson", {"type": "FeatureCollection", "features": frame.features})
            write_json_atomic(var_root / f"fh{frame.fh:03d}.json", _build_product_frame_sidecar(run_id=run_id, product=product, frame=frame))
            targets.append((var_id, frame.fh))

    source_fingerprint = build_spc_products_fingerprint(products)
    promote_run(data_root=data_root, model=SPC_MODEL_ID, run_id=run_id)
    write_run_manifest(
        data_root=data_root,
        model=SPC_MODEL_ID,
        run_id=run_id,
        targets=targets,
        plugin=SPC_MODEL,
        metadata={
            "source": "spc",
            "source_fingerprint": source_fingerprint,
            "time_axis_mode": "valid",
            "target_frame_count": total_frames,
            "available_frame_count": total_frames,
            "latest_valid_time": (latest_valid_time or issue_time).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    write_latest_pointer(data_root=data_root, model=SPC_MODEL_ID, run_id=run_id, source="spc_publish")
    return SPCPublishResult(
        run_id=run_id,
        published_run_dir=data_root / "published" / SPC_MODEL_ID / run_id,
        manifest_path=data_root / "manifests" / SPC_MODEL_ID / f"{run_id}.json",
        frame_count=total_frames,
        variable_ids=sorted(products.keys()),
    )


def collect_latest_spc_products(
    *,
    timeout_seconds: float = 30.0,
    base_url: str = SPC_LAYER_BASE_URL,
) -> tuple[dict[str, list[SPCFramePayload]], datetime]:
    products: dict[str, list[SPCFramePayload]] = {}
    for product in SPC_PRODUCT_CONFIGS.values():
        frames: list[SPCFramePayload] = []
        for fh, (layer_id, day_label) in enumerate(product.day_layers):
            payload = fetch_spc_layer_geojson(layer_id, timeout_seconds=timeout_seconds, base_url=base_url)
            try:
                frames.append(_normalize_spc_geojson(payload, product=product, day_label=day_label, fh=fh))
            except SPCPublishError as error:
                logger.warning("Skipping SPC product day var=%s day=%s error=%s", product.var_id, day_label, error)
                continue
        if frames:
            products[product.var_id] = frames

    if not products:
        raise SPCPublishError("SPC publish failed: no products available")

    issue_time = min(frame.issue_time for frames in products.values() for frame in frames)
    return products, issue_time


def publish_latest_spc_outlooks(
    *,
    data_root: Path,
    timeout_seconds: float = 30.0,
    base_url: str = SPC_LAYER_BASE_URL,
) -> SPCPublishResult:
    products, issue_time = collect_latest_spc_products(timeout_seconds=timeout_seconds, base_url=base_url)
    return publish_spc_products_bundle(data_root=data_root, products=products, issue_time=issue_time)
