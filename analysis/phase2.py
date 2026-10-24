"""Phase 2: Clonmel (16011) level forecasts at 6/12/24/48 h.

Usage: python analysis/phase2.py <curated dir>
Writes docs/phase2/report.md and figures. Fully time-based: train < 2019, calibrate 2019-2021,
test >= 2022. The 8 largest floods since 2006 are removed from train/calibration (with
-5 d/+3 d windows) and scored as backtests.
"""

import json
import pathlib
import sys
import time

import matplotlib

matplotlib.use("Agg")
import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from river_forecast.model import dataset as ds
from river_forecast.model import metrics as M
from river_forecast.model.models import LGBMQuantile, Persistence, UpstreamLinear

CUR = pathlib.Path(sys.argv[1]).as_posix()
OUT = ROOT / "docs" / "phase2"
OUT.mkdir(parents=True, exist_ok=True)
HORIZONS = [6, 12, 24, 48]
TRAIN_END, CAL_END = pd.Timestamp("2019-01-01", tz="UTC"), pd.Timestamp("2022-01-01", tz="UTC")
N_EVENTS = 8
# OPW Hydro-Data statistics for Clonmel S (alarmlevel.json, 2026-10-24): 1%-exceedance level and
# median annual maximum. These are statistical thresholds, not flood-warning levels.
THRESHOLDS = {"P1 (16.702 m)": 16.702, "AMAX median (16.914 m)": 16.914}
HIGH_REGIME = 15.74  # OPW 10%-exceedance level, used for Mondrian conformal regimes
lines = []


def md(s=""):
    lines.append(s)


def table(df, floatfmt=3):
    md("| " + " | ".join(map(str, df.columns)) + " |")
    md("|" + "---|" * len(df.columns))
    for row in df.itertuples(index=False):
        cells = [f"{v:.{floatfmt}f}" if isinstance(v, float) else str(v) for v in row]
        md("| " + " | ".join(cells) + " |")
    md()


t_start = time.time()
cache = pathlib.Path(CUR).parent / "clonmel_hourly.parquet"
frame = pd.read_parquet(cache) if cache.exists() else ds.load_hourly(CUR)[0]
y = frame[f"lvl_{ds.TARGET}"]
X = ds.features(frame)
T = ds.targets(frame, HORIZONS)

# ---- events ---------------------------------------------------------------
events = ds.flood_events(y, N_EVENTS)
db = duckdb.connect()
db.sql("SET TimeZone='UTC'")
full = (
    db.sql(f"""select date_trunc('hour', timestamp) t, avg(value) v
               from read_parquet('{CUR}/opw_hydrodata/snapshot=*/param=S/station=16011.parquet')
               where quality_status in ('checked', 'provisional') group by 1 order by 1""")
    .df()
    .set_index("t")
    .v.asfreq("h")
)
record_top = ds.flood_events(full, 10)
in_events = ds.in_windows(frame.index, events)

idx = frame.index
split = pd.Series(np.where(idx < TRAIN_END, "train", np.where(idx < CAL_END, "cal", "test")), idx)

# ---- fit + predict ----------------------------------------------------------
preds, intervals, fitted = {}, {}, {}
oracle_preds = {}
for h in HORIZONS:
    t = T[h]
    ok_label = t.checked & t.dy.notna() & ~in_events
    tr = (split == "train") & ok_label
    ca = (split == "cal") & ok_label
    models = [Persistence(), UpstreamLinear(), LGBMQuantile(HIGH_REGIME)]
    for m in models:
        if isinstance(m, LGBMQuantile):
            m.fit(X[tr], t.dy[tr], X[ca], t.dy[ca])
            lo, hi = m.predict_interval(X)
            intervals[h] = ((y + lo).shift(h), (y + hi).shift(h))  # by valid time
        else:
            m.fit(X[tr], t.dy[tr])
        dy_hat = m.predict(X)
        preds[(m.name, h)] = (y + dy_hat).shift(h)  # forecast indexed by valid time
        fitted[(m.name, h)] = m
    # Upper bound: LightGBM given the *observed* future rain, as if rainfall forecasts were perfect.
    Xo = X.assign(rain_future=ds.future_rain(frame, h))
    mo = LGBMQuantile(HIGH_REGIME).fit(Xo[tr], t.dy[tr], Xo[ca], t.dy[ca])
    oracle_preds[h] = (y + mo.predict(Xo)).shift(h)
    print(f"h={h} fitted; lgbm trees={fitted[('lightgbm', h)].point.best_iteration_}", flush=True)

