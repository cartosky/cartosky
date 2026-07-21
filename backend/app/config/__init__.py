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
    return raw in {"1", "true", "yes", "y", "on"}


@lru_cache(maxsize=1)
def clerk_secret_key() -> str:
    return _env_value("CLERK_SECRET_KEY").strip()


@lru_cache(maxsize=1)
def clerk_auth_enabled() -> bool:
    return bool(clerk_secret_key())


@lru_cache(maxsize=1)
def clerk_jwt_audience() -> str:
    return _env_value("CLERK_JWT_AUDIENCE").strip()


@lru_cache(maxsize=1)
def clerk_authorized_parties() -> list[str]:
    raw = _env_value("CLERK_AUTHORIZED_PARTIES").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


@lru_cache(maxsize=1)
def billing_enabled() -> bool:
    return _env_bool("CARTOSKY_BILLING_ENABLED", False)


@lru_cache(maxsize=1)
def pro_gating_enabled() -> bool:
    return _env_bool("CARTOSKY_PRO_GATING_ENABLED", False)


@lru_cache(maxsize=1)
def stripe_secret_key() -> str:
    return _env_value("STRIPE_SECRET_KEY").strip()


@lru_cache(maxsize=1)
def stripe_webhook_secret() -> str:
    return _env_value("STRIPE_WEBHOOK_SECRET").strip()


@lru_cache(maxsize=1)
def stripe_pro_price_id() -> str:
    return _env_value("STRIPE_PRO_PRICE_ID").strip()


@lru_cache(maxsize=1)
def stripe_checkout_success_url() -> str:
    return _env_value("STRIPE_CHECKOUT_SUCCESS_URL").strip()


@lru_cache(maxsize=1)
def stripe_checkout_cancel_url() -> str:
    return _env_value("STRIPE_CHECKOUT_CANCEL_URL").strip()


@lru_cache(maxsize=1)
def stripe_portal_return_url() -> str:
    return _env_value("STRIPE_PORTAL_RETURN_URL").strip()


def cog_sampling_models() -> frozenset[str]:
    """Emergency opt-OUT list for the value-COG substrate.

    Comma-separated (``CARTOSKY_COG_SAMPLING_MODELS=mrms`` or ``mrms,ndfd``);
    empty (the default) means NO model uses the COG path — binary sampling is
    the sole pipeline. Not lru_cached: the read is trivially cheap per
    request, and caching would make the flag unswitchable in tests without
    cache invalidation.
    """
    raw = _env_value("CARTOSKY_COG_SAMPLING_MODELS").strip().lower()
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


_BINARY_ALLOWLIST_DEPRECATION_LOGGED = False


def _warn_if_deprecated_binary_allowlist_set() -> None:
    global _BINARY_ALLOWLIST_DEPRECATION_LOGGED
    if _BINARY_ALLOWLIST_DEPRECATION_LOGGED:
        return
    _BINARY_ALLOWLIST_DEPRECATION_LOGGED = True
    if _env_value("CARTOSKY_BINARY_SAMPLING_MODELS").strip():
        logging.getLogger(__name__).warning(
            "CARTOSKY_BINARY_SAMPLING_MODELS is deprecated and IGNORED: binary "
            "sampling is now the default for every model (COG->binary "
            "migration complete). Remove it from the unit files; to opt a "
            "model back onto the COG path use CARTOSKY_COG_SAMPLING_MODELS."
        )


def binary_sampling_enabled(model: str) -> bool:
    """Whether ``model`` uses grid binaries as its sole sampling/write
    substrate. This is the DEFAULT for every model — including models added in
    the future, with zero configuration: no COG writes, enforced pre-encode
    gate, binary reads.

    ``CARTOSKY_COG_SAMPLING_MODELS`` is a per-model emergency opt-out. Note
    the lever is not instant: opting a model out switches reads to COGs and
    resumes COG writes, but only runs published AFTER the change have COGs —
    recent binary-only runs do not, so recovery starts from the next publish.

    The retired opt-in allowlist (``CARTOSKY_BINARY_SAMPLING_MODELS``) is a
    no-op; a deprecation warning is logged once if it is still set.
    """
    _warn_if_deprecated_binary_allowlist_set()
    return str(model or "").strip().lower() not in cog_sampling_models()


def member_publish_models() -> frozenset[str]:
    """Models whose scheduler runs the ensemble member publish pass.

    Comma-separated model allowlist (``CARTOSKY_MEMBER_PUBLISH_MODELS=gefs``);
    empty (the default) means member publishing is off everywhere. Removing a
    model is the kill switch — already-published member frames age out with
    run retention (member pipeline plan Phase 3 / Phase 2 design R8). Same
    per-model-list pattern and no-cache rationale as
    :func:`cog_sampling_models`.
    """
    raw = _env_value("CARTOSKY_MEMBER_PUBLISH_MODELS").strip().lower()
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def stats_publish_models() -> frozenset[str]:
    """Models whose scheduler runs the ensemble STATS publish pass (member
    pipeline Phase 6 / Tier 2 — percentile + probability map products).

    Same pattern and kill-switch semantics as :func:`member_publish_models`:
    ``CARTOSKY_STATS_PUBLISH_MODELS=gefs``; empty default = off everywhere;
    published stat frames age out with run retention. Rollout stages 6A→6C
    (stats design §9) are driven by this list plus the per-variable
    descriptor ``enabled`` flags.
    """
    raw = _env_value("CARTOSKY_STATS_PUBLISH_MODELS").strip().lower()
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


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
