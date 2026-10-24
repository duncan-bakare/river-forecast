"""Lambda: hourly Clonmel forecast -> site/data/forecast.json (site bucket) + archive (data bucket).

Env: DATA_BUCKET, SITE_BUCKET, MODEL_PREFIX (e.g. models/clonmel/v1/). Layer: AWS SDK for pandas (numpy, pandas).
"""

import json
import logging
import os
import pathlib
import tempfile

import pandas as pd

from river_forecast import config
from river_forecast.ingest.http import fetch as default_fetch
from river_forecast.live import forecast, inputs
from river_forecast.live import warnings as met_warnings
from river_forecast.live.bundle import Bundle

log = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)

LIVE_CONFIG = pathlib.Path(__file__).with_name("live_config.json")
RAIN_CACHE_KEY = "live/rain_daily.json"
RAIN_CACHE_MAX_AGE_H = 6
KEEP_DAYS = 60
FORECAST_KEY = "data/forecast.json"


def load_live_config():
    d = json.loads(LIVE_CONFIG.read_text(encoding="utf-8"))
    flags = pd.DataFrame(d["flags"], columns=["station_no", "year", "reason", "value"])
    return d["gauges"], flags


def download_bundle(store, prefix, dest):
    for key in store.list(prefix):
        name = key[len(prefix) :]
        if name:
            (pathlib.Path(dest) / name).write_bytes(store.get(key))
    return Bundle(dest)


def daily_rain(store, fetch, gauges, now):
    """{gauge: (daily Series, accuracy Series)} from a cache in the data bucket, refreshed every few hours."""
    cached = None
    if store.exists(RAIN_CACHE_KEY):
        cached = json.loads(store.get(RAIN_CACHE_KEY))
        age = now - pd.Timestamp(cached["fetched_at"])
        if age < pd.Timedelta(hours=RAIN_CACHE_MAX_AGE_H):
            return _parse_cache(cached), None
    data, errors = {}, {}
    for g in gauges:
        try:
            raw = fetch(f"{config.HYDRODATA_BASE}/stations/0/{g}/Precip/year.json")
            daily, acc = inputs.parse_daily_rain(raw)
            data[g] = [[str(d.date()), v, acc[d]] for d, v in daily.iloc[-KEEP_DAYS:].items()]
        except Exception as e:
            errors[g] = str(e)
            if cached and g in cached["gauges"]:
                data[g] = cached["gauges"][g]  # stale beats nothing for 30-day totals
    doc = {"fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "gauges": data}
    store.put(RAIN_CACHE_KEY, json.dumps(doc).encode(), "application/json")
    return _parse_cache(doc), errors


def _parse_cache(doc):
    out = {}
    for g, rows in doc["gauges"].items():
        idx = [pd.Timestamp(r[0], tz="UTC") for r in rows]
        out[g] = (
            pd.Series([r[1] for r in rows], index=idx, dtype=float),
            pd.Series([r[2] for r in rows], index=idx, dtype=float),
        )
    return out


def run(data_store, site_store, bundle, now, fetch=default_fetch, live_config=None):
    now = pd.Timestamp(now).tz_convert("UTC")
    gauges, flags = live_config or load_live_config()
    end = now.floor("h")

    levels, level_errors, latest = inputs.fetch_levels(fetch)
    daily, _ = daily_rain(data_store, fetch, gauges, now)
    index = pd.date_range(end=end, periods=inputs.WINDOW_H, freq="h", tz="UTC")
    cols = {}
    for g in gauges:
        try:
            hourly = inputs.parse_hourly_rain(fetch(f"{config.HYDRODATA_BASE}/stations/0/{g}/Precip/week.json"))
        except Exception as e:
            log.warning("rain gauge %s week.json failed: %s", g, e)
            hourly = pd.Series(dtype=float)
        d, acc = daily.get(g, (pd.Series(dtype=float), pd.Series(dtype=float)))
        cols[g] = inputs.gauge_hourly(hourly, d, acc, index)
    gauge_frame = pd.DataFrame(cols)
    rain, used = inputs.catchment_rain(gauge_frame, inputs.carry_forward_flags(flags, end.year))
    frame = inputs.build_frame(levels, rain, end)

    t = forecast.pick_issue_hour(frame, None)
    n_gauges = int(used.loc[t]) if t is not None else 0
    try:
        wdoc = met_warnings.document(fetch(config.MET_WARNINGS_URL), now)
    except Exception as e:
        wdoc = met_warnings.document(None, now, error=e)
    doc = forecast.make_forecast(bundle, frame, now, latest, level_errors, (n_gauges, len(gauges)), wdoc)
    return doc


def write(data_store, site_store, doc, now):
    body = json.dumps(doc, separators=(",", ":")).encode()
    site_store.put(FORECAST_KEY, body, "application/json", cache_control="public, max-age=60")
    stamp = pd.Timestamp(now).strftime("issued_date=%Y-%m-%d/issued=%H%M%SZ")
    data_store.put(f"forecasts/clonmel/{stamp}.json", body, "application/json")


def lambda_handler(event, context):
    from river_forecast.ingest.storage import S3Store

    data_store, site_store = S3Store(os.environ["DATA_BUCKET"]), S3Store(os.environ["SITE_BUCKET"])
    now = pd.Timestamp.now(tz="UTC")
    try:
        bundle = download_bundle(data_store, os.environ["MODEL_PREFIX"], tempfile.mkdtemp(dir="/tmp"))
        doc = run(data_store, site_store, bundle, now)
    except Exception as e:
        log.exception("forecast failed")
        doc = {
            "schema": forecast.SCHEMA,
            "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "unavailable",
            "reasons": [f"internal error: {type(e).__name__}"],
            "disclaimer": forecast.DISCLAIMER,
            "forecasts": [],
        }
        write(data_store, site_store, doc, now)
        raise
    write(data_store, site_store, doc, now)
    log.info("forecast status=%s reasons=%s", doc["status"], doc["reasons"])
    return {"status": doc["status"], "reasons": doc["reasons"], "data_through": doc.get("data_through")}
