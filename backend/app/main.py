"""CartoSky API — canonical discovery + sampling endpoints."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import re
import secrets
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import numpy as np
import rasterio
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from opentelemetry.trace import SpanKind
from PIL import Image, ImageFilter
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import Window
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from .config.regions import REGION_PRESETS
from .models.registry import list_model_capabilities
from .models.registry import get_model
from .models.serialization import (
    serialize_model_capability,
    serialize_variable_capability,
)
from .services.observed_bundle_health import build_observed_bundle_health, is_observed_model_capability
from .services.boundary_tiles import (
    BOUNDARIES_MBTILES,
    BOUNDARY_CACHE_HIT,
    BOUNDARY_CACHE_MISS,
    build_boundaries_tilejson,
    empty_mvt_response,
    lookup_mbtiles_tile,
)
from .services.builder.colorize import float_to_rgba
from .services.grid import (
    expected_grid_frame_size_bytes,
    grid_frame_path,
    grid_manifest_path,
    grid_supported,
)
from .services.render_resampling import (
    allow_high_quality_loop_resampling,
    compute_loop_output_shape,
    high_quality_loop_resampling,
    log_fixed_loop_size_once,
    loop_fixed_width_for_tier,
    loop_max_dim_for_tier,
    loop_quality_for_tier,
    loop_webp_save_kwargs,
    rasterio_resampling_for_loop,
    use_value_render_for_variable,
    variable_kind,
    variable_color_map_id,
)
from .services.run_ids import RUN_ID_RE, parse_run_id_datetime, run_id_hour
from .services import admin_telemetry, feedback_service, forecast_page as forecast_page_service, otel_tracing, prometheus_metrics, share_media as share_media_service
from .services import nws as nws_service
from backend.app import config as app_config
from backend.app.auth.clerk import ClerkPrincipal, fetch_clerk_user_profile, require_clerk_admin, require_clerk_user
from backend.app.auth import twf_oauth

logger = logging.getLogger(__name__)

def _env_value(*names: str, default: str = "") -> str:
    for name in names:
        raw = os.environ.get(name)
        if raw is not None and raw != "":
            return raw
    return default


def _normalized_path_prefix(value: str, *, default: str) -> str:
    raw = (value or default).strip()
    if not raw:
        raw = default
    return f"/{raw.strip('/')}/"


DATA_ROOT = Path(_env_value("CARTOSKY_DATA_ROOT", "CARTOSKY_V3_DATA_ROOT", "TWF_V3_DATA_ROOT", default="./data"))
nws_service.configure_data_root(DATA_ROOT)
forecast_page_service.configure_data_root(DATA_ROOT)
PUBLISHED_ROOT = DATA_ROOT / "published"
MANIFESTS_ROOT = DATA_ROOT / "manifests"
LOOP_CACHE_ROOT = Path(
    _env_value(
        "CARTOSKY_LOOP_CACHE_ROOT",
        "CARTOSKY_V3_LOOP_CACHE_ROOT",
        "TWF_V3_LOOP_CACHE_ROOT",
        default=str(DATA_ROOT / "loop_cache"),
    )
)
LOOP_URL_PREFIX = _normalized_path_prefix(
    _env_value("CARTOSKY_LOOP_URL_PREFIX", "CARTOSKY_V3_LOOP_URL_PREFIX", "TWF_V3_LOOP_URL_PREFIX", default="/loop/"),
    default="/loop/",
)
CAPABILITIES_CONTRACT_VERSION = "v2"
_JSON_CACHE_RECHECK_SECONDS = float(
    _env_value(
        "CARTOSKY_JSON_CACHE_RECHECK_SECONDS",
        "CARTOSKY_V3_JSON_CACHE_RECHECK_SECONDS",
        "TWF_V3_JSON_CACHE_RECHECK_SECONDS",
        default="1.0",
    )
)


def _env_bool(*names: str, default: bool) -> bool:
    raw = _env_value(*names).strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    logger.warning("Invalid %s=%r; using fallback=%s", "/".join(names), raw, default)
    return default


def _env_int(*names: str, default: int, min_value: int = 0) -> int:
    raw = _env_value(*names).strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using fallback=%d", "/".join(names), raw, default)
        return default
    return parsed if parsed >= min_value else default


def _env_float(*names: str, default: float, min_value: float = 0.0) -> float:
    raw = _env_value(*names).strip()
    if not raw:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using fallback=%s", "/".join(names), raw, default)
        return default
    return parsed if parsed >= min_value else default


GRID_ACCEL_REDIRECT_ENABLED = _env_bool("CARTOSKY_GRID_ACCEL_REDIRECT_ENABLED", default=False)
GRID_ACCEL_REDIRECT_PREFIX = _normalized_path_prefix(
    _env_value("CARTOSKY_GRID_ACCEL_REDIRECT_PREFIX", default="/_cartosky_grid_internal/"),
    default="/_cartosky_grid_internal/",
)


def _legacy_telemetry_write_enabled() -> bool:
    return _env_bool("CARTOSKY_LEGACY_TELEMETRY_WRITE_ENABLED", default=True)


LOOP_WEBP_QUALITY = int(
    _env_value("CARTOSKY_LOOP_WEBP_QUALITY", "CARTOSKY_V3_LOOP_WEBP_QUALITY", "TWF_V3_LOOP_WEBP_QUALITY", default="82")
)
LOOP_WEBP_MAX_DIM = int(
    _env_value("CARTOSKY_LOOP_WEBP_MAX_DIM", "CARTOSKY_V3_LOOP_WEBP_MAX_DIM", "TWF_V3_LOOP_WEBP_MAX_DIM", default="2300")
)
LOOP_WEBP_TIER1_QUALITY = int(
    _env_value(
        "CARTOSKY_LOOP_WEBP_TIER1_QUALITY",
        "CARTOSKY_V3_LOOP_WEBP_TIER1_QUALITY",
        "TWF_V3_LOOP_WEBP_TIER1_QUALITY",
        default="86",
    )
)
LOOP_WEBP_TIER1_MAX_DIM = int(
    _env_value(
        "CARTOSKY_LOOP_WEBP_TIER1_MAX_DIM",
        "CARTOSKY_V3_LOOP_WEBP_TIER1_MAX_DIM",
        "TWF_V3_LOOP_WEBP_TIER1_MAX_DIM",
        default="2400",
    )
)
LOOP_WEBP_TIER0_FIXED_W = int(
    _env_value(
        "CARTOSKY_LOOP_WEBP_TIER0_FIXED_W",
        "CARTOSKY_V3_LOOP_WEBP_TIER0_FIXED_W",
        "TWF_V3_LOOP_WEBP_TIER0_FIXED_W",
        default="2300",
    )
)
LOOP_WEBP_TIER1_FIXED_W = int(
    _env_value(
        "CARTOSKY_LOOP_WEBP_TIER1_FIXED_W",
        "CARTOSKY_V3_LOOP_WEBP_TIER1_FIXED_W",
        "TWF_V3_LOOP_WEBP_TIER1_FIXED_W",
        default="2400",
    )
)
LOOP_SHARPEN_ENABLE = _env_bool(
    "CARTOSKY_LOOP_SHARPEN_ENABLE",
    "CARTOSKY_V3_LOOP_SHARPEN_ENABLE",
    "TWF_V3_LOOP_SHARPEN_ENABLE",
    default=True,
)
LOOP_SHARPEN_RADIUS = _env_float(
    "CARTOSKY_LOOP_SHARPEN_RADIUS",
    "CARTOSKY_V3_LOOP_SHARPEN_RADIUS",
    "TWF_V3_LOOP_SHARPEN_RADIUS",
    default=1.2,
    min_value=0.0,
)
LOOP_SHARPEN_PERCENT = _env_int(
    "CARTOSKY_LOOP_SHARPEN_PERCENT",
    "CARTOSKY_V3_LOOP_SHARPEN_PERCENT",
    "TWF_V3_LOOP_SHARPEN_PERCENT",
    default=35,
    min_value=0,
)
LOOP_SHARPEN_THRESHOLD = _env_int(
    "CARTOSKY_LOOP_SHARPEN_THRESHOLD",
    "CARTOSKY_V3_LOOP_SHARPEN_THRESHOLD",
    "TWF_V3_LOOP_SHARPEN_THRESHOLD",
    default=3,
    min_value=0,
)
SAMPLE_CACHE_TTL_SECONDS = float(
    _env_value(
        "CARTOSKY_SAMPLE_CACHE_TTL_SECONDS",
        "CARTOSKY_V3_SAMPLE_CACHE_TTL_SECONDS",
        "TWF_V3_SAMPLE_CACHE_TTL_SECONDS",
        default="2.0",
    )
)
SAMPLE_INFLIGHT_WAIT_SECONDS = float(
    _env_value(
        "CARTOSKY_SAMPLE_INFLIGHT_WAIT_SECONDS",
        "CARTOSKY_V3_SAMPLE_INFLIGHT_WAIT_SECONDS",
        "TWF_V3_SAMPLE_INFLIGHT_WAIT_SECONDS",
        default="0.2",
    )
)
SAMPLE_RATE_LIMIT_WINDOW_SECONDS = float(
    _env_value(
        "CARTOSKY_SAMPLE_RATE_LIMIT_WINDOW_SECONDS",
        "CARTOSKY_V3_SAMPLE_RATE_LIMIT_WINDOW_SECONDS",
        "TWF_V3_SAMPLE_RATE_LIMIT_WINDOW_SECONDS",
        default="1.0",
    )
)
SAMPLE_RATE_LIMIT_MAX_REQUESTS = int(
    _env_value(
        "CARTOSKY_SAMPLE_RATE_LIMIT_MAX_REQUESTS",
        "CARTOSKY_V3_SAMPLE_RATE_LIMIT_MAX_REQUESTS",
        "TWF_V3_SAMPLE_RATE_LIMIT_MAX_REQUESTS",
        default="240",
    )
)

LOOP_TIER_CONFIG: dict[int, dict[str, int]] = {
    0: {
        "max_dim": LOOP_WEBP_MAX_DIM,
        "quality": LOOP_WEBP_QUALITY,
        "fixed_w": LOOP_WEBP_TIER0_FIXED_W,
    },
}

CACHE_HIT = "public, max-age=31536000, immutable"
CACHE_MISS = "public, max-age=15"
_TWF_SHARE_BODY_CAP_BYTES = 16 * 1024
_TWF_RATE_WINDOW_SECONDS = 60.0
_TWF_IP_LIMIT = 20
_TWF_SESSION_LIMIT = 10
_TWF_RATE_LIMIT_MESSAGE = "Too many requests. Try again shortly."
_TWF_RATE_LIMIT_PATHS = {"/twf/share/topic", "/twf/share/post"}
_TWF_GUARDED_PATHS = _TWF_RATE_LIMIT_PATHS
_TWF_ERROR_PATHS = {
    "/api/v4/feedback",
    "/auth/twf/status",
    "/auth/twf/disconnect",
    "/twf/forums",
    "/twf/topics",
    "/twf/share/topic",
    "/twf/share/post",
}
_TWF_RATE_PRUNE_INTERVAL_SECONDS = 60.0
_ADMIN_WINDOW_SECONDS = {
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
}


def _parse_admin_member_ids(raw: str) -> set[int]:
    member_ids: set[int] = set()
    for part in raw.split(","):
        trimmed = part.strip()
        if not trimmed:
            continue
        try:
            member_ids.add(int(trimmed))
        except ValueError:
            logger.warning("Skipping invalid CARTOSKY_ADMIN_MEMBER_IDS/TWM_ADMIN_MEMBER_IDS entry %r", trimmed)
    return member_ids


ADMIN_MEMBER_IDS = _parse_admin_member_ids(_env_value("CARTOSKY_ADMIN_MEMBER_IDS", "TWM_ADMIN_MEMBER_IDS"))

_twf_rate_lock = threading.Lock()
_twf_ip_windows: dict[str, deque[float]] = {}
_twf_session_windows: dict[str, deque[float]] = {}
_twf_last_prune_monotonic = 0.0
_LOOP_REQUEST_SOURCE_LOG_EVERY = 100
_loop_request_counter_lock = threading.Lock()
_loop_request_source_totals: dict[str, int] = {"cache": 0, "generated": 0, "rendered": 0}
_loop_request_source_by_target: dict[str, dict[tuple[str, str, int], int]] = {
    "cache": {},
    "generated": {},
    "rendered": {},
}


def _record_loop_request_source(source: str, *, model: str, var: str, tier: int) -> None:
    if source not in _loop_request_source_totals:
        return
    with _loop_request_counter_lock:
        _loop_request_source_totals[source] = _loop_request_source_totals.get(source, 0) + 1
        key = (str(model or "").strip().lower(), str(var or "").strip().lower(), int(tier))
        per_source = _loop_request_source_by_target.setdefault(source, {})
        per_source[key] = per_source.get(key, 0) + 1

        total = sum(_loop_request_source_totals.values())
        if total <= 0 or total % _LOOP_REQUEST_SOURCE_LOG_EVERY != 0:
            return

        top_targets = {
            current_source: sorted(entries.items(), key=lambda item: item[1], reverse=True)[:4]
            for current_source, entries in _loop_request_source_by_target.items()
        }

    logger.info(
        "Loop WebP request sources total=%d cache=%d generated=%d rendered=%d top_targets=%s",
        total,
        _loop_request_source_totals.get("cache", 0),
        _loop_request_source_totals.get("generated", 0),
        _loop_request_source_totals.get("rendered", 0),
        top_targets,
    )


def _frames_cache_control(run: str, *, run_complete: bool) -> str:
    if run == "latest" or not run_complete:
        return "public, max-age=60"
    return "public, max-age=31536000, immutable"


def _if_none_match_values(header_value: str) -> list[str]:
    return [v.strip() for v in header_value.split(",") if v.strip()]


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    vals = _if_none_match_values(if_none_match)
    if "*" in vals:
        return True
    return etag in vals


def _make_etag(payload: object) -> str:
    digest = hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return f'"{digest}"'


def _make_etag_from_parts(*parts: object) -> str:
    digest = hashlib.md5(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return f'"{digest}"'


def _maybe_304(request: Request, *, etag: str, cache_control: str) -> Response | None:
    inm = request.headers.get("if-none-match")
    if _etag_matches(inm, etag):
        return Response(
            status_code=304,
            headers={
                "ETag": etag,
                "Cache-Control": cache_control,
            },
        )
    return None


def _format_server_timing(metrics: list[tuple[str, float]]) -> str:
    parts: list[str] = []
    for name, duration_ms in metrics:
        safe_duration = max(0.0, float(duration_ms))
        parts.append(f"{name};dur={safe_duration:.1f}")
    return ", ".join(parts)


def _origin_from_url(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def _cors_origin_aliases(origin: str) -> set[str]:
    parsed = urlsplit(origin)
    host = parsed.hostname
    if not host:
        return {origin}

    aliases = {origin.rstrip("/")}
    if host in {"localhost", "127.0.0.1"}:
        return aliases
    if host.startswith("www."):
        alias_host = host[4:]
    elif host.count(".") == 1:
        alias_host = f"www.{host}"
    else:
        return aliases

    alias_netloc = alias_host
    if parsed.port is not None:
        alias_netloc = f"{alias_host}:{parsed.port}"
    aliases.add(urlunsplit((parsed.scheme, alias_netloc, "", "", "")).rstrip("/"))
    return aliases


def _resolve_cors_origins(raw_origins: str | None, frontend_return: str | None) -> list[str]:
    configured_raw = False
    configured: set[str] = set()
    for origin in (raw_origins or "").split(","):
        normalized = _origin_from_url(origin) or origin.strip().rstrip("/")
        if not normalized:
            continue
        configured_raw = True
        configured.update(_cors_origin_aliases(normalized))

    frontend_origin = _origin_from_url(frontend_return)
    if frontend_origin:
        configured.update(_cors_origin_aliases(frontend_origin))

    if configured_raw and configured:
        return sorted(configured)

    fallback_origins = {
        "http://127.0.0.1:4173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://localhost:5173",
    }
    if frontend_origin:
        fallback_origins.update(_cors_origin_aliases(frontend_origin))
    return sorted(fallback_origins)


app = FastAPI(title="CartoSky API", version="4.0.0")

origins = _resolve_cors_origins(os.getenv("CORS_ORIGINS"), twf_oauth.FRONTEND_RETURN)
cors_allow_headers = [
    "Accept",
    "Accept-Language",
    "Content-Language",
    "Content-Type",
    "If-None-Match",
    "Origin",
    "Authorization",
    "X-Requested-With",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=cors_allow_headers,
)
# GZip-compress responses >= 1 KB.  Critical for grid binary frames (.u16.bin)
# which are ~72 MB raw but highly compressible (mostly nodata sentinels).
# Starlette automatically skips responses that already carry Content-Encoding
# (e.g. pre-compressed boundary MVT tiles), so no interference with existing
# endpoints.  Added *after* CORSMiddleware so it wraps the outermost layer.
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)


def _prometheus_route_label(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str) and route_path.strip():
        return route_path
    raw_path = request.url.path
    if raw_path.startswith("/api/v4/"):
        return "unmatched_api"
    return raw_path


def _append_exposed_headers(response: Response, *header_names: str) -> None:
    existing = response.headers.get("Access-Control-Expose-Headers", "")
    values = {item.strip() for item in existing.split(",") if item.strip()}
    values.update(name for name in header_names if name)
    if values:
        response.headers["Access-Control-Expose-Headers"] = ", ".join(sorted(values))


def _append_default_exposed_headers(response: Response) -> None:
    _append_exposed_headers(
        response,
        "CF-Cache-Status",
        "Server-Timing",
        "X-Request-ID",
        "X-Trace-ID",
        "Content-Length",
        "Content-Encoding",
        "ETag",
        "Accept-Ranges",
        "Cache-Control",
    )

@dataclass
class TwfApiError(Exception):
    status_code: int
    code: str
    message: str
    upstream_status: int | None = None
    upstream_code: str | None = None
    upstream_message: str | None = None


def _error_payload(
    *,
    code: str,
    message: str,
    upstream_status: int | None = None,
    upstream_code: str | None = None,
    upstream_message: str | None = None,
    upstream_url: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if upstream_status is not None:
        payload["upstream_status"] = upstream_status
    if upstream_code is not None:
        payload["upstream_code"] = upstream_code
    if upstream_message is not None:
        payload["upstream_message"] = upstream_message
    if upstream_url is not None:
        payload["upstream_url"] = upstream_url
    return {"error": payload}


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    upstream_status: int | None = None,
    upstream_code: str | None = None,
    upstream_message: str | None = None,
    upstream_url: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=_error_payload(
            code=code,
            message=message,
            upstream_status=upstream_status,
            upstream_code=upstream_code,
            upstream_message=upstream_message,
            upstream_url=upstream_url,
        ),
        headers=headers,
    )


def _validation_message(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Invalid request payload."
    first = errors[0]
    msg = first.get("msg")
    if isinstance(msg, str) and msg.strip():
        return msg
    return "Invalid request payload."


def _rate_limit_check(
    bucket: dict[str, deque[float]],
    *,
    key: str,
    limit: int,
    window_seconds: float,
    now: float,
) -> int:
    timestamps = bucket.setdefault(key, deque())
    cutoff = now - window_seconds
    while timestamps and timestamps[0] <= cutoff:
        timestamps.popleft()
    if len(timestamps) >= limit:
        retry_after = max(1, int(math.ceil(window_seconds - (now - timestamps[0]))))
        return retry_after
    timestamps.append(now)
    return 0


def _prune_rate_limit_bucket(
    bucket: dict[str, deque[float]],
    *,
    cutoff: float,
) -> None:
    to_delete: list[str] = []
    for key, timestamps in bucket.items():
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()
        if not timestamps:
            to_delete.append(key)
    for key in to_delete:
        bucket.pop(key, None)


def _maybe_prune_rate_limit_state(now: float) -> None:
    global _twf_last_prune_monotonic
    if now - _twf_last_prune_monotonic < _TWF_RATE_PRUNE_INTERVAL_SECONDS:
        return
    cutoff = now - _TWF_RATE_WINDOW_SECONDS
    _prune_rate_limit_bucket(_twf_ip_windows, cutoff=cutoff)
    _prune_rate_limit_bucket(_twf_session_windows, cutoff=cutoff)
    _twf_last_prune_monotonic = now


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _observe_prometheus_request(request: Request, *, status_code: int, duration_seconds: float) -> None:
    if not prometheus_metrics.prometheus_enabled():
        return
    route = _prometheus_route_label(request)
    # Exclude Prometheus scrape traffic from the API request summary and dashboards.
    if route == "/metrics":
        return
    prometheus_metrics.observe_http_request(
        route=route,
        method=request.method,
        status_code=status_code,
        duration_seconds=duration_seconds,
    )


@app.middleware("http")
async def twf_share_guards(request: Request, call_next):
    request_id = secrets.token_hex(8)
    request.state.request_id = request_id
    request_started_at = time.perf_counter()
    trace_span_cm = otel_tracing.start_as_current_span(
        f"{request.method} {request.url.path}",
        kind=SpanKind.SERVER,
        attributes={
            "cartosky.trace.root": True,
            "cartosky.request_id": request_id,
            "http.method": request.method,
            "url.path": request.url.path,
        },
    )

    with trace_span_cm as trace_span:
        request.state.trace_id = otel_tracing.current_trace_id()

        if request.method == "POST" and request.url.path in _TWF_GUARDED_PATHS:
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > _TWF_SHARE_BODY_CAP_BYTES:
                        logger.warning(
                            "TWF payload too large request_id=%s path=%s method=%s ip=%s has_session=%s content_length=%s",
                            request_id,
                            request.url.path,
                            request.method,
                            _client_ip(request),
                            bool(request.headers.get("authorization")),
                            content_length,
                        )
                        response = _error_response(
                            status_code=413,
                            code="PAYLOAD_TOO_LARGE",
                            message="Request body too large",
                        )
                        route = _prometheus_route_label(request)
                        response.headers["X-Request-ID"] = request_id
                        if request.state.trace_id:
                            response.headers["X-Trace-ID"] = request.state.trace_id
                        _append_default_exposed_headers(response)
                        otel_tracing.finalize_request_span(
                            trace_span,
                            route=route,
                            status_code=response.status_code,
                            duration_seconds=time.perf_counter() - request_started_at,
                            request_id=request_id,
                        )
                        _observe_prometheus_request(
                            request,
                            status_code=response.status_code,
                            duration_seconds=time.perf_counter() - request_started_at,
                        )
                        return response
                except ValueError:
                    pass

            body = await request.body()
            buffered_body = body

            async def receive() -> dict[str, Any]:
                nonlocal buffered_body
                chunk = buffered_body
                buffered_body = b""
                return {"type": "http.request", "body": chunk, "more_body": False}

            request = Request(request.scope, receive)
            request._body = body
            request.state.request_id = request_id
            request.state.trace_id = otel_tracing.current_trace_id()
            if len(body) > _TWF_SHARE_BODY_CAP_BYTES:
                logger.warning(
                    "TWF payload too large request_id=%s path=%s method=%s ip=%s has_session=%s body_bytes=%s",
                    request_id,
                    request.url.path,
                    request.method,
                    _client_ip(request),
                    bool(request.headers.get("authorization")),
                    len(body),
                )
                response = _error_response(
                    status_code=413,
                    code="PAYLOAD_TOO_LARGE",
                    message="Request body too large",
                )
                route = _prometheus_route_label(request)
                response.headers["X-Request-ID"] = request_id
                if request.state.trace_id:
                    response.headers["X-Trace-ID"] = request.state.trace_id
                _append_default_exposed_headers(response)
                otel_tracing.finalize_request_span(
                    trace_span,
                    route=route,
                    status_code=response.status_code,
                    duration_seconds=time.perf_counter() - request_started_at,
                    request_id=request_id,
                )
                _observe_prometheus_request(
                    request,
                    status_code=response.status_code,
                    duration_seconds=time.perf_counter() - request_started_at,
                )
                return response

            now = time.monotonic()
            ip = _client_ip(request)
            authorization = request.headers.get("authorization", "").strip()
            auth_key = hashlib.sha256(authorization.encode("utf-8")).hexdigest() if authorization else ""
            retry_after = 0
            with _twf_rate_lock:
                _maybe_prune_rate_limit_state(now)
                retry_after = _rate_limit_check(
                    _twf_ip_windows,
                    key=ip,
                    limit=_TWF_IP_LIMIT,
                    window_seconds=_TWF_RATE_WINDOW_SECONDS,
                    now=now,
                )
                if retry_after == 0 and auth_key:
                    retry_after = _rate_limit_check(
                        _twf_session_windows,
                        key=auth_key,
                        limit=_TWF_SESSION_LIMIT,
                        window_seconds=_TWF_RATE_WINDOW_SECONDS,
                        now=now,
                    )
            if retry_after > 0:
                logger.warning(
                    "TWF rate limit exceeded request_id=%s path=%s ip=%s has_session=%s retry_after=%s",
                    request_id,
                    request.url.path,
                    ip,
                    bool(auth_key),
                    retry_after,
                )
                response = _error_response(
                    status_code=429,
                    code="RATE_LIMITED",
                    message=_TWF_RATE_LIMIT_MESSAGE,
                    headers={"Retry-After": str(retry_after)},
                )
                route = _prometheus_route_label(request)
                response.headers["X-Request-ID"] = request_id
                if request.state.trace_id:
                    response.headers["X-Trace-ID"] = request.state.trace_id
                _append_default_exposed_headers(response)
                otel_tracing.finalize_request_span(
                    trace_span,
                    route=route,
                    status_code=response.status_code,
                    duration_seconds=time.perf_counter() - request_started_at,
                    request_id=request_id,
                )
                _observe_prometheus_request(
                    request,
                    status_code=response.status_code,
                    duration_seconds=time.perf_counter() - request_started_at,
                )
                return response

        try:
            response = await call_next(request)
        except Exception as exc:
            route = _prometheus_route_label(request)
            otel_tracing.finalize_request_span(
                trace_span,
                route=route,
                status_code=500,
                duration_seconds=time.perf_counter() - request_started_at,
                request_id=request_id,
                error=exc,
            )
            _observe_prometheus_request(
                request,
                status_code=500,
                duration_seconds=time.perf_counter() - request_started_at,
            )
            raise
        route = _prometheus_route_label(request)
        response.headers["X-Request-ID"] = request_id
        if request.state.trace_id:
            response.headers["X-Trace-ID"] = request.state.trace_id
        _append_default_exposed_headers(response)
        otel_tracing.finalize_request_span(
            trace_span,
            route=route,
            status_code=response.status_code,
            duration_seconds=time.perf_counter() - request_started_at,
            request_id=request_id,
        )
        _observe_prometheus_request(
            request,
            status_code=response.status_code,
            duration_seconds=time.perf_counter() - request_started_at,
        )
        return response


@app.exception_handler(twf_oauth.TwfUpstreamError)
async def twf_upstream_error_handler(request: Request, exc: twf_oauth.TwfUpstreamError) -> JSONResponse:
    rid = getattr(request.state, "request_id", None)
    logger.warning(
        "TWF upstream error request_id=%s path=%s method=%s ip=%s has_auth=%s error_code=%s upstream_status=%s upstream_code=%s upstream_message=%s upstream_url=%s status_code=%s",
        rid,
        request.url.path,
        request.method,
        _client_ip(request),
        bool(request.headers.get("authorization")),
        exc.code,
        exc.upstream_status,
        exc.upstream_code,
        exc.upstream_message,
        exc.upstream_url,
        exc.status_code,
        extra={
            "request_id": rid,
            "path": request.url.path,
            "method": request.method,
            "ip": _client_ip(request),
            "has_auth": bool(request.headers.get("authorization")),
            "error_code": exc.code,
            "upstream_status": exc.upstream_status,
            "upstream_code": exc.upstream_code,
            "upstream_message": exc.upstream_message,
            "upstream_url": exc.upstream_url,
            "status_code": exc.status_code,
        },
    )
    return _error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        upstream_status=exc.upstream_status,
        upstream_code=exc.upstream_code,
        upstream_message=exc.upstream_message,
        upstream_url=exc.upstream_url,
    )


@app.exception_handler(TwfApiError)
async def twf_api_error_handler(request: Request, exc: TwfApiError) -> JSONResponse:
    rid = getattr(request.state, "request_id", None)
    logger.warning(
        "TWF API error request_id=%s path=%s method=%s ip=%s has_auth=%s error_code=%s status_code=%s",
        rid,
        request.url.path,
        request.method,
        _client_ip(request),
        bool(request.headers.get("authorization")),
        exc.code,
        exc.status_code,
    )
    return _error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        upstream_status=exc.upstream_status,
        upstream_code=exc.upstream_code,
        upstream_message=exc.upstream_message,
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    if request.url.path in _TWF_ERROR_PATHS:
        return _error_response(
            status_code=400,
            code="TWF_VALIDATION_ERROR",
            message=_validation_message(exc),
        )
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if request.url.path in _TWF_ERROR_PATHS:
        detail = exc.detail
        if isinstance(detail, dict):
            return _error_response(
                status_code=exc.status_code,
                code=str(detail.get("code") or "HTTP_ERROR"),
                message=str(detail.get("message") or "Request failed."),
            )
        if isinstance(detail, str):
            return _error_response(
                status_code=exc.status_code,
                code="HTTP_ERROR",
                message=detail,
            )
        return _error_response(
            status_code=exc.status_code,
            code="HTTP_ERROR",
            message="Request failed.",
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled server exception")
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "Unexpected server error"}},
    )

def _require_twf_session(current_user: ClerkPrincipal) -> twf_oauth.TwfSession:
    """Load the linked The Weather Forums OAuth session for the Clerk-authenticated user."""
    sess = twf_oauth.get_session_for_clerk_user(current_user.user_id)
    if not sess:
        raise TwfApiError(
            status_code=401,
            code="TWF_SESSION_NOT_FOUND",
            message="Session not found",
        )
    return sess


def _maybe_twf_session(current_user: ClerkPrincipal | Request | None) -> twf_oauth.TwfSession | None:
    if isinstance(current_user, Request):
        sid = current_user.cookies.get(twf_oauth.SESSION_COOKIE_NAME)
        return twf_oauth.get_session(sid) if sid else None
    if not isinstance(current_user, ClerkPrincipal):
        return None
    return twf_oauth.get_session_for_clerk_user(current_user.user_id)


def _is_admin_member(member_id: int) -> bool:
    return member_id in ADMIN_MEMBER_IDS


def _require_admin_session(current_user: ClerkPrincipal) -> twf_oauth.TwfSession:
    sess = _require_twf_session(current_user)
    if not _is_admin_member(sess.member_id):
        raise TwfApiError(
            status_code=403,
            code="TWF_ADMIN_REQUIRED",
            message="Admin access required",
        )
    return sess


def _require_legacy_admin_session(request: Request) -> twf_oauth.TwfSession:
    sid = request.cookies.get(twf_oauth.SESSION_COOKIE_NAME)
    if not sid:
        raise TwfApiError(status_code=401, code="TWF_NOT_LOGGED_IN", message="Not logged in")
    sess = twf_oauth.get_session(sid)
    if not sess:
        raise TwfApiError(status_code=401, code="TWF_SESSION_NOT_FOUND", message="Session not found")
    if not _is_admin_member(sess.member_id):
        raise TwfApiError(status_code=403, code="TWF_ADMIN_REQUIRED", message="Admin access required")
    return sess


async def _require_admin_identity(request: Request) -> ClerkPrincipal | twf_oauth.TwfSession:
    if app_config.clerk_auth_enabled():
        return await require_clerk_admin(request)
    return _require_legacy_admin_session(request)


@app.get("/api/v4/auth/me")
async def clerk_auth_me(current_user: ClerkPrincipal = Depends(require_clerk_user)) -> dict[str, Any]:
    return {
        "user_id": current_user.user_id,
        "role": current_user.role,
        "is_admin": current_user.is_admin,
    }


def _resolve_window_seconds(window: str) -> int:
    normalized = window.strip().lower()
    if normalized not in _ADMIN_WINDOW_SECONDS:
        raise TwfApiError(
            status_code=400,
            code="INVALID_WINDOW",
            message="Window must be one of: 24h, 7d, 30d.",
        )
    return _ADMIN_WINDOW_SECONDS[normalized]


def _resolve_bucket(window: str, bucket: str) -> str:
    normalized = bucket.strip().lower()
    if normalized == "auto":
        return "hour" if window in {"24h", "7d"} else "day"
    if normalized not in {"hour", "day"}:
        raise TwfApiError(
            status_code=400,
            code="INVALID_BUCKET",
            message="Bucket must be one of: auto, hour, day.",
        )
    return normalized


def _normalize_filter_value(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed or trimmed.lower() == "all":
        return None
    return trimmed


def _share_media_error_response(*, status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
            }
        },
    )

# ----------------------------
# TWF OAuth + Share Routes
# ----------------------------

# NOTE: add these imports near your other imports if you don't already have them:
# from pydantic import BaseModel, Field


def _sanitize_twf_return_to(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed or not trimmed.startswith("/") or trimmed.startswith("//"):
        return None
    parsed = urlsplit(trimmed)
    if parsed.scheme or parsed.netloc:
        return None
    return trimmed


def _twf_frontend_redirect_url(return_to: str | None, **params: str) -> str:
    fallback = urlsplit(twf_oauth.FRONTEND_RETURN)
    target_path = _sanitize_twf_return_to(return_to) or fallback.path or "/"
    existing_params = dict(parse_qsl(fallback.query, keep_blank_values=True)) if target_path == fallback.path else {}
    existing_params.update({key: value for key, value in params.items() if value})
    return urlunsplit((fallback.scheme, fallback.netloc, target_path, urlencode(existing_params), ""))


@app.get("/auth/twf/start")
async def twf_start(
    request: Request,
    return_to: str | None = None,
    current_user: ClerkPrincipal = Depends(require_clerk_user),
) -> Response:
    state = secrets.token_urlsafe(24)
    verifier, challenge = twf_oauth.pkce_pair()
    url = twf_oauth.build_authorize_url(state, challenge)
    resolved_return_to = _sanitize_twf_return_to(return_to)

    wants_json = "application/json" in request.headers.get("accept", "").lower()
    resp: Response
    if wants_json:
        resp = JSONResponse({"authorize_url": url})
    else:
        resp = RedirectResponse(url=url, status_code=302)
    # Store only state + PKCE verifier (short-lived)
    resp.set_cookie(
        key=twf_oauth.OAUTH_COOKIE_NAME,
        value=twf_oauth.pack_oauth_cookie(state, verifier, resolved_return_to, current_user.user_id),
        httponly=True,
        secure=True,
        samesite="none",
        max_age=10 * 60,
        path="/",
    )
    return resp

@app.get("/auth/twf/callback")
async def twf_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    def _error_redirect(message: str, return_to: str | None = None) -> RedirectResponse:
        return RedirectResponse(
            url=_twf_frontend_redirect_url(return_to, twf="error", twf_message=message),
            status_code=302,
        )

    packed: dict[str, str] | None = None
    try:
        if not code or not state:
            return _error_redirect("Missing code or state.")

        cookie_val = request.cookies.get(twf_oauth.OAUTH_COOKIE_NAME)
        if not cookie_val:
            return _error_redirect("OAuth session expired. Try again.")

        packed = twf_oauth.unpack_oauth_cookie(cookie_val)
        if packed.get("state") != state:
            return _error_redirect("Login verification failed. Try again.", packed.get("return_to"))
        clerk_user_id = packed.get("clerk_user_id")
        if not clerk_user_id:
            return _error_redirect("OAuth session expired. Try again.", packed.get("return_to"))

        tok = await twf_oauth.exchange_code_for_token(code, packed["verifier"])
        access = tok.get("access_token")
        refresh = tok.get("refresh_token")
        if not isinstance(access, str) or not access:
            return _error_redirect("Login failed. No access token returned.", packed.get("return_to"))
        if not isinstance(refresh, str) or not refresh:
            return _error_redirect("Login failed. No refresh token returned.", packed.get("return_to"))

        expires_in = int(tok.get("expires_in", 3600))
        me = await twf_oauth.twf_me(access)

        member_id = int(me["id"])
        display_name = str(me.get("name") or f"member-{member_id}")
        photo_url_raw = me.get("photoUrl")
        photo_url = str(photo_url_raw) if isinstance(photo_url_raw, str) and photo_url_raw.strip() else None

        sid = twf_oauth.new_session_id()
        twf_oauth.upsert_session(
            twf_oauth.TwfSession(
                session_id=sid,
                clerk_user_id=clerk_user_id,
                member_id=member_id,
                display_name=display_name,
                photo_url=photo_url,
                access_token=access,
                refresh_token=refresh,
                expires_at=int(time.time()) + expires_in,
            )
        )

        redirect_url = _twf_frontend_redirect_url(packed.get("return_to") or "/account", twf="linked")
        logger.info("TWF OAuth linked user_id=%s redirect_url=%s", clerk_user_id, redirect_url)
        resp = RedirectResponse(url=redirect_url, status_code=302)

        # Clear short-lived OAuth temp cookie
        resp.delete_cookie(key=twf_oauth.OAUTH_COOKIE_NAME, path="/")
        return resp
    except Exception:
        logger.exception("TWF OAuth callback failed")
        return _error_redirect("Login failed. Please try again.", packed.get("return_to") if packed else None)


@app.get("/auth/twf/status")
async def twf_status(current_user: ClerkPrincipal = Depends(require_clerk_user)) -> dict[str, Any]:
    sess = twf_oauth.get_session_for_clerk_user(current_user.user_id)
    if not sess:
        return {"linked": False, "admin": False}

    payload: dict[str, Any] = {
        "linked": True,
        "admin": _is_admin_member(sess.member_id),
        "member_id": sess.member_id,
        "display_name": sess.display_name,
    }
    if sess.photo_url:
        payload["photo_url"] = sess.photo_url
    return payload


class TelemetryEventBase(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    session_id: str = Field(min_length=1, max_length=128)
    model_id: str | None = Field(default=None, max_length=32)
    variable_id: str | None = Field(default=None, max_length=64)
    run_id: str | None = Field(default=None, max_length=32)
    region_id: str | None = Field(default=None, max_length=32)
    forecast_hour: int | None = Field(default=None, ge=0, le=999)
    device_type: str | None = Field(default=None, max_length=24)
    viewport_bucket: str | None = Field(default=None, max_length=24)
    page: str | None = Field(default=None, max_length=120)
    meta: dict[str, Any] | None = None


class PerfTelemetryIn(TelemetryEventBase):
    event_name: str = Field(min_length=1, max_length=64)
    duration_ms: float = Field(ge=0, le=600000)


class UsageTelemetryIn(TelemetryEventBase):
    event_name: str = Field(min_length=1, max_length=64)


class RumTelemetryIn(TelemetryEventBase):
    metric_name: str = Field(min_length=1, max_length=64)
    metric_value: float = Field(ge=0, le=600000)
    metric_unit: str = Field(min_length=1, max_length=16)
    sample_rate: float | None = Field(default=None, gt=0, le=1)


FeedbackCategory = Literal["bug", "performance", "feature", "data_accuracy", "ui_ux"]


class FeedbackSubmission(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    category: FeedbackCategory
    message: str = Field(min_length=1, max_length=1000)
    page_context: str = Field(min_length=1, max_length=300)
    model_context: str | None = Field(default=None, max_length=64)
    fhr_context: int | None = Field(default=None, ge=0, le=1000)
    app_version: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def require_non_blank_text(self) -> "FeedbackSubmission":
        if not self.message.strip():
            raise ValueError("message must not be blank")
        if not self.page_context.strip():
            raise ValueError("page_context must not be blank")
        return self


def _clean_claim_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _feedback_display_name_from_claims(claims: dict[str, Any]) -> str | None:
    for key in ("name", "username", "email", "email_address", "primary_email_address", "primary_email"):
        value = _clean_claim_string(claims.get(key))
        if value:
            return value

    email_addresses = claims.get("email_addresses")
    if isinstance(email_addresses, list):
        for item in email_addresses:
            if isinstance(item, dict):
                value = _clean_claim_string(item.get("email_address"))
                if value:
                    return value

    return None


async def _feedback_display_name(current_user: ClerkPrincipal) -> str:
    claims = current_user.claims
    claim_display_name = _feedback_display_name_from_claims(claims)
    if claim_display_name:
        return claim_display_name

    first_name = claims.get("first_name")
    last_name = claims.get("last_name")
    full_name = " ".join(
        part.strip()
        for part in (first_name, last_name)
        if isinstance(part, str) and part.strip()
    )
    if full_name:
        return full_name

    profile = await run_in_threadpool(fetch_clerk_user_profile, current_user.user_id)
    if profile and profile.display_name:
        return profile.display_name

    return f"Clerk user {current_user.user_id[:12]}"


@app.post("/api/v4/feedback", status_code=201)
async def post_feedback(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: FeedbackSubmission,
    current_user: ClerkPrincipal = Depends(require_clerk_user),
) -> dict[str, Any]:
    retry_after = feedback_service.check_rate_limit(clerk_user_id=current_user.user_id)
    if retry_after > 0:
        raise TwfApiError(
            status_code=429,
            code="FEEDBACK_RATE_LIMITED",
            message="Too many feedback submissions. Please try again later.",
        )
    display_name = await _feedback_display_name(current_user)
    try:
        record = feedback_service.insert_feedback(
            category=payload.category,
            message=payload.message.strip(),
            clerk_user_id=current_user.user_id,
            member_id=None,
            forums_display_name=display_name,
            page_context=payload.page_context.strip(),
            model_context=payload.model_context.strip() if payload.model_context and payload.model_context.strip() else None,
            fhr_context=payload.fhr_context,
            user_agent=(request.headers.get("user-agent") or "unknown")[:512],
            app_version=payload.app_version.strip() if payload.app_version and payload.app_version.strip() else None,
        )
    except ValueError as exc:
        raise TwfApiError(status_code=400, code="INVALID_FEEDBACK", message=str(exc)) from exc
    background_tasks.add_task(
        feedback_service.send_feedback_notification,
        record,
        feedback_service.notification_settings_from_env(),
    )
    return {
        "ok": True,
        "id": record["id"],
        "submitted_at": record["submitted_at"],
    }


@app.post("/api/v4/telemetry/perf", status_code=204)
async def post_perf_telemetry(request: Request, payload: PerfTelemetryIn) -> Response:
    if not _legacy_telemetry_write_enabled():
        return Response(status_code=204, headers={"X-Cartosky-Legacy-Telemetry": "disabled"})
    sess = _maybe_twf_session(request)
    try:
        admin_telemetry.record_perf_event(payload.model_dump(), member_id=sess.member_id if sess else None)
    except ValueError as exc:
        raise TwfApiError(status_code=400, code="INVALID_PERF_EVENT", message=str(exc)) from exc
    return Response(status_code=204)


@app.post("/api/v4/telemetry/usage", status_code=204)
async def post_usage_telemetry(request: Request, payload: UsageTelemetryIn) -> Response:
    if not _legacy_telemetry_write_enabled():
        return Response(status_code=204, headers={"X-Cartosky-Legacy-Telemetry": "disabled"})
    sess = _maybe_twf_session(request)
    try:
        admin_telemetry.record_usage_event(payload.model_dump(), member_id=sess.member_id if sess else None)
    except ValueError as exc:
        raise TwfApiError(status_code=400, code="INVALID_USAGE_EVENT", message=str(exc)) from exc
    return Response(status_code=204)


@app.post("/api/v4/telemetry/rum", status_code=204)
async def post_rum_telemetry(request: Request, payload: RumTelemetryIn) -> Response:
    sess = _maybe_twf_session(request)
    try:
        admin_telemetry.record_rum_metric(payload.model_dump(), member_id=sess.member_id if sess else None)
    except ValueError as exc:
        raise TwfApiError(status_code=400, code="INVALID_RUM_METRIC", message=str(exc)) from exc
    return Response(status_code=204)


@app.get("/api/v4/admin/performance/summary")
async def admin_perf_summary(
    request: Request,
    window: str = Query("7d"),
    device: str | None = Query(None),
    model: str | None = Query(None),
    variable: str | None = Query(None),
    latest_runs: int | None = Query(None, ge=1, le=12),
    _admin_identity: ClerkPrincipal | twf_oauth.TwfSession = Depends(_require_admin_identity),
) -> dict[str, Any]:
    normalized_window = window.strip().lower()
    since_ts = int(time.time()) - _resolve_window_seconds(normalized_window)
    summary = admin_telemetry.get_perf_summary(
        since_ts=since_ts,
        device_type=_normalize_filter_value(device),
        model_id=_normalize_filter_value(model),
        variable_id=_normalize_filter_value(variable),
        latest_runs=latest_runs,
    )
    return {
        "window": normalized_window,
        "filters": {
            "device": _normalize_filter_value(device),
            "model": _normalize_filter_value(model),
            "variable": _normalize_filter_value(variable),
            "latest_runs": latest_runs,
        },
        **summary,
    }


@app.get("/api/v4/admin/performance/timeseries")
async def admin_perf_timeseries(
    request: Request,
    metric: str = Query(...),
    window: str = Query("7d"),
    bucket: str = Query("auto"),
    device: str | None = Query(None),
    model: str | None = Query(None),
    variable: str | None = Query(None),
    latest_runs: int | None = Query(None, ge=1, le=12),
    _admin_identity: ClerkPrincipal | twf_oauth.TwfSession = Depends(_require_admin_identity),
) -> dict[str, Any]:
    normalized_window = window.strip().lower()
    since_ts = int(time.time()) - _resolve_window_seconds(normalized_window)
    resolved_bucket = _resolve_bucket(normalized_window, bucket)
    try:
        points = admin_telemetry.get_perf_timeseries(
            since_ts=since_ts,
            metric=metric.strip(),
            bucket=resolved_bucket,
            device_type=_normalize_filter_value(device),
            model_id=_normalize_filter_value(model),
            variable_id=_normalize_filter_value(variable),
            latest_runs=latest_runs,
        )
    except ValueError as exc:
        raise TwfApiError(status_code=400, code="INVALID_PERF_QUERY", message=str(exc)) from exc
    return {
        "metric": metric.strip(),
        "window": normalized_window,
        "bucket": resolved_bucket,
        "filters": {
            "device": _normalize_filter_value(device),
            "model": _normalize_filter_value(model),
            "variable": _normalize_filter_value(variable),
            "latest_runs": latest_runs,
        },
        "points": points,
    }


@app.get("/api/v4/admin/performance/breakdown")
async def admin_perf_breakdown(
    request: Request,
    metric: str = Query(...),
    by: str = Query("model"),
    window: str = Query("7d"),
    device: str | None = Query(None),
    model: str | None = Query(None),
    variable: str | None = Query(None),
    latest_runs: int | None = Query(None, ge=1, le=12),
    limit: int = Query(8, ge=1, le=20),
    _admin_identity: ClerkPrincipal | twf_oauth.TwfSession = Depends(_require_admin_identity),
) -> dict[str, Any]:
    normalized_window = window.strip().lower()
    since_ts = int(time.time()) - _resolve_window_seconds(normalized_window)
    try:
        items = admin_telemetry.get_perf_breakdown(
            since_ts=since_ts,
            metric=metric.strip(),
            breakdown_by=by.strip().lower(),
            limit=limit,
            device_type=_normalize_filter_value(device),
            model_id=_normalize_filter_value(model),
            variable_id=_normalize_filter_value(variable),
            latest_runs=latest_runs,
        )
    except ValueError as exc:
        raise TwfApiError(status_code=400, code="INVALID_PERF_QUERY", message=str(exc)) from exc
    return {
        "metric": metric.strip(),
        "window": normalized_window,
        "by": by.strip().lower(),
        "filters": {
            "device": _normalize_filter_value(device),
            "model": _normalize_filter_value(model),
            "variable": _normalize_filter_value(variable),
            "latest_runs": latest_runs,
        },
        "items": items,
    }


@app.get("/api/v4/admin/usage/summary")
async def admin_usage_summary(
    request: Request,
    window: str = Query("30d"),
    _admin_identity: ClerkPrincipal | twf_oauth.TwfSession = Depends(_require_admin_identity),
) -> dict[str, Any]:
    normalized_window = window.strip().lower()
    since_ts = int(time.time()) - _resolve_window_seconds(normalized_window)
    return {
        "window": normalized_window,
        **admin_telemetry.get_usage_summary(since_ts=since_ts),
    }


@app.get("/api/v4/admin/feedback")
async def admin_feedback(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    category: str | None = Query(None),
    since: str | None = Query(None),
    until: str | None = Query(None),
    display_name: str | None = Query(None),
    _admin_identity: ClerkPrincipal | twf_oauth.TwfSession = Depends(_require_admin_identity),
) -> dict[str, Any]:
    try:
        normalized_since = feedback_service.normalize_datetime_filter(since)
        normalized_until = feedback_service.normalize_datetime_filter(until)
        if normalized_since and normalized_until and normalized_since > normalized_until:
            raise ValueError("since must be before until")
        return feedback_service.get_admin_feedback(
            page=page,
            page_size=page_size,
            category=category,
            since=normalized_since,
            until=normalized_until,
            display_name=display_name,
        )
    except ValueError as exc:
        raise TwfApiError(status_code=400, code="INVALID_FEEDBACK_QUERY", message=str(exc)) from exc


@app.get("/api/v4/admin/overview/summary")
async def admin_overview_summary(
    request: Request,
    window: str = Query("7d"),
    _admin_identity: ClerkPrincipal | twf_oauth.TwfSession = Depends(_require_admin_identity),
) -> dict[str, Any]:
    normalized_window = window.strip().lower()
    since_ts = int(time.time()) - _resolve_window_seconds(normalized_window)
    return {
        "window": normalized_window,
        **admin_telemetry.get_overview_summary(since_ts=since_ts),
    }


@app.get("/api/v4/admin/overview/network-diagnostics")
async def admin_overview_network_diagnostics(
    request: Request,
    window: str = Query("7d"),
    _admin_identity: ClerkPrincipal | twf_oauth.TwfSession = Depends(_require_admin_identity),
) -> dict[str, Any]:
    normalized_window = window.strip().lower()
    since_ts = int(time.time()) - _resolve_window_seconds(normalized_window)
    return {
        "window": normalized_window,
        **admin_telemetry.get_network_diagnostics_summary(since_ts=since_ts),
    }


@app.get("/api/v4/admin/observability/summary")
async def admin_observability_summary(
    request: Request,
    _admin_identity: ClerkPrincipal | twf_oauth.TwfSession = Depends(_require_admin_identity),
) -> dict[str, Any]:
    _refresh_prometheus_gauges()
    return prometheus_metrics.get_observability_summary()


@app.get("/api/v4/admin/traces/summary")
async def admin_traces_summary(
    request: Request,
    _admin_identity: ClerkPrincipal | twf_oauth.TwfSession = Depends(_require_admin_identity),
) -> dict[str, Any]:
    return otel_tracing.get_traces_summary()


@app.get("/api/v4/admin/status/results")
async def admin_status_results(
    request: Request,
    window: str = Query("30d"),
    model: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    include_details: bool = Query(False),
    _admin_identity: ClerkPrincipal | twf_oauth.TwfSession = Depends(_require_admin_identity),
) -> dict[str, Any]:
    normalized_window = window.strip().lower()
    since_ts = int(time.time()) - _resolve_window_seconds(normalized_window)
    return {
        "window": normalized_window,
        "filters": {
            "model": _normalize_filter_value(model),
            "status": _normalize_filter_value(status),
        },
        "results": admin_telemetry.get_operational_status_results(
            data_root=DATA_ROOT,
            since_ts=since_ts,
            model_id=_normalize_filter_value(model),
            status_filter=_normalize_filter_value(status),
            limit=limit,
            include_details=include_details,
        ),
    }


@app.get("/api/v4/admin/status/run")
async def admin_status_run_detail(
    request: Request,
    model: str = Query(...),
    run: str = Query(...),
    _admin_identity: ClerkPrincipal | twf_oauth.TwfSession = Depends(_require_admin_identity),
) -> dict[str, Any]:
    return {
        "result": admin_telemetry.get_operational_status_run_detail(
            data_root=DATA_ROOT,
            model_id=model.strip().lower(),
            run_id=run.strip(),
        )
    }


@app.get("/api/v4/admin/status/qa-summary")
async def admin_status_qa_summary(
    request: Request,
    _admin_identity: ClerkPrincipal | twf_oauth.TwfSession = Depends(_require_admin_identity),
) -> dict[str, Any]:
    return admin_telemetry.get_status_qa_summary()


@app.get("/metrics")
async def metrics() -> Response:
    if not prometheus_metrics.prometheus_enabled():
        return Response(status_code=404)
    _refresh_prometheus_gauges()
    return Response(
        content=prometheus_metrics.metrics_payload(),
        media_type=prometheus_metrics.metrics_content_type(),
    )


@app.post("/auth/twf/disconnect")
async def twf_disconnect(current_user: ClerkPrincipal = Depends(require_clerk_user)) -> JSONResponse:
    twf_oauth.delete_session_for_clerk_user(current_user.user_id)
    return JSONResponse({"ok": True})


@app.delete("/api/v4/user/connections/twf")
async def delete_twf_connection(current_user: ClerkPrincipal = Depends(require_clerk_user)) -> JSONResponse:
    twf_oauth.delete_session_for_clerk_user(current_user.user_id)
    return JSONResponse({"ok": True})


@app.get("/twf/forums")
async def twf_forums(current_user: ClerkPrincipal = Depends(require_clerk_user)) -> dict[str, Any]:
    sess = _require_twf_session(current_user)
    return await twf_oauth.list_forums(sess)


def _extract_topics(payload: dict[str, Any]) -> list[Any]:
    results = payload.get("results")
    if isinstance(results, list):
        return results
    topics = payload.get("topics")
    if isinstance(topics, list):
        return topics
    items = payload.get("items")
    if isinstance(items, list):
        return items
    return []


def _topic_forum_id(t: dict[str, Any]) -> int | None:
    """Best-effort extraction of a topic's forum id across IPS shapes."""
    v = t.get("forum")
    if isinstance(v, dict):
        fid = v.get("id")
        try:
            return int(fid) if fid is not None else None
        except Exception:
            return None
    if isinstance(v, (int, str)):
        try:
            return int(v)
        except Exception:
            return None
    v2 = t.get("forum_id")
    if isinstance(v2, (int, str)):
        try:
            return int(v2)
        except Exception:
            return None
    return None


