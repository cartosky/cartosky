"""Manually run the fast-path canary for one run (WP5 dev observation).

The automatic trigger waits for the delayed build to complete, which never
happens on the vars-restricted dev scheduler — this drives the same
compare_frame code path directly. Run on the dev box:

  sudo env CARTOSKY_DATA_ROOT=/opt/cartosky-dev/data-fastpath \
    CARTOSKY_FASTPATH_MODELS=ecmwf \
    PYTHONPATH=/opt/cartosky-dev/backend \
    /opt/cartosky-dev/.venv/bin/python \
    /opt/cartosky-dev/scripts/spikes/openmeteo_9km/phase5_canary_manual.py 20260805_12z
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.services.fastpath.canary import (
    DEFAULT_CANARY_VARS,
    PREFERRED_CANARY_FH,
    compare_frame,
    reference_catalog_for,
)
from app.services.sources.openmeteo import source as om_source
from app.services.sources.openmeteo.catalog import ECMWF_IFS_CATALOG

run_id = sys.argv[1] if len(sys.argv) > 1 else "20260805_12z"
run_dt = datetime.strptime(run_id, "%Y%m%d_%Hz").replace(tzinfo=timezone.utc)
data_root = Path(__import__("os").environ["CARTOSKY_DATA_ROOT"])
reference = reference_catalog_for(ECMWF_IFS_CATALOG)

for var_id in DEFAULT_CANARY_VARS:
    for domain in ("na", "global"):
        outcome = compare_frame(
            data_root=data_root,
            model_id="ecmwf",
            run_dt=run_dt,
            run_id=run_id,
            var_id=var_id,
            domain=domain,
            fh=PREFERRED_CANARY_FH,
            catalog=ECMWF_IFS_CATALOG,
            reference_catalog=reference,
            om_source=om_source,
        )
        if outcome.result is not None:
            r = outcome.result
            print(
                f"{var_id:14s} {domain:6s} fh{PREFERRED_CANARY_FH:03d}  "
                f"bias={r.bias:+.4f} mae={r.mae:.4f} synoptic={r.synoptic_mae:.4f} "
                f"corr={r.corr if r.corr is not None else float('nan'):.5f} "
                f"n={r.n}  {'FAILED: ' + ','.join(r.failures) if r.failed else 'ok'}"
            )
        else:
            print(f"{var_id:14s} {domain:6s} fh{PREFERRED_CANARY_FH:03d}  skip: {outcome.skip_reason}")
