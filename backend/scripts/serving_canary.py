#!/usr/bin/env python3
"""Serving canary: continuously time a real grid-file fetch through Cloudflare.

Purpose
-------
This is a long-running probe that periodically fetches a published weather
grid file end-to-end through the public API host (``https://api.cartosky.com``
by default, which is fronted by Cloudflare) and records how long it took,
how many bytes came back, the resulting throughput, the HTTP status, and the
``cf-cache-status`` edge header. It exposes those numbers as Prometheus gauges
so that origin<->Cloudflare *serving* degradation is visible in Grafana within
minutes, instead of only being noticed when a human reports slow tiles.

It exists because of a real incident: grid fetches were taking 83-180 seconds
while every host-level health signal (CPU, memory, process liveness) looked
perfectly fine. Nothing in the existing monitoring exercised the actual
"fetch a file the way a browser does" path, so the degradation was invisible.
This canary closes that gap by measuring the serving path itself.

Each probe cycle does three things:

  1. GET ``/api/v4/{model}/latest/{var}/grid-manifest?fh=0`` and take the LAST
     frame's relative ``url`` from ``lods[0].frames`` (a real, current grid
     file). The manifest request is itself timed.
  2. Stream-download that grid file with ``Accept-Encoding: br, gzip`` under a
     hard wall-clock deadline. A fetch that blows the deadline is abandoned and
     recorded as a slow/failed probe (partial bytes and elapsed time are still
     kept so the throughput gauge reflects the degradation) -- it must never
     hang the loop.
  3. GET ``/api/v4/capabilities`` as a small-response control, timed, so a
     slow *small* response can be distinguished from a slow *large* transfer
     (i.e. latency vs. throughput problems).

Limitation
----------
This canary is intended to run *on the origin box* (as a systemd service
alongside the API). That means it exercises the origin<->Cloudflare segment and
the Cloudflare edge nearest the origin -- NOT the transatlantic / user
last-mile path a real distant browser traverses. A green canary therefore
proves the origin and its adjacent edge are serving grid files quickly; it does
NOT prove that a user in, say, Europe is getting fast tiles. For last-mile
coverage you would need an external synthetic probe (e.g. a checker running in
the target region). Read the metrics with that scope in mind.

Prometheus metrics
-------------------
Served on ``--port`` (default 9105) at ``/metrics``. All gauges are prefixed
``cartosky_canary_`` and carry NO labels (one canary instance probes exactly one
product) except ``grid_fetch_cache_status_info`` which is labelled by ``status``.

  cartosky_canary_grid_fetch_seconds           wall-clock seconds for the grid fetch
  cartosky_canary_grid_fetch_bytes             bytes received for the grid fetch
  cartosky_canary_grid_fetch_bytes_per_second  computed throughput
  cartosky_canary_grid_fetch_http_status       HTTP status of the grid fetch (0 = transport error)
  cartosky_canary_grid_fetch_complete          1 = full body within deadline, 0 = truncated/failed
  cartosky_canary_manifest_fetch_seconds       wall-clock seconds for the manifest request
  cartosky_canary_capabilities_fetch_seconds   wall-clock seconds for the small control request
  cartosky_canary_probe_success                1 = manifest parsed AND grid fetch completed, else 0
  cartosky_canary_last_probe_timestamp_seconds unix time of the last completed cycle
  cartosky_canary_last_success_timestamp_seconds unix time of the last fully-successful cycle
  cartosky_canary_probes_total{result="success"|"failure"}  Counter of cycles
  cartosky_canary_grid_fetch_cache_status_info{status="HIT"|"MISS"|...}  1 for the
      observed cf-cache-status on the latest probe, 0 for all previously-seen others

Suggested Grafana alert expressions
------------------------------------
Slow serving (throughput collapse) -- fire when the grid transfer is slower
than ~1 MB/s for two consecutive scrapes (guard against the "no bytes" case so
a hard failure doesn't masquerade as slow):

    cartosky_canary_grid_fetch_bytes_per_second < 1e6
        and cartosky_canary_grid_fetch_bytes > 0

Stale success (the canary has not had a clean probe in 15 minutes -- covers both
"probes failing" and "process stuck/dead"):

    time() - cartosky_canary_last_success_timestamp_seconds > 900

You may also alert directly on ``cartosky_canary_probe_success == 0`` for N
consecutive scrapes, and watch ``cartosky_canary_grid_fetch_complete`` for
deadline-busting fetches.

Usage
-----
    # Long-running service (default: mrms/reflectivity, 5-min interval, :9105)
    python backend/scripts/serving_canary.py

    # One-shot probe for manual testing: prints a JSON result and exits
    # 0 on success / 1 on failure. Does NOT require prometheus_client.
    python backend/scripts/serving_canary.py --once

This script is intentionally standalone: it imports only ``requests`` and
(in loop mode) ``prometheus_client``, and does NOT import any CartoSky app
code -- so it keeps running and reporting even if the app itself is broken.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any

import requests
import urllib3

# prometheus_client is only needed for the long-running metrics server.
# Guard the import so `--once` (manual testing) works on a plain checkout
# where prometheus_client may not be installed.
try:
    from prometheus_client import Counter, Gauge, start_http_server

    _HAS_PROMETHEUS = True
except ImportError:  # pragma: no cover - environment dependent
    _HAS_PROMETHEUS = False


# ── Logging ──────────────────────────────────────────────────────────
logger = logging.getLogger("serving_canary")


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logging.basicConfig(level=level, handlers=[handler], force=True)


# ── Defaults ─────────────────────────────────────────────────────────
DEFAULT_BASE_URL = "https://api.cartosky.com"
DEFAULT_MODEL = "mrms"
DEFAULT_VAR = "reflectivity"
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_METRICS_PORT = 9105
DEFAULT_FETCH_TIMEOUT_SECONDS = 90

_CHUNK_SIZE = 64 * 1024
_USER_AGENT = "cartosky-serving-canary/1"


# ── Probe ────────────────────────────────────────────────────────────


def _timed_get_json(
    session: requests.Session, url: str, timeout: float
) -> tuple[dict[str, Any] | None, float, int]:
    """GET a small JSON endpoint. Returns (parsed_json_or_None, seconds, status)."""
    start = time.monotonic()
    status = 0
    parsed: dict[str, Any] | None = None
    try:
        resp = session.get(url, timeout=timeout)
        status = resp.status_code
        if resp.ok:
            parsed = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("GET %s failed: %s", url, exc)
    elapsed = time.monotonic() - start
    return parsed, elapsed, status


def _timed_get_status(
    session: requests.Session, url: str, timeout: float
) -> tuple[float, int]:
    """GET a small control endpoint, discarding the body. Returns (seconds, status)."""
    start = time.monotonic()
    status = 0
    try:
        resp = session.get(url, timeout=timeout)
        status = resp.status_code
        # Touch the body so the transfer time is included, then release.
        _ = resp.content
    except requests.RequestException as exc:
        logger.warning("GET %s failed: %s", url, exc)
    elapsed = time.monotonic() - start
    return elapsed, status


def _last_frame_url(manifest: dict[str, Any]) -> str | None:
    """Extract the LAST frame url from ``lods[0].frames`` of a grid manifest."""
    lods = manifest.get("lods")
    if not isinstance(lods, list) or not lods:
        return None
    first_lod = lods[0]
    if not isinstance(first_lod, dict):
        return None
    frames = first_lod.get("frames")
    if not isinstance(frames, list) or not frames:
        return None
    last = frames[-1]
    if not isinstance(last, dict):
        return None
    url = last.get("url")
    if isinstance(url, str) and url:
        return url
    return None


def _stream_download(
    session: requests.Session, url: str, timeout: float
) -> dict[str, Any]:
    """Stream-download a grid file under a hard wall-clock deadline.

    Counts wire bytes (``decode_content=False``), not decompressed bytes: the
    grids are served brotli-compressed at ~60:1 and the canary exists to measure
    network-path throughput, so transparent decompression would inflate the
    gauge by a weather-dependent factor. Abandons the transfer the moment the
    wall-clock deadline (``timeout`` seconds from the start of the request) is
    exceeded. A timeout is recorded as an incomplete probe -- it never hangs
    the loop. Partial bytes and the elapsed time are always kept so the
    throughput gauge reflects a slow transfer rather than reading as "no data".

    Returns a dict with: elapsed_seconds, bytes, bytes_per_second, http_status,
    complete (bool), cf_cache_status (str|None), deadline_exceeded (bool).
    """
    start = time.monotonic()
    deadline = start + timeout
    total_bytes = 0
    status = 0
    cf_cache_status: str | None = None
    deadline_exceeded = False
    complete = False

    try:
        resp = session.get(
            url,
            headers={"Accept-Encoding": "br, gzip"},
            stream=True,
            timeout=timeout,
        )
        status = resp.status_code
        cf_cache_status = resp.headers.get("cf-cache-status")
        try:
            for chunk in resp.raw.stream(_CHUNK_SIZE, decode_content=False):
                if chunk:
                    total_bytes += len(chunk)
                if time.monotonic() > deadline:
                    deadline_exceeded = True
                    logger.warning(
                        "Grid fetch exceeded %.0fs deadline after %d bytes -- "
                        "abandoning: %s",
                        timeout,
                        total_bytes,
                        url,
                    )
                    break
            else:
                # iter_content finished without hitting the deadline.
                complete = status == 200
        finally:
            resp.close()
    except (requests.RequestException, urllib3.exceptions.HTTPError, OSError) as exc:
        # raw.stream() surfaces urllib3/socket errors directly; iter_content
        # would have wrapped them in requests.RequestException.
        logger.warning("Grid fetch failed: %s (%s)", url, exc)

    elapsed = time.monotonic() - start
    bytes_per_second = (total_bytes / elapsed) if elapsed > 0 else 0.0
    return {
        "elapsed_seconds": elapsed,
        "bytes": total_bytes,
        "bytes_per_second": bytes_per_second,
        "http_status": status,
        "complete": complete,
        "cf_cache_status": cf_cache_status,
        "deadline_exceeded": deadline_exceeded,
    }


def run_probe(
    session: requests.Session,
    base_url: str,
    model: str,
    var: str,
    timeout: float,
) -> dict[str, Any]:
    """Run one full probe cycle and return a plain-dict result.

    Never raises: any unexpected error is captured into the ``error`` field and
    ``probe_success`` is set False so the caller's loop keeps running.
    """
    base = base_url.rstrip("/")
    result: dict[str, Any] = {
        "timestamp": time.time(),
        "base_url": base,
        "model": model,
        "var": var,
        "manifest_seconds": 0.0,
        "manifest_status": 0,
        "frame_url": None,
        "grid_seconds": 0.0,
        "grid_bytes": 0,
        "grid_bytes_per_second": 0.0,
        "grid_status": 0,
        "grid_complete": False,
        "grid_deadline_exceeded": False,
        "cf_cache_status": None,
        "capabilities_seconds": 0.0,
        "capabilities_status": 0,
        "probe_success": False,
        "error": None,
    }

    try:
        # 1. Manifest.
        manifest_url = (
            f"{base}/api/v4/{model}/latest/{var}/grid-manifest?fh=0"
        )
        manifest, manifest_seconds, manifest_status = _timed_get_json(
            session, manifest_url, timeout
        )
        result["manifest_seconds"] = manifest_seconds
        result["manifest_status"] = manifest_status

        frame_url = _last_frame_url(manifest) if manifest else None
        result["frame_url"] = frame_url

        # 2. Grid file (only if we resolved a frame url).
        if frame_url:
            grid = _stream_download(session, f"{base}{frame_url}", timeout)
            result["grid_seconds"] = grid["elapsed_seconds"]
            result["grid_bytes"] = grid["bytes"]
            result["grid_bytes_per_second"] = grid["bytes_per_second"]
            result["grid_status"] = grid["http_status"]
            result["grid_complete"] = grid["complete"]
            result["grid_deadline_exceeded"] = grid["deadline_exceeded"]
            result["cf_cache_status"] = grid["cf_cache_status"]
        else:
            logger.warning(
                "Could not resolve a frame url from manifest (status=%s) -- "
                "grid fetch skipped",
                manifest_status,
            )

        # 3. Capabilities control (independent of the above).
        cap_seconds, cap_status = _timed_get_status(
            session, f"{base}/api/v4/capabilities", timeout
        )
        result["capabilities_seconds"] = cap_seconds
        result["capabilities_status"] = cap_status

        # Success = manifest parsed AND grid body fully fetched within deadline.
        result["probe_success"] = bool(manifest) and bool(result["grid_complete"])
    except Exception as exc:  # noqa: BLE001 - loop must never crash
        logger.error("Unexpected error during probe: %s", exc, exc_info=True)
        result["error"] = str(exc)
        result["probe_success"] = False

    return result


# ── Prometheus metrics ───────────────────────────────────────────────


class _Metrics:
    """Holds every gauge/counter and knows how to apply a probe result."""

    def __init__(self) -> None:
        self.grid_fetch_seconds = Gauge(
            "cartosky_canary_grid_fetch_seconds",
            "Wall-clock seconds for the grid file fetch.",
        )
        self.grid_fetch_bytes = Gauge(
            "cartosky_canary_grid_fetch_bytes",
            "Bytes received for the grid file fetch.",
        )
        self.grid_fetch_bytes_per_second = Gauge(
            "cartosky_canary_grid_fetch_bytes_per_second",
            "Computed throughput (bytes / elapsed) for the grid file fetch.",
        )
        self.grid_fetch_http_status = Gauge(
            "cartosky_canary_grid_fetch_http_status",
            "HTTP status of the grid file fetch (0 = transport error).",
        )
        self.grid_fetch_complete = Gauge(
            "cartosky_canary_grid_fetch_complete",
            "1 if the full grid body arrived within the deadline, else 0.",
        )
        self.manifest_fetch_seconds = Gauge(
            "cartosky_canary_manifest_fetch_seconds",
            "Wall-clock seconds for the grid-manifest request.",
        )
        self.capabilities_fetch_seconds = Gauge(
            "cartosky_canary_capabilities_fetch_seconds",
            "Wall-clock seconds for the small capabilities control request.",
        )
        self.probe_success = Gauge(
            "cartosky_canary_probe_success",
            "1 if manifest parsed AND grid fetch completed, else 0.",
        )
        self.last_probe_timestamp_seconds = Gauge(
            "cartosky_canary_last_probe_timestamp_seconds",
            "Unix timestamp of the last completed probe cycle.",
        )
        self.last_success_timestamp_seconds = Gauge(
            "cartosky_canary_last_success_timestamp_seconds",
            "Unix timestamp of the last fully-successful probe cycle.",
        )
        self.probes_total = Counter(
            "cartosky_canary_probes_total",
            "Total probe cycles by result.",
            ["result"],
        )
        self.grid_fetch_cache_status_info = Gauge(
            "cartosky_canary_grid_fetch_cache_status_info",
            "1 for the cf-cache-status observed on the latest probe, 0 for "
            "previously-seen others.",
            ["status"],
        )
        # Pre-touch the counter label values so both series exist from startup.
        self.probes_total.labels(result="success")
        self.probes_total.labels(result="failure")
        self._seen_cache_statuses: set[str] = set()

    def _record_cache_status(self, status: str | None) -> None:
        # cf-cache-status may be absent (e.g. non-CDN path or transport error).
        observed = status if status else "absent"
        self._seen_cache_statuses.add(observed)
        for known in self._seen_cache_statuses:
            self.grid_fetch_cache_status_info.labels(status=known).set(
                1.0 if known == observed else 0.0
            )

    def apply(self, result: dict[str, Any]) -> None:
        self.grid_fetch_seconds.set(result["grid_seconds"])
        self.grid_fetch_bytes.set(result["grid_bytes"])
        self.grid_fetch_bytes_per_second.set(result["grid_bytes_per_second"])
        self.grid_fetch_http_status.set(result["grid_status"])
        self.grid_fetch_complete.set(1 if result["grid_complete"] else 0)
        self.manifest_fetch_seconds.set(result["manifest_seconds"])
        self.capabilities_fetch_seconds.set(result["capabilities_seconds"])

        success = bool(result["probe_success"])
        self.probe_success.set(1 if success else 0)
        self.last_probe_timestamp_seconds.set(result["timestamp"])
        if success:
            self.last_success_timestamp_seconds.set(result["timestamp"])
        self.probes_total.labels(
            result="success" if success else "failure"
        ).inc()
        self._record_cache_status(result["cf_cache_status"])


# ── Loop ─────────────────────────────────────────────────────────────


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT})
    return session


def _log_probe(result: dict[str, Any]) -> None:
    mbps = result["grid_bytes_per_second"] / 1e6
    logger.info(
        "probe success=%s | grid: %.2fs %d bytes %.2f MB/s status=%s "
        "complete=%s cache=%s | manifest: %.2fs status=%s | capabilities: "
        "%.2fs status=%s",
        result["probe_success"],
        result["grid_seconds"],
        result["grid_bytes"],
        mbps,
        result["grid_status"],
        result["grid_complete"],
        result["cf_cache_status"],
        result["manifest_seconds"],
        result["manifest_status"],
        result["capabilities_seconds"],
        result["capabilities_status"],
    )


def _run_loop(args: argparse.Namespace) -> int:
    if not _HAS_PROMETHEUS:
        logger.error(
            "prometheus_client is not installed -- required for loop mode. "
            "Install it, or use --once for a one-shot probe."
        )
        return 1

    metrics = _Metrics()
    start_http_server(args.port)
    logger.info(
        "Serving canary started: probing %s [%s/%s] every %ds, "
        "timeout %ds, metrics on :%d/metrics",
        args.base_url,
        args.model,
        args.var,
        args.interval,
        args.timeout,
        args.port,
    )

    session = _build_session()
    while True:
        cycle_start = time.monotonic()
        try:
            result = run_probe(
                session, args.base_url, args.model, args.var, args.timeout
            )
            metrics.apply(result)
            _log_probe(result)
        except Exception as exc:  # noqa: BLE001 - the loop must never crash
            logger.error("Probe cycle raised unexpectedly: %s", exc, exc_info=True)
            metrics.probe_success.set(0)
            metrics.last_probe_timestamp_seconds.set(time.time())
            metrics.probes_total.labels(result="failure").inc()

        # Sleep the remainder of the interval (never negative).
        elapsed = time.monotonic() - cycle_start
        sleep_for = max(0.0, args.interval - elapsed)
        time.sleep(sleep_for)


def _run_once(args: argparse.Namespace) -> int:
    session = _build_session()
    result = run_probe(session, args.base_url, args.model, args.var, args.timeout)
    print(json.dumps(result, indent=2))
    return 0 if result["probe_success"] else 1


# ── Entry point ──────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    def _env_int(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            return default
        try:
            return int(raw)
        except ValueError:
            logger.warning("Ignoring non-integer %s=%r; using %d", name, raw, default)
            return default

    parser = argparse.ArgumentParser(
        description=(
            "Serving canary: periodically fetch a grid file through Cloudflare, "
            "time it, and expose Prometheus metrics."
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CARTOSKY_CANARY_BASE_URL", DEFAULT_BASE_URL),
        help="API base URL (env CARTOSKY_CANARY_BASE_URL; default %(default)s)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("CARTOSKY_CANARY_MODEL", DEFAULT_MODEL),
        help="Model id to probe (env CARTOSKY_CANARY_MODEL; default %(default)s)",
    )
    parser.add_argument(
        "--var",
        default=os.environ.get("CARTOSKY_CANARY_VAR", DEFAULT_VAR),
        help="Variable id to probe (env CARTOSKY_CANARY_VAR; default %(default)s)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=_env_int(
            "CARTOSKY_CANARY_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS
        ),
        help="Seconds between probe cycles (env CARTOSKY_CANARY_INTERVAL_SECONDS; "
        "default %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_env_int("CARTOSKY_CANARY_METRICS_PORT", DEFAULT_METRICS_PORT),
        help="Prometheus metrics port (env CARTOSKY_CANARY_METRICS_PORT; "
        "default %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=_env_int(
            "CARTOSKY_CANARY_FETCH_TIMEOUT_SECONDS", DEFAULT_FETCH_TIMEOUT_SECONDS
        ),
        help="Hard per-request wall-clock timeout in seconds (env "
        "CARTOSKY_CANARY_FETCH_TIMEOUT_SECONDS; default %(default)s)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single probe, print a JSON result to stdout, and exit "
        "(0 if the probe succeeded, 1 otherwise). Useful for manual testing; "
        "does not require prometheus_client.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Debug-level logging.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    _setup_logging(verbose=args.verbose)

    if args.once:
        sys.exit(_run_once(args))
    sys.exit(_run_loop(args))


if __name__ == "__main__":
    main()