def _is_truthy_topic_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in {"1", "true", "yes", "on"}
    return False


def _normalize_topic(raw_topic: Any, *, force_pinned: bool) -> dict[str, Any] | None:
    if not isinstance(raw_topic, dict):
        return None

    raw_id = raw_topic.get("id")
    if raw_id is None:
        return None
    try:
        topic_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    if topic_id <= 0:
        return None

    raw_title = raw_topic.get("title")
    title = str(raw_title).strip() if raw_title is not None else ""
    raw_url = raw_topic.get("url")
    url = str(raw_url).strip() if raw_url is not None else ""
    if not title or not url:
        return None

    pinned = force_pinned or _is_truthy_topic_flag(raw_topic.get("pinned"))
    normalized: dict[str, Any] = {
        "id": topic_id,
        "title": title,
        "url": url,
        "pinned": pinned,
    }

    updated = raw_topic.get("updated")
    if updated is not None:
        normalized["updated"] = str(updated) if not isinstance(updated, str) else updated

    starter: str | None = None
    raw_starter = raw_topic.get("starter")
    if isinstance(raw_starter, dict):
        for key in ("name", "display_name", "displayName"):
            value = raw_starter.get(key)
            if isinstance(value, str) and value.strip():
                starter = value.strip()
                break
    if starter is None:
        raw_author = raw_topic.get("author")
        if isinstance(raw_author, dict):
            for key in ("name", "display_name", "displayName"):
                value = raw_author.get(key)
                if isinstance(value, str) and value.strip():
                    starter = value.strip()
                    break
    if starter is not None:
        normalized["starter"] = starter

    return normalized


