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
    """Times the series first rises above threshold, with episodes closer than min_gap_h merged."""
    above = series > threshold
    starts = series.index[above & ~above.shift(1, fill_value=False)]
    out = []
    last_above = None
    for t in starts:
        if last_above is None or (t - last_above) > pd.Timedelta(hours=min_gap_h):
            out.append(t)
        # find end of this episode
        after = above.loc[t:]
        ends = after[~after]
        last_above = (ends.index[0] if len(ends) else series.index[-1]) - pd.Timedelta(hours=1)
    return out


def detection_lead(obs, forecasts, threshold, tolerance_h=3):
    """For each observed exceedance onset, the longest lead at which a forecast caught it.

    forecasts: {lead_h: Series of forecasts indexed by valid time}. A lead-h forecast
    catches an onset if any of its values valid within onset +- tolerance exceed the
    threshold. Returns DataFrame[onset, peak, lead_h] (lead_h = 0 if never caught)."""
    rows = []
    tol = pd.Timedelta(hours=tolerance_h)
    for t0 in onsets(obs, threshold):
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


def false_alarm_episodes(obs, fc, threshold, window_h=24):
    """Forecast exceedance episodes with no observed exceedance within +-window_h."""
    obs_above = obs > threshold
    n = 0
    for t in onsets(fc.dropna(), threshold, min_gap_h=window_h):
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
