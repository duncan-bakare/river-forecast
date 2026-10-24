"""Export what the live forecast Lambda needs from the Phase 2 work (run locally, after phase2_rerun.py):

  1. src/river_forecast/live/live_config.json: the rain gauges and their gauge-year QC flags (D17).
  2. A model bundle directory (LightGBM boosters, linear sub-models, calibration, verification summary),
     optionally uploaded to s3://<bucket>/<prefix>.

Usage: python scripts/export_live_assets.py <curated dir> <bundle dir> [--upload <bucket> <prefix>] [--version V]
"""

import argparse
import json
import pathlib
import pickle
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from river_forecast.live import bundle  # noqa: E402
from river_forecast.model import dataset as ds  # noqa: E402

HORIZONS = [6, 12, 24, 48]
RERUN = ROOT / "docs" / "phase2" / "rerun"


def verification_summary():
    skill = pd.read_csv(RERUN / "skill.csv")
    ints = pd.read_csv(RERUN / "intervals.csv")
    out = {}
    for h in HORIZONS:
        fl = skill[(skill.set.str.startswith("flood")) & (skill.h == h)].set_index("model")
        te = skill[(skill.set.str.startswith("test, checked")) & (skill.h == h)].set_index("model")
        i = ints[ints.h == h].set_index("set")
        out[str(h)] = {
            "skill_floods": {
                "lightgbm": round(float(fl.loc["lightgbm", "skill"]), 3),
                "lightgbm_5": round(float(fl.loc["lightgbm", "5%"]), 3),
                "lightgbm_95": round(float(fl.loc["lightgbm", "95%"]), 3),
                "linear": round(float(fl.loc["upstream_linear", "skill"]), 3),
            },
            "skill_test": {
                "lightgbm": round(float(te.loc["lightgbm", "skill"]), 3),
                "linear": round(float(te.loc["upstream_linear", "skill"]), 3),
            },
            "interval_coverage": {
                "nominal": 0.9,
                "floods_out_of_fold": round(float(i.loc["flood events (out-of-fold recal.)", "cov_shipped"]), 3),
                "test": round(float(i.loc["test, checked", "cov_shipped"]), 3),
            },
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("curated")
    ap.add_argument("out")
    ap.add_argument("--upload", nargs=2, metavar=("BUCKET", "PREFIX"))
    ap.add_argument("--version", default="v1-2026-10-24")
    a = ap.parse_args()
    store = pathlib.Path(a.curated).parent

    frame, flags = ds.load_hourly(pathlib.Path(a.curated).as_posix())
    gauges = sorted(
        p.stem.split("=")[1]
        for p in (pathlib.Path(a.curated) / "opw_hydrodata").glob("snapshot=*/param=Precip/station=*.parquet")
    )
    gauges = sorted(set(gauges))
    cfg = {
        "gauges": gauges,
        "flags": flags[["station_no", "year", "reason", "value"]].astype({"year": int}).values.tolist(),
    }
    (ROOT / "src/river_forecast/live/live_config.json").write_text(json.dumps(cfg), encoding="utf-8")
    print(f"live_config.json: {len(gauges)} gauges, {len(flags)} flagged gauge-years")

    models = pickle.load(open(store / "phase2_rerun_models.pkl", "rb"))
    X = ds.features(frame)
    corr_all = json.loads((RERUN / "interval_corrections.json").read_text(encoding="utf-8"))
    meta = {
        "version": a.version,
        "trained_through": "2018-12-31",
        "interval_note": (
            "90% conformalised quantile interval; high-flow correction recalibrated on the 8 largest past floods"
        ),
        "horizons": HORIZONS,
        "feature_columns": list(X.columns),
        "high_level": 15.74,
        "corrections": {
            str(h): {"normal": corr_all[str(h)]["normal"], "high": corr_all[str(h)]["all_events"]} for h in HORIZONS
        },
        "verification": verification_summary(),
    }
    bundle.export(models, a.out, meta)

    # parity: the exported boosters must reproduce the fitted sklearn models
    b = bundle.Bundle(a.out)
    rows = X.dropna(subset=["y"]).sample(300, random_state=0)
    for h in HORIZONS:
        g = models[("lightgbm", h)]
        point, lo, hi = b.lgbm_dy(h, rows)
        lo_ref, hi_ref = g.predict_interval(rows, q=meta["corrections"][str(h)])
        assert np.allclose(point, g.predict(rows), atol=1e-9), f"point mismatch at {h}"
        assert np.allclose(lo, lo_ref, atol=1e-9) and np.allclose(hi, hi_ref, atol=1e-9), f"interval mismatch at {h}"
        lin_ref = models[("upstream_linear", h)].predict(rows)
        lin = b.linear[h].predict(rows)
        assert np.allclose(lin.fillna(-9), lin_ref.fillna(-9), atol=1e-9), f"linear mismatch at {h}"
    print("bundle parity OK (point, interval, linear)")

    if a.upload:
        import boto3

        s3 = boto3.client("s3")
        bucket, prefix = a.upload
        for f in sorted(pathlib.Path(a.out).iterdir()):
            s3.upload_file(str(f), bucket, f"{prefix}{f.name}")
        print(f"uploaded {len(list(pathlib.Path(a.out).iterdir()))} files to s3://{bucket}/{prefix}")


if __name__ == "__main__":
    main()
