"""Live inputs for the Clonmel forecast.

Levels: OPW real-time 7-day CSVs (15-min, UTC) -> hourly means, same convention as training
(hour-start label, mean of the 15-min values in the hour, jumps over 1 m/h dropped).
Rain: OPW Hydro-Data `Precip/week.json` (hourly totals, hour-start label, identical to the sum of the
15-min values: checked against the archive) for the last ~7 days, and daily totals from `year.json`
spread evenly over the hours of older days, for the 30-day window. Same gauge QC as training
(`river_forecast.features.rain_qc`): 50 mm spike filter, flagged gauge-years removed, mean of at least
5 reporting gauges.
"""

import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from river_forecast import config
from river_forecast.features import rain_qc
from river_forecast.ingest import parse
from river_forecast.model import dataset as ds

LEVEL_STATIONS = (ds.TARGET, *ds.UPSTREAM)
MIN_READINGS = 3  # of 4 per hour
WINDOW_H = 24 * 31  # 720 h rain window plus slack
MIN_DAILY_ACCURACY = 80.0  # % of the day's readings behind a Hydro-Data daily total
HOURLY_SPIKE_MM = rain_qc.SPIKE_MM  # the training filter was per 15 min; > 50 mm in an hour is implausible too


def hourly_levels(raw):
    """OPW CSV bytes -> (hourly mean Series, latest reading as (time, value) or None). Hourly means are UTC with
    an hour-start label; hours with fewer than MIN_READINGS readings and sensor jumps over SPIKE_M_PER_H are NaN."""
    _, rows = parse.opw_csv(raw)
    s = pd.Series({r["datetime"]: r["value"] for r in rows if r["datetime"] is not None}, dtype=float).sort_index()
    if s.empty:
        return s, None
    latest = (s.dropna().index.max(), float(s.dropna().iloc[-1])) if s.notna().any() else None
    grouped = s.resample("h")
    mean, count = grouped.mean(), grouped.count()
    mean[count < MIN_READINGS] = np.nan
    mean[mean.diff().abs() > ds.SPIKE_M_PER_H] = np.nan
    return mean, latest


def fetch_levels(fetch, stations=LEVEL_STATIONS, workers=6):
    """-> (hourly levels DataFrame (lvl_<station> columns), {station: error}, Clonmel's latest (time, value))."""

    def one(st):
        try:
            return st, hourly_levels(fetch(f"{config.OPW_BASE}/data/week/{st}_OD.csv")), None
        except Exception as e:
            return st, None, e

    cols, errors, latest = {}, {}, None
    with ThreadPoolExecutor(workers) as pool:
        for st, res, err in pool.map(one, stations):
            if err is not None or res is None or res[0].empty:
                errors[st] = str(err) if err else "empty"
                continue
            cols[f"lvl_{st}"] = res[0]
            if st == ds.TARGET:
                latest = res[1]
    return pd.DataFrame(cols), errors, latest


def parse_hourly_rain(raw):
    data = json.loads(raw)[0]["data"]
    return pd.Series({pd.Timestamp(t): v for t, v in data}, dtype=float).sort_index()


def parse_daily_rain(raw):
    """-> (daily totals Series, accuracy % Series) indexed by UTC day start."""
    data = json.loads(raw)[0]["data"]
    idx = [pd.Timestamp(r[0]) for r in data]
    return (
        pd.Series([r[1] for r in data], index=idx, dtype=float),
        pd.Series([r[2] if len(r) > 2 else 100.0 for r in data], index=idx, dtype=float),
    )


def gauge_hourly(hourly, daily, accuracy, index):
    """One gauge's hourly series over `index`: hourly totals where present, else the daily total / 24."""
    out = pd.Series(np.nan, index=index)
    for day, mm in daily.items():
        if accuracy.get(day, 100.0) >= MIN_DAILY_ACCURACY and not np.isnan(mm):
            out.loc[day : day + pd.Timedelta(hours=23)] = mm / 24.0
    h = hourly.reindex(index)
    out[h.notna()] = h[h.notna()]
    out[out > HOURLY_SPIKE_MM] = np.nan
    return out


def catchment_rain(gauges, flags, min_gauges=5):
    """gauges: DataFrame hourly mm, one column per gauge -> (catchment mean Series, gauges used per hour)."""
    clean = rain_qc.apply_flags(gauges, flags)
    return rain_qc.catchment_mean(gauges, flags, min_gauges), clean.count(axis=1)


def carry_forward_flags(flags, year):
    """Gauge-year flags for `year`. The flags file is built from history, so a new year has none yet:
    carry a gauge's persistent step bias forward until the flags are regenerated."""
    f = flags
    if (f.year == year).any() or f.empty:
        return f
    last = f[(f.reason == "step_bias") & (f.year == f.year.max())]
    return pd.concat([f, last.assign(year=year)], ignore_index=True)


def build_frame(levels, rain, end):
    """Hourly frame ending at hour-start `end` in the layout `dataset.features` expects."""
    index = pd.date_range(end=end, periods=WINDOW_H, freq="h", tz="UTC")
    frame = pd.DataFrame(index=index)
    for st in LEVEL_STATIONS:
        col = f"lvl_{st}"
        frame[col] = levels[col].reindex(index) if col in levels else np.nan
    frame["rain"] = rain.reindex(index)
    frame["target_checked"] = False
    return frame
