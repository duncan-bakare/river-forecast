"""Met Éireann weather warnings relevant to the Suir catchment, for display next to the forecast.

The feed lists regions by code; the headline lists county or province names. We match on the headline and
fail open: a warning whose area we can't parse is shown rather than hidden."""

import json
import re

import pandas as pd

# Clonmel is in Tipperary; the Suir also drains Waterford, Kilkenny, and small parts of Limerick and Cork.
COUNTIES = ("Tipperary", "Waterford", "Kilkenny", "Limerick", "Cork")
AREAS_OK = (*COUNTIES, "Munster", "Leinster", "Ireland", "Nationwide", "all counties")
ALL_NAMES = (
    "Carlow Cavan Clare Cork Donegal Dublin Galway Kerry Kildare Kilkenny Laois Leitrim Limerick Longford Louth "
    "Mayo Meath Monaghan Offaly Roscommon Sligo Tipperary Waterford Westmeath Wexford Wicklow "
    "Connacht Leinster Munster Ulster Ireland Nationwide"
).split()
SOURCE_URL = "https://www.met.ie/warnings"


def _utc(s):
    return pd.Timestamp(s).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _areas(headline):
    tail = re.split(r"warning for", headline, maxsplit=1, flags=re.I)[-1]
    return [n for n in ALL_NAMES if re.search(rf"\b{n}\b", tail, re.I)], tail.strip()


def relevant(raw, now):
    """-> list of active or upcoming warnings affecting the catchment, soonest first."""
    now = pd.Timestamp(now).tz_convert("UTC")
    out = []
    for w in json.loads(raw):
        try:
            if pd.Timestamp(w["expiry"]) < now:
                continue
            names, tail = _areas(w.get("headline", ""))
            parsed = bool(names)
            if parsed and not any(n.lower() in (a.lower() for a in AREAS_OK) for n in names):
                continue
            out.append(
                {
                    "type": w.get("type"),
                    "level": w.get("level"),
                    "headline": w.get("headline"),
                    "areas": tail if parsed else None,
                    "area_parsed": parsed,
                    "onset": _utc(w["onset"]),
                    "expiry": _utc(w["expiry"]),
                }
            )
        except (KeyError, ValueError, TypeError):
            out.append(
                {
                    "type": None,
                    "level": None,
                    "headline": "Warning could not be read; check met.ie",
                    "area_parsed": False,
                }
            )
    return sorted(out, key=lambda w: w.get("onset") or "")


def document(raw, now, error=None):
    base = {"source": "Met Éireann", "url": SOURCE_URL, "checked_at": _utc(now)}
    if raw is None:
        return {**base, "status": "unavailable", "error": str(error), "items": []}
    return {**base, "status": "ok", "items": relevant(raw, now)}
