import numpy as np
import pandas as pd
import pytest

from river_forecast.model import dataset as ds
from river_forecast.model.models import LGBMQuantile, Persistence, UpstreamLinear, linear_columns


def _frame(n=24 * 200, seed=0):
    """Synthetic catchment: rain drives an upstream station, which drives Clonmel 9 h later."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2010-01-01", periods=n, freq="h", tz="UTC")
    rain = rng.gamma(0.3, 1.0, n) * (rng.random(n) < 0.2)
    up = 14 + np.convolve(rain, np.exp(-np.arange(72) / 20), "full")[:n] * 0.05
    clon = 14.5 + np.roll(up - 14, 9) * 1.5
    f = pd.DataFrame({f"lvl_{s}": up + rng.normal(0, 0.002, n) for s in ds.UPSTREAM}, index=idx)
    f[f"lvl_{ds.TARGET}"] = clon
    f["rain"] = rain
    f["target_checked"] = True
    return f


def test_features_use_only_past_values():
    f = _frame(500)
    X1 = ds.features(f)
    g = f.copy()
    num = [c for c in g.columns if c != "target_checked"]
    g.loc[g.index[300:], num] = 999.0  # change the future after t=299
    X2 = ds.features(g)
    pd.testing.assert_frame_equal(X1.iloc[:300], X2.iloc[:300])


def test_targets_are_future_changes_and_carry_label_quality():
    f = _frame(100)
    f.loc[f.index[50], "target_checked"] = False
    t = ds.targets(f, [6])[6]
    y = f[f"lvl_{ds.TARGET}"]
    assert t.dy.iloc[10] == pytest.approx(y.iloc[16] - y.iloc[10])
    assert not t.checked.iloc[44] and t.checked.iloc[43]
    assert t.dy.iloc[-6:].isna().all()


def test_flood_events_are_independent_and_windowed():
    idx = pd.date_range("2020-01-01", periods=24 * 60, freq="h", tz="UTC")
    s = pd.Series(1.0, idx)
    for day, h in ((5, 3.0), (6, 2.5), (30, 2.0), (50, 4.0)):
        s.iloc[day * 24] = h
    ev = ds.flood_events(s, 3)
    # day 6 (2.5) is within 7 days of day 5 (3.0), so it isn't an independent event
    assert list(ev.peak_m) == [4.0, 3.0, 2.0]
    assert ds.in_windows(pd.DatetimeIndex([ev.peak_time[0]]), ev).all()


def test_persistence_and_linear_beat_nothing_and_learn_lag():
    f = _frame()
    X, t = ds.features(f), ds.targets(f, [6])[6]
    tr = t.dy.notna()
    assert (Persistence().fit(X, t.dy).predict(X) == 0).all()
    lin = UpstreamLinear().fit(X[tr], t.dy[tr])
    err_lin = ((lin.predict(X)[tr] - t.dy[tr]) ** 2).mean()
    err_per = (t.dy[tr] ** 2).mean()
    assert err_lin < 0.5 * err_per  # upstream changes 9 h ahead explain most of a 6 h change
    assert set(linear_columns()) <= set(X.columns)


def _fit_linear(f, h=6):
    X, t = ds.features(f), ds.targets(f, [h])[h]
    tr = t.dy.notna()
    return UpstreamLinear(min_rows=200).fit(X[tr], t.dy[tr]), X, t


def test_linear_missing_station_uses_reduced_model_not_persistence():
    f = _frame()
    lin, X, t = _fit_linear(f)
    Xm = X.copy()
    # An informative station is missing for a stretch, the rest are fine.
    Xm.loc[Xm.index[1000:1500], [f"du_16139_{w}" for w in (6, 12, 24)]] = np.nan
    p = lin.predict(Xm)
    seg = slice(1000, 1500)
    assert p.iloc[seg].notna().all()
    assert (p.iloc[seg] != 0).mean() > 0.9  # a real forecast, not dy = 0
    assert (lin.used_.iloc[seg] == len(lin.stations) - 1).all()  # one station fewer
    full_err = ((lin.predict(X).iloc[seg] - t.dy.iloc[seg]) ** 2).mean()
    red_err = ((p.iloc[seg] - t.dy.iloc[seg]) ** 2).mean()
    persist_err = (t.dy.iloc[seg] ** 2).mean()
    assert red_err < 0.7 * persist_err and red_err < 5 * full_err + 1e-9


def test_linear_without_upstream_data_uses_own_columns_only():
    f = _frame()
    lin, X, _ = _fit_linear(f)
    Xm = X.copy()
    Xm.loc[Xm.index[200:260], [c for c in Xm.columns if c.startswith("du_")]] = np.nan
    p = lin.predict(Xm)
    assert p.iloc[200:260].notna().all() and (lin.used_.iloc[200:260] == 0).all()
    u = lin.usage()
    assert 0 < u["own_only_rate"] < 0.1 and u["mean_stations"] > 7


def test_linear_returns_nan_when_own_data_missing():
    f = _frame()
    lin, X, _ = _fit_linear(f)
    Xm = X.copy()
    Xm.loc[Xm.index[300:310], "dy_6"] = np.nan
    p = lin.predict(Xm)
    assert p.iloc[300:310].isna().all()  # no silent persistence
    assert (lin.used_.iloc[300:310] == -1).all()
    assert lin.usage()["no_forecast_rate"] > 0


def test_conformal_helpers():
    from river_forecast.model.models import conformal_quantile, cqr_scores

    y = np.array([0.0, 1.0, 2.0, 3.0])
    sc = cqr_scores(np.array([0.5] * 4), np.array([1.5] * 4), y)
    assert list(sc) == [0.5, -0.5, 0.5, 1.5]  # below, inside, above, above
    assert conformal_quantile([], 0.1) == 0.0
    assert conformal_quantile(np.arange(100.0), 0.1) == 90.0  # ceil(101 * 0.9) = 91st smallest


def test_lgbm_intervals_cover_on_calibration_like_data():
    pytest.importorskip("lightgbm")
    f = _frame(24 * 300, seed=1)
    X, t = ds.features(f), ds.targets(f, [6])[6]
    n = len(X)
    tr, ca, te = slice(0, n // 2), slice(n // 2, 3 * n // 4), slice(3 * n // 4, n - 6)
    m = LGBMQuantile(high_level=14.6, params={"n_estimators": 200}).fit(X[tr], t.dy[tr], X[ca], t.dy[ca])
    lo, hi = m.predict_interval(X[te])
    cov = ((t.dy[te] >= lo) & (t.dy[te] <= hi)).mean()
    assert 0.8 <= cov <= 1.0
    assert set(m.q_) == {"normal", "high"}