def _topic_updated_sort_key(updated: Any) -> tuple[int, float, str]:
    if isinstance(updated, (int, float)):
        return (2, float(updated), "")
    if isinstance(updated, str):
        text = updated.strip()
        if not text:
            return (0, 0.0, "")
        iso_value = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(iso_value)
            return (2, parsed.timestamp(), "")
        except ValueError:
            pass
        try:
            return (2, float(text), "")
        except ValueError:
            return (1, 0.0, text.lower())
    return (0, 0.0, "")


@app.get("/twf/topics")
async def twf_topics(
    request: Request,
    forum_id: int = Query(..., ge=1),
    limit: int = Query(15, ge=1, le=25),
    current_user: ClerkPrincipal = Depends(require_clerk_user),
) -> dict[str, Any]:
    sess = _require_twf_session(current_user)

    pinned_payload = await twf_oauth.list_topics(sess, forum_id=forum_id, pinned=True, per_page=min(5, limit))
    regular_payload = await twf_oauth.list_topics(sess, forum_id=forum_id, pinned=False, per_page=limit)
    pinned_items = [item for item in _extract_topics(pinned_payload) if isinstance(item, dict)]
    unpinned_items = [item for item in _extract_topics(regular_payload) if isinstance(item, dict)]

    def _filter_forum(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for it in items:
            fid = _topic_forum_id(it)
            if fid is None:
                continue
            if fid == forum_id:
                out.append(it)
        return out

    pinned_items = _filter_forum(pinned_items)
    unpinned_items = _filter_forum(unpinned_items)
    logger.info(
        "TWF topics filtered",
        extra={
            "request_id": getattr(request.state, "request_id", None),
            "forum_id": forum_id,
            "pinned_count": len(pinned_items),
            "unpinned_count": len(unpinned_items),
        },
    )

    merged_by_id: dict[int, dict[str, Any]] = {}
    for raw_topic in pinned_items:
        normalized = _normalize_topic(raw_topic, force_pinned=True)
        if normalized is None:
            continue
        merged_by_id[normalized["id"]] = normalized

    for raw_topic in unpinned_items:
        normalized = _normalize_topic(raw_topic, force_pinned=False)
        if normalized is None:
            continue
        topic_id = normalized["id"]
        existing = merged_by_id.get(topic_id)
        if existing is None:
            merged_by_id[topic_id] = normalized
            continue
        if not existing.get("pinned", False) and normalized.get("pinned", False):
            merged_by_id[topic_id] = normalized
            continue
        if "updated" not in existing and "updated" in normalized:
            existing["updated"] = normalized["updated"]
        if "starter" not in existing and "starter" in normalized:
            existing["starter"] = normalized["starter"]

    results = list(merged_by_id.values())
    results.sort(
        key=lambda item: (
            1 if item.get("pinned") else 0,
            *_topic_updated_sort_key(item.get("updated")),
            int(item.get("id") or 0),
        ),
        reverse=True,
    )
    return {"forum_id": forum_id, "results": results}


class ShareTopicIn(BaseModel):
    forum_id: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=255)
    content: str | None = Field(None, min_length=1, max_length=5000)
    summary: str | None = Field(None, min_length=1, max_length=5000)
    permalink: str | None = Field(None, min_length=1, max_length=4096)
    image_url: str | None = Field(None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def validate_share_payload(self) -> "ShareTopicIn":
        has_content = isinstance(self.content, str) and bool(self.content.strip())
        has_summary = isinstance(self.summary, str) and bool(self.summary.strip())
        has_permalink = isinstance(self.permalink, str) and bool(self.permalink.strip())
        has_image = isinstance(self.image_url, str) and bool(self.image_url.strip())
        if has_content or (has_summary and has_permalink):
            return self
        if has_summary or has_permalink or has_image:
            raise ValueError("Summary and permalink are required.")
        raise ValueError("Content is required.")


def _twf_share_body_from_request(
    *,
    content: str | None,
    summary: str | None,
    permalink: str | None,
    image_url: str | None,
) -> tuple[str, str]:
    summary_value = summary.strip() if isinstance(summary, str) else ""
    permalink_value = permalink.strip() if isinstance(permalink, str) else ""
    image_value = image_url.strip() if isinstance(image_url, str) else ""

    if summary_value or permalink_value or image_value:
        if not summary_value:
            raise TwfApiError(status_code=400, code="TWF_VALIDATION_ERROR", message="Summary is required.")
        if not permalink_value:
            raise TwfApiError(status_code=400, code="TWF_VALIDATION_ERROR", message="Permalink is required.")
        try:
            return (
                twf_oauth.build_twf_share_html(
                summary=summary_value,
                permalink=permalink_value,
                image_url=image_value or None,
                ),
                "html",
            )
        except ValueError as exc:
            raise TwfApiError(status_code=400, code="TWF_VALIDATION_ERROR", message=str(exc)) from exc

    content_value = content.strip() if isinstance(content, str) else ""
    if not content_value:
        raise TwfApiError(status_code=400, code="TWF_VALIDATION_ERROR", message="Content is required.")
    return content_value, "plain"


@app.post("/twf/share/topic")
async def twf_share_topic(
    body: ShareTopicIn,
    current_user: ClerkPrincipal = Depends(require_clerk_user),
) -> dict[str, Any]:
    sess = _require_twf_session(current_user)
    title = body.title.strip()
    content, content_format = _twf_share_body_from_request(
        content=body.content,
        summary=body.summary,
        permalink=body.permalink,
        image_url=body.image_url,
    )
    if not title:
        raise TwfApiError(status_code=400, code="TWF_VALIDATION_ERROR", message="Title is required.")

    topic = await twf_oauth.create_topic(
        sess,
        forum_id=body.forum_id,
        title=title,
        content=content,
        content_format=content_format,
    )

    # IPS returns a big object; return only what the frontend actually needs.
    topic_id = topic.get("id")
    topic_url = topic.get("url")
    forum = topic.get("forum") or {}
    forum_id = forum.get("id") or body.forum_id

    if not topic_id or not topic_url:
        raise TwfApiError(
            status_code=502,
            code="IPS_UPSTREAM_ERROR",
            message="Forum API temporarily unavailable.",
        )

    return {
        "topicId": int(topic_id),
        "topicUrl": str(topic_url),
        "forumId": int(forum_id),
        "title": str(topic.get("title") or body.title),
    }


class SharePostIn(BaseModel):
    topic_id: int = Field(..., ge=1)
    content: str | None = Field(None, min_length=1, max_length=5000)
    summary: str | None = Field(None, min_length=1, max_length=5000)
    permalink: str | None = Field(None, min_length=1, max_length=4096)
    image_url: str | None = Field(None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def validate_share_payload(self) -> "SharePostIn":
        has_content = isinstance(self.content, str) and bool(self.content.strip())
        has_summary = isinstance(self.summary, str) and bool(self.summary.strip())
        has_permalink = isinstance(self.permalink, str) and bool(self.permalink.strip())
        has_image = isinstance(self.image_url, str) and bool(self.image_url.strip())
        if has_content or (has_summary and has_permalink):
            return self
        if has_summary or has_permalink or has_image:
            raise ValueError("Summary and permalink are required.")
        raise ValueError("Content is required.")


@app.post("/twf/share/post")
async def twf_share_post(
    body: SharePostIn,
    current_user: ClerkPrincipal = Depends(require_clerk_user),
) -> dict[str, Any]:
    sess = _require_twf_session(current_user)
    content, content_format = _twf_share_body_from_request(
        content=body.content,
        summary=body.summary,
        permalink=body.permalink,
        image_url=body.image_url,
    )

    post = await twf_oauth.create_post(
        sess,
        topic_id=body.topic_id,
        content=content,
        content_format=content_format,
    )

    post_id = post.get("id")
    post_url = post.get("url")
    topic_id = post.get("topic", {}).get("id") if isinstance(post.get("topic"), dict) else post.get("topic")
    if not topic_id:
        topic_id = body.topic_id

    if not post_id or not post_url:
        raise TwfApiError(
            status_code=502,
            code="IPS_UPSTREAM_ERROR",
            message="Forum API temporarily unavailable.",
        )

    return {
        "postId": int(post_id),
        "postUrl": str(post_url),
        "topicId": int(topic_id),
    }


@app.post("/api/v4/share/media")
async def share_media_upload(
    file: UploadFile | None = File(None),
    model: str | None = Form(None),
    run: str | None = Form(None),
    fh: str | None = Form(None),
    variable: str | None = Form(None),
    region: str | None = Form(None),
) -> JSONResponse:
    if file is None:
        return _share_media_error_response(
            status_code=400,
            code="MISSING_FILE",
            message="A PNG file upload is required.",
        )

    content_type = (file.content_type or "").strip().lower()
    if content_type != share_media_service.PNG_CONTENT_TYPE:
        await file.close()
        return _share_media_error_response(
            status_code=400,
            code="INVALID_CONTENT_TYPE",
            message="Only PNG uploads are supported.",
        )

    data = await file.read()
    await file.close()

    if not data:
        return _share_media_error_response(
            status_code=400,
            code="EMPTY_FILE",
            message="Uploaded file is empty.",
        )

    if len(data) > share_media_service.MAX_SHARE_PNG_BYTES:
        return _share_media_error_response(
            status_code=413,
            code="FILE_TOO_LARGE",
            message="PNG upload exceeds the 10 MB limit.",
        )

    filename_hint = share_media_service.build_share_png_filename_hint(
        model=model,
        run=run,
        fh=fh,
        variable=variable,
        region=region,
    )

    try:
        result = share_media_service.upload_share_png(
            data=data,
            filename_hint=filename_hint,
            content_type=content_type,
        )
    except share_media_service.ShareMediaError as exc:
        return _share_media_error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
        )

    return JSONResponse(content={"ok": True, **result})


class SampleBatchPointIn(BaseModel):
    id: str = Field(..., min_length=1, max_length=128)
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class SampleBatchIn(BaseModel):
    model: str = Field(..., min_length=1, max_length=64)
    run: str = Field(..., min_length=1, max_length=32)
    variable: str = Field(..., min_length=1, max_length=128)
    region: str | None = Field(default=None, max_length=32)
    ensemble_view: str | None = Field(default=None, max_length=64)
    forecast_hour: int = Field(..., ge=0)
    points: list[SampleBatchPointIn] = Field(..., min_length=1, max_length=500)

_ds_cache: dict[str, rasterio.DatasetReader] = {}
_ds_cache_lock = threading.Lock()
_DS_CACHE_MAX = 16

_manifest_cache: dict[str, dict[str, Any]] = {}
_sidecar_cache: dict[str, dict[str, Any]] = {}
_grid_manifest_cache: dict[str, dict[str, Any]] = {}
_json_cache_lock = threading.Lock()


class _SampleInflight:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.payload: dict[str, Any] | None = None


_sample_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_sample_inflight: dict[str, _SampleInflight] = {}
_sample_rate_window: dict[str, list[float]] = {}
_sample_lock = threading.Lock()

LOOP_MANIFEST_VERSION = 1
LOOP_MANIFEST_PROJECTION = "EPSG:4326"
LOOP_MANIFEST_BBOX = [-134.0, 24.0, -60.0, 55.0]
_LOOP_MANIFEST_BBOX_DENSIFY_POINTS = 21


def _run_hour(run_id: str) -> int | None:
    return run_id_hour(run_id)


@lru_cache(maxsize=64)
def _model_allowed_cycle_hours(model: str) -> set[int]:
    model_id = model.strip().lower()
    capabilities = list_model_capabilities().get(model_id)
    run_discovery = getattr(capabilities, "run_discovery", {}) if capabilities is not None else {}

    explicit_hours = run_discovery.get("cycle_hours") if isinstance(run_discovery, dict) else None
    if isinstance(explicit_hours, (list, tuple, set)):
        resolved = {
            int(hour)
            for hour in explicit_hours
            if isinstance(hour, int) and 0 <= int(hour) <= 23
        }
        if resolved:
            return resolved

    cadence_raw = run_discovery.get("cycle_cadence_hours") if isinstance(run_discovery, dict) else 1
    try:
        cadence = max(1, int(cadence_raw if cadence_raw is not None else 1))
    except (TypeError, ValueError):
        cadence = 1
    return set(range(0, 24, cadence))


def _run_matches_model_cycle(model: str, run_id: str) -> bool:
    capabilities = list_model_capabilities().get(model.strip().lower())
    if capabilities is not None:
        product = str(getattr(capabilities, "product", "") or "").strip().lower()
        ui_constraints = getattr(capabilities, "ui_constraints", {}) or {}
        time_axis_mode = str(ui_constraints.get("time_axis_mode", "")).strip().lower()
        if product == "obs" or time_axis_mode in {"observed", "valid"}:
            return parse_run_id_datetime(run_id) is not None
    hour = _run_hour(run_id)
    if hour is None:
        return False
    return hour in _model_allowed_cycle_hours(model)


def _load_json_cached(path: Path, cache: dict[str, dict[str, Any]]) -> dict | None:
    key = str(path)
    now = time.monotonic()

    with _json_cache_lock:
        entry = cache.get(key)
        if entry is not None:
            last_checked = float(entry.get("last_checked", 0.0))
            if now - last_checked < _JSON_CACHE_RECHECK_SECONDS:
                payload = entry.get("payload")
                return payload if isinstance(payload, dict) else None

    try:
        stat = path.stat()
        mtime_ns = int(stat.st_mtime_ns)
    except OSError:
        with _json_cache_lock:
            cache.pop(key, None)
        return None

    with _json_cache_lock:
        entry = cache.get(key)
        if entry is not None and int(entry.get("mtime_ns", -1)) == mtime_ns:
            entry["last_checked"] = now
            payload = entry.get("payload")
            return payload if isinstance(payload, dict) else None

    try:
        payload = json.loads(path.read_text())
    except Exception:
        logger.warning("Failed to read JSON cache file %s; serving last-good payload if available", path)
        with _json_cache_lock:
            entry = cache.get(key)
            if entry is not None:
                entry["last_checked"] = now
                cached_payload = entry.get("payload")
                return cached_payload if isinstance(cached_payload, dict) else None
        return None

    if not isinstance(payload, dict):
        return None

    with _json_cache_lock:
        cache[key] = {
            "mtime_ns": mtime_ns,
            "last_checked": now,
            "payload": payload,
        }
    return payload


def _get_cached_dataset(path: Path) -> rasterio.DatasetReader:
    key = str(path)
    with _ds_cache_lock:
        ds = _ds_cache.get(key)
        if ds is not None and not ds.closed:
            return ds
        if len(_ds_cache) >= _DS_CACHE_MAX:
            evict_key = next(iter(_ds_cache))
            try:
                _ds_cache.pop(evict_key).close()
            except Exception:
                _ds_cache.pop(evict_key, None)
        ds = rasterio.open(path)
        _ds_cache[key] = ds
        return ds


def _latest_run_from_pointer(model: str) -> str | None:
    return _latest_run_from_pointer_for_region(model, region=None)


def _canonical_region_for_model(model: str) -> str:
    capability = list_model_capabilities().get(model)
    return _model_canonical_region(capability)


def _normalized_request_region(model: str, region: str | None) -> str:
    normalized = str(region or "").strip().lower()
    if normalized is not None:
        if normalized:
            return normalized
    return _canonical_region_for_model(model)


def _latest_pointer_path(model: str, *, region: str | None = None) -> Path:
    del region
    return PUBLISHED_ROOT / model / "LATEST.json"


def _manifest_path(model: str, run: str, *, region: str | None = None) -> Path:
    del region
    return MANIFESTS_ROOT / model / f"{run}.json"


def _latest_run_from_pointer_for_region(model: str, *, region: str | None = None) -> str | None:
    latest_path = _latest_pointer_path(model, region=region)
    if not latest_path.is_file():
        return None
    try:
        payload = json.loads(latest_path.read_text())
    except Exception:
        logger.warning("Failed reading LATEST.json at %s", latest_path)
        return None

    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        logger.warning("Invalid run_id in LATEST.json at %s: %r", latest_path, run_id)
        return None
    if not _run_matches_model_cycle(model, run_id):
        logger.warning("LATEST.json points to out-of-cycle run for %s: %s", model, run_id)
        return None

    run_dir = PUBLISHED_ROOT / model / run_id
    manifest_path = _manifest_path(model, run_id)
    if not run_dir.is_dir() or not manifest_path.is_file():
        logger.warning("LATEST.json points to incomplete run state for %s/%s", model, run_id)
        return None
    return run_id


def _scan_manifest_runs(model: str, *, region: str | None = None) -> list[str]:
    del region
    model_manifest_dir = MANIFESTS_ROOT / model
    if not model_manifest_dir.is_dir():
        return []
    runs: list[str] = []
    for file_path in model_manifest_dir.glob("*.json"):
        run_id = file_path.stem
        if not RUN_ID_RE.match(run_id):
            continue
        if not _run_matches_model_cycle(model, run_id):
            continue
        if not (PUBLISHED_ROOT / model / run_id).is_dir():
            continue
        runs.append(run_id)
    return sorted(
        set(runs),
        key=lambda run_id: (
            (parse_run_id_datetime(run_id).timestamp() if parse_run_id_datetime(run_id) is not None else float("-inf")),
            run_id,
        ),
        reverse=True,
    )


def _serialize_variable_capability(model_id: str, capability: Any) -> dict[str, Any]:
    return serialize_variable_capability(model_id, capability)


def _serialize_model_capability(model_id: str, capability: Any) -> dict[str, Any]:
    return serialize_model_capability(model_id, capability)


def _manifest_var_available_frames(var_entry: dict[str, Any]) -> int:
    available_raw = var_entry.get("available_frames")
    if isinstance(available_raw, int):
        return max(0, available_raw)
    frames = var_entry.get("frames")
    if isinstance(frames, list):
        return len(frames)
    return 0


def _var_has_grid_runtime_ready(model_id: str, run_id: str, var_key: str) -> bool:
    return _var_has_grid_runtime_ready_for_region(model_id, run_id, var_key, region=None)


def _var_has_grid_runtime_ready_for_region(model_id: str, run_id: str, var_key: str, *, region: str | None = None) -> bool:
    manifest = _load_grid_manifest(model_id, run_id, var_key, region=region)
    if not isinstance(manifest, dict):
        return False
    lods = manifest.get("lods")
    if not isinstance(lods, list):
        return False
    for lod in lods:
        if not isinstance(lod, dict):
            continue
        frames = lod.get("frames")
        if isinstance(frames, list) and len(frames) > 0:
            return True
    return False


def _ready_runtime_state_for_run(
    model_id: str,
    run_id: str,
    *,
    model_capability: Any | None,
    region: str | None = None,
) -> tuple[list[str], int]:
    manifest = _load_manifest(model_id, run_id, region=region)
    if not isinstance(manifest, dict):
        return [], 0

    variables = manifest.get("variables")
    if not isinstance(variables, dict):
        return [], 0

    variable_catalog = getattr(model_capability, "variable_catalog", {}) if model_capability is not None else {}
    catalog_present = isinstance(variable_catalog, dict) and bool(variable_catalog)
    buildable_keys: set[str] = set()
    if catalog_present:
        buildable_keys = {
            str(var_key)
            for var_key, capability in variable_catalog.items()
            if bool(getattr(capability, "buildable", False))
        }

    ready_vars: list[str] = []
    ready_frame_count = 0
    for var_key, var_entry in variables.items():
        if not isinstance(var_entry, dict):
            continue
        if catalog_present and var_key not in buildable_keys:
            continue
        available_frames = _manifest_var_available_frames(var_entry)
        if available_frames <= 0:
            continue
        if grid_supported(model_id, var_key) and not _var_has_grid_runtime_ready_for_region(model_id, run_id, var_key, region=region):
            continue
        ready_vars.append(var_key)
        ready_frame_count += available_frames

    ready_vars.sort()
    return ready_vars, ready_frame_count


def _latest_run_readiness(
    model_id: str,
    latest_run: str | None,
    *,
    model_capability: Any | None,
    region: str | None = None,
) -> tuple[bool, list[str], int]:
    if latest_run is None:
        return False, [], 0
    ready_vars, ready_frame_count = _ready_runtime_state_for_run(
        model_id,
        latest_run,
        model_capability=model_capability,
        region=region,
    )
    return bool(ready_vars), ready_vars, ready_frame_count


def _latest_run_target_max_fh(model_id: str, latest_run: str | None) -> int | None:
    if not isinstance(latest_run, str) or not latest_run:
        return None
    run_dt = parse_run_id_datetime(latest_run)
    if run_dt is None:
        return None
    try:
        model_plugin = get_model(model_id)
        scheduled = model_plugin.target_fhs(run_dt.hour)
    except Exception:
        return None
    resolved = [int(fh) for fh in scheduled if isinstance(fh, int) or str(fh).isdigit()]
    return max(resolved) if resolved else None


def _availability_for_models(
    model_ids: list[str],
    capabilities_by_model: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    availability: dict[str, dict[str, Any]] = {}
    for model_id in model_ids:
        model_capability = capabilities_by_model.get(model_id)
        published_runs = _scan_manifest_runs(model_id)
        latest_run = _resolve_latest_run(model_id)
        latest_run_ready, latest_run_ready_vars, latest_run_ready_frame_count = _latest_run_readiness(
            model_id,
            latest_run,
            model_capability=model_capability,
        )
        latest_run_target_max_fh = _latest_run_target_max_fh(model_id, latest_run)
        availability[model_id] = {
            "latest_run": latest_run,
            "published_runs": published_runs,
            "latest_run_ready": latest_run_ready,
            "latest_run_ready_vars": latest_run_ready_vars,
            "latest_run_ready_frame_count": latest_run_ready_frame_count,
            "latest_run_target_max_fh": latest_run_target_max_fh,
        }
        if is_observed_model_capability(model_capability):
            availability[model_id].update(
                build_observed_bundle_health(
                    latest_run=latest_run,
                    manifest=_load_manifest(model_id, latest_run) if latest_run else None,
                    source=model_id,
                )
            )
    return availability


def _published_run_observability_rows() -> list[dict[str, float | str]]:
    capabilities_by_model = list_model_capabilities()
    availability = _availability_for_models(sorted(capabilities_by_model.keys()), capabilities_by_model)
    rows: list[dict[str, float | str]] = []
    now_utc = datetime.utcnow()
    for model_id, item in availability.items():
        latest_run = item.get("latest_run")
        if not isinstance(latest_run, str) or not latest_run:
            continue
        manifest = _load_manifest(model_id, latest_run)
        variables = manifest.get("variables") if isinstance(manifest, dict) else None
        variable_catalog = getattr(capabilities_by_model.get(model_id), "variable_catalog", {}) or {}
        buildable_keys = {
            str(var_key)
            for var_key, capability in variable_catalog.items()
            if bool(getattr(capability, "buildable", False))
        }
        total_variables = 0
        ready_variables = 0
        if isinstance(variables, dict):
            for var_key, var_entry in variables.items():
                if buildable_keys and var_key not in buildable_keys:
                    continue
                if not isinstance(var_entry, dict):
                    continue
                total_variables += 1
                if _manifest_var_available_frames(var_entry) > 0:
                    ready_variables += 1
        completion_ratio = (ready_variables / total_variables) if total_variables > 0 else 0.0
        run_age_hours = 0.0
        try:
            run_dt = parse_run_id_datetime(latest_run)
            if run_dt is None:
                raise ValueError(latest_run)
            run_age_hours = max(0.0, (now_utc - run_dt.replace(tzinfo=None)).total_seconds() / 3600.0)
        except ValueError:
            run_age_hours = 0.0
        row: dict[str, float | str | bool | None] = {
            "model_id": model_id,
            "run_age_hours": run_age_hours,
            "completion_ratio": completion_ratio,
        }
        if is_observed_model_capability(capabilities_by_model.get(model_id)):
            latest_scan_age_minutes = item.get("latest_scan_age_minutes")
            if isinstance(latest_scan_age_minutes, (int, float)):
                row["run_age_hours"] = max(0.0, float(latest_scan_age_minutes) / 60.0)
                row["latest_scan_age_minutes"] = float(latest_scan_age_minutes)
            row["freshness_state"] = str(item.get("freshness_state") or "unavailable")
            row["usable"] = bool(item.get("usable"))
        rows.append(row)
    return rows


def _refresh_prometheus_gauges() -> None:
    if not prometheus_metrics.prometheus_enabled():
        return
    with _sample_lock:
        active_entries = sum(1 for expires_at, _ in _sample_cache.values() if expires_at > time.monotonic())
    prometheus_metrics.set_sample_cache_entries(endpoint="all", entries=active_entries)
    prometheus_metrics.replace_published_run_health(_published_run_observability_rows())


def _build_capabilities_payload() -> dict[str, Any]:
    return _build_capabilities_payload_for_models(list_model_capabilities())


def _build_capabilities_payload_for_models(
    capabilities_by_model: dict[str, Any],
    *,
    availability: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    model_catalog = {
        model_id: _serialize_model_capability(model_id, capability)
        for model_id, capability in sorted(capabilities_by_model.items(), key=lambda item: item[0])
    }
    supported_models = sorted(model_catalog.keys())
    resolved_availability = (
        availability if availability is not None else _availability_for_models(supported_models, capabilities_by_model)
    )
    return {
        "contract_version": CAPABILITIES_CONTRACT_VERSION,
        "supported_models": supported_models,
        "model_catalog": model_catalog,
        "availability": resolved_availability,
    }


@lru_cache(maxsize=1)
def _capabilities_catalog_signature() -> str:
    capabilities_by_model = list_model_capabilities()
    model_catalog = {
        model_id: _serialize_model_capability(model_id, capability)
        for model_id, capability in sorted(capabilities_by_model.items(), key=lambda item: item[0])
    }
    supported_models = sorted(model_catalog.keys())
    return _make_etag_from_parts(CAPABILITIES_CONTRACT_VERSION, supported_models, model_catalog)


def _capabilities_state_etag(
    capabilities_by_model: dict[str, Any],
    availability: dict[str, dict[str, Any]],
) -> str:
    observed_minute_bucket = int(time.time() // 60) if any(
        is_observed_model_capability(capability) for capability in capabilities_by_model.values()
    ) else None
    return _make_etag_from_parts(
        CAPABILITIES_CONTRACT_VERSION,
        _capabilities_catalog_signature(),
        availability,
        observed_minute_bucket,
    )


def _model_default_var(model_capability: Any | None) -> str:
    if model_capability is None:
        return ""
    defaults = getattr(model_capability, "ui_defaults", {}) or {}
    return str(defaults.get("default_var_key") or "").strip()


def _model_canonical_region(model_capability: Any | None) -> str:
    if model_capability is None:
        return "conus"
    canonical_region = str(getattr(model_capability, "canonical_region", "") or "").strip().lower()
    return canonical_region or "conus"


def _path_mtime_ns(path: Path) -> int:
    try:
        return int(path.stat().st_mtime_ns)
    except OSError:
        return 0


def _bootstrap_frames_state_token(
    model: str,
    run: str,
    var: str,
    manifest: dict[str, Any] | None,
    *,
    ensemble_view: str | None = None,
    region: str | None = None,
) -> str:
    variables = manifest.get("variables") if isinstance(manifest, dict) else None
    var_entry = variables.get(var) if isinstance(variables, dict) else None
    frame_entries = var_entry.get("frames") if isinstance(var_entry, dict) else None
    if not isinstance(frame_entries, list):
        return ""

    runtime_var = _runtime_var_id_for_request(model, var, ensemble_view)
    var_dir = _published_var_dir(model, run, runtime_var, region=region)
    frame_state: list[tuple[int, int, int]] = []
    for item in frame_entries:
        if not isinstance(item, dict):
            continue
        fh = item.get("fh")
        if not isinstance(fh, int):
            continue
        sidecar_path = var_dir / f"fh{fh:03d}.json"
        cog_path = var_dir / f"fh{fh:03d}.val.cog.tif"
        frame_state.append((fh, _path_mtime_ns(sidecar_path), _path_mtime_ns(cog_path)))
    return _make_etag_from_parts(frame_state) if frame_state else ""


def _bootstrap_selection_state(
    *,
    model: str | None,
    run: str,
    var: str | None,
    ensemble_view: str | None,
    region: str | None,
    capabilities_by_model: dict[str, Any],
) -> dict[str, Any]:
    supported_models = sorted(capabilities_by_model.keys())
    requested_model = (model or "").strip().lower()
    selected_model = requested_model if requested_model in supported_models else ""
    if not selected_model:
        selected_model = "hrrr" if "hrrr" in supported_models else (supported_models[0] if supported_models else "")

    selected_run: str | None = None
    run_manifest: dict[str, Any] | None = None
    selected_var = ""
    selected_ensemble_view = ""
    manifest_load_ms = 0.0

    model_capability = capabilities_by_model.get(selected_model) if selected_model else None

    default_region = "conus"
    canonical_region = _model_canonical_region(model_capability)
    requested_region = (region or "").strip().lower()
    if requested_region in REGION_PRESETS:
        selected_region = requested_region
    elif canonical_region in REGION_PRESETS:
        selected_region = canonical_region
    else:
        selected_region = default_region

    if selected_model:
        manifest_started_at = time.perf_counter()
        selected_run = _resolve_run(selected_model, run, region=selected_region) or _resolve_latest_run(selected_model, region=selected_region)
        if selected_run:
            run_manifest = _load_manifest(selected_model, selected_run, region=selected_region)
        manifest_load_ms = (time.perf_counter() - manifest_started_at) * 1000.0

        requested_var = (var or "").strip()
        default_var = _model_default_var(model_capability)
        if run_manifest and isinstance(run_manifest.get("variables"), dict):
            manifest_vars = run_manifest.get("variables", {})
            ordered_manifest_vars = _ordered_manifest_var_keys(selected_model, manifest_vars)
            if requested_var and requested_var in ordered_manifest_vars:
                selected_var = requested_var
            elif default_var and default_var in ordered_manifest_vars:
                selected_var = default_var
            elif ordered_manifest_vars:
                selected_var = ordered_manifest_vars[0]
            if selected_var:
                selected_ensemble_view = _resolve_requested_ensemble_view(
                    selected_model,
                    selected_var,
                    ensemble_view,
                ) or ""

    return {
        "selected_model": selected_model,
        "selected_run": selected_run,
        "selected_var": selected_var,
        "selected_ensemble_view": selected_ensemble_view,
        "selected_region": selected_region,
        "run_manifest": run_manifest,
        "model_capability": model_capability,
        "manifest_load_ms": manifest_load_ms,
    }


def _bootstrap_state_etag(
    *,
    requested_run: str,
    capabilities_etag: str,
    selection_state: dict[str, Any],
) -> str:
    selected_model = str(selection_state.get("selected_model") or "")
    selected_run = str(selection_state.get("selected_run") or "")
    selected_var = str(selection_state.get("selected_var") or "")
    selected_ensemble_view = str(selection_state.get("selected_ensemble_view") or "")
    selected_region = str(selection_state.get("selected_region") or "")
    run_manifest = selection_state.get("run_manifest")
    manifest_token = (
        _run_version_token(selected_model, selected_run, region=selected_region)
        if selected_model and selected_run
        else ""
    )
    frames_token = (
        _bootstrap_frames_state_token(
            selected_model,
            selected_run,
            selected_var,
            run_manifest,
            ensemble_view=selected_ensemble_view,
            region=selected_region,
        )
        if selected_model and selected_run and selected_var
        else ""
    )
    return _make_etag_from_parts(
        CAPABILITIES_CONTRACT_VERSION,
        capabilities_etag,
        selected_model,
        selected_run or requested_run,
        selected_var,
        selected_ensemble_view,
        selected_region,
        manifest_token,
        frames_token,
    )


def _ordered_manifest_var_keys(model: str, manifest_vars: dict[str, Any]) -> list[str]:
    if not manifest_vars:
        return []
    capability_map = list_model_capabilities().get(model)
    if capability_map is None:
        return sorted(manifest_vars.keys())

    variable_catalog = getattr(capability_map, "variable_catalog", {}) or {}
    known: list[str] = []
    unknown: list[str] = []
    for var_key in manifest_vars.keys():
        if var_key in variable_catalog:
            known.append(var_key)
        else:
            unknown.append(var_key)

    known.sort(
        key=lambda key: (
            getattr(variable_catalog[key], "order", None) is None,
            getattr(variable_catalog[key], "order", 0)
            if getattr(variable_catalog[key], "order", None) is not None
            else 0,
            key,
        )
    )
    unknown.sort()
    return known + unknown


def _resolve_latest_run(model: str, *, region: str | None = None) -> str | None:
    model_capability = list_model_capabilities().get(model)
    pointed = _latest_run_from_pointer_for_region(model, region=region)
    if pointed is not None:
        ready_vars, _ready_frame_count = _ready_runtime_state_for_run(
            model,
            pointed,
            model_capability=model_capability,
            region=region,
        )
        if ready_vars:
            return pointed
    runs = _scan_manifest_runs(model, region=region)
    for run_id in runs:
        ready_vars, _ready_frame_count = _ready_runtime_state_for_run(
            model,
            run_id,
            model_capability=model_capability,
            region=region,
        )
        if ready_vars:
            return run_id
    return runs[0] if runs else None


def _resolve_run(model: str, run: str, *, region: str | None = None) -> str | None:
    del region
    if run == "latest":
        return _resolve_latest_run(model)
    if not RUN_ID_RE.match(run):
        return None
    if not _run_matches_model_cycle(model, run):
        return None
    run_dir = PUBLISHED_ROOT / model / run
    manifest_path = _manifest_path(model, run)
    if run_dir.is_dir() and manifest_path.is_file():
        return run
    return None


def _load_manifest(model: str, run: str, *, region: str | None = None) -> dict | None:
    path = _manifest_path(model, run, region=region)
    if not path.is_file():
        return None
    return _load_json_cached(path, _manifest_cache)


def _manifest_run_complete(manifest: dict[str, Any]) -> bool:
    variables = manifest.get("variables")
    if not isinstance(variables, dict) or not variables:
        return False

    saw_expected = False
    for var_entry in variables.values():
        if not isinstance(var_entry, dict):
            return False

        expected_raw = var_entry.get("expected_frames")
        available_raw = var_entry.get("available_frames")
        expected = int(expected_raw) if isinstance(expected_raw, int) else None
        available = int(available_raw) if isinstance(available_raw, int) else None

        if expected is None:
            frames = var_entry.get("frames")
            if isinstance(frames, list):
                expected = len(frames)
                available = len(frames)
            else:
                return False

        if available is None:
            frames = var_entry.get("frames")
            if isinstance(frames, list):
                available = len(frames)
            else:
                return False

        saw_expected = saw_expected or expected > 0
        if available < expected:
            return False

    return saw_expected


def _run_version_token(model: str, run: str, *, region: str | None = None) -> str:
    path = _manifest_path(model, run, region=region)
    try:
        mtime_ns = int(path.stat().st_mtime_ns)
    except OSError:
        mtime_ns = 0
    return f"{run}-{mtime_ns}"


def _grid_version_token(model: str, run: str, var: str, *, region: str | None = None) -> str:
    path = grid_manifest_path(DATA_ROOT, model, run, var, region=region)
    try:
        mtime_ns = int(path.stat().st_mtime_ns)
    except OSError:
        mtime_ns = 0
    return f"{run}-{var}-{mtime_ns}"


def _normalize_ensemble_view(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized or None


def _resolve_requested_ensemble_view(model: str, var: str, ensemble_view: str | None) -> str | None:
    try:
        plugin = get_model(model)
    except HTTPException:
        return _normalize_ensemble_view(ensemble_view)

    normalized_var = plugin.normalize_var_id(var) if hasattr(plugin, "normalize_var_id") else str(var)
    requested_view = _normalize_ensemble_view(ensemble_view)
    supported_views = (
        plugin.supported_ensemble_views(normalized_var)
        if hasattr(plugin, "supported_ensemble_views")
        else []
    )
    if requested_view:
        if supported_views and requested_view not in supported_views:
            raise HTTPException(
                status_code=404,
                detail=f"Unsupported ensemble_view for {model}/{normalized_var}: {requested_view}",
            )
        if not supported_views:
            raise HTTPException(
                status_code=404,
                detail=f"ensemble_view is unsupported for {model}/{normalized_var}",
            )
        return requested_view
    if hasattr(plugin, "default_ensemble_view"):
        return _normalize_ensemble_view(plugin.default_ensemble_view(normalized_var))
    return None


def _runtime_var_id_for_request(model: str, var: str, ensemble_view: str | None) -> str:
    plugin = get_model(model)
    normalized_var = plugin.normalize_var_id(var) if hasattr(plugin, "normalize_var_id") else str(var)
    resolved_view = _resolve_requested_ensemble_view(model, normalized_var, ensemble_view)
    if hasattr(plugin, "resolve_runtime_var_id"):
        runtime_var = str(plugin.resolve_runtime_var_id(normalized_var, resolved_view)).strip()
        if runtime_var:
            return runtime_var
    return normalized_var


def _published_var_dir(model: str, run: str, var: str, *, region: str | None = None) -> Path:
    del region
    return PUBLISHED_ROOT / model / run / var


def _resolve_val_cog(model: str, run: str, var: str, fh: int, *, ensemble_view: str | None = None, region: str | None = None) -> Path | None:
    del region
    resolved = _resolve_run(model, run) or run
    runtime_var = _runtime_var_id_for_request(model, var, ensemble_view)
    candidate = _published_var_dir(model, resolved, runtime_var) / f"fh{fh:03d}.val.cog.tif"
    if candidate.is_file():
        return candidate
    return None


def _resolve_sidecar(model: str, run: str, var: str, fh: int, *, ensemble_view: str | None = None, region: str | None = None) -> dict | None:
    del region
    resolved = _resolve_run(model, run) or run
    runtime_var = _runtime_var_id_for_request(model, var, ensemble_view)
    candidate = _published_var_dir(model, resolved, runtime_var) / f"fh{fh:03d}.json"
    if candidate.is_file():
        return _load_json_cached(candidate, _sidecar_cache)
    return None


def _frame_has_cog(model: str, run: str, var: str, fh: int, *, ensemble_view: str | None = None, region: str | None = None) -> bool:
    return _resolve_val_cog(model, run, var, fh, ensemble_view=ensemble_view, region=region) is not None


def _load_grid_manifest(model: str, run: str, var: str, *, ensemble_view: str | None = None, region: str | None = None) -> dict[str, Any] | None:
    del region
    runtime_var = _runtime_var_id_for_request(model, var, ensemble_view)
    path = grid_manifest_path(DATA_ROOT, model, run, runtime_var)
    if not path.is_file():
        return None
    loaded = _load_json_cached(path, _grid_manifest_cache)
    if isinstance(loaded, dict):
        return loaded
    return None


def _grid_file_url(model: str, run: str, var: str, filename: str, *, version_token: str, region: str | None = None) -> str:
    del region
    safe_filename = Path(filename).name
    return (
        f"/api/v4/grid/{model}/{run}/{var}/{safe_filename}"
        f"?v={version_token}"
    )


def _grid_manifest_frame_file_is_valid(
    *,
    model: str,
    run: str,
    var: str,
    filename: str,
    width: int,
    height: int,
    dtype: str,
    region: str | None = None,
) -> bool:
    safe_filename = Path(filename).name
    if not safe_filename or width <= 0 or height <= 0:
        return False
    candidate = grid_frame_path(DATA_ROOT, model, run, var, 0, region=region).parent / safe_filename
    if not candidate.is_file():
        return False
    expected_size_bytes = expected_grid_frame_size_bytes(width=width, height=height, dtype=dtype)
    try:
        return candidate.stat().st_size == expected_size_bytes
    except OSError:
        return False


def _resolve_frame_var_dir(model: str, run: str, var: str, fh: int, *, region: str | None = None) -> Path | None:
    del fh
    del region
    resolved = _resolve_run(model, run)
    if resolved is None:
        return None
    runtime_var = _runtime_var_id_for_request(model, var, None)
    var_dir = _published_var_dir(model, resolved, runtime_var)
    if not var_dir.is_dir():
        return None
    return var_dir


def _sample_cache_key(model: str, run: str, var: str, fh: int, row: int, col: int, ensemble_view: str | None = None) -> str:
    view = _normalize_ensemble_view(ensemble_view) or "-"
    return f"{model}:{run}:{var}:{view}:{fh}:{row}:{col}"


def _sample_batch_cache_key(
    model: str,
    run: str,
    var: str,
    fh: int,
    points_hash: str,
    ensemble_view: str | None = None,
) -> str:
    view = _normalize_ensemble_view(ensemble_view) or "-"
    return f"batch:{model}:{run}:{var}:{view}:{fh}:{points_hash}"


def _sample_points_hash(points: list[SampleBatchPointIn]) -> str:
    canonical_points = [
        {
            "id": point.id,
            "lat": float(point.lat),
            "lon": float(point.lon),
        }
        for point in sorted(points, key=lambda point: point.id)
    ]
    return hashlib.md5(
        json.dumps(canonical_points, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@lru_cache(maxsize=16)
def _sample_transformer(dst_crs: str) -> Transformer:
    return Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)


def _sample_dataset_xy(ds: rasterio.DatasetReader, *, lon: float, lat: float) -> tuple[float, float]:
    ds_crs = ds.crs
    if ds_crs is None:
        raise ValueError(f"Sample dataset missing CRS: {ds.name}")
    dst_crs = ds_crs.to_string()
    if dst_crs == "EPSG:4326":
        return float(lon), float(lat)
    return _sample_transformer(dst_crs).transform(lon, lat)


def _sample_dataset_index(ds: rasterio.DatasetReader, *, lon: float, lat: float) -> tuple[int, int]:
    x, y = _sample_dataset_xy(ds, lon=lon, lat=lat)
    row, col = ds.index(x, y)
    return row, col


def _read_sample_value(
    ds: rasterio.DatasetReader,
    *,
    row: int,
    col: int,
    masked: bool,
) -> tuple[float | None, bool]:
    if row < 0 or row >= ds.height or col < 0 or col >= ds.width:
        return None, True

    window = Window(col, row, 1, 1)  # type: ignore[call-arg]
    pixel = ds.read(1, window=window, masked=masked)
    raw_value = pixel[0, 0]
    if np.ma.is_masked(raw_value):
        return None, True

    value = float(raw_value)
    if np.isnan(value):
        return None, True
    return value, False


def _sample_batch_values(
    ds: rasterio.DatasetReader,
    *,
    points: list[SampleBatchPointIn],
) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    for point in points:
        row, col = _sample_dataset_index(ds, lon=point.lon, lat=point.lat)
        value, no_data = _read_sample_value(ds, row=row, col=col, masked=True)
        values[point.id] = None if no_data or value is None else round(float(value), 1)
    return values


def _sample_rate_limit_allow(client_id: str) -> tuple[bool, float]:
    if SAMPLE_RATE_LIMIT_MAX_REQUESTS <= 0:
        return True, 0.0

    now = time.monotonic()
    cutoff = now - max(0.01, SAMPLE_RATE_LIMIT_WINDOW_SECONDS)
    retry_after = max(1.0, SAMPLE_RATE_LIMIT_WINDOW_SECONDS)

    with _sample_lock:
        window = _sample_rate_window.get(client_id)
        if window is None:
            window = []
            _sample_rate_window[client_id] = window
        while window and window[0] < cutoff:
            window.pop(0)
        if len(window) >= SAMPLE_RATE_LIMIT_MAX_REQUESTS:
            return False, retry_after
        window.append(now)

    return True, 0.0


def _sample_payload(
    *,
    model: str,
    run: str,
    var: str,
    fh: int,
    lat: float,
    lon: float,
    value: float | None,
    units: str,
    valid_time: str,
    no_data: bool,
) -> dict[str, Any]:
    return {
        "value": round(float(value), 1) if value is not None else None,
        "units": units,
        "model": model,
        "run": run,
        "var": var,
        "fh": fh,
        "valid_time": valid_time,
        "lat": lat,
        "lon": lon,
        "noData": no_data,
    }


def _ptype_intensity_sample_label(
    *,
    var: str,
    value: float | None,
    sidecar: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    if str(var).strip().lower() != "ptype_intensity" or value is None or not isinstance(sidecar, dict):
        return None, None

    if not np.isfinite(value):
        return None, None

    ptype_order = sidecar.get("ptype_order")
    ptype_breaks = sidecar.get("ptype_breaks")
    ptype_levels = sidecar.get("ptype_levels")
    if not isinstance(ptype_order, list) or not isinstance(ptype_breaks, dict) or not isinstance(ptype_levels, dict):
        return None, None

    index = int(round(float(value)))
    if index < 0:
        return None, None

    for raw_code in ptype_order:
        code = str(raw_code)
        raw_breaks = ptype_breaks.get(code)
        raw_levels = ptype_levels.get(code)
        if not isinstance(raw_breaks, dict) or not isinstance(raw_levels, list):
            continue
        try:
            offset = int(raw_breaks.get("offset"))
            count = int(raw_breaks.get("count"))
        except (TypeError, ValueError):
            continue
        if count <= 0 or index < offset or index >= offset + count:
            continue

        family = code.capitalize()
        local_idx = index - offset
        lower = float(raw_levels[max(0, min(local_idx, len(raw_levels) - 1))])
        upper: float | None = None
        if local_idx + 1 < len(raw_levels):
            upper = float(raw_levels[local_idx + 1])

        if upper is None:
            return family, f">= {lower:.2f} in/hr"
        return family, f"{lower:.2f}-{upper:.2f} in/hr"

    return None, None


# ---------------------------------------------------------------------------
# NWS Anchor City Weather
# ---------------------------------------------------------------------------


@app.get("/api/locations/search")
async def forecast_location_search(
    q: str = Query(..., min_length=2, description="ZIP, City, ST, or plain city name"),
):
    try:
        payload = await forecast_page_service.search_locations(q)
    except forecast_page_service.LocationNotFoundError as exc:
        return _error_response(status_code=404, code=exc.code, message=exc.message)
    except forecast_page_service.ForecastPageError as exc:
        return _error_response(
            status_code=502 if exc.upstream_status else 400,
            code=exc.code,
            message=exc.message,
            upstream_status=exc.upstream_status,
        )

    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/v4/locations/search")
async def forecast_location_search_v4(
    q: str = Query(..., min_length=2, description="ZIP, City, ST, or plain city name"),
):
    return await forecast_location_search(q)


@app.get("/api/forecast-page")
async def forecast_page(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    display_name: str | None = Query(None),
    timezone: str | None = Query(None),
    country_code: str | None = Query(None),
    admin1: str | None = Query(None),
    country: str | None = Query(None),
):
    try:
        location_hint = None
        if display_name:
            location_hint = forecast_page_service.LocationHint(
                display_name=display_name,
                timezone=timezone,
                country_code=country_code,
                admin1=admin1,
                country=country,
            )
        payload = await forecast_page_service.get_forecast_page(lat, lon, location_hint=location_hint)
    except forecast_page_service.ForecastPageError as exc:
        status_code = 404 if exc.code == "LOCATION_NOT_FOUND" else 502 if exc.upstream_status else 500
        return _error_response(
            status_code=status_code,
            code=exc.code,
            message=exc.message,
            upstream_status=exc.upstream_status,
        )

    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "public, max-age=60"},
    )


@app.get("/api/v4/forecast-page")
async def forecast_page_v4(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    display_name: str | None = Query(None),
    timezone: str | None = Query(None),
    country_code: str | None = Query(None),
    admin1: str | None = Query(None),
    country: str | None = Query(None),
):
    return await forecast_page(lat, lon, display_name, timezone, country_code, admin1, country)


@app.get("/api/forecast-page/by-query")
async def forecast_page_by_query(
    q: str = Query(..., min_length=2, description="ZIP, City, ST, or plain city name"),
):
    try:
        payload = await forecast_page_service.get_forecast_page_by_query(q)
    except forecast_page_service.LocationNotFoundError as exc:
        return _error_response(status_code=404, code=exc.code, message=exc.message)
    except forecast_page_service.ForecastPageError as exc:
        return _error_response(
            status_code=502 if exc.upstream_status else 500,
            code=exc.code,
            message=exc.message,
            upstream_status=exc.upstream_status,
        )

    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "public, max-age=60"},
    )


@app.get("/api/v4/forecast-page/by-query")
async def forecast_page_by_query_v4(
    q: str = Query(..., min_length=2, description="ZIP, City, ST, or plain city name"),
):
    return await forecast_page_by_query(q)


@app.get("/api/forecast-discussion")
async def forecast_discussion(
    office: str = Query(..., min_length=3, max_length=4, description="NWS forecast office code"),
):
    try:
        payload = await forecast_page_service.get_forecast_discussion(office)
    except forecast_page_service.ForecastPageError as exc:
        return _error_response(
            status_code=502 if exc.upstream_status else 400,
            code=exc.code,
            message=exc.message,
            upstream_status=exc.upstream_status,
        )

    if payload is None:
        return JSONResponse(
            content={"afd": None, "reason": "no_afd_available", "meta": {"office": office.strip().upper()}},
            headers={"Cache-Control": "public, max-age=900"},
        )

    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "public, max-age=900"},
    )


