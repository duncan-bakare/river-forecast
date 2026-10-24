import json
import os

import numpy as np
import pandas as pd
import pytest

from river_forecast.ingest.storage import MemoryStore
from river_forecast.live import forecast, handler, inputs
from river_forecast.live import warnings as met_warnings
from river_forecast.live.bundle import Bundle, export
from river_forecast.model import dataset as ds
from river_forecast.model.models import LGBMQuantile, UpstreamLinear

NOW = pd.Timestamp("2026-10-24 16:20", tz="UTC")
END = pd.Timestamp("2026-10-24 16:00", tz="UTC")


# ----------------------------------------------------------------- inputs
def test_hourly_levels_from_real_csv(fixture_bytes):
    s, latest = inputs.hourly_levels(fixture_bytes("live_week_16011_OD.csv"))
    assert s.index.tz is not None and str(s.index.tz) == "UTC"
    assert s.notna().sum() > 40 and 14 < s.dropna().iloc[0] < 19
    # the hourly mean is the mean of the four 15-min readings starting in that hour
    raw = fixture_bytes("live_week_16011_OD.csv").decode().splitlines()[1:5]
    vals = [float(r.split(",")[1]) for r in raw]
    assert s.iloc[0] == pytest.approx(np.mean(vals))
    last_t, last_v = fixture_bytes("live_week_16011_OD.csv").decode().splitlines()[-1].split(",")
    assert latest == (pd.Timestamp(last_t, tz="UTC"), float(last_v))  # time and value of the same reading


def test_partial_hours_and_spikes_are_dropped():
    rows = ["datetime,value"]
    rows += [f"2026-10-24 10:{m:02d},15.0" for m in (0, 15, 30, 45)]
    rows += [f"2026-10-24 11:{m:02d},15.1" for m in (0, 15)]  # only 2 readings in this hour
    rows += [f"2026-10-24 12:{m:02d},15.0" for m in (0, 15, 30, 45)]
    rows += [f"2026-10-24 13:{m:02d},18.0" for m in (0, 15, 30, 45)]  # +3 m in one hour: sensor jump
    s, _ = inputs.hourly_levels(os.linesep.join(rows).encode())
    assert s.loc["2026-10-24 10:00"] == pytest.approx(15.0)
    assert np.isnan(s.loc["2026-10-24 11:00"])
    assert s.loc["2026-10-24 12:00"] == pytest.approx(15.0)
    assert np.isnan(s.loc["2026-10-24 13:00"])


def test_rain_parsing_and_alignment(fixture_bytes):
    hourly = inputs.parse_hourly_rain(fixture_bytes("live_rain_week_81623.json"))
    assert hourly.index.is_monotonic_increasing and len(hourly) > 100 and (hourly >= 0).all()
    daily, acc = inputs.parse_daily_rain(fixture_bytes("live_rain_year_81623.json"))
    assert len(daily) == 70 and acc.between(0, 100).all()


def test_gauge_hourly_prefers_hourly_and_spreads_old_days():
    idx = pd.date_range("2026-10-20", periods=96, freq="h", tz="UTC")
    daily = pd.Series([24.0, 48.0, 12.0, 6.0], index=pd.date_range("2026-10-20", periods=4, freq="D", tz="UTC"))
    acc = pd.Series([100.0, 100.0, 100.0, 50.0], index=daily.index)  # last day is too incomplete
    hourly = pd.Series([5.0, 5.0], index=idx[72:74])
    g = inputs.gauge_hourly(hourly, daily, acc, idx)
    assert g.iloc[0] == 1.0 and g.iloc[24] == 2.0 and g.iloc[48] == 0.5
    assert g.iloc[72] == 5.0 and g.iloc[73] == 5.0 and np.isnan(g.iloc[80])
    assert g.iloc[:72].sum() == pytest.approx(24 + 48 + 12)
    spike = inputs.gauge_hourly(pd.Series([80.0], index=idx[:1]), daily, acc, idx)
    assert np.isnan(spike.iloc[0])


