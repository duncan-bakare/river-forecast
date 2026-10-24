"""Rain-gauge QC by neighbour comparison.

OPW rain data is almost all quality code 254 (unchecked). Checks, all at the
gauge-year level against the median of each gauge's nearest neighbours:

  spike        15-min value > SPIKE_MM (applied before totals)
  low_corr     daily correlation with neighbours < MIN_CORR
  step_bias    a step change in the gauge/neighbour ratio (e.g. Rosegreen 81615,
               ~1.07x before 2019, ~2.1x after); the segment further from 1.0 is flagged
  ratio_outlier a single year whose ratio is > RATIO_OUTLIER x (or < 1/x) the
               gauge's typical ratio

Upland gauges that read consistently high (Knockmealdown, Rossadrehid ~1.5x) are
not flagged: only changes relative to the gauge's own level are.
"""

import math

import numpy as np
import pandas as pd

SPIKE_MM = 50.0
MIN_CORR = 0.6
N_NEIGHBOURS = 4
MIN_DAYS_PER_YEAR = 200
STEP_MIN_YEARS = 3
STEP_FACTOR = 1.6
RATIO_OUTLIER = 1.5


def _km(a, b):
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def neighbours(coords, k=N_NEIGHBOURS):
    """coords: {station: (lat, lon)} -> {station: [k nearest other stations]}"""
    return {s: sorted((o for o in coords if o != s), key=lambda o: _km(coords[s], coords[o]))[:k] for s in coords}


def _best_step(years, logr):
    """Single breakpoint maximising the jump in mean log-ratio. Returns (split_index, jump) or None."""
    best = None
    for i in range(STEP_MIN_YEARS, len(years) - STEP_MIN_YEARS + 1):
        jump = logr[i:].mean() - logr[:i].mean()
        if best is None or abs(jump) > abs(best[1]):
            best = (i, jump)
    return best


def flag_gauge_years(daily, coords, iterations=3):
    """daily: DataFrame indexed by day, one column per station, mm/day (NaN = incomplete day).
    Returns DataFrame[station, year, reason, value].

    Iterates: gauge-years flagged in one pass are removed from the *neighbour reference*
    in the next, so one bad gauge doesn't get its neighbours flagged too."""
    flags = _flag_pass(daily, daily, coords)
    for _ in range(iterations - 1):
        new = _flag_pass(daily, apply_flags(daily, flags), coords)
        if new.equals(flags):
            break
        flags = new
    return flags


def _flag_pass(daily, reference, coords):
    nb = neighbours({s: coords[s] for s in daily.columns if s in coords})
    flags = []
    for s, others in nb.items():
        ref = reference[others].median(axis=1)
        pair = pd.DataFrame({"x": daily[s], "ref": ref}).dropna()
        if pair.empty:
            continue
        by_year = pair.groupby(pair.index.year)
        stats = pd.DataFrame(
            {
                "days": by_year.size(),
                "corr": by_year.apply(lambda v: v.x.corr(v.ref)),
                "ratio": by_year.x.sum() / by_year.ref.sum(),
            }
        )
        stats = stats[stats.days >= MIN_DAYS_PER_YEAR]
        bad = set()
        for y, row in stats.iterrows():
            if row["corr"] < MIN_CORR:
                flags.append((s, y, "low_corr", round(row["corr"], 3)))
                bad.add(y)
        ok = stats[~stats.index.isin(bad) & (stats.ratio > 0)]
        years = ok.index.to_numpy()
        logr = np.log(ok.ratio.to_numpy())
        if len(years) >= 2 * STEP_MIN_YEARS:
            step = _best_step(years, logr)
            if step and abs(step[1]) >= math.log(STEP_FACTOR):
                i = step[0]
                before, after = logr[:i], logr[i:]
                # flag the side whose level is further from agreeing with neighbours (ratio 1)
                flagged = years[i:] if abs(after.mean()) > abs(before.mean()) else years[:i]
                for y in flagged:
                    flags.append((s, int(y), "step_bias", round(float(ok.ratio[y]), 3)))
                    bad.add(int(y))
        rest = ok[~ok.index.isin(bad)]
        if len(rest) >= 3:
            typical = float(np.median(rest.ratio))
            for y, r in rest.ratio.items():
                if r > typical * RATIO_OUTLIER or r < typical / RATIO_OUTLIER:
                    flags.append((s, int(y), "ratio_outlier", round(float(r), 3)))
    return pd.DataFrame(flags, columns=["station_no", "year", "reason", "value"])


def apply_flags(frame, flags):
    """Blank flagged gauge-years in a time-indexed frame (one column per station)."""
    out = frame.copy()
    years = out.index.year
    for row in flags.itertuples(index=False):
        if row.station_no in out.columns:
            out.loc[years == row.year, row.station_no] = np.nan
    return out


def catchment_mean(frame, flags, min_gauges=5):
    """Mean of unflagged gauges per time step (NaN where fewer than min_gauges report).
    Mean, not median: the median of intermittent rain is biased low."""
    clean = apply_flags(frame, flags)
    mean = clean.mean(axis=1)
    return mean.where(clean.count(axis=1) >= min_gauges)