MODELS = ["persistence", "upstream_linear", "lightgbm"]
obs = y  # observed level by valid time
checked_valid = frame.target_checked
valid_split = split  # by valid time
event_mask_valid = pd.Series(in_events, idx)


def eval_sets():
    test = valid_split == "test"
    return {
        "test, checked labels (2022-2026)": test & checked_valid & ~event_mask_valid,
        "test, provisional labels (2026)": test & ~checked_valid & obs.notna(),
        "flood backtests (8 events, -5 d/+3 d)": event_mask_valid,
    }


def bootstrap_skill(o, f, r, block="7D", n=300, seed=0):
    df = pd.DataFrame({"o": o, "f": f, "r": r}).dropna()
    if len(df) < 50:
        return (float("nan"), float("nan"))
    groups = [g for _, g in df.groupby(pd.Grouper(freq=block)) if len(g)]
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        pick = pd.concat([groups[i] for i in rng.integers(0, len(groups), len(groups))])
        vals.append(1 - ((pick.f - pick.o) ** 2).mean() / ((pick.r - pick.o) ** 2).mean())
    return tuple(np.percentile(vals, [5, 95]))


# ---- report ------------------------------------------------------------------
md("# Phase 2: Clonmel (16011) level forecasts")
md()
md(f"Generated by `analysis/phase2.py` ({pd.Timestamp.now(tz='UTC'):%Y-%m-%d}). Hourly data {idx.min():%Y-%m-%d} to")
md(f"{idx.max():%Y-%m-%d}; levels in m OD. Forecasts are of the Clonmel level at issue time + h.")
md()
md("## Set-up")
md()
md("- **Horizons 6, 12, 24, 48 h**, chosen from measured peak travel times to Clonmel (median over the 40")
md("  largest events since 2006). Knocklofty about 4 h and Newcastle about 7 h, so **6 h** is about the main-stem")
md("  lag and the ~5 h rain-to-rise lag. Caher Park about 9 h and New Bridge about 13 h, so **12 h**. Aherlow, Tar")
md("  and Clodiagh 15-18 h, and Multeen, Clobanna and Nire 23-27 h, so **24 h** is the longest lead the gauge")
md("  network can supply. **48 h** is beyond every travel time and is there to show where models stop beating")
md("  persistence. It matches HARMONIE's +48 h, which we can't yet use because the forecast archive only began")
md("  on 2026-10-24.")
md("- **Splits (time only):** train < 2019, calibration (early stopping + conformal) 2019-2021, test >= 2022.")
md(f"  The {N_EVENTS} largest independent floods since 2006 are cut out of train/calibration (peak -5 d/+3 d) and")
md("  scored as backtests. Only one (2022-11) falls in the test period, two in calibration and five in the training")
md("  period, so most backtests are held-out *events*, not strictly forward-in-time forecasts.")
md("- **Models:** persistence; upstream-lag linear model (OLS on Clonmel's and 8 upstream stations' level")
md("  changes over 6/12/24 h, no rain); LightGBM on all upstream levels and changes, QC'd catchment rain over")
md("  1 h-30 d, and season. Every model predicts the *change* in level, so the trees aren't asked to")
md("  extrapolate absolute levels they never saw. Intervals: 90% conformalised quantile regression (5%/95%")
md("  LightGBM quantiles, calibrated on 2019-2021, separately below and above the 10%-exceedance level).")
md("- **Rain:** only *observed* rain up to issue time. No rainfall forecast is used, because none is archived")
md("  yet. A separate LightGBM fed the *observed* future rain gives an upper bound for perfect rain forecasts.")
md("  It isn't deployable and isn't counted as a result.")
md()
md("**Flood events.** Largest independent peaks in the whole Clonmel record (1953-), for context:")
md()
table(
    record_top.assign(
        peak_time=record_top.peak_time.dt.strftime("%Y-%m-%d %H:%M"), in_window=record_top.peak_time >= idx.min()
    )[["peak_time", "peak_m", "in_window"]]
)
md(f"Held out as backtests (top {N_EVENTS} since 2006, where rain-gauge data exists):")
md()
ev_tab = events.assign(
    peak_time=events.peak_time.dt.strftime("%Y-%m-%d %H:%M"),
    period=[valid_split.get(t, "?") for t in events.peak_time],
)[["peak_time", "peak_m", "period"]]
table(ev_tab)
md("The 2000 and 2004 floods (2nd and 3rd largest on record) pre-date the rain gauges and are not in the")
md("modelling window.")
md()

