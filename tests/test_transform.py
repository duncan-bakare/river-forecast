import gzip
import io

import pyarrow.parquet as pq

from river_forecast.ingest.storage import MemoryStore
from river_forecast.transform import handler, parquet


def _read(store, key):
    return pq.read_table(io.BytesIO(store.get(key)))


def test_quality_status_mapping():
    import numpy as np

    codes = [31, 32, 36, 41, 46, 56, 254, 101, 151, 255, -1, 99, 96, -9999, 31]
    values = [1.0] * 14 + [float("nan")]
    got = parquet.quality_status(np.array(codes), np.array(values)).to_pylist()
    assert got == (
        ["checked"] * 6
        + ["provisional", "unreliable", "unusable", "missing", "missing", "undocumented", "undocumented"]
        + ["provisional", "missing"]  # null code = real-time; NaN value is missing whatever the code
    )


def test_hydrodata_zip_keeps_quality_code(fixture_bytes):
    table, meta = parquet.hydrodata_zip_to_table(fixture_bytes("hd_16011_S.zip"), "S", "src")
    assert table.schema == parquet.SCHEMA
    assert table.num_rows == 40
    assert meta["station_no"] == "16011"
    assert table["quality_code"].null_count == 0
    rows = table.to_pylist()
    assert rows[0]["quality_code"] == 31 and rows[0]["quality_status"] == "checked"
    assert rows[-1]["quality_code"] == 254 and rows[-1]["quality_status"] == "provisional"
    assert str(rows[0]["timestamp"]).startswith("1953-10-24 11:30:00")


def test_rain_zip_extra_column_and_missing(fixture_bytes):
    table, _ = parquet.hydrodata_zip_to_table(fixture_bytes("hd_81623_Precip.zip"), "Precip", "src")
    first = table.to_pylist()[0]
    assert first["value"] is None and first["quality_code"] == -1 and first["quality_status"] == "missing"
    assert table["unit"][0].as_py() == "mm"


def test_hydrodata_job_writes_partitions_and_prunes(fixture_bytes):
    store = MemoryStore()
    for snap in ("2026-08-01", "2026-09-01", "2026-10-24"):
        store.put(
            f"raw/opw_hydrodata/snapshot={snap}/param=S/station=16011/Waterlevel_complete.zip",
            fixture_bytes("hd_16011_S.zip"),
        )
        store.put(
            f"raw/opw_hydrodata/snapshot={snap}/param=Q/station=09369/Discharge_complete.zip",
            fixture_bytes("hd_09369_Q_empty.zip"),
        )
        handler.hydrodata(store, snapshot=snap)
    out = handler.hydrodata(store)  # defaults to latest raw snapshot
    assert out["snapshot"] == "2026-10-24" and out["files"] == 1  # empty Q series skipped
    snaps = {k.split("/")[2] for k in store.list("curated/opw_hydrodata/")}
    assert snaps == {"snapshot=2026-09-01", "snapshot=2026-10-24"}
    t = _read(store, "curated/opw_hydrodata/snapshot=2026-10-24/param=S/station=16011.parquet")
    assert t.num_rows == 40
    stations = _read(store, "curated/opw_hydrodata/snapshot=2026-10-24/stations.parquet").to_pylist()
    assert {(s["station_no"], s["param"], s["rows"]) for s in stations} == {("16011", "S", 40), ("09369", "Q", 0)}


def test_realtime_job_merges_and_newer_wins():
    store = MemoryStore()
    month = b"datetime,value\n2026-10-01 00:00,0.50\n2026-10-01 00:15,0.51\n"
    week = b"datetime,value\n2026-10-01 00:15,0.99\n2026-10-01 00:30,0.52\n"
    store.put("raw/opw_csv/period=month/date=2026-10-24/station=09369/sensor=OD.csv.gz", gzip.compress(month))
    store.put("raw/opw_csv/period=week/date=2026-10-24/station=09369/sensor=OD.csv.gz", gzip.compress(week))
    # Not a configured station: ignored.
    store.put("raw/opw_csv/period=month/date=2026-10-24/station=01041/sensor=OD.csv.gz", gzip.compress(month))
    assert handler.realtime(store) == {"job": "realtime", "files": 1}
    rows = _read(store, "curated/opw_realtime/sensor=OD/station=09369.parquet").to_pylist()
    assert [r["value"] for r in rows] == [0.50, 0.99, 0.52]
    assert {r["quality_status"] for r in rows} == {"provisional"}
    assert all(r["quality_code"] is None for r in rows)
    assert rows[1]["source"].endswith("period=week/date=2026-10-24/station=09369/sensor=OD.csv.gz")
