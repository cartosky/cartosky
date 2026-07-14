"""Registry-wide grid-packing invariant.

Binary sampling is the DEFAULT substrate for every model (COG->binary
migration complete; ``binary_sampling_enabled`` returns True unless a model
is opted out via ``CARTOSKY_COG_SAMPLING_MODELS``). That makes packing
coverage load-bearing at model-add time: a new model without
``_PACKING_BY_MODEL_VAR`` entries cannot publish (the unconditional grid
write raises "Unsupported grid pack target") — this test moves that failure
from the first production build to CI.

Generalizes the per-model invariants (test_ndfd_publish /
test_wpc_invariants) to every registered model: every buildable catalog
variable whose render substrates include "grid" must be packing-supported
under its catalog id or its resolved runtime id (ensemble models publish
under ``__mean`` artifact ids; the bare catalog keys are runtime aliases).
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest

from app.config import binary_sampling_enabled, cog_sampling_models
from app.models.registry import MODEL_REGISTRY
from app.services.grid import grid_code_supported


def _grid_substrate_buildable_vars(plugin) -> list[str]:
    catalog = getattr(getattr(plugin, "capabilities", None), "variable_catalog", None) or {}
    var_ids: list[str] = []
    for var_id, capability in sorted(catalog.items()):
        if not bool(getattr(capability, "buildable", False)):
            continue
        substrates = getattr(capability, "render_substrates", None)
        if isinstance(substrates, (list, tuple)) and substrates:
            normalized = [str(item).strip().lower() for item in substrates]
            if "grid" not in normalized:
                continue
        var_ids.append(str(var_id))
    return var_ids


def test_every_registered_model_has_full_grid_packing_coverage() -> None:
    missing: list[str] = []
    for model_id, plugin in sorted(MODEL_REGISTRY.items()):
        for var_id in _grid_substrate_buildable_vars(plugin):
            runtime_var = var_id
            if hasattr(plugin, "resolve_runtime_var_id"):
                try:
                    runtime_var = str(plugin.resolve_runtime_var_id(var_id, None))
                except Exception:
                    runtime_var = var_id
            if grid_code_supported(model_id, var_id) or grid_code_supported(model_id, runtime_var):
                continue
            missing.append(f"{model_id}/{var_id} (runtime={runtime_var})")
    assert not missing, (
        "Buildable grid-substrate variables without a _PACKING_BY_MODEL_VAR "
        "entry — binary sampling is the default for every model, so these "
        "cannot publish. Add packing (scale/offset/nodata/dtype, audited per "
        "the migration plan's Phase G checklist) before registering them:\n"
        + "\n".join(missing)
    )


def test_binary_sampling_default_and_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the inversion: binary is the zero-config default for every model —
    including ids that do not exist yet — and CARTOSKY_COG_SAMPLING_MODELS is
    the only lever, while the retired opt-in allowlist is ignored."""
    monkeypatch.delenv("CARTOSKY_COG_SAMPLING_MODELS", raising=False)
    monkeypatch.delenv("CARTOSKY_BINARY_SAMPLING_MODELS", raising=False)
    assert cog_sampling_models() == frozenset()
    for model_id in list(MODEL_REGISTRY) + ["some_future_model"]:
        assert binary_sampling_enabled(model_id) is True

    monkeypatch.setenv("CARTOSKY_COG_SAMPLING_MODELS", " MRMS , ndfd ")
    assert binary_sampling_enabled("mrms") is False
    assert binary_sampling_enabled("ndfd") is False
    assert binary_sampling_enabled("gfs") is True

    # The retired opt-in allowlist must have no effect in either direction.
    monkeypatch.setenv("CARTOSKY_BINARY_SAMPLING_MODELS", "gfs,hrrr")
    assert binary_sampling_enabled("mrms") is False
    assert binary_sampling_enabled("gfs") is True
    monkeypatch.delenv("CARTOSKY_COG_SAMPLING_MODELS", raising=False)
    assert binary_sampling_enabled("mrms") is True
