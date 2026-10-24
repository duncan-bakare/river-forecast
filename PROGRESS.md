# Progress

## Phase 0: ingestion for both catchments (2026-10-24)

**Done**
- Guardrails first: AWS Budget at USD 11/month (EUR isn't supported, see D3) with 50/80/100% actual and 100%
  forecast alerts, a billing alarm, and a Terraform state bucket. All tagged `project=river-forecast`.
- Snapshotted OPW's full 5-week archive nationally before anything else: 2,030 series, 0 download failures,
  108 MB, covering 2026-09-19 12:00 → 2026-10-24 12:30 UTC. All 2,030 are validated and in S3 under
  `raw/opw_csv/period=month/`.
- Ingest Lambda with five scheduled jobs (OPW week CSVs, national OPW snapshot, Met Éireann point forecasts,
  warnings, observations). Every job was run in AWS by hand, and the schedules were then seen firing on their
  own.
- GitHub repo, CI (pytest, ruff, terraform fmt/validate) and an OIDC deploy to AWS on merge to `main`. A
  scoped deploy role with a permissions boundary, so no stored keys. Branch protection requires `test` and
  `terraform-check`.
- 21 tests covering parsing, validation, the S3 key layout, forecast de-duplication, quarantine, and the
  handler's failure semantics.

**Numbers**
- Dodder (2 stations): 100% of 15-minute values present over the 5 weeks. Anglesea Road ranged 0.36–1.29 m,
  Waldron's Bridge 0.12–0.58 m.
- Suir (35 stations): 31 have ≥99% of values present. Rathkennan has 90.0%, Anner 91.2%, Sandy Banks 93.7%
  and Tar Bridge back-up 93.9%. **Piltown 16125 has 5.7%** (offline since 2026-09-24) and **Hollyford 16201
  has 11.4%**. Clonmel ranged 0.16–1.60 m over the period.
- First daily `opw_csv` run: 68 files, 0 failures, 24 with non-fatal issues (mostly 1–14 blank values).
- Met Éireann forecasts: 11 points per model issue, about 224 time steps each (HARMONIE hourly to +48 h, then
  ECMWF to about +9 days). The hourly poll stores a run only once.
- S3 after day one: ~4.3k objects, ~19 MB. Expected AWS cost: well under USD 1/month.

**What didn't work / surprises**
- The first deploy failed: the OIDC trust policy used `repo:owner/repo:...`, but this repo issues GitHub's
  immutable subject `repo:owner@id/repo@id:...`. Fixed in PR #1.
- The Met Éireann obs endpoint silently returns Dublin Airport for unknown station slugs (D7.1). It is now
  rejected by validation and covered by a test.
- The first snapshot upload quarantined 53 files because of OPW CSV header variants (`backup_value`,
  `main_value`). The parser was fixed, the files reprocessed, and the quarantine copies removed (D7.2).
- Back-up loggers and Hollyford have no OD sensor, so their OD CSVs return 404 (D7.3).

**Owner actions (done 2026-10-24)**
- Both SNS email subscriptions are confirmed (billing alerts in us-east-1, ingest failures in eu-west-1).
- "Receive CloudWatch billing alerts" is on. `AWS/Billing EstimatedCharges` (Currency=USD) is now published,
  so the billing alarm has a metric to evaluate.

**Next (Phase 1 candidates)**
- Daily compaction of raw → Parquet, plus Athena/DuckDB views.
- Evaluate Met Éireann's documented historical hourly station CSVs as the training rainfall source.
- Data freshness alarm (e.g. no new `opw_csv` object in 36 h).

## Phase 1: history, curated data and analysis (2026-10-24)

**Done**
- Pulled the OPW Hydro-Data full-history ZIPs into S3, untouched and dated:
  `raw/opw_hydrodata/snapshot=2026-10-24/`, 134 objects, 301 MB, 0 failures. Refreshes monthly.
- Converted Hydro-Data and the real-time data to Parquet with `quality_code` and a derived `quality_status` on
  every row (D13): 70 Hydro-Data files and 68 real-time files.
- Lifecycle rule for old Hydro-Data snapshots, plus pruning of curated snapshots (D14).
- Phase 1 analysis: `analysis/phase1.py` writes `docs/phase1/report.md` and two figures (D15). Recommendation
  in D16.
- Tests: 34 (Hydro-Data validation and pull, quality mapping, Parquet jobs, pruning and merge precedence).

**Numbers**
- Hydro-Data: S has 33 stations and 42.0 M rows from 1940. Q has 13 stations with data and 27.5 M rows. Precip
  has 24 gauges and 12.0 M rows from 2005. Clonmel level is 97.9% checked, Anglesea Road 93.2%.
- Waldron's Bridge → Anglesea Road: 30-min cross-correlation peak, event lags 30–60 min (median 52, n = 4).
- 14 Nov 2025: in the record, 384/384 rows, all checked. It peaked at 4.422 m OD at 04:45 UTC on **15 Nov**,
  the #2 peak since 2021.
- Suir rain gauges: 97.9% unchecked. After QC the Clonmel runoff coefficient is 0.63 and losses 425 mm/yr
  (2011–2025).

**What didn't work / surprises**
- My first look at the Hydro-Data archive came only when asked. Phase 0 had assumed history was limited to
  35 days.
- A config edit silently failed because Python on Windows read UTF-8 as cp1252. All scripted edits now pass
  an explicit encoding.
- Transform failures: no zstd in the layer's pyarrow, then out of memory at 2 GB. Both fixed (D13). Those
  failed runs will have triggered the transform-errors alarm email.
- Using the *median* of gauges for catchment rain gave runoff coefficients near 1.0 (biased low). The mean
  is correct.
- Waldron's Bridge is not on Hydro-Data, so the lag rests on 5 weeks and 4 events.
- Quality codes 96 and 99 are undocumented, and Rosegreen's post-2019 bias isn't caught by the current QC.

**Next (Phase 2)**
- Rain QC (spike, neighbour-correlation and neighbour-ratio) as a transform step, producing catchment rainfall
  series.
- Baseline models for Clonmel level/flow at 6–48 h (persistence, linear/GBM on upstream levels and rain),
  verified on held-out flood events.
- Archive HARMONIE forecasts long enough to train or evaluate with forecast rain rather than observed rain.

## Phase 2: Clonmel level forecasts (2026-10-24)

**Done**
- Fixed the Rosegreen gap: neighbour-based rain QC (`features/rain_qc`, D17) flags step biases and bad gauge-years.
  Rosegreen 2020–2026 is now caught, with 22 gauge-years flagged in all.
- Measured upstream travel times to Clonmel and chose horizons of 6, 12, 24 and 48 h (D18).
- Models for Clonmel 16011: persistence, an upstream-lag linear model, and LightGBM with 90% conformalised
  quantile intervals (D19). Time-based splits only (train < 2019, calibration 2019–2021, test ≥ 2022). The 8 biggest floods
  since 2006 were found in the data and held out as backtests.
- Metrics: NSE, KGE, MSE skill vs persistence with a block bootstrap, peak timing and size error, exceedance detection
  with lead time, false alarms and CSI, and interval coverage (`model/metrics.py`).
- Report and figures: `docs/phase2/report.md` (from `analysis/phase2.py`). Tests: 56 in all (10 metrics, 6 models,
  6 rain QC, plus the earlier suites).

**Numbers** (full table in D20)
- Checked test set 2022–2025, LightGBM skill vs persistence: **0.85 / 0.61 / 0.41 / 0.19** at 6 / 12 / 24 / 48 h.
  NSE 0.997 / 0.981 / 0.932 / 0.822 (persistence 0.982 / 0.952 / 0.886 / 0.780).
- Flood backtests: skill 0.85 / 0.63 / 0.34 / 0.08 (the 48 h interval spans zero).
- Exceedance of 16.702 m: LightGBM catches 16 of 16 onsets at 6 h, 9 at 12 h, 3 at 24 h and 0 at 48 h. Persistence
  catches 2, 1, 0 and 0.
- 90% intervals cover 0.86–0.87 on the test set but only 0.61–0.79 on the flood backtests.
- With *perfect* future rainfall, skill at 24 and 48 h would be 0.79 and 0.82. Nearly all of the missing long-lead
  skill is rainfall forecast, not the gauge network.

**What didn't work / needs saying**
- Beyond 12 h the model is close to persistence: at 48 h it is not distinguishable from persistence on floods, and
  worse than persistence on 2026 provisional data (skill −0.09, KGE also lower).
- The upstream-linear baseline silently fell back to persistence for 32% of hours, and entirely in 4 of the 8
  flood events. Its numbers understate what a working linear baseline would do (D21.1).
- Intervals under-cover in floods. Exceedance onset counts have a small splice artifact. Five of eight backtests
  are held-out events rather than forward-in-time forecasts (D19, D21).
- The provisional-data test contains no flood (2026 provisional maximum 15.85 m).
- Trained models were not saved, so re-running takes about 14 minutes (D21.4).
- Beakstown and Thurles gave non-physical (negative) travel times (D18).
- Not suitable for publishing as a flood forecast.

**Next (Phase 3 candidates)**
- Fix the linear baseline and the onset search, persist models and calibration counts, and re-run.
- Calibrate the high-flow intervals properly (blocked cross-validation that keeps the floods).
- Evaluate with HARMONIE rain once enough forecast history exists, and keep archiving it.
- Dodder as a secondary rainfall-driven nowcast, once there is a winter of Waldron's Bridge data.

## Phase 2 corrections (2026-10-24)

**Done** (D22; `analysis/phase2_rerun.py`, `analysis/phase2_intervals.py`, `docs/phase2/rerun/`)
- Linear baseline: no silent fallback. Sub-models per available station subset, NaN only if Clonmel's own data are missing.
- Onset counting: continuous series, previous hour observed and below, then filtered to the scored period. Persistence now catches 0 onsets.
- Re-ran the flood comparisons on identical rows for all models. Saved models and predictions (outside the repo).
- Flood intervals: event-fold conformal recalibration, plus a rise-aware variant (rejected: no extra gain). Tests: 63 (was 56).

**Numbers** (floods, skill vs persistence; linear / LightGBM): 6 h 0.35 / 0.85, 12 h 0.46 / 0.63, 24 h 0.32 / 0.34, 48 h 0.155 / 0.083.
- LightGBM's margin over the corrected linear baseline is real at 6–12 h, nil at 24 h in floods, and reversed at 48 h.
- Peak timing: LightGBM best at 6–12 h, linear best at 24–48 h. Onsets caught of 16 at ≥ 24 h: linear 4, LightGBM 2, none at 48 h. The
  linear model catches more but has about 4× the false alarms.
- Out-of-fold flood interval coverage rose from 0.79/0.69/0.63/0.61 to 0.81/0.72/0.71/0.65 against a nominal 0.90.

**What didn't work:** recalibration only partly helps. Flood coverage stays at 65–81%, because errors are heavy-tailed on rising limbs and in the
lead-up to floods, so it is a documented limitation. The rise-aware regime gave no further gain. Only 4 events per fold support these coverage numbers.
LightGBM's results are unchanged and reproduced exactly. The v1 exceedance counts for LightGBM were slightly inflated by the onset artifact.

## Live forecast and frontend (2026-10-24)

**Done** (D23–D26)
- Live pipeline: an hourly Lambda reads OPW levels and rain, applies the saved models (numpy tree evaluator, verified against
  LightGBM), and writes `forecast.json` with an hourly archive for later verification. It hides itself when inputs are old or missing.
- Static site on S3 + CloudFront: live forecast with intervals, verification page (the corrected Phase 2 table against persistence and
  the linear baseline, generated from the result CSVs), and method and limitations. Met Éireann warnings shown. OPW and Met Éireann
  attribution, "not an official flood warning" and UTC/Irish-time labels on every page.
- README rewritten in first person with architecture, trade-offs, results and limitations.
- Tests: 94 Python and 21 JavaScript (time zones including the 25 Oct clock change, status rules, chart maths, CSP and attribution
  checks on the static pages).

**What didn't work / found along the way**
- No design brief exists in CLAUDE.md (D25). The styling is a neutral default in CSS variables.
- LightGBM can't run in the Lambda (needs scipy); wrote a tree evaluator. Its first version silently skipped trees until a parity test
  caught it.
- `latest_observation` mixed two readings; the `.gitignore` swallowed `site/data/`; the verification page clipped, mislabelled
  and was unreadable on a phone. All found by rendering the pages, and fixed.
- Rain arrives 2–3 h after levels, so forecasts are issued from an older hour than the latest level.
- The rain-gauge flags in `live_config.json` are a snapshot and need regenerating monthly (manual).

**Next**
- Decide D26 (public or not). Apply a design brief if one exists. Keep archiving; compare live forecasts with outcomes.
- Try archived Met Éireann HARMONIE rain as a model input once enough history exists.
