from __future__ import annotations

import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)


def _env_value(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env_value(name).strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    logger.warning("Invalid %s=%r; using fallback=%s", name, raw, default)
    return default


def _parse_model_var_pairs(raw: str, *, env_name: str) -> set[tuple[str, str]]:
    allowed: set[tuple[str, str]] = set()
    for chunk in raw.split(","):
        normalized = chunk.strip().lower()
        if not normalized:
            continue
        if ":" not in normalized:
            logger.warning("Ignoring invalid %s entry %r", env_name, chunk)
            continue
        model_id, var_key = normalized.split(":", 1)
        model_id = model_id.strip()
        var_key = var_key.strip()
        if not model_id or not var_key:
            logger.warning("Ignoring invalid %s entry %r", env_name, chunk)
            continue
        allowed.add((model_id, var_key))
    return allowed


@lru_cache(maxsize=1)
def grid_v1_enabled() -> bool:
    return _env_bool("CARTOSKY_GRID_V1_ENABLED", default=False)


@lru_cache(maxsize=1)
def grid_v1_build_enabled() -> bool:
    return _env_bool("CARTOSKY_GRID_V1_BUILD_ENABLED", default=False)


@lru_cache(maxsize=1)
def grid_v1_workers() -> int:
    raw = _env_value("CARTOSKY_GRID_V1_WORKERS", default="1").strip()
    if not raw:
        return 1
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning("Invalid CARTOSKY_GRID_V1_WORKERS=%r; using fallback=1", raw)
        return 1
    return max(1, parsed)


@lru_cache(maxsize=1)
def grid_v1_allowlist_override() -> set[tuple[str, str]]:
    raw = _env_value("CARTOSKY_GRID_V1_ALLOWLIST", default="").strip()
    if not raw:
        return set()
    return _parse_model_var_pairs(raw, env_name="CARTOSKY_GRID_V1_ALLOWLIST")


@lru_cache(maxsize=1)
def grid_v1_denylist() -> set[tuple[str, str]]:
    raw = _env_value("CARTOSKY_GRID_V1_DENYLIST", default="").strip()
    if not raw:
        return set()
    return _parse_model_var_pairs(raw, env_name="CARTOSKY_GRID_V1_DENYLIST")


def grid_v1_pair_enabled(model_id: str, var_key: str) -> bool:
    normalized_model = str(model_id or "").strip().lower()
    normalized_var = str(var_key or "").strip().lower()
    if not normalized_model or not normalized_var:
        return False

    from ..services.grid_v1 import grid_v1_code_supported

    if not grid_v1_code_supported(normalized_model, normalized_var):
        return False

    if (normalized_model, normalized_var) in grid_v1_denylist():
        return False

    allowlist = grid_v1_allowlist_override()
    if allowlist:
        return (normalized_model, normalized_var) in allowlist

    return True


def grid_v1_render_substrates(model_id: str, var_key: str) -> tuple[str, ...]:
    if grid_v1_enabled() and grid_v1_pair_enabled(model_id, var_key):
        return ("legacy", "grid_webgl_v1")
    return ("legacy",)
