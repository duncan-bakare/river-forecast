"""Validation of parsed payloads. Each validator returns a report dict:
{"ok": bool, "n": int, "issues": [str], ...}. "ok" is False only for problems
that make the payload unusable; suspicious-but-usable data goes in "issues"."""

from datetime import timedelta
from itertools import pairwise

from river_forecast.ingest import parse

# Plausible ranges. OPW 0001 is a local staff-gauge level; OD is metres above
# Ordnance Datum (Suir headwaters are ~150 m OD).
LEVEL_RANGE = {"0001": (-5.0, 15.0), "OD": (-10.0, 250.0)}
MAX_GAP = timedelta(hours=2)
MOSTLY_EMPTY = 0.5


def _report(n, issues, fatal):
    return {"ok": not fatal, "n": n, "issues": issues}


def opw_csv(header, rows, sensor):
    issues, fatal = [], False
    if header[:1] != ["datetime"] or not any(c in header for c in parse.PRIMARY_COLUMNS):
        issues.append(f"unexpected header {header!r}")
        fatal = True
    elif header != ["datetime", "value"]:
        issues.append(f"non-standard header {header!r}")
    if not rows:
        issues.append("no rows")
        return _report(0, issues, True)

    bad_ts = [r["datetime_raw"] for r in rows if r["datetime"] is None]
    if bad_ts:
        issues.append(f"{len(bad_ts)} unparseable timestamps, e.g. {bad_ts[0]!r}")
        fatal = fatal or len(bad_ts) == len(rows)

    good = [r for r in rows if r["datetime"] is not None]
    times = [r["datetime"] for r in good]
    if any(b <= a for a, b in pairwise(times)):
        issues.append("timestamps not strictly increasing")

    missing = sum(1 for r in good if r["value"] is None)
    if missing:
        issues.append(f"{missing} missing/non-numeric values")
    if good and missing / len(good) > MOSTLY_EMPTY:
        issues.append(f"station mostly offline: {missing / len(good):.0%} of values missing")

    lo, hi = LEVEL_RANGE.get(sensor, (float("-inf"), float("inf")))
    out_of_range = [r["value"] for r in good if r["value"] is not None and not lo <= r["value"] <= hi]
    if out_of_range:
        issues.append(f"{len(out_of_range)} values outside [{lo}, {hi}], e.g. {out_of_range[0]}")

    gaps = [(a, b) for a, b in pairwise(times) if b - a > MAX_GAP]
    if gaps:
        a, b = max(gaps, key=lambda g: g[1] - g[0])
        issues.append(f"{len(gaps)} gaps > {MAX_GAP}, longest {a:%Y-%m-%d %H:%M} -> {b:%Y-%m-%d %H:%M}")

    report = _report(len(rows), issues, fatal)
    if times:
        report["first"] = times[0].isoformat()
        report["last"] = times[-1].isoformat()
    return report


def opw_latest(records):
    issues = []
    if not records:
        return _report(0, ["no features"], True)
    no_time = sum(1 for r in records if not r["datetime"])
    if no_time:
        issues.append(f"{no_time} features without datetime")
    non_numeric = sum(1 for r in records if r["value"] is None)
    if non_numeric:
        issues.append(f"{non_numeric} features with non-numeric value")
    # A sudden drop in station count usually means a partial/broken feed.
    fatal = len(records) < 100
    if fatal:
        issues.append(f"only {len(records)} features (expected ~2000)")
    return _report(len(records), issues, fatal)


def met_forecast(parsed, min_hours=24):
    issues, fatal = [], False
    harmonie = parsed["models"].get("harmonie")
    if not harmonie or harmonie["run"] is None:
        issues.append("no harmonie model run time")
        fatal = True
    rows = parsed["instants"]
    if len(rows) < min_hours:
        issues.append(f"only {len(rows)} instant time steps")
        fatal = True
    neg = [p for p in parsed["precipitation"] if p["mm"] is not None and p["mm"] < 0]
    if neg:
        issues.append(f"{len(neg)} negative precipitation values")
        fatal = True
    no_precip = sum(1 for p in parsed["precipitation"] if p["mm"] is None)
    if no_precip:
        issues.append(f"{no_precip} precipitation windows without a value")
    temps = [r.get("temperature_c") for r in rows if r.get("temperature_c") is not None]
    if any(not -30 <= t <= 45 for t in temps):
        issues.append("temperature outside [-30, 45] C")
    return _report(len(rows), issues, fatal)


def met_warnings(items):
    issues = []
    required = ("id", "type", "level", "onset", "expiry", "regions")
    for i, w in enumerate(items):
        missing = [k for k in required if k not in w]
        if missing:
            issues.append(f"warning {i} missing {missing}")
    return _report(len(items), issues, False)


def met_obs(items, expected_name):
    """The obs endpoint returns Dublin Airport for unknown slugs, so the
    station name in the payload must match what we asked for."""
    issues = []
    if not items:
        return _report(0, ["empty observations list"], False)
    names = {o.get("name") for o in items}
    if names != {expected_name}:
        issues.append(f"expected station {expected_name!r}, got {sorted(map(str, names))}")
        return _report(len(items), issues, True)
    no_rain = sum(1 for o in items if str(o.get("rainfall", "")).strip() in ("", "-", "N/A"))
    if no_rain:
        issues.append(f"{no_rain} observations without rainfall")
    return _report(len(items), issues, False)