@app.get("/api/v4/forecast-discussion")
async def forecast_discussion_v4(
    office: str = Query(..., min_length=3, max_length=4, description="NWS forecast office code"),
):
    return await forecast_discussion(office)


@app.get("/api/model-guidance")
async def model_guidance_placeholder(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
):
    payload = await forecast_page_service.get_model_guidance_placeholder(lat, lon)
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/api/v4/model-guidance")
async def model_guidance_placeholder_v4(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
):
    return await model_guidance_placeholder(lat, lon)


@app.get("/api/v4/anchors/{anchor_id}/weather")
async def nws_anchor_weather(anchor_id: str):
    """Current observations + 7-day forecast for an anchor city."""
    try:
        bundle = await nws_service.get_weather_bundle(anchor_id)
    except nws_service.AnchorNotFoundError:
        return _error_response(
            status_code=404,
            code="ANCHOR_NOT_FOUND",
            message=f"Anchor '{anchor_id}' not found.",
        )
    except nws_service.NwsUpstreamError as exc:
        return _error_response(
            status_code=502,
            code=exc.code,
            message=exc.message,
            upstream_status=exc.upstream_status,
        )
    except nws_service.NwsServiceError as exc:
        return _error_response(
            status_code=500,
            code=exc.code,
            message=exc.message,
        )

    return JSONResponse(
        content=nws_service.serialize_weather_bundle(bundle),
        headers={"Cache-Control": "public, max-age=180"},
    )


