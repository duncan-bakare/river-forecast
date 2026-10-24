"""Assemble the live Clonmel forecast document (site/data/forecast.json).

Forecast times are hour-start labels of hourly means (the mean level over [t, t+1 h)), like the training data,
and are UTC. `data_through` is the latest hour used for all inputs. The site hides the numbers whenever
`status` is not "ok" or the document is old, so every failure path ends in "no forecast", never a stale one.
"""

import numpy as np
import pandas as pd

from river_forecast.model import dataset as ds

SCHEMA = 1
OBSERVED_HOURS = 96
MAX_LEVEL_AGE_H = 3  # latest Clonmel reading older than this -> unavailable
MAX_ISSUE_LAG_H = 4  # data_through older than this -> unavailable
MIN_UPSTREAM = 10  # of 14 upstream stations, else degraded
MIN_RAIN_GAUGES = 12  # of the QC'd gauges, else degraded

THRESHOLDS = [
    {
        "id": "p1",
        "label": "OPW statistical level, exceeded 1% of the time (Hydro-Data percentile 1)",
        "level_m": 16.702,
        "is_warning_level": False,
    },
    {
        "id": "amax_median",
        "label": "OPW statistical level, median annual maximum",
        "level_m": 16.914,
        "is_warning_level": False,
    },
]
DISCLAIMER = (
    "Experimental research forecast. Not an official flood warning and not a flood forecasting service. "
    "For warnings see floodinfo.ie, met.ie/warnings and your local authority."
)


def _iso(ts):
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(x, nd=3):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def pick_issue_hour(frame, rain_gauges_used):
    """Latest hour with a Clonmel level and catchment rain (>= 5 gauges, already enforced in `rain`)."""
    ok = frame[f"lvl_{ds.TARGET}"].notna() & frame["rain"].notna()
    return ok[ok].index.max() if ok.any() else None


def make_forecast(bundle, frame, now, latest_reading, level_errors, rain_gauges, warnings_doc):
    """frame: hourly frame from `inputs.build_frame`; latest_reading: (time, value) of Clonmel's newest 15-minute
    reading, or None; rain_gauges: (used at issue hour, total)."""
    now = pd.Timestamp(now).tz_convert("UTC")
    reasons, status = [], "ok"
    y = frame[f"lvl_{ds.TARGET}"]
    t = pick_issue_hour(frame, rain_gauges[0])

    doc = {
        "schema": SCHEMA,
        "issued_at": _iso(now),
        "status": "unavailable",
        "reasons": reasons,
        "disclaimer": DISCLAIMER,
        "station": {"id": ds.TARGET, "name": "Clonmel", "river": "Suir", "unit": "m OD (as published by OPW)"},
        "thresholds": THRESHOLDS,
        "model": {
            "version": bundle.meta["version"],
            "trained_through": bundle.meta["trained_through"],
            "interval": bundle.meta["interval_note"],
            "verification_url": "verification.html",
        },
        "warnings": warnings_doc,
        "latest_observation": {
            "time": _iso(latest_reading[0]) if latest_reading else None,
            "level_m": _num(latest_reading[1]) if latest_reading else None,
        },
        "observed": [{"t": _iso(i), "v": _num(v)} for i, v in y.iloc[-OBSERVED_HOURS:].items() if np.isfinite(v)],
        "forecasts": [],
        "inputs": {},
    }
    if level_errors:
        reasons.append(f"could not read {len(level_errors)} level station(s): {', '.join(sorted(level_errors))}")
    if latest_reading is None or (now - pd.Timestamp(latest_reading[0])) > pd.Timedelta(hours=MAX_LEVEL_AGE_H):
        reasons.append(f"latest Clonmel reading is older than {MAX_LEVEL_AGE_H} h")
        return doc
    if t is None:
        reasons.append("no hour with both a Clonmel level and catchment rain")
        return doc
    if (now - t) > pd.Timedelta(hours=MAX_ISSUE_LAG_H):
        reasons.append(f"inputs are more than {MAX_ISSUE_LAG_H} h old (latest complete hour {_iso(t)})")
        return doc

    X = ds.features(frame)
    row = X.loc[[t]]
    up_total = len(ds.UPSTREAM)
    up_used = int(sum(np.isfinite(row[f"u_{s}"].iloc[0]) for s in ds.UPSTREAM))
    doc["inputs"] = {
        "upstream_stations_used": up_used,
        "upstream_stations_total": up_total,
        "rain_gauges_used": int(rain_gauges[0]),
        "rain_gauges_total": int(rain_gauges[1]),
    }
    if up_used < MIN_UPSTREAM:
        status = "degraded"
        reasons.append(f"only {up_used} of {up_total} upstream stations available")
    if rain_gauges[0] < MIN_RAIN_GAUGES:
        status = "degraded"
        reasons.append(f"only {rain_gauges[0]} rain gauges available at the issue hour")

    y_t = float(y.loc[t])
    for h in bundle.horizons:
        point, lo, hi = bundle.lgbm_dy(h, row)
        lin = bundle.linear[h]
        lin_dy = float(lin.predict(row).iloc[0])
        ver = bundle.meta["verification"][str(h)]
        doc["forecasts"].append(
            {
                "horizon_h": h,
                "valid_at": _iso(t + pd.Timedelta(hours=h)),
                "lightgbm": {
                    "level_m": _num(y_t + point[0]),
                    "lo_m": _num(y_t + lo[0]),
                    "hi_m": _num(y_t + hi[0]),
                },
                "linear": {"level_m": _num(y_t + lin_dy) if np.isfinite(lin_dy) else None},
                "persistence": {"level_m": _num(y_t)},
                "verification": ver,
            }
        )
    doc.update(
        {
            "status": status,
            "data_through": _iso(t),
            "current_level_m": _num(y_t),
        }
    )
    return doc
