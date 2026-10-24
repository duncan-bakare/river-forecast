"""OPW Hydro-Data bulk archive pull.

Hydro-Data publishes, per station and parameter, a ZIP holding `tsvalues.csv`:
`#key;value` header lines, then `#Timestamp;Value;Quality Code[;...]`, then
semicolon-separated rows. ZIPs are stored byte-for-byte under a dated snapshot
so OPW's later revisions (e.g. quality 254 -> 31) can be compared.
"""

import io
import json
import math
import zipfile

from river_forecast import config


def _km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def select_stations(param, layer):
    """Station numbers to pull for one parameter, given its layer index."""
    if param == "Precip":
        pts = config.FORECAST_POINTS.values()
        return sorted(
            {
                r["station_no"]
                for r in layer
                if r.get("station_latitude") is not None
                and min(_km(lat, lon, r["station_latitude"], r["station_longitude"]) for lat, lon in pts)
                <= config.RAIN_GAUGE_RADIUS_KM
            }
        )
    return sorted(
        {
            r["station_no"]
            for r in layer
            if r["station_no"].startswith(config.HYDRODATA_AREA_PREFIXES)
            or r["station_no"] in config.HYDRODATA_EXTRA_STATIONS
        }
    )


def read_tsvalues(raw_zip):
    """Yield (meta dict, column names, row iterator) from a bulk ZIP without
    loading the whole CSV into memory."""
    zf = zipfile.ZipFile(io.BytesIO(raw_zip))
    names = zf.namelist()
    if names != ["tsvalues.csv"]:
        raise ValueError(f"unexpected zip members {names}")
    fh = io.TextIOWrapper(zf.open("tsvalues.csv"), encoding="utf-8-sig")
    meta, columns = {}, None
    for line in fh:
        line = line.rstrip("\r\n")
        if not line.startswith("#"):
            # first data row
            return meta, columns, _chain(line, fh)
        key, _, value = line[1:].partition(";")
        if key == "Timestamp":
            columns = line[1:].split(";")
        else:
            meta[key] = value
    return meta, columns, iter(())


def _chain(first, fh):
    yield first
    for line in fh:
        line = line.rstrip("\r\n")
        if line:
            yield line


def validate_zip(raw_zip):
    issues, fatal = [], False
    try:
        meta, columns, rows = read_tsvalues(raw_zip)
    except (zipfile.BadZipFile, ValueError, UnicodeDecodeError) as e:
        return {"ok": False, "n": 0, "issues": [f"unreadable: {e}"]}
    if not columns or columns[:3] != ["Timestamp", "Value", "Quality Code"]:
        return {"ok": False, "n": 0, "issues": [f"unexpected columns {columns}"]}
    n, first, last, codes, missing = 0, None, None, {}, 0
    for line in rows:
        parts = line.split(";")
        ts, val, q = parts[0], parts[1], parts[2]
        first = first or ts
        last = ts
        codes[q] = codes.get(q, 0) + 1
        if val == "":
            missing += 1
        n += 1
    declared = int(meta.get("rows") or -1)
    if declared not in (-1, n):
        issues.append(f"header says {declared} rows, found {n}")
        fatal = True
    if n == 0:
        issues.append("empty series")
    return {
        "ok": not fatal,
        "n": n,
        "issues": issues,
        "first": first,
        "last": last,
        "missing": missing,
        "quality_codes": codes,
        "meta": meta,
        "columns": columns,
    }


def pull(ctx, params=("S", "Q", "Precip"), delay=0.5):
    snapshot = ctx.now.strftime("%Y-%m-%d")
    base = config.HYDRODATA_BASE
    for param in params:
        layer_id, filename = config.HYDRODATA_PARAMS[param]
        url = f"{base}/layers/{layer_id}/index.json"
        try:
            raw_layer = ctx.fetch(url)
            layer = json.loads(raw_layer)
        except Exception as e:
            ctx.fail(url, e)
            continue
        stations = select_stations(param, layer)
        ctx.save(
            f"opw_hydrodata/snapshot={snapshot}/layers/{layer_id}.json",
            raw_layer,
            {"ok": True, "n": len(layer), "issues": [], "selected": stations},
            "application/json",
        )
        for station in stations:
            zurl = f"{base}/stations/0/{station}/{param}/{filename}"
            try:
                raw = ctx.fetch(zurl, timeout=120)
            except Exception as e:
                ctx.fail(zurl, e)
                continue
            report = validate_zip(raw)
            report["url"] = zurl
            ctx.save(
                f"opw_hydrodata/snapshot={snapshot}/param={param}/station={station}/{filename}",
                raw,
                report,
                "application/zip",
                compress=False,
            )
            _save_station_index(ctx, base, snapshot, station)
            ctx.sleep(delay)


def _save_station_index(ctx, base, snapshot, station):
    url = f"{base}/stations/0/{station}/index.json"
    key = f"opw_hydrodata/snapshot={snapshot}/stations/{station}/index.json"
    if station in ctx.seen:
        return
    ctx.seen.add(station)
    try:
        raw = ctx.fetch(url)
        json.loads(raw)
    except Exception as e:
        ctx.fail(url, e)
        return
    ctx.save(key, raw, {"ok": True, "n": 1, "issues": []}, "application/json")