# --- checked vs provisional skew
md("## Checked vs provisional data")
md()
dy1 = y.diff().abs()
prov = (~checked_valid) & y.notna() & (idx >= pd.Timestamp("2026-01-01", tz="UTC"))
chk = checked_valid & (idx >= pd.Timestamp("2022-01-01", tz="UTC"))
sk = pd.DataFrame(
    {
        "set": ["checked 2022-2025", "provisional 2026"],
        "hours": [int(chk.sum()), int(prov.sum())],
        "median abs dy 1h (mm)": [dy1[chk].median() * 1000, dy1[prov].median() * 1000],
        "p99.9 abs dy 1h (mm)": [dy1[chk].quantile(0.999) * 1000, dy1[prov].quantile(0.999) * 1000],
        "level p50 (m)": [y[chk].median(), y[prov].median()],
        "level max (m)": [y[chk].max(), y[prov].max()],
    }
)
table(sk)
md("Handling: (1) labels for training and calibration are **checked hours only**; (2) the same input")
md(
    f"preprocessing (hourly means, spike removal at > {ds.SPIKE_M_PER_H} m/h, rain QC) is applied whatever the status, so"
)
md("training and serving inputs go through the same pipeline; (3) provisional-label hours are scored separately")
md("below. Clonmel's provisional data is almost all 2026 (OPW checks with a lag of months), so the provisional")
md("set is ~4 months of mostly low flows. It tests input/label noise, not flood skill.")
md()

# --- skill tables
md("## Skill by horizon")
md()
md("NSE and KGE are on the forecast level. Because Clonmel's level is strongly autocorrelated, NSE is high even")
md("for persistence. **Skill vs persistence** (MSE skill score, with a 5-95% weekly block bootstrap) is the")
md("number that matters: 0 means no better than persistence.")
md()
skill_rows = []
for set_name, mask in eval_sets().items():
    for h in HORIZONS:
        ref = preds[("persistence", h)][mask]
        o = obs[mask]
        for m in MODELS:
            f = preds[(m, h)][mask]
            lo, hi = bootstrap_skill(o, f, ref) if m != "persistence" else (float("nan"), float("nan"))
            skill_rows.append(
                {
                    "set": set_name,
                    "h": h,
                    "model": m,
                    "n": int(pd.concat([o, f], axis=1).dropna().shape[0]),
                    "NSE": M.nse(o, f),
                    "KGE": M.kge(o, f),
                    "skill_vs_persist": M.skill_vs(o, f, ref) if m != "persistence" else 0.0,
                    "skill_5%": lo,
                    "skill_95%": hi,
                }
            )
        o_or = oracle_preds[h][mask]
        skill_rows.append(
            {
                "set": set_name,
                "h": h,
                "model": "lightgbm+perfect rain (bound)",
                "n": int(o_or.notna().sum()),
                "NSE": M.nse(o, o_or),
                "KGE": M.kge(o, o_or),
                "skill_vs_persist": M.skill_vs(o, o_or, ref),
                "skill_5%": float("nan"),
                "skill_95%": float("nan"),
            }
        )
skill = pd.DataFrame(skill_rows)
for set_name, g in skill.groupby("set", sort=False):
    md(f"**{set_name}**")
    md()
    table(g.drop(columns="set"))

# --- peaks
md("## Flood backtests: peak timing and size")
md()
md("For each event and lead h, the *fixed-lead trace* (all forecasts issued h hours before each valid time) is")
md("compared with the observed hydrograph over the event window. Timing in hours (+ = forecast peak late);")
md("size in m (forecast - observed).")
md()
peak_rows = []
for e in events.itertuples(index=False):
    for h in HORIZONS:
        row = {"event": f"{e.peak_time:%Y-%m-%d}", "obs_peak_m": e.peak_m, "h": h}
        for m in MODELS:
            pe = M.peak_error(obs, preds[(m, h)], e.start, e.end)
            row[f"{m}_timing_h"] = pe["timing_h"]
            row[f"{m}_size_m"] = round(pe["size_m"], 3)
        peak_rows.append(row)
peaks = pd.DataFrame(peak_rows)
summary = (
    peaks.groupby("h")
    .agg(
        **{
            f"{m}_{k}": (f"{m}_{k}_h" if k == "timing" else f"{m}_size_m", (lambda s: s.abs().median()))
            for m in MODELS
            for k in ("timing", "size")
        }
    )
    .reset_index()
)
md("Median absolute error over the 8 events:")
md()
table(summary.rename(columns=lambda c: c.replace("_timing", " abs timing h").replace("_size", " abs size m")))
md("Per event:")
md()
table(peaks)

