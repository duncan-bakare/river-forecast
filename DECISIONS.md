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
- **Correction (see D11):** The 35-day limit applies only to the real-time feed. For 29 of our 37 stations,
  OPW Hydro-Data publishes the full history. The snapshot is still the only history we have for the other 8,
  and for the local-gauge `0001` series.

## D3. Budget in USD, not EUR
- **What:** AWS Budget of **USD 11/month** with ACTUAL alerts at 50/80/100% and a FORECASTED alert at 100%.
  A CloudWatch billing alarm fires at USD 11 EstimatedCharges.
- **Why:** The account rejects EUR (`EUR is not in the supported unit set: [USD]`). USD 11 is just under
  EUR 10 at current rates, so alerts fire slightly early rather than late.
- **Caveat:** The billing alarm needs "Receive CloudWatch billing alerts" turned on in the Billing console
  preferences. That console-only setting has no API, so the owner turned it on by hand (done 2026-10-24).

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

## D10. Met Éireann point forecasts: archived by issue time, scheduled (checked 2026-10-24)
- **Yes, they are archived with issue times for both catchments.** There are 11 points: 3 on the Dodder
  (`dodder_glenasmole`, `dodder_firhouse`, `dodder_ballsbridge`) and 8 on the Suir (`suir_templemore`,
  `_thurles`, `_multeen`, `_cahir`, `_nire`, `_anner`, `_clonmel`, `_carrick`). Keys are
  `raw/met_forecast/issue_date=YYYY-MM-DD/issue_time=HHMMZ/point=<name>/ecmwf_run=YYYYMMDDTHHMMZ.xml.gz`.
  The issue time is the HARMONIE `termin` in the payload. The ECMWF run that supplies the tail beyond about
  +48 h is in the filename, because the two models are issued at different times. First archived issue:
  2026-10-24 06:00Z HARMONIE / 00:00Z ECMWF, all 11 points.
- **Yes, it is one of the 5 EventBridge rules.** `river-forecast-met-forecast`, `cron(15 * * * ? *)`, input
  `{"job":"met_forecast"}`, targeting `river-forecast-ingest`, ENABLED. The other four are `opw-latest`,
  `opw-csv-week`, `met-warnings` and `met-obs`. Polling is hourly, but each issue is stored once: the job
  fetches the first point, and if that issue is already in S3 it skips the rest. The scheduled run at
  13:15Z logged "forecast run already archived, skipping" as intended.
- **Caveat:** The API only serves the *latest* run, and there is no archive of past forecasts. The forecast
  archive therefore starts at 2026-10-24 06:00Z, and a run that is superseded before an hourly poll catches it
  is lost. Hourly polling makes that unlikely, but the actual issue cadence still needs confirming from a few
  days of keys.

## D11. OPW Hydro-Data: documented bulk download of full history (investigated 2026-10-24)
- **Not covered before this date.** D2 and D6 only considered the real-time waterlevel.ie feeds. Hydro-Data
  (`waterlevel.ie/hydro-data/`) is a separate KISTERS web app. Its own "API" page
  (`/hydro-data/html/API.html`) documents bulk and automated access.
- **What's offered:** These are static files per station, regenerated daily (Last-Modified 2026-10-24 01:00–05:30
  UTC), under `https://waterlevel.ie/hydro-data/data/internet/stations/0/<station_no>/`:
  - bulk ZIPs: `S/Waterlevel_complete.zip`, `Q/Discharge_complete.zip`, `TWater/WaterTemperature_complete.zip`,
    `Precip/Rainfall_complete.zip` (full period of record), plus `S/Waterlevel_calibration.zip`
  - JSON/CSV feeds: `week.json`, `month.json`, `year.json` (daily means), `annual_statistics.json` (AMAX),
    `gaugings.json`, `alarmlevel.json`, `*_48h.csv`
  - metadata: `<station>/index.json`, and a layer index at `/hydro-data/data/internet/layers/10/index.json`
- **Format:** Each ZIP holds a single `tsvalues.csv`, semicolon-separated, with `#key;value` header lines
  (station, lat/lon, parameter, unit, river, row count). Columns are `Timestamp;Value;Quality Code`, at 15-minute
  resolution with ISO UTC timestamps.
- **Coverage for our stations:** Level (S) ZIPs exist for 29 of 37. The 8 missing (404) are Waldron's Bridge
  09010 (the Dodder's second gauge), the six FFWS back-up loggers 16149–16154, and Hollyford 16201.
  Discharge (Q) has data for 14 Suir stations: 16001–16013 and 16051. All other Q ZIPs are header-only
  (`#rows;0`), including Anglesea Road, which is level-only. Examples: Clonmel 16011 level runs from
  1953-10-24 (2,559,601 rows, 9.7 MB zipped). Anglesea Road 09369 runs from 2021-10-06 (177,023 rows).
- **Datum:** The Hydro-Data `S` series ("Absolute", m) equals the real-time `OD` sensor, not `0001`. Both read
  15.581 for Clonmel and 2.937 for Anglesea Road at 2026-10-23 23:30Z.
- **Quality codes (water level):** 31 = inspected/approved, 32 = corrected, 41/42 = poor, 101 = unreliable,
  151 = unusable, 254 = unchecked provisional, 255/-1 = missing. Recent data is 254, so the archive mixes
  approved history with provisional recent values. Keep the code on every row and decide at training time.
- **Licence and terms:** CC BY 4.0. The suggested attribution names `waterlevel.ie/hydro-data/` and the OPW
  disclaimer. The disclaimer is a terms-of-use waiver (data is provisional, no warranty; OPW may revise or
  remove data at any time). Neither the disclaimer nor the licence page says anything against automated
  access, and the API page invites scripted use ("JSON feeds are best for API access or scripting"). There
  is also an in-app data-request form (it asks the requester to accept the Terms of Use), but nothing
  requires using it for these public files.
- **Implication (not yet implemented):** This replaces scraping as the training-history source for 29
  stations and adds discharge for 14. Plan for Phase 1: a one-off, polite, sequential pull of the S, Q and
  Precip ZIPs for our stations into `raw/opw_hydrodata/`. That is roughly 200–300 MB zipped and costs cents in
  S3. Then refresh monthly, because OPW revises history as data is checked (254 → 31). Keep the real-time CSV
  ingest for the 8 stations Hydro-Data lacks and for the `0001` series. Rainfall ZIPs come from separate OPW
  rain gauges (e.g. 80101), so those near each catchment still need identifying.
