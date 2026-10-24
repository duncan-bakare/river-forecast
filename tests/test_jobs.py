import gzip
import json
import urllib.error
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from river_forecast import config
from river_forecast.ingest import handler, jobs
from river_forecast.ingest.storage import MemoryStore, S3Store

NOW = datetime(2026, 10, 24, 13, 5, 0, tzinfo=UTC)


def fake_fetch(responses):
    calls = []

    def fetch(url):
        calls.append(url)
        for frag, body in responses.items():
            if frag in url:
                if isinstance(body, Exception):
                    raise body
                return body
        raise AssertionError(f"unexpected url {url}")

    fetch.calls = calls
    return fetch


def make_ctx(store, responses):
    return jobs.Ctx(store, fetch=fake_fetch(responses), now=NOW, sleep=lambda s: None)


def test_opw_csv_keys_and_validation(fixture_bytes):
    store = MemoryStore()
    ctx = make_ctx(store, {"waterlevel.ie/data/week/": fixture_bytes("opw_09369_0001.csv")})
    jobs.opw_csv(ctx, stations=["09369"], sensors=["0001"])
    key = "raw/opw_csv/period=week/date=2026-10-24/station=09369/sensor=0001.csv.gz"
    assert gzip.decompress(store.get(key)) == fixture_bytes("opw_09369_0001.csv")
    report = json.loads(store.get("validation/opw_csv/period=week/date=2026-10-24/station=09369/sensor=0001.csv.json"))
    assert report["ok"] and report["n"] == 49


def test_opw_csv_404_is_recorded_not_raised():
    store = MemoryStore()
    err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    ctx = make_ctx(store, {"waterlevel.ie": err})
    jobs.opw_csv(ctx, stations=["99999"], sensors=["0001"])
    assert store.objects == {}
    assert ctx.results[0]["ok"] is False


def test_bad_payload_goes_to_quarantine(fixture_bytes):
    store = MemoryStore()
    ctx = make_ctx(store, {"observations/clonmel": fixture_bytes("met_obs_unknown_slug.json")})
    jobs.met_obs(ctx, stations={"clonmel": "Clonmel"})
    keys = list(store.objects)
    assert any(k.startswith("quarantine/met_obs/") for k in keys)
    assert not any(k.startswith("raw/") for k in keys)


def test_forecast_dedupes_on_issue_time(fixture_bytes):
    store = MemoryStore()
    points = {"a": (53.0, -6.0), "b": (52.0, -7.0)}
    ctx = make_ctx(store, {"locationforecast": fixture_bytes("met_forecast.xml")})
    jobs.met_forecast(ctx, points=points)
    raw_keys = [k for k in store.objects if k.startswith("raw/")]
    assert len(raw_keys) == 2
    assert all("/issue_time=" in k and "/point=" in k for k in raw_keys)

    # Same run again: first point already archived, so only one request is made.
    ctx2 = make_ctx(store, {"locationforecast": fixture_bytes("met_forecast.xml")})
    jobs.met_forecast(ctx2, points=points)
    assert len(ctx2.fetch.calls) == 1
    assert len([k for k in store.objects if k.startswith("raw/")]) == 2


def test_handler_raises_when_everything_fails():
    with pytest.raises(handler.IngestError):
        handler.run(
            {"job": "met_warnings"},
            MemoryStore(),
            fetch=fake_fetch({"met.ie": OSError("boom")}),
            now=NOW,
            sleep=lambda s: None,
        )


def test_handler_summary(fixture_bytes):
    store = MemoryStore()
    out = handler.run(
        {"job": "met_warnings"}, store, fetch=fake_fetch({"met.ie": fixture_bytes("met_warnings.json")}), now=NOW
    )
    assert out == {"job": "met_warnings", "items": 1, "failed": 0, "with_issues": 0}
    assert "raw/met_warnings/date=2026-10-24/fetched=130500Z.json.gz" in store.objects


@mock_aws
def test_s3_store_roundtrip():
    s3 = boto3.client("s3", region_name="eu-west-1")
    s3.create_bucket(Bucket="test-bucket", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})
    store = S3Store("test-bucket", client=s3)
    assert not store.exists("raw/x.gz")
    store.put("raw/x.gz", b"hi")
    assert store.exists("raw/x.gz")
    assert not store.exists("raw/x")  # prefix match must not count
    assert store.get("raw/x.gz") == b"hi"


def test_stations_without_od_skip_od(fixture_bytes):
    store = MemoryStore()
    ctx = make_ctx(store, {"waterlevel.ie/data/week/": fixture_bytes("opw_09369_0001.csv")})
    jobs.opw_csv(ctx, stations=["16149", "09369"])
    assert ctx.fetch.calls == [
        "https://waterlevel.ie/data/week/16149_0001.csv",
        "https://waterlevel.ie/data/week/09369_0001.csv",
        "https://waterlevel.ie/data/week/09369_OD.csv",
    ]


def test_config_station_numbers_are_five_digits():
    stations = config.all_stations()
    assert len({n for _, n, _ in stations}) == len(stations)
    assert all(len(n) == 5 and n.isdigit() for _, n, _ in stations)
    assert {c for c, _, _ in stations} == {"dodder", "suir"}
