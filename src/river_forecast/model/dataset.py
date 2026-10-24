"""Hourly modelling frame for Clonmel (16011) from the curated Parquet.

Quality handling:
- Values with quality_status in {unreliable, unusable, missing, undocumented} are dropped
  before hourly averaging (inputs and target).
- `target_checked` marks hours where every 15-min target value was OPW-checked. Training
  and calibration labels use only those hours; provisional hours are evaluated separately.
- Real-time inference will see provisional data only. The same spike filter is applied to
  inputs whatever their status, so training and serving see the same preprocessing.
"""

import duckdb
import numpy as np
import pandas as pd

from river_forecast.features import rain_qc

TARGET = "16011"
# Upstream of Clonmel, with peak travel times measured in Phase 2 (median hours, 40 largest events
# since 2006). Anner 16010 is excluded because it joins the Suir downstream of Clonmel. Ardfinnan 16155
# is excluded because it only starts in 2022.
UPSTREAM = {
    "16139": "Knocklofty",  # ~4 h
    "16137": "Newcastle",  # ~7 h
    "16009": "Caher Park",  # ~9 h
    "16008": "New Bridge",  # ~13 h
    "16007": "Killardry (Aherlow)",  # ~15 h
    "16012": "Tar Bridge",  # ~17 h
    "16003": "Rathkennan (Clodiagh)",  # ~17.5 h
    "16051": "Clobanna",  # ~23 h
    "16006": "Ballinaclogh (Multeen)",  # ~23.5 h
    "16005": "Aughnagross (Multeen)",  # ~24 h
    "16013": "Fourmilewater (Nire)",  # ~27 h
    "16001": "Athlummon (Drish)",  # ~9 h, wide spread
    "16002": "Beakstown",  # peaks after Clonmel
    "16004": "Thurles",  # peaks after Clonmel
}
USABLE = ("checked", "provisional")
SPIKE_M_PER_H = 1.0  # hourly level change above this is treated as a sensor spike


def _sql(db, q):
    return db.sql(q).df()


def load_hourly(curated, start="2006-01-01"):
    db = duckdb.connect()
    db.sql("SET TimeZone='UTC'")
    hd = f"{curated}/opw_hydrodata/snapshot=*"
    stations = "', '".join([TARGET, *UPSTREAM])
    lv = _sql(
        db,
        f"""select station_no, date_trunc('hour', timestamp) t,
                   avg(value) filter (where quality_status in ('checked', 'provisional')) v,
                   bool_and(quality_status = 'checked') all_checked
            from read_parquet('{hd}/param=S/*.parquet')
            where station_no in ('{stations}') and timestamp >= '{start}'
            group by all""",
    )
    levels = lv.pivot(index="t", columns="station_no", values="v").asfreq("h")
    checked = lv[lv.station_no == TARGET].set_index("t").all_checked.reindex(levels.index).fillna(False)

    for s in levels:
        jump = levels[s].diff().abs()
        levels.loc[jump > SPIKE_M_PER_H, s] = np.nan

    gauges = _sql(db, f"select station_no, lat, lon from read_parquet('{hd}/stations/param=Precip.parquet')")
    coords = {r.station_no: (r.lat, r.lon) for r in gauges.itertuples()}
    rain15 = f"""from read_parquet('{hd}/param=Precip/*.parquet')
                 where timestamp >= '{start}' and quality_status in ('provisional', 'checked')"""
    daily = _sql(
        db,
        f"""select station_no, date_trunc('day', timestamp) d,
                   sum(case when value > {rain_qc.SPIKE_MM} then null else value end) mm, count(value) n
            {rain15} group by all""",
    )
    D = daily[daily.n >= 90].pivot(index="d", columns="station_no", values="mm")
    flags = rain_qc.flag_gauge_years(D, coords)
    hourly = _sql(
        db,
        f"""select station_no, date_trunc('hour', timestamp) t,
                   sum(case when value > {rain_qc.SPIKE_MM} then null else value end) mm
            {rain15} group by all""",
    ).pivot(index="t", columns="station_no", values="mm")
    rain = rain_qc.catchment_mean(hourly, flags).reindex(levels.index)

    frame = levels.rename(columns=lambda s: f"lvl_{s}")
    frame["rain"] = rain
    frame["target_checked"] = checked.astype(bool)
    return frame, flags


def features(frame):
    """Features known at issue time t (no future information)."""
    y = frame[f"lvl_{TARGET}"]
    X = pd.DataFrame(index=frame.index)
    X["y"] = y
    for d in (1, 3, 6, 12, 24):
        X[f"dy_{d}"] = y - y.shift(d)
    for s in UPSTREAM:
        u = frame[f"lvl_{s}"]
        X[f"u_{s}"] = u
        for d in (3, 6, 12, 24):
            X[f"du_{s}_{d}"] = u - u.shift(d)
    r = frame["rain"]
    for w in (1, 3, 6, 12, 24, 48, 72, 168, 720):
        X[f"rain_{w}h"] = r.rolling(w, min_periods=max(1, int(w * 0.8))).sum()
    doy = frame.index.day_of_year
    X["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    X["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    return X


def future_rain(frame, h):
    """Observed rain over (t, t+h]: used ONLY for the 'perfect rain forecast' upper bound."""
    return frame["rain"].rolling(h, min_periods=int(h * 0.8)).sum().shift(-h)


def targets(frame, horizons):
    """Change in Clonmel level from t to t+h, plus whether the label hour is OPW-checked."""
    y = frame[f"lvl_{TARGET}"]
    out = {}
    for h in horizons:
        out[h] = pd.DataFrame(
            {
                "dy": y.shift(-h) - y,
                "y_future": y.shift(-h),
                "checked": frame["target_checked"].shift(-h, fill_value=False),
            }
        )
    return out


def flood_events(level, n, sep_days=7, window=("-5D", "3D")):
    """Top-n independent peaks (separated by sep_days) with removal windows."""
    roll = level.rolling(24 * 2 * 3 + 1, center=True, min_periods=24).max()
    peaks = level[level == roll].dropna().sort_values(ascending=False)
    chosen = []
    for t, v in peaks.items():
        if all(abs((t - c).total_seconds()) > sep_days * 86400 for c, _ in chosen):
            chosen.append((t, float(v)))
        if len(chosen) == n:
            break
    return pd.DataFrame(
        [(t, v, t + pd.Timedelta(window[0]), t + pd.Timedelta(window[1])) for t, v in chosen],
        columns=["peak_time", "peak_m", "start", "end"],
    )


def in_windows(index, events):
    mask = np.zeros(len(index), bool)
    for e in events.itertuples(index=False):
        mask |= (index >= e.start) & (index <= e.end)
    return mask