@app.get("/api/v4/anchors/{anchor_id}/afd")
async def nws_anchor_afd(anchor_id: str):
    """Latest Area Forecast Discussion for an anchor city's WFO."""
    try:
        afd = await nws_service.get_afd(anchor_id)
    except nws_service.AnchorNotFoundError:
        return _error_response(
            status_code=404,
            code="ANCHOR_NOT_FOUND",
            message=f"Anchor '{anchor_id}' not found.",
        )
    except nws_service.NwsUpstreamError as exc:
        return _error_response(
            status_code=502,
            code=exc.code,
            message=exc.message,
            upstream_status=exc.upstream_status,
        )
    except nws_service.NwsServiceError as exc:
        return _error_response(
            status_code=500,
            code=exc.code,
            message=exc.message,
        )

    if afd is None:
        return JSONResponse(
            content={"afd": None, "reason": "no_afd_available", "meta": {"anchorId": anchor_id}},
            headers={"Cache-Control": "public, max-age=1800"},
        )

    return JSONResponse(
        content=nws_service.serialize_afd(afd, anchor_id),
        headers={"Cache-Control": "public, max-age=1800"},
    )


@app.get("/api/v4/health")
def health_v4():
    return {"ok": True, "data_root": str(DATA_ROOT)}


@app.get("/api/v4/nws-hazards/alert")
async def nws_hazards_alert_detail(
    id: str = Query(..., min_length=1, description="NWS alert id"),
):
    from .services import nws_hazards as nws_hazards_service

    try:
        feature = await run_in_threadpool(nws_hazards_service.fetch_alert_geojson, id)
    except nws_hazards_service.NWSHazardsError as exc:
        return _error_response(
            status_code=502,
            code="NWS_HAZARDS_ALERT_UNAVAILABLE",
            message=str(exc),
        )
    return JSONResponse(
        content=nws_hazards_service.serialize_alert_detail(feature),
        headers={"Cache-Control": "public, max-age=60"},
    )


