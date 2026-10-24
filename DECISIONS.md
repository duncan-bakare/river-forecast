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

## D18. Phase 2 forecast horizons: 6, 12, 24 and 48 h, from measured travel times
- **Measurement:** For the 40 largest independent Clonmel peaks since 2006 (hourly levels, checked plus provisional),
  the time from each upstream station's maximum in the window peak −72 h to peak +12 h to Clonmel's peak. Median
  hours (p25–p75): Knocklofty 16139 **4** (3–5), Newcastle 16137 **7** (−1 to 8), Caher Park 16009 **9** (0–13),
  Athlummon 16001 9 (0–28), Anner 16010 12, New Bridge 16008 **13** (−2 to 23), Killardry 16007 **15** (8–21),
  Tar Bridge 16012 **17** (9–23), Rathkennan 16003 17.5 (2–27), Clobanna 16051 23, Ballinaclogh 16006 23.5,
  Aughnagross 16005 24, Fourmilewater 16013 **27** (21–34). Phase 1 also gave about 5 h from catchment rain to the rise
  at Clonmel, and 24-h rain correlated best with flow about 15 h later.
- **Choice:** 6 h (main-stem gauges plus the rain-to-rise lag), 12 h (Caher Park and New Bridge), 24 h (the longest
  lead the gauge network can supply: Aherlow, Tar, Clodiagh, Multeen and Nire at 15–27 h), and 48 h, which is
  **deliberately beyond every travel time**, to show where the gauge network stops helping. 48 h also matches
  HARMONIE's range.
- **Caveats:** The spreads are wide (beyond Knocklofty, interquartile ranges are mostly 12–28 h), so the horizons are indicative.
  Beakstown 16002 and Thurles 16004 gave *negative* lags, i.e. a maximum after Clonmel's peak, which is not
  physical for one flood wave. That is probably a different peak inside the 84-hour window. Not investigated.
  Anner joins the Suir downstream of Clonmel, so it is not a model input. The travel-time table came from exploratory
  queries and is **not scripted in the repo**. This entry is its record.

