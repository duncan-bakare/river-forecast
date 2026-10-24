import math

import numpy as np
import pandas as pd

from river_forecast.model import metrics

IDX = pd.date_range("2020-01-01", periods=200, freq="h", tz="UTC")


def _wave(peak_at=100, height=2.0, base=1.0):
    t = np.arange(len(IDX))
    return pd.Series(base + height * np.exp(-((t - peak_at) ** 2) / 200.0), IDX)


def test_nse_kge_perfect_and_mean():
    o = _wave()
    assert metrics.nse(o, o) == 1.0
    assert metrics.kge(o, o) == 1.0
    assert abs(metrics.nse(o, pd.Series(o.mean(), IDX))) < 1e-12
    # KGE of a constant forecast: r undefined -> nan, and handled
    assert math.isnan(metrics.kge(o, pd.Series(o.mean(), IDX))) or metrics.kge(o, pd.Series(o.mean(), IDX)) < 0


def test_kge_bias_component():
    o = _wave()
    k = metrics.kge(o, o * 1.1)  # r=1, alpha=1.1, beta=1.1
    assert abs(k - (1 - math.sqrt(0.01 + 0.01))) < 1e-9


def test_nan_pairs_dropped():
    o = _wave()
    s = o.copy()
    s.iloc[:50] = np.nan
    assert metrics.nse(o, s) == 1.0


def test_skill_vs_reference():
    o = _wave()
    ref = o.shift(6)
    assert metrics.skill_vs(o, o, ref) == 1.0
    assert metrics.skill_vs(o, ref, ref) == 0.0
    assert metrics.skill_vs(o, o.shift(12), ref) < 0


def test_peak_error_timing_and_size():
    o = _wave(peak_at=100, height=2.0)
    f = _wave(peak_at=104, height=1.5)
    e = metrics.peak_error(o, f, IDX[50], IDX[150])
    assert e["timing_h"] == 4.0
    assert abs(e["size_m"] - (-0.5)) < 1e-9


def test_onsets_merge_close_episodes():
    s = pd.Series(0.0, IDX)
    s.iloc[10:15] = 5
    s.iloc[20:25] = 5  # within 48 h of the first episode -> merged
    s.iloc[100:105] = 5
    assert metrics.onsets(s, 1.0) == [IDX[10], IDX[100]]


def test_detection_lead_takes_longest_lead_that_caught_it():
    o = _wave(peak_at=100)  # crosses 2.0 a little before t=100
    t0 = metrics.onsets(o, 2.0)[0]
    fc = {6: o, 12: o.shift(-3), 24: pd.Series(1.0, IDX)}  # 24 h forecast never exceeds
    d = metrics.detection_lead(o, fc, 2.0)
    assert list(d.onset) == [t0]
    assert d.lead_h.iloc[0] == 12


def test_persistence_like_forecast_never_detects_onset_in_advance():
    o = _wave(peak_at=100)
    d = metrics.detection_lead(o, {6: o.shift(6), 12: o.shift(12)}, 2.0, tolerance_h=0)
    assert d.lead_h.iloc[0] == 0


def test_contingency_and_false_alarms():
    o = pd.Series(0.0, IDX)
    o.iloc[50:60] = 5
    f = pd.Series(0.0, IDX)
    f.iloc[55:65] = 5
    f.iloc[150:152] = 5  # false alarm episode
    c = metrics.contingency(o, f, 1.0)
    assert (c["hits"], c["misses"], c["false_alarms"]) == (5, 5, 7)
    assert metrics.false_alarm_episodes(o, f, 1.0) == 1


def test_interval_coverage():
    o = pd.Series([1.0, 2.0, 3.0, 4.0], IDX[:4])
    cov, width = metrics.interval_coverage(o, o - 0.5, o + 0.5)
    assert cov == 1.0 and width == 1.0
    cov, _ = metrics.interval_coverage(o, o + 0.1, o + 0.5)
    assert cov == 0.0
