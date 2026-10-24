"""Model bundle for the live forecast: LightGBM boosters (text), linear sub-models and calibration, as a
directory of plain files, so the Lambda needs only numpy and pandas (trees are evaluated by `live.trees`)."""

import json
import pathlib

import numpy as np

from river_forecast.live.trees import TreeModel
from river_forecast.model.models import UpstreamLinear

KINDS = ("point", "lo", "hi")


def export(models, out_dir, meta):
    """models: {(name, h): fitted model} as produced by analysis/phase2_rerun.py; meta: dict (see
    scripts/export_bundle.py). Writes boosters, linear.json and meta.json; returns the meta written."""
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    linear = {}
    for h in meta["horizons"]:
        g = models[("lightgbm", h)]
        g.point.booster_.save_model(str(out / f"lgbm_h{h}_point.txt"), num_iteration=g.point.best_iteration_)
        g.lo.booster_.save_model(str(out / f"lgbm_h{h}_lo.txt"))
        g.hi.booster_.save_model(str(out / f"lgbm_h{h}_hi.txt"))
        linear[str(h)] = models[("upstream_linear", h)].to_dict()
    (out / "linear.json").write_text(json.dumps(linear), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


class Bundle:
    def __init__(self, directory):
        d = pathlib.Path(directory)
        self.meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.horizons = self.meta["horizons"]
        self.columns = self.meta["feature_columns"]
        self.high_level = self.meta["high_level"]
        self.boosters = {(h, k): TreeModel.from_file(d / f"lgbm_h{h}_{k}.txt") for h in self.horizons for k in KINDS}
        raw = json.loads((d / "linear.json").read_text(encoding="utf-8"))
        self.linear = {int(h): UpstreamLinear.from_dict(v) for h, v in raw.items()}

    def lgbm_dy(self, h, X):
        """Point and calibrated 90% interval for dy at horizon h. X: one-row (or n-row) feature DataFrame."""
        a = X[self.columns].to_numpy(dtype=float)
        point = self.boosters[(h, "point")].predict(a)
        lo, hi = self.boosters[(h, "lo")].predict(a), self.boosters[(h, "hi")].predict(a)
        c = self.meta["corrections"][str(h)]
        corr = np.where(X["y"].to_numpy(dtype=float) >= self.high_level, c["high"], c["normal"])
        return point, lo - corr, hi + corr