# --- exceedance
md("## Exceedance detection and lead time")
md()
md("An onset is the first hour above the threshold (episodes < 48 h apart are merged). A model *catches* an onset at")
md("lead h if its lead-h forecast exceeds the threshold within +-3 h of the onset. Lead time is the longest such h.")
md("Persistence can only catch an onset once the level is already above the threshold, which means a lead of 0.")
md("Scored over the test period (2022-) plus the backtest windows. False-alarm episodes are forecast exceedances")
md("with no observed exceedance within 24 h, counted over the test period.")
md()
score_mask = (valid_split == "test") | event_mask_valid
det_rows = []
for tname, thr in THRESHOLDS.items():
    o = obs.where(score_mask)
    for m in MODELS:
        d = M.detection_lead(o.dropna(), {h: preds[(m, h)].where(score_mask) for h in HORIZONS}, thr)
        row = {"threshold": tname, "model": m, "onsets": len(d)}
        for h in HORIZONS:
            row[f"caught@{h}h"] = int((d.lead_h >= h).sum())
        row["median_lead_h"] = float(d.lead_h.median()) if len(d) else float("nan")
        test_only = valid_split == "test"
        for h in (12, 24):
            row[f"false_alarms@{h}h (test)"] = M.false_alarm_episodes(
                obs.where(test_only).dropna(), preds[(m, h)].where(test_only).dropna(), thr
            )
            c = M.contingency(obs[score_mask], preds[(m, h)][score_mask], thr)
            row[f"CSI@{h}h"] = round(c["CSI"], 3)
        det_rows.append(row)
det = pd.DataFrame(det_rows)
table(det)

# --- intervals
md("## Prediction intervals (LightGBM, nominal 90%)")
md()
int_rows = []
for set_name, mask in {
    **eval_sets(),
    "high flow (level >= 15.74 m), all test + backtests": (score_mask & (obs >= HIGH_REGIME)),
}.items():
    for h in HORIZONS:
        lo, hi = intervals[h]
        cov, width = M.interval_coverage(obs[mask], lo[mask], hi[mask])
        int_rows.append({"set": set_name, "h": h, "coverage": cov, "mean_width_m": width})
table(pd.DataFrame(int_rows))
md("Conformal quantiles (m) per regime: " + json.dumps({h: fitted[("lightgbm", h)].q_ for h in HORIZONS}))
md()

# --- figures
for e in events.head(4).itertuples(index=False):
    fig, axes = plt.subplots(1, 2, figsize=(12, 3.6), sharey=True)
    w = slice(e.start, e.end)
    for ax, h in zip(axes, (12, 24), strict=True):
        ax.plot(obs[w].index, obs[w], "k", lw=1.6, label="observed")
        for m, c in zip(MODELS, ("tab:gray", "tab:orange", "tab:blue"), strict=True):
            ax.plot(preds[(m, h)][w].index, preds[(m, h)][w], c=c, lw=1, label=m)
        lo, hi = intervals[h]
        ax.fill_between(lo[w].index, lo[w], hi[w], color="tab:blue", alpha=0.15, label="90% interval")
        for thr in THRESHOLDS.values():
            ax.axhline(thr, ls=":", c="r", lw=0.8)
        ax.set_title(f"{e.peak_time:%Y-%m-%d} flood, {h} h lead")
    axes[0].set_ylabel("m OD")
    axes[0].legend(fontsize=7, loc="upper left")
    fig.tight_layout()
    fname = f"event_{e.peak_time:%Y%m%d}.png"
    fig.savefig(OUT / fname, dpi=100)
    plt.close(fig)
    md(f"![{e.peak_time:%Y-%m-%d}]({fname})")
md()

lin = {h: round(fitted[("upstream_linear", h)].fallback_rate_, 3) for h in HORIZONS}
md(f"Upstream-linear fallback-to-persistence rate (missing upstream inputs), all hours: {lin}")
md(f"LightGBM trees (early stopping): { {h: fitted[('lightgbm', h)].point.best_iteration_ for h in HORIZONS} }")
md(f"Runtime {time.time() - t_start:.0f} s.")

(OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
skill.to_csv(OUT / "skill.csv", index=False)
peaks.to_csv(OUT / "peaks.csv", index=False)
det.to_csv(OUT / "exceedance.csv", index=False)
print("wrote", OUT / "report.md")