## D19. Phase 2 design (code: `river_forecast.model`, `analysis/phase2.py`; report: `docs/phase2/report.md`)
- **Target:** The change in Clonmel level, y(t+h) − y(t), so trees never have to extrapolate absolute levels (the
  record's highest levels sit above anything else in training).
- **Models:** (1) persistence. (2) an upstream-lag linear model (ridge OLS on Clonmel's and 8 upstream stations'
  6/12/24 h level changes, no rain), which falls back to persistence if any input is missing. (3) LightGBM on
  all upstream levels and changes, QC'd catchment rain (D17) summed over 1 h–30 d, and season. Intervals are 90%
  conformalised quantile regression: 5% and 95% quantile models, calibrated on 2019–2021, with separate
  corrections below and above 15.74 m. Rain is observed only up to issue time. There is no rainfall forecast,
  because none is archived yet. A separate LightGBM fed *observed future* rain is reported as an upper bound for
  perfect rain forecasts. It is not a deployable result.
- **Splits (time only):** train < 2019, calibration 2019–2021 (early stopping and conformal), test ≥ 2022 (checked
  labels to the end of 2025, provisional labels in 2026).
- **Flood backtests:** The 8 largest independent peaks since 2006 (found from the data, windows −5 d/+3 d) are cut out of
  training and calibration: 2015-12-30 (18.257 m), 2009-11-20, 2009-01-31, 2014-02-06, 2021-02-24, 2019-11-22,
  2022-11-02 and 2008-01-11. The 2000 and 2004 floods (2nd and 3rd on record) pre-date the rain gauges.
  **Caveat:** only 2022-11 is later than the training data, with two events in calibration and five in the
  training period. So five of eight backtests are held-out *events* but not forward-in-time forecasts, because
  the model has seen later winters. Treat them as optimistic relative to genuine real-time use.
- **Checked vs provisional:** Training and calibration labels are **checked hours only**. Inputs go through the same
  preprocessing whatever their status (hourly means, spike removal above 1.0 m/h, rain QC), so serving data
  sees the pipeline the model trained on. Provisional-label hours are scored separately. Clonmel's provisional data
  is almost entirely 2026 and **has no flood in it** (maximum 15.85 m, below the 16.70 m threshold), so that
  set tests label noise at low and medium flows, not flood skill.
- **Thresholds:** OPW's statistical levels for Clonmel (`alarmlevel.json`): 1%-exceedance (16.702 m) and median annual
  maximum (16.914 m). They are **not flood-warning levels**.
- **Known biases in the set-up:** (a) Hourly means are labelled at the start of the hour, so a "6 h" forecast is
  really about 5.5 h from the last data. All leads are overstated by roughly half an hour. (b) The conformal
  calibration set excludes the biggest floods, which probably makes the high-flow correction too small (not
  verified; the calibration counts per regime are not saved).

## D20. Phase 2 results: where the models do and do not beat persistence
(MSE skill vs persistence; 0 = no better. NSE and KGE are of the forecast level. Full tables: `docs/phase2/report.md`.)

| set | h | NSE pers / lin / LGBM | KGE pers / lin / LGBM | skill lin / LGBM [LGBM 5–95%] |
|---|---|---|---|---|
| test, checked | 6 | 0.982 / 0.991 / 0.997 | 0.991 / 0.996 / 0.998 | 0.52 / 0.85 [0.83, 0.87] |
| test, checked | 12 | 0.952 / 0.968 / 0.981 | 0.976 / 0.984 / 0.986 | 0.34 / 0.61 [0.58, 0.66] |
| test, checked | 24 | 0.886 / 0.907 / 0.932 | 0.943 / 0.953 / 0.944 | 0.18 / 0.41 [0.38, 0.44] |
| test, checked | 48 | 0.780 / 0.794 / 0.822 | 0.890 / 0.896 / **0.862** | 0.06 / 0.19 [0.15, 0.23] |
| floods (8) | 6 | 0.947 / 0.962 / 0.992 | 0.972 / 0.977 / 0.996 | 0.28 / 0.85 [0.82, 0.88] |
| floods (8) | 12 | 0.828 / 0.894 / 0.936 | 0.915 / 0.948 / 0.969 | 0.38 / 0.63 [0.59, 0.68] |
| floods (8) | 24 | 0.519 / 0.673 / 0.684 | 0.775 / 0.847 / 0.857 | 0.32 / 0.34 [0.23, 0.46] |
| floods (8) | 48 | −0.163 / −0.017 / −0.067 | 0.507 / 0.559 / 0.555 | 0.13 / **0.08 [−0.05, 0.21]** |
| provisional 2026 | 24 | 0.679 / 0.716 / 0.731 | 0.783 / 0.810 / 0.813 | 0.11 / **0.16 [−0.09, 0.24]** |
| provisional 2026 | 48 | 0.620 / 0.618 / 0.587 | 0.751 / 0.734 / 0.721 | **−0.01 / −0.09** |

- **Plainly: no better than persistence in these cases.** At 48 h, LightGBM is not distinguishable from
  persistence on the flood backtests (interval spans 0) and is **worse** than persistence on provisional 2026
  data, and its KGE is lower than persistence's on the checked test set too. At 24 h, its skill is not
  distinguishable from zero on provisional data. The linear model is at or below persistence at 48 h on provisional
  data. The gain from 24 h onward is small and fragile.
- **Why:** The perfect-rain bound reaches skill 0.79–0.82 at 24–48 h on the checked test set (LightGBM actually
  scored 0.41 and 0.19), so nearly all the missing skill at long leads is **future rainfall**, which the gauge
  network can't provide. Only 6–12 h are gauge-limited. This points to the HARMONIE archive as the next lever.
- **Peak timing and size (8 floods, median absolute error).** Persistence's peak is the observed peak shifted by h, by
  construction: timing = h, size error 0. It is therefore *not* beaten on size. LightGBM's median timing error is
  3.5, 8, 15 and 43.5 h at 6, 12, 24 and 48 h, with median size errors of 0.03, 0.08, 0.11 and 0.25 m. It
  anticipates the rise by hours at 6–24 h, but at 48 h its timing is within 5 h of persistence's, and it
  underestimates the peak at 48 h in all 8 events (by 0.09–0.46 m). The figures show it still lags sharp
  rising limbs at 12–24 h (e.g. the first 2015 rise).
- **Exceedance detection** (onsets caught at lead ≥ h, ±3 h tolerance; test period plus backtests):
  - 16.702 m (16 onsets): persistence 2/1/0/0 at 6/12/24/48 h, linear 13/8/2/0, LightGBM **16/9/3/0**. Median
    lead 0 / 9 / 12 h. False-alarm episodes at 12 h in the test period: 0 / 8 / 3.
  - 16.914 m (12 onsets): persistence 1/0/0/0, linear 8/8/0/0, LightGBM 10/5/0/0. The linear model catches more at
    12 h here (8 vs 5), and with 12 onsets the difference is small.
  - **No model detects any onset 48 h ahead, and only 3 of 16 (LightGBM) 24 h ahead.** Detection leads of
    useful size (≥ 12 h) are rare.
- **Prediction intervals (nominal 90%):** coverage is 0.86–0.87 on the checked test set (slightly low), but **falls where it
  matters**: 0.61–0.79 on the flood backtests (0.61 at 48 h), 0.70–0.86 for levels above 15.74 m, and 0.61 and
  0.77 at 6 and 12 h on provisional data. Mean widths are 0.04, 0.13, 0.32 and 0.52 m on the test set, but the intervals
  can't be trusted in floods. In the 2015 figure, the observed rises on 29–30 Dec sit above the 24 h interval.
- **Net:** a usable, well-calibrated-on-average 6–12 h model for Clonmel, with real gains in floods at those leads. Beyond
  24 h it is essentially persistence plus rainfall we don't yet have. **This is not suitable for publication as a
  flood forecast** (see also the "not an official flood warning" statement in the README).

## D21. Problems found on review of the Phase 2 run, and what to do next
1. **The upstream-linear baseline is handicapped.** It requires all 8 upstream inputs at all lags and falls back to
   persistence otherwise. That happens in 32% of all hours, and in **4 of 8 backtest events (2014, 2015, 2019,
   2022) it produced exactly persistence** (timing = h, size 0.000, visible in the figures). So its
   skill, and LightGBM's margin over it, are mismeasured. Fix: fit on a robust subset or impute missing
   stations, then re-run. This is the first thing to do before quoting linear-vs-LightGBM comparisons.
2. **Intervals under-cover in floods** (D20). Calibrate the high-flow regime on more data (include flood
   periods via blocked cross-validation instead of cutting them out), then recheck.
3. **Exceedance onsets are slightly contaminated.** Persistence "catches" 2 of 16 onsets at 6 h, which is impossible for a
   true onset. The onset search runs on a series with non-scored periods dropped, so a splice can create a spurious
   onset. Counts are likely off by one or two. Fix and re-run. Also, the ±3 h tolerance means a nominal 6 h
   lead is at least 3 h in practice.
4. **Trained models are not persisted.** Only the report, figures and CSVs are saved (`docs/phase2/`), and the
   hourly frame is cached locally. Reproducing takes about 14 minutes. The next run should save models and
   calibration counts.
5. **No rainfall forecasts yet.** The HARMONIE archive began on 2026-10-24, so it holds only days of forecasts, far
   too few to train on. Options for Phase 3: keep archiving and evaluate in a few months; meanwhile use Met
   Éireann's documented historical data if a forecast reanalysis can be found; or run with observed rain as a
   proxy and say so.
6. **The provisional test is weak** (no floods, 4 months). Revisit once OPW has checked more recent data
   and once the next winter's floods exist.
