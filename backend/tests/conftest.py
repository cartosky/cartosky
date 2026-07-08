from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app import config


def _clear_clerk_config_cache() -> None:
    config.clerk_secret_key.cache_clear()
    config.clerk_auth_enabled.cache_clear()
    config.clerk_jwt_audience.cache_clear()
    config.clerk_authorized_parties.cache_clear()


@pytest.fixture(autouse=True)
def isolate_clerk_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    monkeypatch.delenv("CLERK_JWT_AUDIENCE", raising=False)
    monkeypatch.delenv("CLERK_AUTHORIZED_PARTIES", raising=False)
    _clear_clerk_config_cache()
    yield
    _clear_clerk_config_cache()


@pytest.fixture(autouse=True)
def isolate_data_root_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Services fall back to the repo-relative "./data" when no data root env is
    # set, which lets derive runs leak cumulative-cache files into the repo and
    # poison later tests. Point the env fallback at a per-test tmp dir instead.
    monkeypatch.setenv("CARTOSKY_DATA_ROOT", str(tmp_path / "env-data-root"))


def _clear_billing_config_caches() -> None:
    # backend.app.* and app.* are distinct module instances (each with its own
    # lru_cache) — clear whichever have been imported.
    for module_name in ("backend.app.config", "app.config"):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for accessor in ("billing_enabled", "pro_gating_enabled"):
            try:
                getattr(module, accessor).cache_clear()
            except AttributeError:
                pass


@pytest.fixture(autouse=True)
def isolate_billing_env(monkeypatch: pytest.MonkeyPatch):
    """Pin billing/pro-gating OFF for every test.

    ``app.main`` runs ``load_dotenv("backend/.env.local")`` at import, so
    without this the DEVELOPER'S local dev flags leak into the test process —
    flipping ``CARTOSKY_PRO_GATING_ENABLED=true`` locally silently turned
    entitlement-dependent API tests red (observed 2026-07-08). Tests that
    exercise gating/billing set the env themselves and clear the (lru-cached)
    config accessors, which overrides this pin.
    """
    monkeypatch.setenv("CARTOSKY_BILLING_ENABLED", "false")
    monkeypatch.setenv("CARTOSKY_PRO_GATING_ENABLED", "false")
    _clear_billing_config_caches()
    yield
    _clear_billing_config_caches()