"""Upload a local directory of OPW month CSVs (<station>_<sensor>.csv) to the data
bucket, validated, under raw/opw_csv/period=month/date=<snapshot date>/.

Usage: python scripts/upload_month_snapshot.py <dir> <bucket> <YYYY-MM-DD>
"""

import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor

from river_forecast.ingest import parse, validate
from river_forecast.ingest.jobs import Ctx
from river_forecast.ingest.storage import S3Store


def main(src, bucket, date):
    ctx = Ctx(S3Store(bucket))
    files = sorted(pathlib.Path(src).glob("*_*.csv"))

    def one(path):
        station, sensor = path.stem.split("_", 1)
        raw = path.read_bytes()
        report = validate.opw_csv(*parse.opw_csv(raw), sensor)
        report["source"] = "local snapshot of waterlevel.ie/data/month"
        ctx.save(f"opw_csv/period=month/date={date}/station={station}/sensor={sensor}.csv", raw, report, "text/csv")

    with ThreadPoolExecutor(16) as pool:
        list(pool.map(one, files))
    bad = [r for r in ctx.results if not r["ok"]]
    print(f"uploaded {len(ctx.results)} files, {len(bad)} quarantined")
    for r in bad[:20]:
        print(" ", r["key"], r["issues"])


if __name__ == "__main__":
    main(*sys.argv[1:4])
