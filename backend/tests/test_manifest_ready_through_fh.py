from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.base import BaseModelPlugin, ModelCapabilities, VariableCapability
from app.services import scheduler as scheduler_module

MODEL = "gfs"
RUN_ID = "20260406_12z"
VAR_ID = "tmp2m"


def _write_sidecars(data_root: Path, published_fhs: list[int]) -> None:
    for fh in published_fhs:
        sidecar_path = scheduler_module._frame_sidecar_path(
            data_root, MODEL, RUN_ID, VAR_ID, fh
        )
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(
            json.dumps({"fh": fh, "units": "F", "kind": "continuous"})
        )


def _bake(tmp_path: Path, expected_fhs: list[int], published_fhs: list[int]) -> dict:
    data_root = tmp_path / "data"
    _write_sidecars(data_root, published_fhs)
    scheduler_module._write_run_manifest(
        data_root=data_root,
        model=MODEL,
        run_id=RUN_ID,
        targets=[(VAR_ID, fh) for fh in expected_fhs],
        plugin=None,
    )
    manifest_path = data_root / "manifests" / MODEL / f"{RUN_ID}.json"
    return json.loads(manifest_path.read_text())["variables"][VAR_ID]


def test_contiguous_publish_sets_ready_through_last_published(tmp_path: Path) -> None:
    entry = _bake(tmp_path, expected_fhs=[0, 3, 6, 9, 12], published_fhs=[0, 3, 6])

    assert entry["ready_through_fh"] == 6
    assert entry["expected_max_fh"] == 12


def test_out_of_order_publish_stops_at_first_hole(tmp_path: Path) -> None:
    entry = _bake(tmp_path, expected_fhs=[0, 3, 6, 9, 12], published_fhs=[0, 3, 9, 12])

    published = [frame["fh"] for frame in entry["frames"]]
    assert published == [0, 3, 9, 12]
    # The boundary is the contiguity edge, NOT the greatest published fh.
    assert entry["ready_through_fh"] == 3
    assert entry["ready_through_fh"] != max(published)
    assert entry["expected_max_fh"] == 12


def test_cadence_change_is_not_a_hole(tmp_path: Path) -> None:
    expected_fhs = list(range(0, 241, 3)) + list(range(246, 385, 6))
    published_fhs = [fh for fh in expected_fhs if fh <= 246]

    entry = _bake(tmp_path, expected_fhs=expected_fhs, published_fhs=published_fhs)

    # 241..245 are not expected, so 240 -> 246 is contiguous.
    assert entry["ready_through_fh"] == 246
    assert entry["expected_max_fh"] == 384


def test_nothing_published_yields_null_ready_with_horizon(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    scheduler_module._write_run_manifest(
        data_root=data_root,
        model=MODEL,
        run_id=RUN_ID,
        targets=[(VAR_ID, fh) for fh in (0, 3, 6, 9, 12)],
        plugin=None,
    )
    raw = (data_root / "manifests" / MODEL / f"{RUN_ID}.json").read_text()
    entry = json.loads(raw)["variables"][VAR_ID]

    assert entry["frames"] == []
    assert entry["ready_through_fh"] is None
    assert '"ready_through_fh": null' in raw
    assert entry["expected_max_fh"] == 12


class _CappedPlugin(BaseModelPlugin):
    def target_fhs(self, cycle_hour: int) -> list[int]:
        del cycle_hour
        return [0, 3, 6, 9, 12]


def test_variable_max_fh_constraint_caps_both_scalars(tmp_path: Path) -> None:
    plugin = _CappedPlugin(
        id=MODEL,
        name="GFS",
        capabilities=ModelCapabilities(
            model_id=MODEL,
            name="GFS",
            variable_catalog={
                VAR_ID: VariableCapability(
                    var_key=VAR_ID,
                    name="2m Temperature",
                    constraints={"max_fh": 6},
                )
            },
        ),
    )
    expected_fhs = plugin.scheduled_fhs_for_var(VAR_ID, 12)
    assert expected_fhs == [0, 3, 6]

    entry = _bake(tmp_path, expected_fhs=expected_fhs, published_fhs=[0, 3, 6])

    assert entry["ready_through_fh"] == 6
    assert entry["expected_max_fh"] == 6


def test_observed_variables_omit_both_keys(tmp_path: Path) -> None:
    """Observed/valid models have no expected fh list, so neither key is baked.

    Their entries are written by the observed publishers; ``_write_run_manifest``
    only preserves them (an observed plugin contributes no targets at all).
    """
    data_root = tmp_path / "data"
    manifest_path = data_root / "manifests" / MODEL / f"{RUN_ID}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "contract_version": "3.0",
                "model": MODEL,
                "run": RUN_ID,
                "variables": {
                    "reflectivity": {
                        "display_name": "Reflectivity",
                        "kind": "continuous",
                        "units": "dBZ",
                        "expected_frames": 1,
                        "available_frames": 1,
                        "frames": [{"fh": 0, "valid_time": "2026-04-06T12:00:00Z"}],
                    }
                },
            }
        )
    )

    _write_sidecars(data_root, [0])
    scheduler_module._write_run_manifest(
        data_root=data_root,
        model=MODEL,
        run_id=RUN_ID,
        targets=[(VAR_ID, 0)],
        plugin=None,
    )

    variables = json.loads(manifest_path.read_text())["variables"]
    observed_entry = variables["reflectivity"]
    assert "ready_through_fh" not in observed_entry
    assert "expected_max_fh" not in observed_entry
    # The forecast variable in the same bake still carries both keys.
    assert variables[VAR_ID]["ready_through_fh"] == 0
    assert variables[VAR_ID]["expected_max_fh"] == 0


def test_observed_plugin_contributes_no_expected_targets() -> None:
    from app.models.mrms import MRMS_MODEL

    assert MRMS_MODEL.target_fhs(0) == []
    assert MRMS_MODEL.scheduled_fhs_for_var("reflectivity", 0) == []
