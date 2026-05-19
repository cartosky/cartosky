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