"""Raw -> Parquet. Every row keeps the source's quality code (null for the
real-time feed, which has none) plus a derived `quality_status`:

  checked      OPW-inspected data (codes 31, 32, 36, 41, 42, 46, 56)
  provisional  unchecked data (code 254, and everything from the real-time feed)
  unreliable   code 101
  unusable     code 151
  missing      codes 255 / -1, or an empty value
  undocumented codes OPW doesn't define (e.g. 96, 99) - kept, not interpreted

41/42 ("poor measured level") and 46/56 (poor / extrapolated rating) count as
checked because OPW has inspected them; filter on quality_code if you need more.
"""

import io
import zipfile

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv

CHECKED = (31, 32, 36, 41, 42, 46, 56)
PROVISIONAL = (254,)
UNRELIABLE = (101,)
UNUSABLE = (151,)
MISSING = (255, -1)
STATUSES = ("checked", "provisional", "unreliable", "unusable", "missing", "undocumented")

TS = pa.timestamp("ms", tz="UTC")
DICT = pa.dictionary(pa.int8(), pa.string())
SCHEMA = pa.schema(
    [
        ("station_no", DICT),
        ("param", DICT),
        ("timestamp", TS),
        ("value", pa.float64()),
        ("quality_code", pa.int16()),
        ("quality_status", DICT),
        ("aggregation_accuracy", pa.float64()),
        ("unit", DICT),
        ("source", DICT),
    ]
)


def quality_status(codes, values):
    """codes: int array with -9999 for null; values: float array (NaN = empty).
    Returns a dictionary array built from int8 indices (no per-row Python strings)."""
    codes = np.asarray(codes)
    idx = np.full(codes.shape, STATUSES.index("undocumented"), dtype=np.int8)
    idx[np.isin(codes, CHECKED)] = STATUSES.index("checked")
    idx[np.isin(codes, PROVISIONAL) | (codes == -9999)] = STATUSES.index("provisional")
    idx[np.isin(codes, UNRELIABLE)] = STATUSES.index("unreliable")
    idx[np.isin(codes, UNUSABLE)] = STATUSES.index("unusable")
    idx[np.isin(codes, MISSING) | np.isnan(values)] = STATUSES.index("missing")
    return pa.DictionaryArray.from_arrays(pa.array(idx, pa.int8()), pa.array(STATUSES, pa.string()))


def _const(value, n):
    return pa.DictionaryArray.from_arrays(pa.array(np.zeros(n, np.int8)), pa.array([value], pa.string()))


def _table(station, param, ts, values, codes, accuracy, unit, source):
    n = len(ts)
    vals = values.to_numpy(zero_copy_only=False).astype("float64")
    code_np = pc.fill_null(codes, -9999).to_numpy(zero_copy_only=False) if codes is not None else np.full(n, -9999)
    return pa.table(
        {
            "station_no": _const(station, n),
            "param": _const(param, n),
            "timestamp": ts,
            "value": pa.array(vals, pa.float64(), from_pandas=True),
            "quality_code": codes.cast(pa.int16()) if codes is not None else pa.nulls(n, pa.int16()),
            "quality_status": quality_status(code_np, vals),
            "aggregation_accuracy": accuracy if accuracy is not None else pa.nulls(n, pa.float64()),
            "unit": _const(unit, n),
            "source": _const(source, n),
        }
    ).cast(SCHEMA)


def hydrodata_zip_to_table(raw_zip, param, source):
    """One Hydro-Data `*_complete.zip` -> Arrow table."""
    zf = zipfile.ZipFile(io.BytesIO(raw_zip))
    data = zf.read("tsvalues.csv")
    meta, columns, skip = {}, None, 0
    for raw_line in io.BytesIO(data):
        line = raw_line.decode("utf-8-sig").rstrip("\r\n")
        if not line.startswith("#"):
            break
        skip += 1
        key, _, value = line[1:].partition(";")
        if key == "Timestamp":
            columns = line[1:].split(";")
        else:
            meta[key] = value
    if not columns or columns[:3] != ["Timestamp", "Value", "Quality Code"]:
        raise ValueError(f"unexpected columns {columns}")
    names = ["ts", "value", "code"] + [f"extra{i}" for i in range(len(columns) - 3)]
    types = {"ts": TS, "value": pa.float64(), "code": pa.int32()}
    if len(names) > 3:
        types["extra0"] = pa.float64()
    table = pacsv.read_csv(
        io.BytesIO(data),
        read_options=pacsv.ReadOptions(skip_rows=skip, column_names=names, encoding="utf-8"),
        parse_options=pacsv.ParseOptions(delimiter=";"),
        convert_options=pacsv.ConvertOptions(
            column_types=types, timestamp_parsers=[pacsv.ISO8601], strings_can_be_null=True
        ),
    )
    station = meta.get("station_no", "")
    accuracy = table.column("extra0").combine_chunks() if "extra0" in table.column_names else None
    return (
        _table(
            station,
            param,
            table.column("ts").combine_chunks(),
            table.column("value").combine_chunks(),
            table.column("code").combine_chunks(),
            accuracy,
            meta.get("ts_unitsymbol", ""),
            source,
        ),
        meta,
    )


WRITE_OPTIONS = {
    "compression": "snappy",  # the AWS SDK for pandas layer's pyarrow is built without zstd
    "use_dictionary": ["station_no", "param", "quality_status", "unit", "source"],
    "column_encoding": {
        "timestamp": "DELTA_BINARY_PACKED",
        "quality_code": "DELTA_BINARY_PACKED",
        "value": "BYTE_STREAM_SPLIT",
        "aggregation_accuracy": "BYTE_STREAM_SPLIT",
    },
}


def realtime_csvs_to_table(station, sensor, csvs):
    """Merge OPW real-time CSVs [(source_key, bytes)] for one station/sensor.
    Later sources win on duplicate timestamps, so pass them oldest first."""
    from river_forecast.ingest import parse

    latest = {}
    for source, raw in csvs:
        _, rows = parse.opw_csv(raw)
        for r in rows:
            if r["datetime"] is not None:
                latest[r["datetime"]] = (r["value"], source)
    keys = sorted(latest)
    ts = pa.array(keys, TS)
    values = pa.array([latest[k][0] for k in keys], pa.float64())
    sources = [latest[k][1] for k in keys]
    table = _table(station, sensor, ts, values, None, None, "m", "")
    return table.set_column(
        table.schema.get_field_index("source"), "source", pa.array(sources, pa.string()).dictionary_encode()
    ).cast(SCHEMA)