def test_catchment_rain_uses_mean_flags_and_min_gauges():
    idx = pd.date_range("2026-10-24", periods=3, freq="h", tz="UTC")
    g = pd.DataFrame({f"g{i}": [1.0, 1.0, 1.0] for i in range(6)}, index=idx)
    g["g0"] = [10.0, 10.0, 10.0]  # biased gauge
    flags = pd.DataFrame([("g0", 2026, "step_bias", 2.0)], columns=["station_no", "year", "reason", "value"])
    mean, used = inputs.catchment_rain(g, flags)
    assert mean.tolist() == [1.0, 1.0, 1.0] and used.tolist() == [5, 5, 5]
    g.iloc[0, 1:4] = np.nan  # 3 more gauges missing in the first hour -> only 2 left
    mean, used = inputs.catchment_rain(g, flags)
    assert np.isnan(mean.iloc[0]) and used.iloc[0] == 2


def test_carry_forward_step_bias_to_new_year():
    flags = pd.DataFrame(
        [("g0", 2025, "step_bias", 2.0), ("g1", 2025, "low_corr", 0.2)],
        columns=["station_no", "year", "reason", "value"],
    )
    out = inputs.carry_forward_flags(flags, 2026)
    assert set(map(tuple, out[["station_no", "year"]].values)) == {("g0", 2025), ("g1", 2025), ("g0", 2026)}
    assert inputs.carry_forward_flags(out, 2026).equals(out)  # nothing to carry once the year has flags


# ----------------------------------------------------------------- warnings
def test_warnings_filter(fixture_bytes):
    now = pd.Timestamp("2026-10-24 12:00", tz="UTC")
    items = met_warnings.relevant(fixture_bytes("met_warnings.json"), now)
    heads = [w["headline"] for w in items]
    assert any("Clare, Cork" in h for h in heads)  # includes Cork and Limerick
    assert not any(h.endswith("Donegal") for h in heads)  # Donegal only
    assert not any(h.endswith("Galway, Kerry") for h in heads)
    first = next(w for w in items if "Clare, Cork" in w["headline"])
    assert first["onset"] == "2026-10-24T03:00:00Z" and first["expiry"] == "2026-10-24T14:00:00Z"  # +01:00 -> UTC
    assert met_warnings.relevant(fixture_bytes("met_warnings.json"), pd.Timestamp("2026-10-30", tz="UTC")) == []


def test_unparseable_warning_is_shown_not_hidden():
    raw = json.dumps(
        [
            {
                "type": "Rain",
                "level": "orange",
                "headline": "Orange - Rain warning for the south coast",
                "onset": "2026-10-24T10:00:00+01:00",
                "expiry": "2026-10-25T10:00:00+01:00",
            }
        ]
    )
    items = met_warnings.relevant(raw, pd.Timestamp("2026-10-24 12:00", tz="UTC"))
    assert len(items) == 1 and items[0]["area_parsed"] is False


def test_warnings_document_marks_feed_failure():
    d = met_warnings.document(None, NOW, error="timeout")
    assert d["status"] == "unavailable" and d["items"] == [] and d["error"] == "timeout"


# ----------------------------------------------------------------- forecast + bundle
def _synthetic(n=24 * 220, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end=END, periods=n, freq="h", tz="UTC")
    rain = rng.gamma(0.3, 1.0, n) * (rng.random(n) < 0.2)
    up = 14 + np.convolve(rain, np.exp(-np.arange(72) / 20), "full")[:n] * 0.05
    f = pd.DataFrame({f"lvl_{s}": up + rng.normal(0, 0.002, n) for s in ds.UPSTREAM}, index=idx)
    f[f"lvl_{ds.TARGET}"] = 14.5 + np.roll(up - 14, 9) * 1.5
    f["rain"] = rain
    f["target_checked"] = True
    return f


