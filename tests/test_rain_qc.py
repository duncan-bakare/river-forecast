import numpy as np
import pandas as pd

from river_forecast.features import rain_qc

COORDS = {f"g{i}": (52.5 + 0.02 * i, -7.8) for i in range(7)}


def _daily(seed=0, years=range(2010, 2022)):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(f"{min(years)}-01-01", f"{max(years)}-12-31", freq="D")
    base = rng.gamma(0.6, 5.0, len(idx))  # shared regional rain
    data = {s: base * rng.uniform(0.85, 1.15, len(idx)) for s in COORDS}
    return pd.DataFrame(data, index=idx)


def test_clean_network_has_no_flags():
    flags = rain_qc.flag_gauge_years(_daily(), COORDS)
    assert flags.empty


def test_step_bias_like_rosegreen_is_flagged_after_the_step():
    d = _daily()
    d.loc[d.index.year >= 2016, "g3"] *= 2.0
    flags = rain_qc.flag_gauge_years(d, COORDS)
    step = flags[(flags.station_no == "g3") & (flags.reason == "step_bias")]
    assert sorted(step.year) == list(range(2016, 2022))
    assert not (flags.station_no != "g3").any()


def test_consistently_wet_upland_gauge_is_not_flagged():
    d = _daily()
    d["g5"] *= 1.6  # every year: an upland site, not a fault
    assert rain_qc.flag_gauge_years(d, COORDS).empty


def test_single_bad_year_is_ratio_outlier():
    d = _daily()
    d.loc[d.index.year == 2014, "g1"] *= 0.4
    flags = rain_qc.flag_gauge_years(d, COORDS)
    assert set(map(tuple, flags[["station_no", "year", "reason"]].values)) == {("g1", 2014, "ratio_outlier")}


def test_low_correlation_year_flagged():
    d = _daily()
    rng = np.random.default_rng(9)
    m = d.index.year == 2012
    d.loc[m, "g2"] = rng.permutation(d.loc[m, "g2"].to_numpy())
    flags = rain_qc.flag_gauge_years(d, COORDS)
    assert ("g2", 2012, "low_corr") in set(map(tuple, flags[["station_no", "year", "reason"]].values))


def test_catchment_mean_uses_mean_and_drops_flagged():
    idx = pd.date_range("2020-01-01", periods=3, freq="h")
    f = pd.DataFrame({s: [0.0, 1.0, 2.0] for s in list(COORDS)[:6]}, index=idx)
    f["g0"] = [100.0, 100.0, 100.0]
    flags = pd.DataFrame([("g0", 2020, "step_bias", 9.9)], columns=["station_no", "year", "reason", "value"])
    assert rain_qc.catchment_mean(f, flags).tolist() == [0.0, 1.0, 2.0]
    assert rain_qc.catchment_mean(f, flags, min_gauges=6).isna().all()