@app.get("/tiles/v3/health")
def health_tiles_v3():
    return {
        "ok": True,
        "data_root": str(DATA_ROOT),
        "boundaries_mbtiles": str(BOUNDARIES_MBTILES),
        "boundaries_mbtiles_exists": BOUNDARIES_MBTILES.is_file(),
    }


@app.get("/tiles/v3/boundaries/v1/tilejson.json")
def boundaries_tilejson_v3():
    started_at = time.perf_counter()
    if not BOUNDARIES_MBTILES.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "error": "boundaries tileset not found",
                "path": str(BOUNDARIES_MBTILES),
            },
        )

    timing_header = _format_server_timing(
        [
            ("boundaries_tilejson_total", (time.perf_counter() - started_at) * 1000.0),
        ]
    )
    return Response(
        content=json.dumps(build_boundaries_tilejson()),
        media_type="application/json",
        headers={
            "Cache-Control": BOUNDARY_CACHE_MISS,
            "Server-Timing": timing_header,
        },
    )


@app.get("/tiles/v3/boundaries/v1/{z:int}/{x:int}/{y:int}.mvt")
def boundaries_tile_v3(z: int, x: int, y: int):
    started_at = time.perf_counter()
    tile = lookup_mbtiles_tile(BOUNDARIES_MBTILES, z=z, x=x, y=y)
    if tile is None:
        # Expected-empty vector tiles should still be a normal 200 for map clients.
        response = empty_mvt_response(cache_control=BOUNDARY_CACHE_MISS)
        response.headers["Server-Timing"] = _format_server_timing(
            [("boundaries_tile_total", (time.perf_counter() - started_at) * 1000.0)]
        )
        return response

    headers = {
        "Cache-Control": BOUNDARY_CACHE_HIT,
        "Server-Timing": _format_server_timing(
            [("boundaries_tile_total", (time.perf_counter() - started_at) * 1000.0)]
        ),
    }
    if len(tile) >= 2 and tile[0] == 0x1F and tile[1] == 0x8B:
        headers["Content-Encoding"] = "gzip"

    return Response(
        content=tile,
        media_type="application/vnd.mapbox-vector-tile",
        headers=headers,
    )


@app.get("/api/v4")
def root_v4():
    return {"service": "twf-v4-api", "version": "4.0.0", "capabilities_contract": CAPABILITIES_CONTRACT_VERSION}


@app.get("/api/regions")
def list_region_presets(request: Request):
    started_at = time.perf_counter()
    payload = {"regions": REGION_PRESETS}
    cache_control = "public, max-age=300"
    etag = _make_etag(payload)
    timing_header = _format_server_timing(
        [
            ("regions_total", (time.perf_counter() - started_at) * 1000.0),
        ]
    )
    r304 = _maybe_304(request, etag=etag, cache_control=cache_control)
    if r304 is not None:
        r304.headers["Server-Timing"] = timing_header
        return r304
    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": cache_control,
            "ETag": etag,
            "Server-Timing": timing_header,
        },
    )


@app.get("/api/v4/models")
def list_models_v4(request: Request):
    capabilities_payload = _build_capabilities_payload()
    supported_models = capabilities_payload["supported_models"]
    model_catalog = capabilities_payload["model_catalog"]
    availability = capabilities_payload["availability"]
    payload = [
        {
            "id": model_id,
            "name": model_catalog.get(model_id, {}).get("name", model_id.upper()),
            "latest_run": availability.get(model_id, {}).get("latest_run"),
            "published_runs": availability.get(model_id, {}).get("published_runs", []),
        }
        for model_id in supported_models
    ]
    cache_control = "public, max-age=60"
    etag = _make_etag(payload)
    r304 = _maybe_304(request, etag=etag, cache_control=cache_control)
    if r304 is not None:
        return r304
    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": cache_control,
            "ETag": etag,
        },
    )


@app.get("/api/v4/capabilities")
def get_capabilities_v4(request: Request):
    started_at = time.perf_counter()
    capabilities_by_model = list_model_capabilities()
    supported_models = sorted(capabilities_by_model.keys())
    availability = _availability_for_models(supported_models, capabilities_by_model)
    cache_control = "public, max-age=60"
    etag = _capabilities_state_etag(capabilities_by_model, availability)
    timing_header = _format_server_timing(
        [
            ("capabilities_total", (time.perf_counter() - started_at) * 1000.0),
        ]
    )
    r304 = _maybe_304(request, etag=etag, cache_control=cache_control)
    if r304 is not None:
        r304.headers["Server-Timing"] = timing_header
        return r304
    payload = _build_capabilities_payload_for_models(capabilities_by_model, availability=availability)
    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": cache_control,
            "ETag": etag,
            "Server-Timing": timing_header,
        },
    )


