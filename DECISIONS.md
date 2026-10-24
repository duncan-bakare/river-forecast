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
- **Phase 1** (defined 2026-10-24): full-history acquisition (Hydro-Data), curated Parquet with quality
  tagging, and a data analysis that decides the modelling target. This covers coverage, the Dodder
  upstream lag, a Suir rainfall source assessment and a check of the 14 Nov 2025 event. Phase 2 is
  baseline models.

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

## D12. Hydro-Data snapshot pulled (2026-10-24)
- **What:** Job `opw_hydrodata`. Per-station `*_complete.zip` files are stored byte-for-byte at
  `raw/opw_hydrodata/snapshot=2026-10-24/param={S,Q,Precip}/station=NNNNN/<file>.zip`, with layer and station
  `index.json` metadata alongside and a streaming validation report per ZIP. It refreshes monthly (1st of
  the month, 04:00–04:40 UTC), one EventBridge rule per parameter.
- **Selection:** S and Q cover every hydrometric-area-16 station in the Hydro-Data layer (including inactive
  historical ones) plus 09369. Precip covers every OPW rain gauge within 15 km of a forecast point.
- **Result:** 134 objects, 301 MB, 0 failures, 0 quarantined.
  - S: 33 stations, 42.0 M rows, 1940-01-14 → 2026-10-23. Quality codes: 88.0% code 31, 3.2% −1, 2.6% 32,
    2.4% 254, 2.3% 101.
  - Q: 17 stations, 13 with data, 27.5 M rows. Header-only for 16156, 16157, 16158 and 16159, even though they
    appear in the flow layer.
  - Precip: 24 gauges, 12.0 M rows, 2005-08-09 →. Quality codes: 97.8% code 254 (unchecked).
- **Undocumented quality codes:** 99 (S 0.8%, Precip 1.8%) and 96 (Q 4.2%) are not on OPW's quality-codes
  page. They are kept as-is and not interpreted.
- **Not pulled:** water temperature (`TWater`). It isn't useful for level forecasting and is cheap to add later.

## D13. Curated Parquet with quality code and quality status on every row
- **What:** Lambda `river-forecast-transform` (AWS SDK for pandas layer v31 for pyarrow/numpy) writes:
  - `curated/opw_hydrodata/snapshot=D/param={S,Q,Precip}/station=N.parquet` and
    `.../stations/param=P.parquet` (monthly, after each Hydro-Data pull)
  - `curated/opw_realtime/sensor={0001,OD}/station=N.parquet` (daily; all raw week and month CSVs merged,
    with newer files winning on duplicate timestamps)
- **Columns:** `station_no, param, timestamp (UTC), value, quality_code, quality_status, aggregation_accuracy,
  unit, source`. `quality_code` is OPW's code, kept as-is (null for the real-time feed, which has none).
  `quality_status` is derived: `checked` (31, 32, 36, 41, 42, 46, 56), `provisional` (254 and all real-time
  data), `unreliable` (101), `unusable` (151), `missing` (255, −1 or an empty value) and `undocumented`
  (e.g. 96, 99). Codes 41/42/46/56 are "inspected but poor/extrapolated" and count as checked, so filter on
  `quality_code` for stricter use.
- **Why:** Training wants checked history. Inference has only provisional real-time data. Both need to be
  visible per row, not per file.
- **Query engine:** DuckDB over the Parquet (locally, or later in a job), not Athena: no extra infra and no
  per-query cost. Athena can be added over the same layout if needed.
- **What went wrong on the way:** (1) The layer's pyarrow is built without zstd, so we use snappy.
  (2) The first version ran out of memory at 2 GB, from per-row Python strings and decoding whole 89 MB
  CSVs. It was rewritten with int8 dictionary columns and a streamed header: Clonmel's 2.56 M rows now take
  1.5 s and 139 MB of Arrow memory. The full snapshot (70 files) converts in 45 s.
- **Result (2026-10-24):** 70 Hydro-Data files (33 S, 13 Q, 24 Precip; empty series skipped), 68 real-time
  files, 141 objects, about 296 MB. Each curated Hydro-Data file is about the size of its source ZIP, thanks
  to delta-encoded timestamps and byte-stream-split floats.

## D14. Lifecycle for old Hydro-Data snapshots
- **Raw** (`raw/opw_hydrodata/`, objects > 128 KB, i.e. the ZIPs): Glacier Instant Retrieval at 35 days
  (once the next monthly snapshot exists), Deep Archive at 180 days, and expiry after 5 years. Old snapshots
  are only needed to audit OPW's revisions, so slow and cheap retrieval is fine. At about 300 MB a month this
  stays at cents a year.
- **Curated:** The transform job keeps the newest 2 curated snapshots and deletes older ones. Curated data can
  be rebuilt from raw. Deleting by job rather than by age-based lifecycle means a failed refresh can never
  leave us with zero curated snapshots.
- **Alternatives:** Expire raw snapshots after N months (loses revision history). Keep everything in Standard
  (about 4x the cost, still small).

## D15. Phase 1 findings (details and figures in `docs/phase1/report.md`, from `analysis/phase1.py`)
- **Waldron's Bridge → Anglesea Road lag: 30–60 min.** Cross-correlation of 15-min changes peaks at 30 min
  (r = 0.75). Four events with a rise of at least 0.08 m had peak-to-peak lags of 45, 60, 30 and 60 min
  (median 52). Caveat: only 5 weeks of autumn real-time data, because Waldron's Bridge has no history on
  Hydro-Data. Re-run it as the archive grows.
