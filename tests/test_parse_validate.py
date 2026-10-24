from river_forecast.ingest import parse, validate


def test_opw_csv_fixture_is_valid(fixture_bytes):
    header, rows = parse.opw_csv(fixture_bytes("opw_09369_0001.csv"))
    report = validate.opw_csv(header, rows, "0001")
    assert header == ["datetime", "value"]
    assert len(rows) == 49
    assert report["ok"] and report["issues"] == []
    assert report["first"].startswith("2026-09-")


def test_opw_csv_reports_gaps_missing_and_range():
    raw = b"datetime,value\n2026-10-01 00:00,0.5\n2026-10-01 00:15,\n2026-10-01 06:00,99.0\n"
    header, rows = parse.opw_csv(raw)
    report = validate.opw_csv(header, rows, "0001")
    assert report["ok"]  # usable, but flagged
    text = " ".join(report["issues"])
    assert "1 missing" in text
    assert "outside" in text
    assert "gaps" in text


def test_opw_csv_bad_header_is_fatal():
    header, rows = parse.opw_csv(b"<html>Not found</html>\n")
    assert not validate.opw_csv(header, rows, "0001")["ok"]


def test_opw_csv_non_monotonic():
    raw = b"datetime,value\n2026-10-01 00:15,0.5\n2026-10-01 00:00,0.5\n"
    report = validate.opw_csv(*parse.opw_csv(raw), "0001")
    assert "not strictly increasing" in " ".join(report["issues"])


def test_opw_latest_parses_but_small_feed_is_fatal(fixture_bytes):
    records = parse.opw_latest(fixture_bytes("opw_latest.json"))
    assert {r["station_ref"] for r in records} == {"0000009369", "0000016011", "0000001041"}
    assert all(r["lat"] and r["lon"] for r in records)
    # The fixture is a 3-station extract; a real feed that small means breakage.
    assert not validate.opw_latest(records)["ok"]


def test_met_forecast_fixture(fixture_bytes):
    parsed = parse.met_forecast(fixture_bytes("met_forecast.xml"))
    assert parsed["models"]["harmonie"]["run"] is not None
    assert "ecmwf" in parsed["models"]
    assert len(parsed["instants"]) > 48
    assert all(p["mm"] >= 0 for p in parsed["precipitation"] if p["mm"] is not None)
    times = [r["time"] for r in parsed["instants"]]
    assert times == sorted(times)
    assert validate.met_forecast(parsed)["ok"]


def test_met_forecast_negative_precip_fatal():
    xml = (
        b'<weatherdata created="2026-10-24T12:00:00Z"><meta>'
        b'<model name="harmonie" termin="2026-10-24T06:00:00Z" from="2026-10-24T13:00:00Z" to="2026-10-26T12:00:00Z"/>'
        b'</meta><product><time from="2026-10-24T12:00:00Z" to="2026-10-24T13:00:00Z"><location>'
        b'<precipitation value="-1"/></location></time></product></weatherdata>'
    )
    report = validate.met_forecast(parse.met_forecast(xml))
    assert not report["ok"]
    assert any("negative" in i for i in report["issues"])


def test_met_warnings_fixture(fixture_bytes):
    items = parse.met_warnings(fixture_bytes("met_warnings.json"))
    assert validate.met_warnings(items) == {"ok": True, "n": len(items), "issues": []}


def test_met_obs_matches_station(fixture_bytes):
    items = parse.met_obs(fixture_bytes("met_obs_casement.json"))
    assert validate.met_obs(items, "Casement")["ok"]


def test_met_obs_unknown_slug_returns_dublin_airport_and_is_rejected(fixture_bytes):
    # Real behaviour seen 2026-10-24: /observations/clonmel/today returns Dublin Airport.
    items = parse.met_obs(fixture_bytes("met_obs_unknown_slug.json"))
    report = validate.met_obs(items, "Clonmel")
    assert not report["ok"]
    assert "Dublin Airport" in report["issues"][0]


def test_opw_csv_flags_offline_station():
    lines = ["datetime,value"] + [f"2026-10-01 {h:02d}:00," for h in range(10)] + ["2026-10-01 10:00,0.2"]
    raw = "\n".join(lines).encode()
    report = validate.opw_csv(*parse.opw_csv(raw), "0001")
    assert report["ok"]
    assert any("mostly offline" in i for i in report["issues"])


def test_opw_csv_backup_columns_use_primary_value():
    raw = b"datetime,backup_value,main_value\n2026-10-01 00:00,9.9,0.5\n"
    header, rows = parse.opw_csv(raw)
    assert rows[0]["value"] == 0.5
    report = validate.opw_csv(header, rows, "0001")
    assert report["ok"]
    assert "non-standard header" in report["issues"][0]

    header, rows = parse.opw_csv(b"datetime,value,backup_value\n2026-10-01 00:00,0.4,9.9\n")
    assert rows[0]["value"] == 0.4