@app.get("/api/v4/bootstrap")
def get_bootstrap_v4(
    request: Request,
    model: str | None = Query(None, description="Optional preferred model ID"),
    run: str = Query("latest", description="Preferred run ID or latest"),
    var: str | None = Query(None, description="Optional preferred variable ID"),
    ensemble_view: str | None = Query(None, description="Optional ensemble view"),
    region: str | None = Query(None, description="Optional preferred region preset ID"),
):
    started_at = time.perf_counter()
    capabilities_by_model = list_model_capabilities()
    supported_models = sorted(capabilities_by_model.keys())
    availability = _availability_for_models(supported_models, capabilities_by_model)
    capabilities_etag = _capabilities_state_etag(capabilities_by_model, availability)

    manifest_started_at = time.perf_counter()
    with otel_tracing.start_as_current_span(
        "bootstrap.manifest",
        attributes={
            "cartosky.requested_model": (model or "").strip().lower(),
            "cartosky.requested_run": run,
        },
    ):
        selection_state = _bootstrap_selection_state(
            model=model,
            run=run,
            var=var,
            ensemble_view=ensemble_view,
            region=region,
            capabilities_by_model=capabilities_by_model,
        )
        selected_model = str(selection_state.get("selected_model") or "")
        selected_run = selection_state.get("selected_run")
        run_manifest = selection_state.get("run_manifest")
        selected_var = str(selection_state.get("selected_var") or "")
        selected_ensemble_view = str(selection_state.get("selected_ensemble_view") or "")
        selected_region = str(selection_state.get("selected_region") or "conus")
        if selected_run:
            otel_tracing.set_current_attributes({"cartosky.resolved_run": selected_run})
    manifest_load_ms = (time.perf_counter() - manifest_started_at) * 1000.0

    cache_control = "public, max-age=60"
    etag = _bootstrap_state_etag(
        requested_run=run,
        capabilities_etag=capabilities_etag,
        selection_state=selection_state,
    )
    prebuild_timing_header = _format_server_timing(
        [
            ("bootstrap_capabilities", 0.0),
            ("bootstrap_manifest", manifest_load_ms),
            ("bootstrap_frames", 0.0),
            ("bootstrap_total", (time.perf_counter() - started_at) * 1000.0),
        ]
    )
    r304 = _maybe_304(request, etag=etag, cache_control=cache_control)
    if r304 is not None:
        r304.headers["Server-Timing"] = prebuild_timing_header
        return r304

    capabilities_started_at = time.perf_counter()
    with otel_tracing.start_as_current_span("bootstrap.capabilities") as _span:
        capabilities_payload = _build_capabilities_payload_for_models(
            capabilities_by_model,
            availability=availability,
        )
    capabilities_ms = (time.perf_counter() - capabilities_started_at) * 1000.0

    frames_payload: list[dict[str, Any]] = []
    frames_build_ms = 0.0
    if selected_var and selected_run and run_manifest:
        frames_started_at = time.perf_counter()
        with otel_tracing.start_as_current_span(
            "bootstrap.frames",
            attributes={
                "cartosky.model": selected_model,
                "cartosky.run": selected_run,
                "cartosky.variable": selected_var,
            },
        ):
            variables = run_manifest.get("variables", {})
            var_entry = variables.get(selected_var) if isinstance(variables, dict) else None
            frame_entries = var_entry.get("frames") if isinstance(var_entry, dict) else []
            if not isinstance(frame_entries, list):
                frame_entries = []
            for item in frame_entries:
                if not isinstance(item, dict):
                    continue
                fh = item.get("fh")
                if not isinstance(fh, int):
                    continue
                frames_payload.append(
                    {
                        "fh": fh,
                        "has_cog": _frame_has_cog(
                            selected_model,
                            selected_run,
                            selected_var,
                            fh,
                            ensemble_view=selected_ensemble_view,
                            region=selected_region,
                        ),
                        "run": selected_run,
                        "meta": {
                            "meta": _resolve_sidecar(
                                selected_model,
                                selected_run,
                                selected_var,
                                fh,
                                ensemble_view=selected_ensemble_view,
                                region=selected_region,
                            )
                        },
                    }
                )
            otel_tracing.set_current_attributes({"cartosky.frame_count": len(frames_payload)})
            frames_payload.sort(key=lambda row: int(row["fh"]))
        frames_build_ms = (time.perf_counter() - frames_started_at) * 1000.0

    payload = {
        "contract_version": CAPABILITIES_CONTRACT_VERSION,
        "capabilities": capabilities_payload,
        "regions": {"regions": REGION_PRESETS},
        "selection": {
            "model": selected_model,
            "run": selected_run or run,
            "variable": selected_var,
            "ensemble_view": selected_ensemble_view,
            "region": selected_region,
        },
        "manifest": run_manifest,
        "frames": frames_payload,
    }
    timing_header = _format_server_timing(
        [
            ("bootstrap_capabilities", capabilities_ms),
            ("bootstrap_manifest", manifest_load_ms),
            ("bootstrap_frames", frames_build_ms),
            ("bootstrap_total", (time.perf_counter() - started_at) * 1000.0),
        ]
    )
    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": cache_control,
            "ETag": etag,
            "Server-Timing": timing_header,
        },
    )


@app.get("/api/v4/models/{model}/capabilities")
def get_model_capabilities_v4(request: Request, model: str):
    model_id = model.strip().lower()
    payload = _build_capabilities_payload()
    model_catalog = payload["model_catalog"]
    if model_id not in model_catalog:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_id}")

    model_payload = {
        "contract_version": payload["contract_version"],
        "model_id": model_id,
        "capabilities": model_catalog[model_id],
        "availability": payload["availability"].get(
            model_id,
            {"latest_run": None, "published_runs": []},
        ),
    }
    cache_control = "public, max-age=60"
    etag = _make_etag(model_payload)
    r304 = _maybe_304(request, etag=etag, cache_control=cache_control)
    if r304 is not None:
        return r304
    return JSONResponse(
        content=model_payload,
        headers={
            "Cache-Control": cache_control,
            "ETag": etag,
        },
    )


@app.get("/api/v4/{model}/runs")
def list_runs(request: Request, model: str):
    runs = _scan_manifest_runs(model)
    cache_control = "public, max-age=60"
    etag = _make_etag(runs)
    r304 = _maybe_304(request, etag=etag, cache_control=cache_control)
    if r304 is not None:
        return r304
    return JSONResponse(
        content=runs,
        headers={
            "Cache-Control": cache_control,
            "ETag": etag,
        },
    )


@app.get("/api/v4/{model}/{run}/manifest")
def get_manifest(
    request: Request,
    model: str,
    run: str,
    region: str | None = Query(None, description="Optional region preset ID"),
):
    started_at = time.perf_counter()
    resolve_started_at = time.perf_counter()
    with otel_tracing.start_as_current_span(
        "manifest.resolve",
        attributes={"cartosky.model": model, "cartosky.requested_run": run},
    ):
        resolved = _resolve_run(model, run, region=region)
        if resolved:
            otel_tracing.set_current_attributes({"cartosky.resolved_run": resolved})
    resolve_ms = (time.perf_counter() - resolve_started_at) * 1000.0
    if resolved is None:
        return Response(status_code=404, content='{"error": "run not found"}', media_type="application/json")
    load_started_at = time.perf_counter()
    with otel_tracing.start_as_current_span(
        "manifest.load",
        attributes={"cartosky.model": model, "cartosky.run": resolved},
    ):
        manifest = _load_manifest(model, resolved, region=region)
    load_ms = (time.perf_counter() - load_started_at) * 1000.0
    if manifest is None:
        return Response(status_code=404, content='{"error": "manifest not found"}', media_type="application/json")

    cache_control = "public, max-age=60"
    etag = _make_etag(manifest)
    timing_header = _format_server_timing(
        [
            ("manifest_resolve", resolve_ms),
            ("manifest_load", load_ms),
            ("manifest_total", (time.perf_counter() - started_at) * 1000.0),
        ]
    )
    r304 = _maybe_304(request, etag=etag, cache_control=cache_control)
    if r304 is not None:
        r304.headers["Server-Timing"] = timing_header
        return r304
    return JSONResponse(
        content=manifest,
        headers={
            "Cache-Control": cache_control,
            "ETag": etag,
            "Server-Timing": timing_header,
        },
    )


@app.get("/api/v4/{model}/{run}/vars")
def list_vars(model: str, run: str, region: str | None = Query(None, description="Optional region preset ID")):
    model_id = model.strip().lower()
    resolved = _resolve_run(model_id, run, region=region)
    if resolved is None:
        return Response(status_code=404, content='{"error": "run not found"}', media_type="application/json")

    manifest = _load_manifest(model_id, resolved, region=region)
    if manifest is None:
        return Response(status_code=404, content='{"error": "manifest not found"}', media_type="application/json")

    variables = manifest.get("variables")
    if not isinstance(variables, dict):
        return []

    ordered_var_ids = _ordered_manifest_var_keys(model_id, variables)
    model_capability = list_model_capabilities().get(model_id)
    variable_catalog = getattr(model_capability, "variable_catalog", {}) if model_capability is not None else {}

    result = []
    for var_id in ordered_var_ids:
        capability = variable_catalog.get(var_id) if isinstance(variable_catalog, dict) else None
        display_name = getattr(capability, "name", None) if capability is not None else None
        result.append({"id": var_id, "display_name": display_name or var_id})
    return result


@app.get("/api/v4/{model}/{run}/{var}/frames")
def list_frames(
    request: Request,
    model: str,
    run: str,
    var: str,
    ensemble_view: str | None = Query(None, description="Optional ensemble view"),
    region: str | None = Query(None, description="Optional region preset ID"),
):
    started_at = time.perf_counter()
    resolve_started_at = time.perf_counter()
    with otel_tracing.start_as_current_span(
        "frames.resolve",
        attributes={"cartosky.model": model, "cartosky.requested_run": run, "cartosky.variable": var},
    ):
        resolved = _resolve_run(model, run, region=region)
        if resolved:
            otel_tracing.set_current_attributes({"cartosky.resolved_run": resolved})
    resolve_ms = (time.perf_counter() - resolve_started_at) * 1000.0
    if resolved is None:
        return Response(status_code=404, content='{"error": "run not found"}', media_type="application/json")

    manifest_started_at = time.perf_counter()
    with otel_tracing.start_as_current_span(
        "frames.manifest",
        attributes={"cartosky.model": model, "cartosky.run": resolved, "cartosky.variable": var},
    ):
        manifest = _load_manifest(model, resolved, region=region)
    manifest_ms = (time.perf_counter() - manifest_started_at) * 1000.0
    if manifest is None:
        return Response(status_code=404, content='{"error": "manifest not found"}', media_type="application/json")

    variables = manifest.get("variables")
    if not isinstance(variables, dict):
        return []
    var_entry = variables.get(var)
    if not isinstance(var_entry, dict):
        return []

    frame_entries = var_entry.get("frames")
    if not isinstance(frame_entries, list):
        frame_entries = []

    run_complete = _manifest_run_complete(manifest)

    frames_build_started_at = time.perf_counter()
    frames: list[dict] = []
    with otel_tracing.start_as_current_span(
        "frames.build",
        attributes={"cartosky.model": model, "cartosky.run": resolved, "cartosky.variable": var},
    ):
        for item in frame_entries:
            if not isinstance(item, dict):
                continue
            fh = item.get("fh")
            if not isinstance(fh, int):
                continue

            meta = _resolve_sidecar(model, resolved, var, fh, ensemble_view=ensemble_view, region=region)
            frames.append(
                {
                    "fh": fh,
                    "has_cog": _frame_has_cog(model, resolved, var, fh, ensemble_view=ensemble_view, region=region),
                    "run": resolved,
                    "meta": {
                        "meta": meta,
                    },
                }
            )
        otel_tracing.set_current_attributes({"cartosky.frame_count": len(frames)})

    frames.sort(key=lambda row: row["fh"])
    frames_build_ms = (time.perf_counter() - frames_build_started_at) * 1000.0
    cache_control = _frames_cache_control(run, run_complete=run_complete)
    etag = _make_etag(frames)
    timing_header = _format_server_timing(
        [
            ("frames_resolve", resolve_ms),
            ("frames_manifest", manifest_ms),
            ("frames_build", frames_build_ms),
            ("frames_total", (time.perf_counter() - started_at) * 1000.0),
        ]
    )
    r304 = _maybe_304(request, etag=etag, cache_control=cache_control)
    if r304 is not None:
        r304.headers["Server-Timing"] = timing_header
        return r304

    return JSONResponse(
        content=frames,
        headers={
            "Cache-Control": cache_control,
            "ETag": etag,
            "Server-Timing": timing_header,
        },
    )


@app.get("/api/v4/{model}/{run}/{var}/grid-manifest")
def get_grid_manifest(
    request: Request,
    model: str,
    run: str,
    var: str,
    ensemble_view: str | None = Query(None, description="Optional ensemble view"),
    region: str | None = Query(None, description="Optional region preset ID"),
):
    started_at = time.perf_counter()
    resolve_started_at = time.perf_counter()
    with otel_tracing.start_as_current_span(
        "grid_manifest.resolve",
        attributes={"cartosky.model": model, "cartosky.requested_run": run, "cartosky.variable": var},
    ):
        resolved = _resolve_run(model, run, region=region)
        if resolved:
            otel_tracing.set_current_attributes({"cartosky.resolved_run": resolved})
    resolve_ms = (time.perf_counter() - resolve_started_at) * 1000.0
    if resolved is None:
        return Response(status_code=404, content='{"error": "run not found"}', media_type="application/json")
    runtime_var = _runtime_var_id_for_request(model, var, ensemble_view)
    if not grid_supported(model, runtime_var):
        return Response(status_code=404, content='{"error": "grid manifest not enabled"}', media_type="application/json")

    manifest_started_at = time.perf_counter()
    with otel_tracing.start_as_current_span(
        "grid_manifest.load",
        attributes={"cartosky.model": model, "cartosky.run": resolved, "cartosky.variable": var},
    ):
        manifest = _load_grid_manifest(model, resolved, var, ensemble_view=ensemble_view, region=region)
    manifest_ms = (time.perf_counter() - manifest_started_at) * 1000.0
    if manifest is None:
        return Response(status_code=404, content='{"error": "grid manifest not found"}', media_type="application/json")

    version_token = _grid_version_token(model, resolved, runtime_var, region=region)
    build_started_at = time.perf_counter()
    payload = dict(manifest)
    plugin = get_model(model)
    canonical_var = plugin.normalize_var_id(var) if hasattr(plugin, "normalize_var_id") else str(var)
    payload["var"] = canonical_var
    lods = payload.get("lods")
    grid_meta = payload.get("grid")
    grid_dtype = str(grid_meta.get("dtype") or "uint16") if isinstance(grid_meta, dict) else "uint16"
    if isinstance(lods, list):
        next_lods: list[dict[str, Any]] = []
        for lod in lods:
            if not isinstance(lod, dict):
                continue
            next_lod = dict(lod)
            frames = lod.get("frames")
            next_frames: list[dict[str, Any]] = []
            if isinstance(frames, list):
                for frame in frames:
                    if not isinstance(frame, dict):
                        continue
                    filename = str(frame.get("file") or "").strip()
                    fh = frame.get("fh")
                    if not filename or not isinstance(fh, int):
                        continue
                    frame_width = int(lod.get("width") or 0)
                    frame_height = int(lod.get("height") or 0)
                    if not _grid_manifest_frame_file_is_valid(
                        model=model,
                        run=resolved,
                        var=runtime_var,
                        filename=filename,
                        width=frame_width,
                        height=frame_height,
                        dtype=grid_dtype,
                        region=region,
                    ):
                        continue
                    next_frame = dict(frame)
                    next_frame["url"] = _grid_file_url(
                        model,
                        resolved,
                        runtime_var,
                        filename,
                        version_token=version_token,
                        region=region,
                    )
                    next_frames.append(next_frame)
            next_frames.sort(key=lambda item: int(item.get("fh", 0)))
            if not next_frames:
                continue
            next_lod["frames"] = next_frames
            next_lods.append(next_lod)
        payload["lods"] = next_lods
    contours = payload.get("contours")
    if isinstance(contours, dict):
        next_contours: dict[str, Any] = {}
        for contour_key, contour_meta in contours.items():
            if not isinstance(contour_meta, dict):
                next_contours[contour_key] = contour_meta
                continue
            next_meta = dict(contour_meta)
            contour_grid = contour_meta.get("grid")
            contour_dtype = str(contour_grid.get("dtype") or "uint16") if isinstance(contour_grid, dict) else "uint16"
            contour_lods = contour_meta.get("lods")
            if isinstance(contour_lods, list):
                next_contour_lods: list[dict[str, Any]] = []
                for lod in contour_lods:
                    if not isinstance(lod, dict):
                        continue
                    next_lod = dict(lod)
                    frames = lod.get("frames")
                    next_frames: list[dict[str, Any]] = []
                    if isinstance(frames, list):
                        for frame in frames:
                            if not isinstance(frame, dict):
                                continue
                            filename = str(frame.get("file") or "").strip()
                            fh = frame.get("fh")
                            if not filename or not isinstance(fh, int):
                                continue
                            frame_width = int(lod.get("width") or 0)
                            frame_height = int(lod.get("height") or 0)
                            if not _grid_manifest_frame_file_is_valid(
                                model=model,
                                run=resolved,
                                var=runtime_var,
                                filename=filename,
                                width=frame_width,
                                height=frame_height,
                                dtype=contour_dtype,
                                region=region,
                            ):
                                continue
                            next_frame = dict(frame)
                            next_frame["url"] = _grid_file_url(
                                model,
                                resolved,
                                runtime_var,
                                filename,
                                version_token=version_token,
                                region=region,
                            )
                            next_frames.append(next_frame)
                    next_frames.sort(key=lambda item: int(item.get("fh", 0)))
                    if not next_frames:
                        continue
                    next_lod["frames"] = next_frames
                    next_contour_lods.append(next_lod)
                next_meta["lods"] = next_contour_lods
            next_contours[contour_key] = next_meta
        payload["contours"] = next_contours
    build_ms = (time.perf_counter() - build_started_at) * 1000.0

    cache_control = "public, max-age=60"
    etag = _make_etag(payload)
    timing_header = _format_server_timing(
        [
            ("grid_manifest_resolve", resolve_ms),
            ("grid_manifest_load", manifest_ms),
            ("grid_manifest_build", build_ms),
            ("grid_manifest_total", (time.perf_counter() - started_at) * 1000.0),
        ]
    )
    r304 = _maybe_304(request, etag=etag, cache_control=cache_control)
    if r304 is not None:
        r304.headers["Server-Timing"] = timing_header
        return r304

    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": cache_control,
            "ETag": etag,
            "Server-Timing": timing_header,
        },
    )


