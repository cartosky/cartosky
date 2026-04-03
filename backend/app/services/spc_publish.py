from __future__ import annotations

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
SPC_VARIABLE_ID = "convective"
SPC_REGION_ID = "conus"
SPC_LAYER_BASE_URL = "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/SPC_wx_outlks/FeatureServer"
SPC_DAY_LAYERS: tuple[tuple[int, str], ...] = (
    (1, "Day 1"),
    (9, "Day 2"),
    (17, "Day 3"),
)

SPC_RISK_STYLE_BY_CODE: dict[int, dict[str, object]] = {
    1: {"risk_label": "T-Storms", "fill": "#808080", "sort_rank": 1},
    2: {"risk_label": "Marginal", "fill": "#008000", "sort_rank": 2},
    3: {"risk_label": "Slight", "fill": "#FFFF00", "sort_rank": 3},
    4: {"risk_label": "Enhanced", "fill": "#FFA500", "sort_rank": 4},
    5: {"risk_label": "Moderate", "fill": "#FF0000", "sort_rank": 5},
    6: {"risk_label": "High", "fill": "#FF00FF", "sort_rank": 6},
}

SPC_LEGEND_ENTRIES = [
    {"value": code, "color": str(style["fill"]), "label": str(style["risk_label"])}
    for code, style in sorted(SPC_RISK_STYLE_BY_CODE.items())
]


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


def fetch_spc_layer_geojson(
    layer_id: int,
    *,
    timeout_seconds: float = 30.0,
    base_url: str = SPC_LAYER_BASE_URL,
) -> dict:
    query = urlencode(
        {
            "where": "1=1",
            "outFields": "*",
            "f": "geojson",
        }
    )
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

    dn_map = {
        1: 1,
        2: 1,
        3: 2,
        4: 3,
        5: 4,
        6: 5,
        7: 6,
    }
    for candidate in (
        props.get("dn"),
        props.get("DN"),
        props.get("RISK_CODE"),
        props.get("risk"),
        props.get("RISKLVL"),
        props.get("LEVEL"),
    ):
        try:
            code = int(candidate)
        except (TypeError, ValueError):
            continue
        mapped = dn_map.get(code)
        if mapped in SPC_RISK_STYLE_BY_CODE:
            return mapped

    return None


def normalize_spc_geojson(payload: dict, *, day_label: str, fh: int) -> SPCFramePayload:
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
        valid_time = valid_time or _parse_timestamp(
            props,
            "valid",
            "VALID",
            "VALID2",
            "VALID_TIME",
            "VALIDTIME",
            "prodValid",
        )
        issue_time = issue_time or _parse_timestamp(
            props,
            "issue",
            "ISSUE",
            "ISSUE2",
            "ISSUED",
            "ISSUE_TIME",
            "productIssued",
        )

    if not normalized_features:
        raise SPCPublishError(f"SPC {day_label} payload had no recognized categorical features")
    if valid_time is None or issue_time is None:
        raise SPCPublishError(f"SPC {day_label} payload is missing issue/valid timestamps")

    normalized_features.sort(key=lambda feature: int(feature["properties"]["sort_rank"]))
    return SPCFramePayload(
        fh=int(fh),
        day_label=day_label,
        valid_time=valid_time.astimezone(timezone.utc),
        issue_time=issue_time.astimezone(timezone.utc),
        features=normalized_features,
    )


def _build_frame_sidecar(*, run_id: str, frame: SPCFramePayload) -> dict:
    return {
        "contract_version": "3.0",
        "model": SPC_MODEL_ID,
        "run": run_id,
        "var": SPC_VARIABLE_ID,
        "fh": int(frame.fh),
        "region": SPC_REGION_ID,
        "valid_time": frame.valid_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "categorical",
        "legend_title": "Severe Storm Outlook",
        "display_name": "SPC Convective Outlook",
        "day_label": frame.day_label,
        "legend_entries": SPC_LEGEND_ENTRIES,
        "vector_layers": {
            "primary": {
                "format": "geojson",
                "path": f"vectors/fh{frame.fh:03d}.geojson",
                "style_key": "spc_convective",
            }
        },
    }


def publish_spc_bundle(
    *,
    data_root: Path,
    frames: list[SPCFramePayload],
    issue_time: datetime,
) -> SPCPublishResult:
    if not frames:
        raise SPCPublishError("SPC publish requires at least one frame")

    run_id = format_run_id(issue_time.astimezone(timezone.utc), include_minutes=True)
    staging_run_root = data_root / "staging" / SPC_MODEL_ID / run_id
    if staging_run_root.exists():
        shutil.rmtree(staging_run_root, ignore_errors=True)
    var_root = staging_run_root / SPC_VARIABLE_ID
    vector_root = var_root / "vectors"
    vector_root.mkdir(parents=True, exist_ok=True)

    ordered_frames = sorted(frames, key=lambda item: int(item.fh))
    for frame in ordered_frames:
        write_json_atomic(
            vector_root / f"fh{frame.fh:03d}.geojson",
            {"type": "FeatureCollection", "features": frame.features},
        )
        write_json_atomic(var_root / f"fh{frame.fh:03d}.json", _build_frame_sidecar(run_id=run_id, frame=frame))

    promote_run(data_root=data_root, model=SPC_MODEL_ID, run_id=run_id)
    write_run_manifest(
        data_root=data_root,
        model=SPC_MODEL_ID,
        run_id=run_id,
        targets=[(SPC_VARIABLE_ID, frame.fh) for frame in ordered_frames],
        plugin=SPC_MODEL,
        metadata={
            "source": "spc",
            "time_axis_mode": "valid",
            "target_frame_count": len(ordered_frames),
            "available_frame_count": len(ordered_frames),
            "latest_valid_time": max(frame.valid_time for frame in ordered_frames).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    write_latest_pointer(data_root=data_root, model=SPC_MODEL_ID, run_id=run_id, source="spc_publish")

    return SPCPublishResult(
        run_id=run_id,
        published_run_dir=data_root / "published" / SPC_MODEL_ID / run_id,
        manifest_path=data_root / "manifests" / SPC_MODEL_ID / f"{run_id}.json",
        frame_count=len(ordered_frames),
    )


def publish_latest_spc_outlooks(
    *,
    data_root: Path,
    timeout_seconds: float = 30.0,
    base_url: str = SPC_LAYER_BASE_URL,
) -> SPCPublishResult:
    frames: list[SPCFramePayload] = []
    for fh, (layer_id, day_label) in enumerate(SPC_DAY_LAYERS):
        payload = fetch_spc_layer_geojson(layer_id, timeout_seconds=timeout_seconds, base_url=base_url)
        frames.append(normalize_spc_geojson(payload, day_label=day_label, fh=fh))

    issue_time = min(frame.issue_time for frame in frames)
    return publish_spc_bundle(data_root=data_root, frames=frames, issue_time=issue_time)
