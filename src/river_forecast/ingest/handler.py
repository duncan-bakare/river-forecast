"""Lambda entry point. Event: {"job": "<name>", ...job kwargs}."""

import logging
import os

from river_forecast.ingest.jobs import JOBS, Ctx
from river_forecast.ingest.storage import S3Store

logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger(__name__)


class IngestError(Exception):
    pass


def run(event, store, **ctx_kwargs):
    event = dict(event)
    name = event.pop("job")
    ctx = Ctx(store, **ctx_kwargs)
    JOBS[name](ctx, **event)
    failed = [r for r in ctx.results if not r["ok"]]
    summary = {
        "job": name,
        "items": len(ctx.results),
        "failed": len(failed),
        "with_issues": sum(1 for r in ctx.results if r["issues"]),
    }
    log.info("summary %s", summary)
    # Fail the invocation (and trip the Errors alarm) only if nothing usable
    # came back; partial failures are recorded in validation/ and quarantine/.
    if ctx.results and len(failed) == len(ctx.results):
        raise IngestError(f"{name}: all {len(failed)} items failed: {failed[0]['issues']}")
    return summary


def lambda_handler(event, context):
    return run(event, S3Store(os.environ["DATA_BUCKET"]))