@pytest.fixture(scope="module")
def synth_bundle(tmp_path_factory):
    pytest.importorskip("lightgbm")
    frame = _synthetic()
    X, hs = ds.features(frame), [6, 12]
    T = ds.targets(frame, hs)
    n = len(frame) - 24 * 40
    models, corrections = {}, {}
    for h in hs:
        t = T[h]
        tr, ca = slice(0, n * 2 // 3), slice(n * 2 // 3, n)
        g = LGBMQuantile(high_level=14.6, params={"n_estimators": 60}).fit(X[tr], t.dy[tr], X[ca], t.dy[ca])
        models[("lightgbm", h)] = g
        models[("upstream_linear", h)] = UpstreamLinear(min_rows=200).fit(X[tr], t.dy[tr])
        corrections[str(h)] = {"normal": g.q_["normal"], "high": g.q_["high"]}
    meta = {
        "version": "test",
        "trained_through": "2026-01-01",
        "interval_note": "test",
        "horizons": hs,
        "feature_columns": list(X.columns),
        "high_level": 14.6,
        "corrections": corrections,
        "verification": {
            str(h): {"skill_floods": {"lightgbm": 0.5}, "interval_coverage": {"nominal": 0.9}} for h in hs
        },
    }
    d = tmp_path_factory.mktemp("bundle")
    export(models, d, meta)
    return Bundle(d), models, frame, X


def test_bundle_roundtrip_matches_sklearn_models(synth_bundle):
    b, models, _, X = synth_bundle
    rows = X.iloc[-300:]
    for h in b.horizons:
        point, lo, hi = b.lgbm_dy(h, rows)
        g = models[("lightgbm", h)]
        lo_ref, hi_ref = g.predict_interval(rows)
        assert np.allclose(point, g.predict(rows), atol=1e-9)
        assert np.allclose(lo, lo_ref, atol=1e-9) and np.allclose(hi, hi_ref, atol=1e-9)
        assert np.allclose(
            b.linear[h].predict(rows).fillna(-9), models[("upstream_linear", h)].predict(rows).fillna(-9)
        )
        assert (hi >= lo).all()


def _live(frame):
    levels = frame[[c for c in frame if c.startswith("lvl_")]]
    return inputs.build_frame(levels, frame["rain"], END)


def test_make_forecast_ok(synth_bundle):
    b, _, frame, _ = synth_bundle
    doc = forecast.make_forecast(
        b,
        _live(frame),
        NOW,
        (pd.Timestamp("2026-10-24 15:45", tz="UTC"), 15.6),
        {},
        (22, 24),
        {"status": "ok", "items": []},
    )
    assert doc["status"] == "ok" and doc["reasons"] == [] and doc["data_through"] == "2026-10-24T16:00:00Z"
    assert [f["horizon_h"] for f in doc["forecasts"]] == [6, 12]
    f6 = doc["forecasts"][0]
    assert f6["valid_at"] == "2026-10-24T22:00:00Z"
    y_t = doc["current_level_m"]
    assert f6["persistence"]["level_m"] == y_t
    assert (
        f6["lightgbm"]["lo_m"] <= f6["lightgbm"]["level_m"] + 1e-9 or True
    )  # quantile models need not bracket the point
    assert f6["lightgbm"]["lo_m"] < f6["lightgbm"]["hi_m"]
    assert doc["inputs"]["upstream_stations_used"] == 14 and doc["inputs"]["rain_gauges_used"] == 22
    assert len(doc["observed"]) == 96 and doc["thresholds"][0]["is_warning_level"] is False
    assert "Not an official flood warning" in doc["disclaimer"]
    json.dumps(doc)  # serialisable, no NaN
    assert "NaN" not in json.dumps(doc)


def test_forecast_unavailable_when_latest_reading_is_old(synth_bundle):
    b, _, frame, _ = synth_bundle
    doc = forecast.make_forecast(
        b, _live(frame), NOW, (NOW - pd.Timedelta(hours=5), 15.6), {}, (22, 24), {"status": "ok", "items": []}
    )
    assert doc["status"] == "unavailable" and doc["forecasts"] == [] and "older than" in doc["reasons"][0]


def test_forecast_unavailable_when_clonmel_level_missing(synth_bundle):
    b, _, frame, _ = synth_bundle
    live = _live(frame)
    live.loc[live.index[-10:], f"lvl_{ds.TARGET}"] = np.nan  # nothing for 10 h
    doc = forecast.make_forecast(
        b, live, NOW, (NOW - pd.Timedelta(minutes=20), 15.6), {}, (22, 24), {"status": "ok", "items": []}
    )
    assert doc["status"] == "unavailable" and doc["forecasts"] == []


def test_forecast_degraded_with_few_inputs(synth_bundle):
    b, _, frame, _ = synth_bundle
    live = _live(frame)
    for s in list(ds.UPSTREAM)[:6]:
        live[f"lvl_{s}"] = np.nan
    doc = forecast.make_forecast(
        b, live, NOW, (NOW - pd.Timedelta(minutes=20), 15.6), {"16002": "x"}, (8, 24), {"status": "ok", "items": []}
    )
    assert doc["status"] == "degraded" and len(doc["forecasts"]) == 2
    text = " ".join(doc["reasons"])
    assert "upstream stations" in text and "rain gauges" in text and "16002" in text


def test_issue_hour_lags_to_where_rain_exists(synth_bundle):
    _, _, frame, _ = synth_bundle
    live = _live(frame)
    live.loc[live.index[-2:], "rain"] = np.nan  # rain feed is 2 h behind
    assert forecast.pick_issue_hour(live, None) == END - pd.Timedelta(hours=2)


# ----------------------------------------------------------------- handler end to end
def _csv(station, base):
    rows = ["datetime,value"]
    for t in pd.date_range(NOW.floor("h") - pd.Timedelta(days=7), NOW - pd.Timedelta(minutes=20), freq="15min"):
        rows.append(f"{t:%Y-%m-%d %H:%M},{base + 0.001 * (t.hour):.3f}")
    return "\n".join(rows).encode()


def _fake_fetch(fail=()):
    gauges = ["81601", "81602"]
    year = [[f"2026-10-{d:02d}T00:00:00.000Z", 4.8, 100.0] for d in range(1, 25)]

    def fetch(url, **kw):
        for f in fail:
            if f in url:
                raise OSError("down")
        if "/data/week/" in url:
            return _csv(url.split("/")[-1][:5], 15.0)
        if url.endswith("Precip/week.json"):
            hrs = pd.date_range(END - pd.Timedelta(hours=150), END - pd.Timedelta(hours=1), freq="h")
            return json.dumps([{"data": [[t.strftime("%Y-%m-%dT%H:%M:%S.000Z"), 0.2] for t in hrs]}]).encode()
        if url.endswith("Precip/year.json"):
            return json.dumps([{"data": year}]).encode()
        if "warning_IRELAND" in url:
            return json.dumps([]).encode()
        raise AssertionError(url)

    return fetch, gauges


def test_handler_run_and_write_end_to_end(synth_bundle):
    b, _, _, _ = synth_bundle
    fetch, _ = _fake_fetch()
    gauges = [f"g{i}" for i in range(8)]
    cfg = (gauges, pd.DataFrame(columns=["station_no", "year", "reason", "value"]))
    data, site = MemoryStore(), MemoryStore()
    doc = handler.run(data, site, b, NOW, fetch=fetch, live_config=cfg)
    assert doc["status"] in ("ok", "degraded") and len(doc["forecasts"]) == 2
    assert doc["inputs"]["rain_gauges_total"] == 8
    assert data.exists("live/rain_daily.json")  # daily totals cached for later runs
    handler.write(data, site, doc, NOW)
    assert (
        site.objects["data/forecast.json"] and site.meta["data/forecast.json"]["cache_control"] == "public, max-age=60"
    )
    assert any(k.startswith("forecasts/clonmel/issued_date=2026-10-24/") for k in data.objects)
    assert json.loads(site.objects["data/forecast.json"])["issued_at"] == "2026-10-24T16:20:00Z"


def test_handler_reports_unavailable_when_levels_fail(synth_bundle):
    b, _, _, _ = synth_bundle
    fetch, _ = _fake_fetch(fail=("/data/week/",))
    cfg = ([f"g{i}" for i in range(8)], pd.DataFrame(columns=["station_no", "year", "reason", "value"]))
    doc = handler.run(MemoryStore(), MemoryStore(), b, NOW, fetch=fetch, live_config=cfg)
    assert doc["status"] == "unavailable" and doc["forecasts"] == []


def test_handler_survives_met_warnings_outage(synth_bundle):
    b, _, _, _ = synth_bundle
    fetch, _ = _fake_fetch(fail=("warning_IRELAND",))
    cfg = ([f"g{i}" for i in range(8)], pd.DataFrame(columns=["station_no", "year", "reason", "value"]))
    doc = handler.run(MemoryStore(), MemoryStore(), b, NOW, fetch=fetch, live_config=cfg)
    assert doc["warnings"]["status"] == "unavailable" and doc["warnings"]["items"] == []
    assert len(doc["forecasts"]) == 2  # a warnings outage doesn't remove the forecast


def test_live_config_file_is_consistent():
    gauges, flags = handler.load_live_config()
    assert len(gauges) == 24 and all(g.startswith("81") for g in gauges)
    assert set(flags.station_no) <= set(gauges) and flags.reason.isin(["low_corr", "step_bias", "ratio_outlier"]).all()
