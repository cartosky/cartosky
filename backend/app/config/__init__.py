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


@lru_cache(maxsize=1)
def clerk_secret_key() -> str:
    return _env_value("CLERK_SECRET_KEY").strip()


@lru_cache(maxsize=1)
def clerk_auth_enabled() -> bool:
    return bool(clerk_secret_key())


@lru_cache(maxsize=1)
def grid_build_enabled() -> bool:
    return True


@lru_cache(maxsize=1)
def grid_workers() -> int:
    raw = _env_value("CARTOSKY_GRID_WORKERS", default="1").strip()
    if not raw:
        return 1
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning("Invalid CARTOSKY_GRID_WORKERS=%r; using fallback=1", raw)
        return 1
    return max(1, parsed)


def grid_supported_pair(model_id: str, var_key: str) -> bool:
    normalized_model = str(model_id or "").strip().lower()
    normalized_var = str(var_key or "").strip().lower()
    if not normalized_model or not normalized_var:
        return False

    from ..services.grid import grid_code_supported

    return grid_code_supported(normalized_model, normalized_var)


def grid_render_substrates(model_id: str, var_key: str) -> tuple[str, ...]:
    if grid_supported_pair(model_id, var_key):
        return ("grid",)
    return ()
