# river-forecast

River level forecasts for the **Dodder** (Dublin) and the **Suir** (Tipperary/Waterford), built from OPW
real-time water levels and Met Éireann weather forecasts.

> **This is not an official flood warning service.** It is an experimental, unofficial project. For flood
> warnings and emergency information see [floodinfo.ie](https://www.floodinfo.ie/), your local authority, and
> [Met Éireann warnings](https://www.met.ie/warnings). Never rely on this project for safety decisions.

## Status

Phases 0 (ingestion), 1 (history, curated Parquet, analysis) and 2 (Clonmel baselines and LightGBM) are **done**. Hourly and daily jobs archive raw data for both catchments to S3. See
[PROGRESS.md](PROGRESS.md) for numbers and [DECISIONS.md](DECISIONS.md) for why things are the way they are.

## Data sources and attribution

- **OPW / waterlevel.ie:** water levels. Contains Irish Public Sector Information licensed under a
  [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)
  licence (source http://waterlevel.ie, provided by the Office of Public Works). OPW's disclaimer applies: the
  data is provisional and unvalidated. We only use the pre-generated files OPW lists for automated access
  (`/geojson/latest/` and `/data/{day,week,month}/*.csv`).
- **Met Éireann:** point forecasts (HARMONIE-AROME + ECMWF via the `locationforecast` API), weather warnings,
  and station observations. Source: Met Éireann, licensed under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Met Éireann does not endorse this project.
  The observations endpoint (`prodapi.metweb.ie`) is not a documented open-data product. We use it on a
  best-effort basis and validate it strictly (see DECISIONS.md, D7).

When forecasts are published, current Met Éireann warnings will be shown alongside them.

## Architecture

```
EventBridge rules ──> Lambda river-forecast-ingest (Python 3.12, arm64, stdlib + boto3)
                          │ fetch → parse → validate
                          ▼
             S3 river-forecast-data-<account>
               raw/          gzipped payloads exactly as fetched
               validation/   one JSON report per payload
               quarantine/   payloads that failed validation fatally
```

| Job            | Schedule (UTC)    | What                                                        |
|----------------|-------------------|-------------------------------------------------------------|
| `opw_latest`   | hourly at :05     | national latest-readings GeoJSON (~2000 series), safety net |
| `opw_csv`      | daily 02:30       | 7-day CSV per Dodder/Suir station, sensors `0001` and `OD`  |
| `met_forecast` | hourly at :15     | 11 catchment points, stored once per model issue time       |
| `met_warnings` | hourly at :25     | national warnings JSON                                      |
| `met_obs`      | every 3 h at :50  | 7 synoptic stations, today's hourly observations            |

S3 key layout (Hive-style, ready for Athena/DuckDB):

```
raw/opw_csv/period={week|month}/date=YYYY-MM-DD/station=NNNNN/sensor=XXXX.csv.gz
raw/opw_latest/date=YYYY-MM-DD/fetched=HHMMSSZ.geojson.gz
raw/met_forecast/issue_date=YYYY-MM-DD/issue_time=HHMMZ/point=NAME/ecmwf_run=YYYYMMDDTHHMMZ.xml.gz
raw/met_warnings/date=YYYY-MM-DD/fetched=HHMMSSZ.json.gz
raw/met_obs/date=YYYY-MM-DD/station=SLUG/fetched=HHMMSSZ.json.gz
```

Everything AWS-side is tagged `project=river-forecast`.

Curated Parquet (D13), queryable with DuckDB:

```
curated/opw_hydrodata/snapshot=YYYY-MM-DD/param={S,Q,Precip}/station=NNNNN.parquet
curated/opw_hydrodata/snapshot=YYYY-MM-DD/stations/param=P.parquet
curated/opw_realtime/sensor={0001,OD}/station=NNNNN.parquet
```

Every row has `quality_code` (OPW's code, null for real-time) and `quality_status`
(`checked` / `provisional` / `unreliable` / `unusable` / `missing` / `undocumented`).

```python
import duckdb
duckdb.sql("select * from read_parquet('curated/opw_hydrodata/snapshot=*/param=S/station=16011.parquet') "
           "where quality_status = 'checked' limit 5")
```

Raw Hydro-Data ZIPs live at `raw/opw_hydrodata/snapshot=YYYY-MM-DD/param=P/station=N/<file>.zip` (byte-for-byte).
They are refreshed monthly; older snapshots move to Glacier IR, then Deep Archive (D14).

## Phase 1 results

See [docs/phase1/report.md](docs/phase1/report.md). The main modelling target is the **Suir at Clonmel**
(DECISIONS D16).

## Phase 2 results

See [docs/phase2/report.md](docs/phase2/report.md), the corrected [re-run](docs/phase2/rerun/report.md) and DECISIONS D18-D22. For Clonmel, LightGBM beats persistence clearly
at 6-12 h, modestly at 24 h, and is **not reliably better at 48 h**. Its 90% intervals under-cover in floods, and
nothing here is suitable as a published flood forecast.

## Repo layout

```
src/river_forecast/config.py      stations, forecast points, endpoints
src/river_forecast/ingest/        http, parse, validate, jobs, Lambda handler, storage
tests/                            pytest (fixtures are real payloads captured 2026-10-24)
scripts/upload_month_snapshot.py  one-off upload of a local OPW month-CSV snapshot
infra/bootstrap/                  budget, billing alarm, TF state bucket, GitHub OIDC + deploy role (applied locally)
infra/main/                       data bucket, ingest + transform Lambdas, schedules, alarms (deployed by CI)
src/river_forecast/transform/     raw -> curated Parquet (Lambda, AWS SDK for pandas layer)
analysis/phase1.py                Phase 1 analysis -> docs/phase1/
analysis/phase2.py                Phase 2 models and verification -> docs/phase2/
src/river_forecast/model/         dataset, models (persistence, linear, LightGBM + conformal), metrics
src/river_forecast/features/      rain QC
```

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
```

Invoke a job by hand:

```bash
aws lambda invoke --function-name river-forecast-ingest \
  --cli-binary-format raw-in-base64-out --payload '{"job":"met_warnings"}' out.json
```

## Deployment

- `infra/bootstrap` is applied locally by an admin (it creates the CI role itself). It needs
  `infra/bootstrap/terraform.tfvars` (gitignored) with `alert_email`.
- `infra/main` is applied by GitHub Actions on every push to `main`, after tests and Terraform checks pass.
  It assumes `river-forecast-gha-deploy` via OIDC, so there are no stored AWS keys.

## Tear-down

```bash
terraform -chdir=infra/main destroy      # empty the data bucket first, or it will refuse
terraform -chdir=infra/bootstrap destroy # last: removes budget, alarms, OIDC role, state bucket
```
