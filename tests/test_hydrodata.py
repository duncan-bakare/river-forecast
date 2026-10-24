import io
import json
import zipfile
from datetime import UTC, datetime

from river_forecast.ingest import hydrodata, jobs
from river_forecast.ingest.storage import MemoryStore

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)


def _zip(text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("tsvalues.csv", text)
    return b.getvalue()


def test_select_stations_level_and_rain(fixture_bytes):
    layer10 = json.loads(fixture_bytes("hd_layer10.json"))
    assert hydrodata.select_stations("S", layer10) == ["09369", "16011", "16136"]
    layer40 = json.loads(fixture_bytes("hd_layer40.json"))
    # Cashel and Dundrum are within 15 km of a Suir point; Fermoy (80727) is not.
    assert hydrodata.select_stations("Precip", layer40) == ["81623", "81630"]


def test_validate_real_level_zip(fixture_bytes):
    r = hydrodata.validate_zip(fixture_bytes("hd_16011_S.zip"))
    assert r["ok"] and r["n"] == 40 and r["issues"] == []
    assert r["meta"]["station_no"] == "16011"
    assert r["first"].startswith("1953-10-24")


def test_validate_rain_zip_has_extra_column(fixture_bytes):
    r = hydrodata.validate_zip(fixture_bytes("hd_81623_Precip.zip"))
    assert r["ok"]
    assert r["columns"] == ["Timestamp", "Value", "Quality Code", "Aggregation Accuracy %"]
    assert r["missing"] >= 1  # first row of the real file is `;;-1;`


def test_validate_empty_series_is_ok_but_flagged(fixture_bytes):
    r = hydrodata.validate_zip(fixture_bytes("hd_09369_Q_empty.zip"))
    assert r["ok"] and r["n"] == 0 and r["issues"] == ["empty series"]


def test_validate_row_count_mismatch_is_fatal():
    raw = _zip("#station_no;1\n#rows;5\n#Timestamp;Value;Quality Code\n2020-01-01T00:00:00.000Z;1.0;31\n")
    r = hydrodata.validate_zip(raw)
    assert not r["ok"] and "header says 5 rows" in r["issues"][0]


def test_validate_not_a_zip():
    assert not hydrodata.validate_zip(b"<html>404</html>")["ok"]


def test_pull_stores_zip_untouched_under_dated_snapshot(fixture_bytes):
    store = MemoryStore()
    level = fixture_bytes("hd_16011_S.zip")
    calls = []

    def fetch(url, **kw):
        calls.append(url)
        if url.endswith("layers/10/index.json"):
            return fixture_bytes("hd_layer10.json")
        if url.endswith("Waterlevel_complete.zip"):
            return level
        if url.endswith("/index.json"):
            return b'{"station_no": "x"}'
        raise AssertionError(url)

    ctx = jobs.Ctx(store, fetch=fetch, now=NOW, sleep=lambda s: None)
    hydrodata.pull(ctx, params=("S",))
    key = "raw/opw_hydrodata/snapshot=2026-10-24/param=S/station=16011/Waterlevel_complete.zip"
    assert store.get(key) == level  # byte-for-byte, not re-compressed
    assert "raw/opw_hydrodata/snapshot=2026-10-24/layers/10.json.gz" in store.objects
    assert "raw/opw_hydrodata/snapshot=2026-10-24/stations/16011/index.json.gz" in store.objects
    report = json.loads(
        store.get("validation/opw_hydrodata/snapshot=2026-10-24/param=S/station=16011/Waterlevel_complete.zip.json")
    )
    assert report["n"] == 40
    # Station metadata fetched once per station.
    assert sum(u.endswith("/16011/index.json") for u in calls) == 1
