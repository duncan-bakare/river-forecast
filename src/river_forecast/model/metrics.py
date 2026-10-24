"""Forecast verification metrics. Inputs are aligned pandas Series indexed by
valid time (hourly); NaNs are dropped pairwise."""

import numpy as np
import pandas as pd


def _pair(obs, sim):
    df = pd.DataFrame({"o": obs, "s": sim}).dropna()
    return df.o.to_numpy(float), df.s.to_numpy(float)


def nse(obs, sim):
    o, s = _pair(obs, sim)
    if len(o) < 2 or np.var(o) == 0:
        return float("nan")
    return float(1 - np.sum((s - o) ** 2) / np.sum((o - o.mean()) ** 2))


def kge(obs, sim):
    """Kling-Gupta efficiency (2009): 1 - sqrt((r-1)^2 + (alpha-1)^2 + (beta-1)^2)."""
    o, s = _pair(obs, sim)
    if len(o) < 2 or o.std() == 0 or o.mean() == 0:
        return float("nan")
    r = np.corrcoef(o, s)[0, 1]
    alpha = s.std() / o.std()
    beta = s.mean() / o.mean()
    return float(1 - np.sqrt((r - 1) ** 2 + (alpha - 1) ** 2 + (beta - 1) ** 2))


def skill_vs(obs, sim, ref):
    """MSE skill score against a reference forecast (1 = perfect, 0 = same as ref, <0 = worse)."""
    df = pd.DataFrame({"o": obs, "s": sim, "r": ref}).dropna()
    mse_ref = np.mean((df.r - df.o) ** 2)
    if len(df) == 0 or mse_ref == 0:
        return float("nan")
    return float(1 - np.mean((df.s - df.o) ** 2) / mse_ref)


def peak_error(obs, sim, start, end):
    """Peak timing (hours, + = forecast peak late) and size (forecast - observed) within [start, end]."""
    o = obs.loc[start:end].dropna()
    s = sim.loc[start:end].dropna()
    if o.empty or s.empty:
        return {"timing_h": float("nan"), "size_m": float("nan")}
    return {
        "timing_h": (s.idxmax() - o.idxmax()).total_seconds() / 3600,
        "size_m": float(s.max() - o.max()),
    }


def onsets(series, threshold, min_gap_h=48):
    """First hour of each exceedance episode in an hourly series.

    An onset needs the previous hour to be observed and at or below the threshold, so the start of
    a record or of a data gap never counts. Episodes with no gap of at least `min_gap_h` hours
    below the threshold before them are merged into the earlier one. The series must not have
    scored/unscored periods spliced out: pass the full series and filter the result with `within`."""
    s = series.asfreq("h")
    above = (s > threshold).astype(int)
    recent = above.rolling(min_gap_h, min_periods=1).sum().shift(1).fillna(0)
    start = (s > threshold) & (s.shift(1) <= threshold) & (recent == 0)
    return list(s.index[start])


def detection_lead(obs, forecasts, threshold, tolerance_h=3, within=None):
    """For each observed exceedance onset, the longest lead at which a forecast caught it.

    forecasts: {lead_h: full Series of forecasts indexed by valid time}. A lead-h forecast catches
    an onset if any of its values valid within onset +- tolerance exceed the threshold, so the real
    advance warning can be up to `tolerance_h` shorter than the nominal lead. `within` (boolean
    Series on obs.index) keeps only onsets that fall in the scored period."""
    rows = []
    tol = pd.Timedelta(hours=tolerance_h)
    for t0 in onsets(obs, threshold):
        if within is not None and not within.get(t0, False):
            continue
        caught = [h for h, f in forecasts.items() if (f.loc[t0 - tol : t0 + tol] > threshold).any()]
        rows.append(
            {"onset": t0, "peak": float(obs.loc[t0 : t0 + pd.Timedelta("72h")].max()), "lead_h": max(caught, default=0)}
        )
    return pd.DataFrame(rows, columns=["onset", "peak", "lead_h"])


def contingency(obs, fc, threshold):
    """Hourly POD / FAR / CSI for 'value > threshold'."""
    df = pd.DataFrame({"o": obs, "f": fc}).dropna()
    o, f = df.o > threshold, df.f > threshold
    hits, misses, fa = int((o & f).sum()), int((o & ~f).sum()), int((~o & f).sum())
    return {
        "hits": hits,
        "misses": misses,
        "false_alarms": fa,
        "POD": hits / (hits + misses) if hits + misses else float("nan"),
        "FAR": fa / (hits + fa) if hits + fa else float("nan"),
        "CSI": hits / (hits + misses + fa) if hits + misses + fa else float("nan"),
    }


def false_alarm_episodes(obs, fc, threshold, window_h=24, within=None):
    """Forecast exceedance onsets with no observed exceedance within +-window_h. `fc` is the full
    forecast series; `within` keeps only forecast onsets in the scored period."""
    obs_above = obs > threshold
    n = 0
    for t in onsets(fc, threshold, min_gap_h=window_h):
        if within is not None and not within.get(t, False):
            continue
        w = obs_above.loc[t - pd.Timedelta(hours=window_h) : t + pd.Timedelta(hours=window_h)]
        if not w.any():
            n += 1
    return n


def interval_coverage(obs, lo, hi):
    df = pd.DataFrame({"o": obs, "lo": lo, "hi": hi}).dropna()
    if df.empty:
        return float("nan"), float("nan")
    inside = (df.o >= df.lo) & (df.o <= df.hi)
    return float(inside.mean()), float((df.hi - df.lo).mean())
