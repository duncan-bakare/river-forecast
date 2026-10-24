"""Rise-aware conformal regimes for the Clonmel flood intervals (D22). Needs the saved models and
predictions from analysis/phase2_rerun.py (stored next to the curated dir; not in the repo).

Usage: python analysis/phase2_intervals.py <curated dir>
Regimes (by issue-time state): `rising` = 6 h rise >= RISE_M, any level; `high` = level >= 15.74 m and not
rising; `normal` = the rest. The correction for each regime is a split-conformal quantile of CQR scores,
evaluated out-of-fold over the flood events exactly as in the re-run (two folds, alternating by size).
RISE_M was fixed *after* seeing that rising limbs had the worst coverage in the re-run, so it is not a
blind choice; the out-of-fold evaluation protects the correction values, not that design decision.
"""

import json
import pathlib
import pickle
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from river_forecast.model import dataset as ds
from river_forecast.model import metrics as M
from river_forecast.model.models import conformal_quantile, cqr_scores

STORE = pathlib.Path(sys.argv[1]).parent
OUT = ROOT / "docs" / "phase2" / "rerun"
HORIZONS = [6, 12, 24, 48]
HIGH, RISE_M, ALPHA = 15.74, 0.10, 0.1
TRAIN_END, CAL_END = pd.Timestamp("2019-01-01", tz="UTC"), pd.Timestamp("2022-01-01", tz="UTC")

F = pd.read_parquet(STORE / "clonmel_hourly.parquet")
y = F[f"lvl_{ds.TARGET}"]
X = ds.features(F)
idx = F.index
T = ds.targets(F, HORIZONS)
events = ds.flood_events(y, 8)
events["fold"] = np.arange(len(events)) % 2
in_events = pd.Series(ds.in_windows(idx, events), idx)
split = pd.Series(np.where(idx < TRAIN_END, "train", np.where(idx < CAL_END, "cal", "test")), idx)
test_valid, checked_valid = split == "test", F.target_checked
fold_valid = {f: pd.Series(ds.in_windows(idx, events[events.fold == f]), idx) & in_events for f in (0, 1)}
models = pickle.load(open(STORE / "phase2_rerun_models.pkl", "rb"))
preds = pd.read_parquet(STORE / "phase2_rerun_preds.parquet")

rising = X["dy_6"] >= RISE_M
high = (X["y"] >= HIGH) & ~rising
rows, corr_out = [], {}
for h in HORIZONS:
    m = models[("lightgbm", h)]
    t = T[h]
    lo_raw, hi_raw = m.raw_interval(X)
    sc = pd.Series(cqr_scores(lo_raw.to_numpy(), hi_raw.to_numpy(), t.dy.to_numpy()), idx)
    usable = t.checked & t.dy.notna()
    cal = usable & (split == "cal") & ~in_events
    fold_rows = {f: usable & pd.Series(ds.in_windows(idx, events[events.fold == f]), idx) for f in (0, 1)}

    def corr(use, h=h, sc=sc, cal=cal, fold_rows=fold_rows, m=m):
        out = {"normal": m.q_["normal"]}
        for name, mask in (("high", high), ("rising", rising)):
            pool = [sc[cal & mask]] + [sc[fold_rows[f] & mask] for f in use]
            out[name] = conformal_quantile(np.concatenate(pool), ALPHA)
        return out

    q = {"from_fold0": corr([0]), "from_fold1": corr([1]), "all_events": corr([0, 1])}
    corr_out[h] = {
        **q,
        "n_cal": {"high": int((cal & high).sum()), "rising": int((cal & rising).sum())},
        "n_events": {
            "high": int(sum((fold_rows[f] & high).sum() for f in (0, 1))),
            "rising": int(sum((fold_rows[f] & rising).sum() for f in (0, 1))),
        },
    }

    def band(qd, h=h, m=m, lo_raw=lo_raw, hi_raw=hi_raw):
        c = np.select([rising, high], [qd["rising"], qd["high"]], qd["normal"])
        return (y + lo_raw - c).shift(h), (y + hi_raw + c).shift(h)

    common = y.notna() & preds[[f"{k}_{h}" for k in ("persistence", "upstream_linear", "lightgbm")]].notna().all(axis=1)
    b0, b1 = band(q["from_fold1"]), band(q["from_fold0"])  # fold-0 events use fold-1's correction
    oof_lo = b0[0].where(fold_valid[0], b1[0].where(fold_valid[1]))
    oof_hi = b0[1].where(fold_valid[0], b1[1].where(fold_valid[1]))
    ball = band(q["all_events"])
    base_lo, base_hi = (y + m.predict_interval(X)[0]).shift(h), (y + m.predict_interval(X)[1]).shift(h)
    issue_rising = rising.shift(h)  # state at issue time, indexed by valid time
    flood = in_events & common
    sets = {
        "flood events, out-of-fold": (flood, oof_lo, oof_hi),
        "flood events, rising at issue, out-of-fold": (flood & issue_rising.fillna(False).astype(bool), oof_lo, oof_hi),
        "flood events, all-events (in-sample)": (flood, ball[0], ball[1]),
        "test, checked": (test_valid & checked_valid & ~in_events & common, ball[0], ball[1]),
        "test, provisional": (test_valid & ~checked_valid & common, ball[0], ball[1]),
    }
    for name, (mask, lo, hi) in sets.items():
        cov, w = M.interval_coverage(y[mask], lo[mask], hi[mask])
        co, wo = M.interval_coverage(y[mask], base_lo[mask], base_hi[mask])
        rows.append(
            {
                "set": name,
                "h": h,
                "n": int(mask.sum()),
                "cov_first_run": co,
                "cov_rise_aware": cov,
                "width_first_run": wo,
                "width_rise_aware": w,
            }
        )

res = pd.DataFrame(rows).sort_values(["set", "h"], kind="stable")
res.to_csv(OUT / "intervals_rise_aware.csv", index=False)
(OUT / "interval_corrections_rise_aware.json").write_text(json.dumps(corr_out, indent=1), encoding="utf-8")
print(res.round(3).to_string(index=False))
print(
    json.dumps(
        {h: {k: v for k, v in c.items() if k in ("all_events", "n_cal", "n_events")} for h, c in corr_out.items()},
        indent=1,
    )
)