def _get_grid_file(model: str, run: str, var: str, filename: str, *, region: str | None = None):
    started_at = time.perf_counter()
    resolved = _resolve_run(model, run, region=region)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not grid_supported(model, var):
        raise HTTPException(status_code=404, detail="Grid artifact not enabled")
    safe_filename = Path(filename).name
    candidate = grid_frame_path(DATA_ROOT, model, resolved, var, 0, region=region).parent / safe_filename
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Grid artifact not found")
    manifest = _load_grid_manifest(model, resolved, var, region=region)
    grid_meta = manifest.get("grid") if isinstance(manifest, dict) else None
    width = int(grid_meta.get("width") or 0) if isinstance(grid_meta, dict) else 0
    height = int(grid_meta.get("height") or 0) if isinstance(grid_meta, dict) else 0
    dtype = str(grid_meta.get("dtype") or "uint16") if isinstance(grid_meta, dict) else "uint16"
    lods = manifest.get("lods") if isinstance(manifest, dict) else None
    matched_manifest_file = False
    if isinstance(lods, list):
        for lod in lods:
            if not isinstance(lod, dict):
                continue
            frames = lod.get("frames")
            if not isinstance(frames, list):
                continue
            if any(isinstance(frame, dict) and str(frame.get("file") or "").strip() == safe_filename for frame in frames):
                width = int(lod.get("width") or width)
                height = int(lod.get("height") or height)
                matched_manifest_file = True
                break
    if not matched_manifest_file and isinstance(manifest, dict):
        contours = manifest.get("contours")
        if isinstance(contours, dict):
            for contour_meta in contours.values():
                if not isinstance(contour_meta, dict):
                    continue
                contour_grid = contour_meta.get("grid")
                contour_lods = contour_meta.get("lods")
                if not isinstance(contour_grid, dict) or not isinstance(contour_lods, list):
                    continue
                for lod in contour_lods:
                    if not isinstance(lod, dict):
                        continue
                    frames = lod.get("frames")
                    if not isinstance(frames, list):
                        continue
                    if any(isinstance(frame, dict) and str(frame.get("file") or "").strip() == safe_filename for frame in frames):
                        width = int(lod.get("width") or contour_grid.get("width") or 0)
                        height = int(lod.get("height") or contour_grid.get("height") or 0)
                        dtype = str(contour_grid.get("dtype") or dtype)
                        matched_manifest_file = True
                        break
                if matched_manifest_file:
                    break
    if not matched_manifest_file:
        raise HTTPException(status_code=404, detail="Grid artifact not listed in manifest")
    if width > 0 and height > 0:
        expected_size_bytes = expected_grid_frame_size_bytes(width=width, height=height, dtype=dtype)
        actual_size_bytes = candidate.stat().st_size
        if actual_size_bytes != expected_size_bytes:
            raise HTTPException(status_code=404, detail="Grid artifact invalid")
    timing_header = _format_server_timing(
        [
            ("grid_file_total", (time.perf_counter() - started_at) * 1000.0),
        ]
    )
    headers = {
        "Cache-Control": "public, max-age=31536000, immutable",
        "Server-Timing": timing_header,
    }
    if GRID_ACCEL_REDIRECT_ENABLED:
        try:
            relative_candidate = candidate.resolve().relative_to(PUBLISHED_ROOT.resolve())
        except ValueError:
            logger.warning("Grid artifact fell outside published root; falling back to FileResponse: %s", candidate)
        else:
            # Let nginx serve validated immutable grid artifacts directly from disk.
            return Response(
                status_code=200,
                media_type="application/octet-stream",
                headers={
                    **headers,
                    "X-Accel-Redirect": f"{GRID_ACCEL_REDIRECT_PREFIX}{relative_candidate.as_posix()}",
                },
            )
    return FileResponse(
        candidate,
        media_type="application/octet-stream",
        headers=headers,
    )


@app.get("/api/v4/grid/{model}/{run}/{var}/{filename}")
def get_grid_file(
    model: str,
    run: str,
    var: str,
    filename: str,
    region: str | None = Query(None, description="Optional region preset ID"),
):
    return _get_grid_file(model, run, var, filename, region=region)


@app.get("/api/v4/grid/v1/{model}/{run}/{var}/{filename}")
def get_grid_file_compat(
    model: str,
    run: str,
    var: str,
    filename: str,
    region: str | None = Query(None, description="Optional region preset ID"),
):
    return _get_grid_file(model, run, var, filename, region=region)


@app.get("/api/v4/sample")
def sample(
    request: Request,
    model: str = Query(..., description="Model ID (e.g. hrrr)"),
    run: str = Query(..., description="Run ID (e.g. 20260217_20z or latest)"),
    var: str = Query(..., description="Variable ID (e.g. tmp2m)"),
    region: str | None = Query(None, description="Optional region preset ID"),
    ensemble_view: str | None = Query(None, description="Optional ensemble view"),
    fh: int = Query(..., description="Forecast hour"),
    lat: float = Query(..., ge=-90, le=90, description="Latitude (WGS84)"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude (WGS84)"),
):
    client_id = request.client.host if request.client and request.client.host else "unknown"
    otel_tracing.set_current_attributes(
        {
            "cartosky.model": model,
            "cartosky.requested_run": run,
            "cartosky.variable": var,
            "cartosky.forecast_hour": fh,
        }
    )
    allowed, retry_after = _sample_rate_limit_allow(client_id)
    if not allowed:
        if prometheus_metrics.prometheus_enabled():
            prometheus_metrics.record_sample_cache_result(endpoint="sample", result="rate_limited")
        otel_tracing.set_current_attributes({"cartosky.sample.cache_result": "rate_limited"})
        return JSONResponse(
            status_code=429,
            content={"error": "rate limit exceeded", "retryAfterSec": retry_after},
            headers={"Retry-After": str(int(max(1, retry_after)))},
        )

    with otel_tracing.start_as_current_span("sample.resolve_cog") as _span:
        val_cog = _resolve_val_cog(model, run, var, fh, ensemble_view=ensemble_view, region=region)
    if val_cog is None:
        return Response(status_code=404, content='{"error": "val.cog.tif not found"}', media_type="application/json")

    try:
        with otel_tracing.start_as_current_span("sample.dataset_lookup"):
            ds = _get_cached_dataset(val_cog)
            row, col = _sample_dataset_index(ds, lon=lon, lat=lat)
            resolved_run = _resolve_run(model, run, region=region) or run
            sidecar = _resolve_sidecar(model, run, var, fh, ensemble_view=ensemble_view, region=region)
            units = sidecar.get("units", "") if sidecar else ""
            valid_time = sidecar.get("valid_time", "") if sidecar else ""
            otel_tracing.set_current_attributes({"cartosky.resolved_run": resolved_run})

        if row < 0 or row >= ds.height or col < 0 or col >= ds.width:
            payload = _sample_payload(
                model=model,
                run=resolved_run,
                var=var,
                fh=fh,
                lat=lat,
                lon=lon,
                value=None,
                units=units,
                valid_time=valid_time,
                no_data=True,
            )
            return JSONResponse(content=payload, headers={"Cache-Control": "private, max-age=300"})

        key = _sample_cache_key(model, resolved_run, var, fh, row, col, ensemble_view)
        now = time.monotonic()
        inflight: _SampleInflight | None = None
        is_leader = False

        with _sample_lock:
            cached = _sample_cache.get(key)
            if cached is not None:
                expires_at, payload = cached
                if expires_at > now:
                    if prometheus_metrics.prometheus_enabled():
                        prometheus_metrics.record_sample_cache_result(endpoint="sample", result="hit")
                    otel_tracing.set_current_attributes({"cartosky.sample.cache_result": "hit"})
                    return JSONResponse(content=payload, headers={"Cache-Control": "private, max-age=300"})
                _sample_cache.pop(key, None)

            inflight = _sample_inflight.get(key)
            if inflight is None:
                inflight = _SampleInflight()
                _sample_inflight[key] = inflight
                is_leader = True
                if prometheus_metrics.prometheus_enabled():
                    prometheus_metrics.record_sample_cache_result(endpoint="sample", result="miss")
                otel_tracing.set_current_attributes({"cartosky.sample.cache_result": "miss"})

        if not is_leader:
            assert inflight is not None
            inflight.event.wait(timeout=SAMPLE_INFLIGHT_WAIT_SECONDS)
            with _sample_lock:
                cached = _sample_cache.get(key)
                if cached is not None:
                    expires_at, payload = cached
                    if expires_at > time.monotonic():
                        if prometheus_metrics.prometheus_enabled():
                            prometheus_metrics.record_sample_cache_result(endpoint="sample", result="hit")
                        otel_tracing.set_current_attributes({"cartosky.sample.cache_result": "hit"})
                        return JSONResponse(content=payload, headers={"Cache-Control": "private, max-age=300"})
                payload = inflight.payload
                if payload is not None:
                    if prometheus_metrics.prometheus_enabled():
                        prometheus_metrics.record_sample_cache_result(endpoint="sample", result="hit")
                    otel_tracing.set_current_attributes({"cartosky.sample.cache_result": "hit"})
                    return JSONResponse(content=payload, headers={"Cache-Control": "private, max-age=300"})

        with otel_tracing.start_as_current_span("sample.read_value"):
            value, no_data = _read_sample_value(ds, row=row, col=col, masked=False)

        payload = _sample_payload(
            model=model,
            run=resolved_run,
            var=var,
            fh=fh,
            lat=lat,
            lon=lon,
            value=value,
            units=units,
            valid_time=valid_time,
            no_data=no_data,
        )
        label, desc = _ptype_intensity_sample_label(var=var, value=value, sidecar=sidecar)
        if label:
            payload["label"] = label
        if desc:
            payload["desc"] = desc

        with _sample_lock:
            _sample_cache[key] = (time.monotonic() + SAMPLE_CACHE_TTL_SECONDS, payload)
            sample_inflight = _sample_inflight.pop(key, None)
            if sample_inflight is not None:
                sample_inflight.payload = payload
                sample_inflight.event.set()
        if prometheus_metrics.prometheus_enabled():
            prometheus_metrics.record_sample_cache_result(endpoint="sample", result="store")
        otel_tracing.set_current_attributes({"cartosky.sample.cache_result": "store"})

        return JSONResponse(content=payload, headers={"Cache-Control": "private, max-age=86400"})

    except Exception:
        if prometheus_metrics.prometheus_enabled():
            prometheus_metrics.record_sample_cache_result(endpoint="sample", result="error")
        otel_tracing.set_current_attributes({"cartosky.sample.cache_result": "error"})
        with _sample_lock:
            key = locals().get("key")
            if isinstance(key, str):
                sample_inflight = _sample_inflight.pop(key, None)
                if sample_inflight is not None:
                    sample_inflight.event.set()
        logger.exception(
            "Sample query failed: %s/%s/%s/fh%03d @ (%.4f, %.4f)",
            model,
            run,
            var,
            fh,
            lat,
            lon,
        )
        return Response(status_code=500, content='{"error": "internal error"}', media_type="application/json")


@app.post("/api/v4/sample/batch")
def sample_batch(request: Request, body: SampleBatchIn):
    client_id = request.client.host if request.client and request.client.host else "unknown"
    otel_tracing.set_current_attributes(
        {
            "cartosky.model": body.model,
            "cartosky.requested_run": body.run,
            "cartosky.variable": body.variable,
            "cartosky.forecast_hour": body.forecast_hour,
            "cartosky.sample_batch.points": len(body.points),
        }
    )
    allowed, retry_after = _sample_rate_limit_allow(client_id)
    if not allowed:
        if prometheus_metrics.prometheus_enabled():
            prometheus_metrics.record_sample_cache_result(endpoint="sample_batch", result="rate_limited")
        otel_tracing.set_current_attributes({"cartosky.sample_batch.cache_result": "rate_limited"})
        return JSONResponse(
            status_code=429,
            content={"error": "rate limit exceeded", "retryAfterSec": retry_after},
            headers={"Retry-After": str(int(max(1, retry_after)))},
        )

    with otel_tracing.start_as_current_span("sample_batch.resolve_cog"):
        val_cog = _resolve_val_cog(
            body.model,
            body.run,
            body.variable,
            body.forecast_hour,
            ensemble_view=body.ensemble_view,
            region=body.region,
        )
    if val_cog is None:
        return Response(status_code=404, content='{"error": "val.cog.tif not found"}', media_type="application/json")

    resolved_run = _resolve_run(body.model, body.run, region=body.region) or body.run
    key = _sample_batch_cache_key(
        body.model,
        resolved_run,
        body.variable,
        body.forecast_hour,
        _sample_points_hash(body.points),
        body.ensemble_view,
    )
    now = time.monotonic()
    inflight: _SampleInflight | None = None
    is_leader = False

    with _sample_lock:
        cached = _sample_cache.get(key)
        if cached is not None:
            expires_at, payload = cached
            if expires_at > now:
                if prometheus_metrics.prometheus_enabled():
                    prometheus_metrics.record_sample_cache_result(endpoint="sample_batch", result="hit")
                otel_tracing.set_current_attributes({"cartosky.sample_batch.cache_result": "hit"})
                return JSONResponse(content=payload, headers={"Cache-Control": "private, max-age=300"})
            _sample_cache.pop(key, None)

        inflight = _sample_inflight.get(key)
        if inflight is None:
            inflight = _SampleInflight()
            _sample_inflight[key] = inflight
            is_leader = True
            if prometheus_metrics.prometheus_enabled():
                prometheus_metrics.record_sample_cache_result(endpoint="sample_batch", result="miss")
            otel_tracing.set_current_attributes({"cartosky.sample_batch.cache_result": "miss"})

    if not is_leader:
        assert inflight is not None
        inflight.event.wait(timeout=SAMPLE_INFLIGHT_WAIT_SECONDS)
        with _sample_lock:
            cached = _sample_cache.get(key)
            if cached is not None:
                expires_at, payload = cached
                if expires_at > time.monotonic():
                    if prometheus_metrics.prometheus_enabled():
                        prometheus_metrics.record_sample_cache_result(endpoint="sample_batch", result="hit")
                    otel_tracing.set_current_attributes({"cartosky.sample_batch.cache_result": "hit"})
                    return JSONResponse(content=payload, headers={"Cache-Control": "private, max-age=300"})
            payload = inflight.payload
            if payload is not None:
                if prometheus_metrics.prometheus_enabled():
                    prometheus_metrics.record_sample_cache_result(endpoint="sample_batch", result="hit")
                otel_tracing.set_current_attributes({"cartosky.sample_batch.cache_result": "hit"})
                return JSONResponse(content=payload, headers={"Cache-Control": "private, max-age=300"})

    try:
        with otel_tracing.start_as_current_span("sample_batch.compute"):
            ds = _get_cached_dataset(val_cog)
            sidecar = _resolve_sidecar(
                body.model,
                body.run,
                body.variable,
                body.forecast_hour,
                ensemble_view=body.ensemble_view,
                region=body.region,
            )
            units = sidecar.get("units", "") if sidecar else ""
            payload = {
                "units": units,
                "values": _sample_batch_values(ds, points=body.points),
            }

        with _sample_lock:
            _sample_cache[key] = (time.monotonic() + SAMPLE_CACHE_TTL_SECONDS, payload)
            sample_inflight = _sample_inflight.pop(key, None)
            if sample_inflight is not None:
                sample_inflight.payload = payload
                sample_inflight.event.set()
        if prometheus_metrics.prometheus_enabled():
            prometheus_metrics.record_sample_cache_result(endpoint="sample_batch", result="store")
        otel_tracing.set_current_attributes({"cartosky.sample_batch.cache_result": "store"})

        return JSONResponse(content=payload, headers={"Cache-Control": "private, max-age=86400"})

    except Exception:
        if prometheus_metrics.prometheus_enabled():
            prometheus_metrics.record_sample_cache_result(endpoint="sample_batch", result="error")
        otel_tracing.set_current_attributes({"cartosky.sample_batch.cache_result": "error"})
        with _sample_lock:
            sample_inflight = _sample_inflight.pop(key, None)
            if sample_inflight is not None:
                sample_inflight.event.set()
        logger.exception(
            "Batch sample query failed: %s/%s/%s/fh%03d points=%d",
            body.model,
            body.run,
            body.variable,
            body.forecast_hour,
            len(body.points),
        )
        return Response(status_code=500, content='{"error": "internal error"}', media_type="application/json")


@app.get("/api/v4/{model}/{run}/{var}/{fh:int}/contours/{key}")
def get_contour_geojson(
    model: str,
    run: str,
    var: str,
    fh: int,
    key: str,
    region: str | None = Query(None, description="Optional region preset ID"),
):
    started_at = time.perf_counter()
    var_dir = _resolve_frame_var_dir(model, run, var, fh, region=region)
    if var_dir is None:
        raise HTTPException(status_code=404, detail="Frame not found")

    sidecar_path = var_dir / f"fh{fh:03d}.json"
    if not sidecar_path.is_file():
        raise HTTPException(status_code=404, detail="Sidecar not found")

    try:
        sidecar = json.loads(sidecar_path.read_text())
    except Exception as exc:
        logger.exception(
            "Failed to read sidecar for contour: %s/%s/%s/fh%03d (%s)",
            model,
            run,
            var,
            fh,
            sidecar_path,
        )
        raise HTTPException(status_code=500, detail=f"Failed to read sidecar: {exc}") from exc

    contours = sidecar.get("contours")
    if not isinstance(contours, dict) or key not in contours:
        raise HTTPException(status_code=404, detail=f"Contour '{key}' not found")

    contour_meta = contours[key]
    contour_rel_path = contour_meta.get("path") if isinstance(contour_meta, dict) else None
    if not isinstance(contour_rel_path, str) or not contour_rel_path:
        raise HTTPException(status_code=500, detail=f"Contour '{key}' has invalid sidecar path")

    contour_path = var_dir / contour_rel_path
    if not contour_path.is_file():
        raise HTTPException(status_code=404, detail=f"Contour file missing: {contour_rel_path}")

    try:
        payload = contour_path.read_bytes()
        timing_header = _format_server_timing(
            [
                ("contour_total", (time.perf_counter() - started_at) * 1000.0),
            ]
        )
        return Response(
            content=payload,
            media_type="application/geo+json",
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "Server-Timing": timing_header,
            },
        )
    except Exception as exc:
        logger.exception(
            "Failed to read contour GeoJSON: %s/%s/%s/fh%03d/%s (%s)",
            model,
            run,
            var,
            fh,
            key,
            contour_path,
        )
        raise HTTPException(status_code=500, detail=f"Failed to read contour GeoJSON: {exc}") from exc


@app.get("/api/v4/{model}/{run}/{var}/{fh:int}/vectors/{key}")
def get_vector_geojson(
    model: str,
    run: str,
    var: str,
    fh: int,
    key: str,
    region: str | None = Query(None, description="Optional region preset ID"),
):
    started_at = time.perf_counter()
    var_dir = _resolve_frame_var_dir(model, run, var, fh, region=region)
    if var_dir is None:
        raise HTTPException(status_code=404, detail="Frame not found")

    sidecar_path = var_dir / f"fh{fh:03d}.json"
    if not sidecar_path.is_file():
        raise HTTPException(status_code=404, detail="Sidecar not found")

    try:
        sidecar = json.loads(sidecar_path.read_text())
    except Exception as exc:
        logger.exception(
            "Failed to read sidecar for vector: %s/%s/%s/fh%03d (%s)",
            model,
            run,
            var,
            fh,
            sidecar_path,
        )
        raise HTTPException(status_code=500, detail=f"Failed to read sidecar: {exc}") from exc

    vector_layers = sidecar.get("vector_layers")
    if not isinstance(vector_layers, dict) or key not in vector_layers:
        raise HTTPException(status_code=404, detail=f"Vector layer '{key}' not found")

    vector_meta = vector_layers[key]
    vector_rel_path = vector_meta.get("path") if isinstance(vector_meta, dict) else None
    if not isinstance(vector_rel_path, str) or not vector_rel_path:
        raise HTTPException(status_code=500, detail=f"Vector layer '{key}' has invalid sidecar path")

    vector_path = var_dir / vector_rel_path
    if not vector_path.is_file():
        raise HTTPException(status_code=404, detail=f"Vector file missing: {vector_rel_path}")

    try:
        payload = vector_path.read_bytes()
        timing_header = _format_server_timing(
            [
                ("vector_total", (time.perf_counter() - started_at) * 1000.0),
            ]
        )
        return Response(
            content=payload,
            media_type="application/geo+json",
            headers={
                "Cache-Control": "no-store",
                "Server-Timing": timing_header,
            },
        )
    except Exception as exc:
        logger.exception(
            "Failed to read vector GeoJSON: %s/%s/%s/fh%03d/%s (%s)",
            model,
            run,
            var,
            fh,
            key,
            vector_path,
        )
        raise HTTPException(status_code=500, detail=f"Failed to read vector GeoJSON: {exc}") from exc
