"""Parsers for the raw payloads. They never raise on bad rows: bad rows are
reported by the validators, and the raw bytes are always archived as fetched."""

import csv
import io
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime


def _float(s):
    if s is None:
        return None
    s = str(s).strip()
    if s in ("", "-", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def opw_latest(raw):
    """Latest-readings GeoJSON -> list of dicts."""
    out = []
    for f in json.loads(raw)["features"]:
        p = f.get("properties", {})
        coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
        out.append(
            {
                "station_ref": p.get("station_ref"),
                "station_name": p.get("station_name"),
                "sensor_ref": p.get("sensor_ref"),
                "datetime": p.get("datetime"),
                "value_raw": p.get("value"),
                "value": _float(p.get("value")),
                "err_code": p.get("err_code"),
                "lon": coords[0],
                "lat": coords[1],
            }
        )
    return out


PRIMARY_COLUMNS = ("value", "main_value")


def opw_csv(raw):
    """OPW day/week/month CSV, times in UTC -> (header, rows).

    Usually `datetime,value`; some stations add `backup_value` or use
    `datetime,backup_value,main_value`. We take the primary column by name.
    """
    text = raw.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    header = [h.strip() for h in next(reader, [])]
    col = next((header.index(c) for c in PRIMARY_COLUMNS if c in header), 1)
    rows = []
    for line in reader:
        if not line:
            continue
        ts = line[0].strip()
        val = line[col] if len(line) > col else ""
        rows.append({"datetime_raw": ts, "datetime": _parse_opw_ts(ts), "value_raw": val, "value": _float(val)})
    return header, rows


def _parse_opw_ts(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
    except ValueError:
        return None


def _iso(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def met_forecast(raw):
    """Met Éireann locationforecast XML -> dict with model run times and hourly rows.

    Instant parameters come in <time from=t to=t>; precipitation comes in
    <time from=t-1h to=t> (later 3h/6h windows on the ECMWF tail).
    """
    root = ET.fromstring(raw)
    models = {}
    for m in root.iter("model"):
        models[m.get("name")] = {
            "run": _iso(m.get("termin")),
            "from": _iso(m.get("from")),
            "to": _iso(m.get("to")),
        }
    instants, precip = {}, []
    for t in root.iter("time"):
        start, end = _iso(t.get("from")), _iso(t.get("to"))
        loc = t.find("location")
        if loc is None:
            continue
        if start == end:
            row = instants.setdefault(start, {"time": start})
            for child in loc:
                if child.tag == "temperature":
                    row["temperature_c"] = _float(child.get("value"))
                elif child.tag == "humidity":
                    row["humidity_pct"] = _float(child.get("value"))
                elif child.tag == "pressure":
                    row["pressure_hpa"] = _float(child.get("value"))
                elif child.tag == "windSpeed":
                    row["wind_mps"] = _float(child.get("mps"))
        else:
            p = loc.find("precipitation")
            if p is not None:
                precip.append(
                    {
                        "from": start,
                        "to": end,
                        "mm": _float(p.get("value")),
                        "mm_min": _float(p.get("minvalue")),
                        "mm_max": _float(p.get("maxvalue")),
                        "probability": _float(p.get("probability")),
                    }
                )
    return {
        "created": _iso(root.get("created")),
        "models": models,
        "instants": [instants[k] for k in sorted(k for k in instants if k)],
        "precipitation": precip,
    }


def met_warnings(raw):
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("warnings payload is not a list")
    return data


def met_obs(raw):
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("observations payload is not a list")
    return data
