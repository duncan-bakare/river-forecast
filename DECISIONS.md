# Decisions

Newest last. Each entry: what, why, alternatives.

## D1. Catchments: Dodder and Suir (2026-10-24)
- **What:** Target the Dodder (OPW stations 09010 Waldron's Bridge, 09369 Anglesea Road) and the whole Suir
  hydrometric area 16 (35 stations, including the FFWS back-up loggers).
- **Why:** CLAUDE.md says "both catchments" without naming them, so the owner was asked and chose these. They
  contrast well: a flashy urban river against a large, slow rural one.
- **Alternatives:** Dodder + Liffey (the Liffey is reservoir-regulated), Shannon + Suir (the Shannon is slow
  and regulated).
- **Limitation:** OPW has only two gauges on the Dodder. Dublin City Council's upstream Dodder gauges are not
  on waterlevel.ie.

## D2. Snapshot the national 5-week archive before building anything (2026-10-24)
- **What:** First action: downloaded all 2,030 OPW `month` CSVs (every station and sensor nationally, 108 MB,
  2026-09-19 12:00 → 2026-10-24 12:30 UTC) to local disk, then uploaded them, validated, to
  `raw/opw_csv/period=month/date=2026-10-24/`.
- **Why:** OPW keeps only 35 days of real-time data. Every day spent on infrastructure first loses a day of
  history. Grabbing everything nationally also hedged against the catchment choice.
- **Alternatives:** Ingest only the target stations (cheaper, but irreversible if scope changes).

## D3. Budget in USD, not EUR
- **What:** AWS Budget of **USD 11/month** with ACTUAL alerts at 50/80/100% and a FORECASTED alert at 100%.
  A CloudWatch billing alarm fires at USD 11 EstimatedCharges.
- **Why:** The account rejects EUR (`EUR is not in the supported unit set: [USD]`). USD 11 is just under
  EUR 10 at current rates, so alerts fire slightly early rather than late.
- **Caveat:** The billing alarm needs "Receive CloudWatch billing alerts" turned on in the Billing console
  preferences. That console-only setting has no API, so it has not been turned on yet, and the alarm stays at
  INSUFFICIENT_DATA until it is. The Budget works regardless.

## D4. Raw-first storage with validation reports and quarantine
- **What:** Every payload is stored gzipped exactly as fetched. A JSON validation report goes next to it under
  `validation/`. Payloads that are unusable (wrong schema, wrong station, negative rain) go to `quarantine/`
  instead of `raw/`. A Lambda invocation only fails (and trips the alarm) when *every* item in the job failed.
- **Why:** Raw is the only thing that can't be recreated once OPW's 35-day window passes. Parsing and Parquet
  can always be redone. One flaky station shouldn't page anyone.
- **Alternatives:** Parse to Parquet in the ingest Lambda (needs pyarrow, a ~50 MB layer, and couples ingest
  to the schema). Parquet compaction is left to the next phase.

## D5. Lambda on stdlib + boto3, arm64, EventBridge rules
- **What:** One Lambda function with a `job` field in the event. Five EventBridge rules. No layers and no
  dependencies beyond the runtime.
- **Why:** This is the cheapest option. Expected cost is a few cents a month: roughly 4k invocations, about
  10k PUTs, and under 1 GB stored per year. EventBridge rules (rather than Scheduler) need no extra IAM role.
- **Alternatives:** EventBridge Scheduler (adds a role), Step Functions (unneeded), ECS (overkill).

## D6. OPW collection strategy
- **What:** A daily `week` CSV for each target station (sensors `0001` = level vs local staff gauge, `OD` =
  level vs Ordnance Datum), plus an hourly national `geojson/latest` snapshot. Snapshots expire after 365 days.
- **Why:** The week CSV has every 15-minute reading, and the 7-day overlap tolerates up to 6 days of ingest
  outage. OPW asks automated users to use only these pre-generated files.
- **Alternatives:** Poll `latest` every 15 minutes (fragile, and it misses readings when late).
- Sensors 0002 (temperature), 0003 (battery voltage) and 0014 (signal strength) are skipped. Their meanings
  were checked on the station pages.

## D7. Source quirks found (stated plainly, not worked around silently)
1. **Met Éireann obs endpoint returns the wrong station for unknown slugs.** `prodapi.metweb.ie/observations/
   clonmel/today` returns Dublin Airport data with HTTP 200. The validator rejects any payload whose `name`
   doesn't match the requested station, and a test pins this behaviour. The endpoint is undocumented. The
   documented alternative for training is the Met Éireann historical hourly CSVs, to be evaluated in Phase 1.
   There is no synoptic station inside the Suir catchment; the nearest are Gurteen, Oak Park, Moore Park and
   Johnstown Castle.
2. **OPW CSV headers vary.** Most are `datetime,value`. Some stations add `backup_value`, and sensor `0011`
   files are `datetime,backup_value,main_value`. The parser takes the primary column by name and the
   validator notes "non-standard header". The first upload quarantined 53 such files; after the fix they were
   reprocessed into `raw/` and the quarantine copies were deleted.
3. **Back-up loggers have no OD sensor.** 16149, 16150, 16151, 16153, 16154 and Hollyford 16201 return 404
   for `_OD.csv`. They are excluded in config (`OPW_NO_OD`).
4. **Offline stations.** Piltown 16125 has had no readings since 2026-09-24 (5.7% of the 5-week values
   present). Hollyford 16201 has 11.4%. They are still archived, and the validator flags them as "mostly
   offline".
5. **Missing values are blank rows, not gaps.** OPW keeps the 15-minute timestamp and leaves `value` empty.
6. **`err_code` in the latest GeoJSON** (e.g. 99) is undocumented. It is stored as-is and not interpreted.

## D8. CI/CD and IAM
- **What:** `infra/bootstrap` (budget, alarms, state bucket, OIDC provider, deploy role, permissions boundary)
  is applied locally. `infra/main` is deployed by GitHub Actions on push to `main` via OIDC role
  `river-forecast-gha-deploy`, which can only be assumed from `refs/heads/main`. The trust policy matches
  GitHub's immutable OIDC subject (`repo:duncan-bakare@<id>/river-forecast@<id>:...`), which this repo uses
  instead of the classic `repo:owner/repo:...`.
- **Least privilege:** The deploy role can only touch `river-forecast-*` resources. It can only create or
  modify IAM roles that carry the `river-forecast-workload-boundary` permissions boundary, and it cannot
  remove that boundary. So a compromised workflow cannot mint an admin role. The state bucket is reachable
  only under `main/`.
- **State:** S3 backend with native lockfile (`use_lockfile`). No DynamoDB table.
- **Alternatives:** Long-lived access keys in GitHub secrets (forbidden by CLAUDE.md). One shared admin
  role (too broad).

## D9. Phase definitions
- **What:** CLAUDE.md names only Phase 0 ("ingestion for both catchments"). Phase 0 here means: raw ingestion,
  validation, tests, infra and CI for both catchments. Parquet compaction, modelling and the site come later.
