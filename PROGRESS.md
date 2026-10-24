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