- **14 Nov 2025 at Anglesea Road: present and complete.** All 384 15-min rows for 13–16 Nov, no gaps, every row
  code 31 (checked). The level rose through 14 Nov (2.717 → 3.702 m OD) but **peaked at 4.422 m OD at
  2025-11-15 04:45 UTC**, after midnight. Anyone labelling this "the 14 Nov event" should know the peak is on
  the 15th. It is the 2nd-highest independent peak since the record began (2021-10-06), behind 27 Jan 2026
  (4.819 m OD).
- **Suir rainfall (OPW gauges): usable as the primary observed rainfall, after our own QC.** There are 24
  gauges at 15-min resolution from 2005/2010, and 97.9% of values are code 254 (unchecked). Problems: impossible
  spikes (Knockmealdown 81603 1,558 mm and Garryduff 81606 1,850 mm in a single 15-min step), bad periods (13
  gauge-years with daily r < 0.6 against neighbours), and persistent bias (Rosegreen 81615 reads about 2x its
  neighbours since 2019). After dropping values > 50 mm/15 min and the bad gauge-years, the gauge-*mean*
  rainfall closes the Clonmel water balance. The runoff coefficient is 0.56–0.74 (mean 0.63) and losses are
  about 425 mm/yr over 14 years, consistent with Irish evapotranspiration. Flow at Clonmel starts rising about
  5 h after catchment rain, and 24-h rain correlates best with flow about 15 h later.
- **Dodder rainfall:** there are no OPW gauges within 15 km. Only Met Éireann synoptic obs (Casement,
  Phoenix Park, Dublin Airport) are available, and the current endpoint for those is undocumented (D7.1).

## D16. Recommendation: the Suir, at Clonmel (16011), is the main modelling target
- **Data:** Clonmel has continuous level from 1953 (97.9% checked) and discharge from 1972 (96.5% checked).
  Upstream it has 31 more Suir level stations and 12 more flow stations with decades of history, plus 24
  co-located 15-min rain gauges whose QC'd mean closes the water balance. The Dodder has one archived gauge
  (Anglesea Road, 5 years, level only, no rating), an upstream gauge with 5 weeks of data, and no
  catchment rain gauges.
- **Hydrology:** The Suir responds over hours to a day (rise ~5 h after rain, flow best correlated with 24-h
  rain ~15 h later), so observed upstream levels and rain carry real predictive lead, and the 48 h of HARMONIE
  extends it. The Dodder's upstream lead is 30–60 min, so a Dodder forecast is essentially a rainfall-forecast
  problem with little observed data to learn from.
- **Relevance:** Clonmel is a flood-defended town with OPW FFWS (Flood Forecasting and Warning System) gauges,
  and has decades of flood events to train and verify on.
- **Plan:** Model the Suir first, with Clonmel as the target and Caher Park, New Bridge, Killardry, Anner and
  Tar as upstream inputs. Keep the Dodder as a secondary, rainfall-driven nowcast. It stays in ingestion so
  the Waldron's Bridge record accumulates, and is revisited once it has a winter of data.
- **Alternatives considered:** Dodder-first (more urgent urban flood risk, but too little data to train and
  verify on). Both equally (splits effort; the Dodder's data won't support it yet).

## D17. Rain-gauge QC by neighbour comparison (fixes the Rosegreen gap)
- **What:** `river_forecast.features.rain_qc`. Spikes > 50 mm/15 min are dropped. Each gauge-year is then
  compared with the median of its 4 nearest gauges, and flagged if it shows `low_corr` (daily r < 0.6),
  `step_bias` (a step of at least 1.6x in the gauge/neighbour ratio, lasting at least 3 years on each side;
  the side further from 1.0 is flagged) or `ratio_outlier` (a single year more than 1.5x off the gauge's
  own typical ratio). The check iterates, removing flagged gauge-years from the neighbour reference and
  re-testing, so one bad gauge doesn't get its neighbours flagged. Catchment rain is the *mean* of
  unflagged gauges, requiring at least 5 reporting.
- **Why:** The D15 correlation filter missed Rosegreen 81615, which reads about 1.07x its neighbours until
  2018 and about 2.1x from 2019 while still correlating well. A fixed ratio threshold would wrongly reject
  upland gauges that are consistently wet (Knockmealdown about 1.5x, Rossadrehid about 1.5x). Checking for
  *changes* relative to each gauge's own level separates a fault from a climate difference.
- **Result:** 22 gauge-years flagged (12 low_corr, 7 step_bias, 3 ratio_outlier). That includes Rosegreen
  2019 (low_corr) and 2020–2026 (step_bias), and Cappaghnagarrane 2012, 2022, 2024 and 2025. Without
  iteration, 3 extra 2019 flags appeared on northern gauges, caused by bad neighbours. Several gauges fail
  together in 2019, so that year may have had a network-wide problem. With the fix, the Clonmel water-balance
  losses for 2022–2025 fall from 469–543 to 440–502 mm/yr (mean coefficient 0.64, loss 416 mm/yr).
  The upward drift that Rosegreen's bias caused is mostly gone.
