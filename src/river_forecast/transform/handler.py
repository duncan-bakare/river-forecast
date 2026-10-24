"""Transform Lambda: raw -> curated Parquet. Event: {"job": "hydrodata" | "realtime"}.

curated/opw_hydrodata/snapshot=YYYY-MM-DD/param=P/station=NNNNN.parquet
curated/opw_hydrodata/snapshot=YYYY-MM-DD/stations/param=P.parquet
curated/opw_realtime/sensor=S/station=NNNNN.parquet
"""

import gzip
import io
import logging
import os
import re

import pyarrow as pa
import pyarrow.parquet as pq

from river_forecast import config
from river_forecast.ingest.storage import S3Store
from river_forecast.transform import parquet

logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger(__name__)

KEEP_CURATED_SNAPSHOTS = 2
HD_RAW = "raw/opw_hydrodata/"
HD_CURATED = "curated/opw_hydrodata/"
ZIP_KEY = re.compile(r"snapshot=([\d-]+)/param=(\w+)/station=(\d+)/[^/]+\.zip$")
CSV_KEY = re.compile(r"raw/opw_csv/period=(\w+)/date=([\d-]+)/station=(\d+)/sensor=(\w+)\.csv\.gz$")


def _parquet_bytes(table):
    buf = io.BytesIO()
    opts = parquet.WRITE_OPTIONS
    if table.schema != parquet.SCHEMA:  # metadata tables: plain defaults
        opts = {"compression": opts["compression"]}
    pq.write_table(table, buf, **opts)
    return buf.getvalue()


def _snapshots(store, prefix):
    return sorted({m.group(1) for k in store.list(prefix) if (m := re.search(r"snapshot=([\d-]+)/", k))})


def hydrodata(store, snapshot=None, params=None):
    """Convert one raw snapshot (default: latest). `params` limits the run to some
    parameters so a refresh can be split across invocations."""
    snapshot = snapshot or _snapshots(store, HD_RAW)[-1]
    written, meta_rows = 0, []
    for key in store.list(f"{HD_RAW}snapshot={snapshot}/param="):
        m = ZIP_KEY.search(key)
        if not m:
            continue
        _, param, station = m.groups()
        if params and param not in params:
            continue
        table, meta = parquet.hydrodata_zip_to_table(store.get(key), param, key)
        meta_rows.append(
            {
                "station_no": station,
                "param": param,
                "station_name": meta.get("station_name"),
                "lat": float(meta["station_latitude"]) if meta.get("station_latitude") else None,
                "lon": float(meta["station_longitude"]) if meta.get("station_longitude") else None,
                "river": meta.get("WTO_OBJECT"),
                "unit": meta.get("ts_unitsymbol"),
                "rows": table.num_rows,
            }
        )
        if table.num_rows == 0:
            continue
        store.put(
            f"{HD_CURATED}snapshot={snapshot}/param={param}/station={station}.parquet",
            _parquet_bytes(table),
            "application/vnd.apache.parquet",
        )
        written += 1
        del table
        pa.default_memory_pool().release_unused()
    for param in sorted({r["param"] for r in meta_rows}):
        store.put(
            f"{HD_CURATED}snapshot={snapshot}/stations/param={param}.parquet",
            _parquet_bytes(pa.Table.from_pylist([r for r in meta_rows if r["param"] == param])),
            "application/vnd.apache.parquet",
        )
    # Curated snapshots are rebuildable from raw; keep only the newest few.
    old = _snapshots(store, HD_CURATED)[:-KEEP_CURATED_SNAPSHOTS]
    for snap in old:
        for k in store.list(f"{HD_CURATED}snapshot={snap}/"):
            store.delete(k)
    return {"job": "hydrodata", "snapshot": snapshot, "files": written, "pruned_snapshots": old}


def realtime(store):
    wanted = {num for _, num, _ in config.all_stations()}
    groups = {}
    for key in store.list("raw/opw_csv/"):
        m = CSV_KEY.search(key)
        if not m:
            continue
        period, date, station, sensor = m.groups()
        if station in wanted and sensor in config.OPW_SENSORS:
            # month snapshots first, then week files, oldest date first: newer wins
            groups.setdefault((station, sensor), []).append(((date, period != "month"), key))
    written = 0
    for (station, sensor), items in sorted(groups.items()):
        csvs = [(key, gzip.decompress(store.get(key))) for _, key in sorted(items)]
        table = parquet.realtime_csvs_to_table(station, sensor, csvs)
        store.put(
            f"curated/opw_realtime/sensor={sensor}/station={station}.parquet",
            _parquet_bytes(table),
            "application/vnd.apache.parquet",
        )
        written += 1
    return {"job": "realtime", "files": written}


JOBS = {"hydrodata": hydrodata, "realtime": realtime}


def run(event, store):
    event = dict(event)
    out = JOBS[event.pop("job")](store, **event)
    log.info("summary %s", out)
    return out


def lambda_handler(event, context):
    return run(event, S3Store(os.environ["DATA_BUCKET"]))
