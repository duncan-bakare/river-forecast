"""Ingestion jobs. Each job archives raw payloads to the store, writes a
validation report next to them, and returns a summary. Payloads that fail
validation fatally go under quarantine/ instead of raw/."""

import json
import logging
import time
from datetime import UTC, datetime

from river_forecast import config
from river_forecast.ingest import parse, validate
from river_forecast.ingest.http import fetch as default_fetch
from river_forecast.ingest.storage import gz

log = logging.getLogger(__name__)


class Ctx:
    def __init__(self, store, fetch=default_fetch, now=None, sleep=time.sleep):
        self.store = store
        self.fetch = fetch
        self.now = now or datetime.now(UTC)
        self.sleep = sleep
        self.results = []
        self.seen = set()  # per-run de-duplication for jobs that need it

    def save(self, rel_key, raw, report, content_type, compress=True):
        """Store raw bytes (gzipped unless compress=False, e.g. files that are
        already ZIPs and must be kept byte-for-byte)."""
        prefix = "raw" if report["ok"] else "quarantine"
        key = f"{prefix}/{rel_key}.gz" if compress else f"{prefix}/{rel_key}"
        self.store.put(key, gz(raw) if compress else raw, content_type)
        self.store.put(f"validation/{rel_key}.json", json.dumps(report, default=str).encode(), "application/json")
        self.results.append({"key": key, **report})
        if not report["ok"] or report["issues"]:
            log.warning("%s ok=%s issues=%s", key, report["ok"], report["issues"])
        return key

    def fail(self, what, err):
        log.error("%s failed: %s", what, err)
        self.results.append({"key": what, "ok": False, "n": 0, "issues": [f"fetch/parse error: {err}"]})

    @property
    def stamp(self):
        return self.now.strftime("date=%Y-%m-%d/fetched=%H%M%SZ")


def opw_latest(ctx):
    url = f"{config.OPW_BASE}/geojson/latest/"
    try:
        raw = ctx.fetch(url)
        report = validate.opw_latest(parse.opw_latest(raw))
    except Exception as e:
        ctx.fail(url, e)
        return
    ctx.save(f"opw_latest/{ctx.stamp}.geojson", raw, report, "application/geo+json")


def opw_csv(ctx, period="week", stations=None, sensors=None, delay=0.5):
    stations = stations or [num for _, num, _ in config.all_stations()]
    date = ctx.now.strftime("%Y-%m-%d")
    for num in stations:
        for sensor in sensors or config.sensors_for(num):
            url = f"{config.OPW_BASE}/data/{period}/{num}_{sensor}.csv"
            try:
                raw = ctx.fetch(url)
                header, rows = parse.opw_csv(raw)
                report = validate.opw_csv(header, rows, sensor)
            except Exception as e:
                ctx.fail(url, e)
            else:
                ctx.save(
                    f"opw_csv/period={period}/date={date}/station={num}/sensor={sensor}.csv", raw, report, "text/csv"
                )
            ctx.sleep(delay)


def _forecast_key(parsed, point):
    run = parsed["models"]["harmonie"]["run"]
    ecmwf = parsed["models"].get("ecmwf", {}).get("run")
    ecmwf_part = ecmwf.strftime("%Y%m%dT%H%MZ") if ecmwf else "none"
    return f"met_forecast/issue_date={run:%Y-%m-%d}/issue_time={run:%H%M}Z/point={point}/ecmwf_run={ecmwf_part}.xml"


def met_forecast(ctx, points=None, delay=0.5):
    """Fetch point forecasts, keyed by model issue time. If the first point's
    issue is already archived, the run hasn't changed and the rest are skipped."""
    points = points or config.FORECAST_POINTS
    for i, (name, (lat, lon)) in enumerate(points.items()):
        url = config.MET_FORECAST_URL.format(lat=lat, lon=lon)
        try:
            raw = ctx.fetch(url)
            parsed = parse.met_forecast(raw)
            report = validate.met_forecast(parsed)
        except Exception as e:
            ctx.fail(url, e)
            continue
        if report["ok"]:
            rel = _forecast_key(parsed, name)
            if ctx.store.exists(f"raw/{rel}.gz"):
                if i == 0:
                    log.info("forecast run already archived, skipping: %s", rel)
                    return
                continue
        else:
            rel = f"met_forecast/invalid/{ctx.stamp}/point={name}.xml"
        report["point"] = {"name": name, "lat": lat, "lon": lon}
        ctx.save(rel, raw, report, "application/xml")
        ctx.sleep(delay)


def met_warnings(ctx):
    url = config.MET_WARNINGS_URL
    try:
        raw = ctx.fetch(url)
        report = validate.met_warnings(parse.met_warnings(raw))
    except Exception as e:
        ctx.fail(url, e)
        return
    ctx.save(f"met_warnings/{ctx.stamp}.json", raw, report, "application/json")


def met_obs(ctx, stations=None, delay=0.5):
    stations = stations or config.OBS_STATIONS
    for slug, expected in stations.items():
        url = config.MET_OBS_URL.format(slug=slug)
        try:
            raw = ctx.fetch(url)
            report = validate.met_obs(parse.met_obs(raw), expected)
        except Exception as e:
            ctx.fail(url, e)
            continue
        date = ctx.now.strftime("%Y-%m-%d")
        ctx.save(f"met_obs/date={date}/station={slug}/fetched={ctx.now:%H%M%S}Z.json", raw, report, "application/json")
        ctx.sleep(delay)


def opw_hydrodata(ctx, params=("S", "Q", "Precip"), delay=0.5):
    # Imported here to keep the module graph simple for the other jobs.
    from river_forecast.ingest import hydrodata

    hydrodata.pull(ctx, params=params, delay=delay)


JOBS = {
    "opw_latest": opw_latest,
    "opw_csv": opw_csv,
    "met_forecast": met_forecast,
    "met_warnings": met_warnings,
    "met_obs": met_obs,
    "opw_hydrodata": opw_hydrodata,
}
