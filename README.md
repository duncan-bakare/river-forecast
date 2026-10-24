# river-forecast

An experimental forecast of the River Suir level at Clonmel, built from open data from the Office of Public Works (OPW) and Met Éireann. I also ingest the Dodder in Dublin, but I haven't built a model for it yet.

> **This is not an official flood warning, and not a flood forecasting service.** It is a personal research project. It can be wrong, late or switched off. Please don't use it to decide anything about safety. Official sources: [floodinfo.ie](https://www.floodinfo.ie/), [met.ie/warnings](https://www.met.ie/warnings), your local authority, and 112/999 in an emergency.

**Live site:** https://d3j8fjouvb0hxp.cloudfront.net (not listed in search engines). It has three pages: the live forecast, how accurate it has been, and the method with its limits.

## What it does

Every hour it reads the latest levels at Clonmel and 14 gauges upstream, plus hourly rain from 24 OPW rain gauges, and forecasts the Clonmel level 6, 12, 24 and 48 hours ahead with a 90% interval. It always shows two simple alternatives next to the model: *persistence* (the level stays where it is) and a *linear baseline*. If its inputs are missing or old, it shows nothing rather than something stale.

## How good is it?

Honestly: useful for the next 6 to 12 hours, and not much more than that.

Skill is how much smaller the squared error is than persistence's (0 = no better). "Floods" are the eight biggest since 2006, held out of training. Full tables are on the site and in [docs/phase2/rerun/report.md](docs/phase2/rerun/report.md).

| Lead | Skill, everyday (linear / LightGBM) | Skill, floods (linear / LightGBM) | Median peak-timing error, floods (persistence / LightGBM) | 90% interval covers, floods |
|---|---|---|---|---|
| 6 h | 0.63 / 0.85 | 0.35 / 0.85 | 6 h / 3.5 h | 81% |
| 12 h | 0.41 / 0.61 | 0.46 / 0.63 | 12 h / 8 h | 72% |
| 24 h | 0.22 / 0.41 | 0.32 / 0.34 | 24 h / 15 h | 71% |
| 48 h | 0.075 / 0.19 | 0.155 / **0.083** | 48 h / 43.5 h | 65% |

What I'd want you to take from that:

- **At 6 to 12 hours the model clearly beats both baselines.**
- **At 24 hours its edge over the linear baseline is gone in floods** (0.34 against 0.32). **At 48 hours it isn't reliably better than persistence in floods** (its 5–95% range includes zero), and it's worse than persistence on this year's unchecked data. The reason is that it has no rainfall forecast. Given perfect future rain, its skill at 24 and 48 hours would be around 0.8, so the missing piece is the weather forecast, not the river.
- **The "90%" intervals are too narrow in floods.** They contain the truth 86 to 88% of the time in ordinary conditions, but only 65 to 81% in the big floods, even after I recalibrated them on those floods.
- **Nothing gives usable warning beyond about 12 hours.** At 24 hours the linear model catches 4 of 16 crossings of the 16.7 m level (LightGBM 2), and at 48 hours none do.
- I only have eight big floods, and five of them are earlier than the training cut-off, so these results are probably optimistic. I haven't tested on genuinely new data yet. The live forecast has been archived since 24 October 2026 so that I can.

## Limitations

1. No rainfall forecast (see above). Met Éireann's forecasts are being archived so I can try them later.
2. Intervals under-cover in floods.
3. Few floods to learn from and to test on.
4. The live inputs are provisional, and the rain gauge data are almost all unchecked by OPW. I screen the rain gauges myself, which catches some faults but not all.
5. It forecasts one gauge. It knows nothing about flood defences, river works, tides or blockages.
6. The forecast is only as fresh as OPW's hourly rain, which arrives 2 to 3 hours late, so "+6 h" is really about 3 to 4 hours from the moment you look.
7. It runs unattended on a small AWS setup and can stop without notice.
8. The dashed levels on the chart (16.70 m and 16.91 m) are OPW *statistical* levels for the gauge (exceeded 1% of the time, and the median annual maximum). They are not flood-warning levels.

## Architecture

```
OPW waterlevel.ie ──┐                                     ┌── site/ (S3 + CloudFront, static)
OPW Hydro-Data ─────┼─> EventBridge ─> Lambda (ingest) ─> S3 raw ─> Lambda (transform) ─> S3 curated (Parquet)
Met Éireann ────────┘   (hourly/daily)                    │ validation/, quarantine/            │
                                                          │                                     └─> analysis/ (DuckDB, LightGBM, local)
                        EventBridge (hourly, :25) ─> Lambda (live forecast) ─> site/data/forecast.json (+ archive in S3)
```

- **Ingest** (`river-forecast-ingest`): five scheduled jobs, plus a monthly pull of OPW's full histories. Everything is stored exactly as fetched (gzipped, or untouched for ZIPs) with a validation report next to it. A payload that fails validation goes to `quarantine/` rather than being dropped or silently used.
- **History:** OPW's real-time feed only keeps about 5 weeks, so on day one I snapshotted all of it. Later I found that OPW's Hydro-Data publishes full histories (Clonmel's goes back to 1953), so I pull those monthly as dated snapshots, untouched.
- **Curated Parquet:** every row keeps OPW's quality code and gets a derived status (checked, provisional, unreliable, unusable, missing). Training uses checked data only. I query it with DuckDB.
- **Models:** persistence, a linear model, and LightGBM with conformalised intervals, trained locally. The live Lambda evaluates the saved trees with a small numpy evaluator of my own, tested to match LightGBM, because LightGBM itself needs scipy, which won't fit in a Lambda layer next to pandas.
- **Live forecast:** `forecast.json` (with the inputs used, its status, and the track record of each horizon) is written to the site bucket every hour and archived. The page hides the numbers if the file is older than 3 hours.
- **Frontend:** plain HTML, CSS and JavaScript with no build step. The chart is hand-written SVG. Every page has the "not an official warning" notice and the data attribution in static HTML, so they show even with JavaScript off. Times are in UTC with Irish time (GMT/IST) alongside.
- **Infrastructure:** all Terraform, all tagged `project=river-forecast`. GitHub Actions deploys through an OIDC role that can only be assumed from `main` and can only touch this project's resources. There are no stored AWS keys. A EUR 10 budget (set as USD 11, the only currency the account accepts) alerts at 50, 80 and 100%. I haven't yet checked the real spend; I expect it to be a few cents a month.

## Trade-offs I made

| Choice | Why | What I gave up |
|---|---|---|
| Lambda + EventBridge + S3, no database or servers | Cheapest by far, nothing to patch | Awkward limits (package size, no scipy) |
| Raw first, parse later, quarantine rather than drop | OPW's data gets revised and I can't re-fetch the past | More storage (still pennies) |
| DuckDB over Parquet, not Athena | No extra infrastructure or per-query cost | Not shareable as a service |
| Hourly mean levels | Matches how the rain is reported and keeps the model small | Misses sub-hourly peaks |
| Predict the *change* in level | Trees can't extrapolate to levels they never saw | Needs the current level as an input |
| Clonmel first, Dodder later | Clonmel has 70+ years of data, many upstream gauges and 24 rain gauges. The Dodder has 5 years, one gauge archived, and only 30 to 60 minutes of upstream warning | No Dodder forecast yet |
| Own tree evaluator instead of the LightGBM package | LightGBM needs scipy, and scipy plus pandas exceeds Lambda's size limit | More code to maintain, but it is tested against LightGBM |
| Vanilla JS instead of Next.js | Three pages, no build to break, no dependencies | No component framework |
| S3 + CloudFront, not GitHub Pages | The forecast file changes every hour | A bit more to set up |
| Hide the forecast when anything is stale or missing | An old forecast that looks current is worse than none | The site is sometimes blank |

## Repository

```
src/river_forecast/ingest/     fetch, parse, validate, store; Hydro-Data pull; Lambda handler
src/river_forecast/transform/  raw -> curated Parquet with quality status
src/river_forecast/features/   rain-gauge QC against neighbouring gauges
src/river_forecast/model/      dataset, models, metrics
src/river_forecast/live/       live inputs, tree evaluator, forecast document, warnings, Lambda handler
analysis/                      Phase 1 and 2 analyses and the re-run (write to docs/)
site/                          the static site, with JavaScript tests
infra/bootstrap/               budget, alarms, state bucket, GitHub OIDC role (applied locally)
infra/site/                    S3 bucket and CloudFront (applied locally)
infra/main/                    data bucket, Lambdas, schedules, alarms (deployed by CI)
DECISIONS.md                   every decision, and the problems I found, including the unflattering ones
```

## Running it

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q && node --test site/tests/site.test.mjs
python scripts/build_site_data.py && python -m http.server -d site     # then open /index.html?sample
```

CI runs the same tests and Terraform checks, and a push to `main` deploys the infrastructure and the site. `infra/bootstrap` and `infra/site` are applied by hand, because they create the deploy role and the CDN. To tear everything down, destroy `infra/site`, `infra/main` and then `infra/bootstrap` (empty the buckets first).

## Data and licences

- **OPW:** Contains Irish Public Sector Information licensed under a Creative Commons Attribution 4.0 International (CC BY 4.0) licence (source http://waterlevel.ie/hydro-data/ – provided by the Office of Public Works.) The data are provisional unless marked checked, and OPW's disclaimer applies.
- **Met Éireann:** Source: Met Éireann, licensed under CC BY 4.0. Met Éireann does not endorse this project. Its warnings are the official source, and the site shows them for the Suir counties.

This project isn't produced, endorsed or approved by OPW or Met Éireann.

## Where it stands

Phases 0 (ingestion), 1 (history, Parquet, analysis), 2 (Clonmel models and verification) and the first frontend are done. Next, in the order I think matters: keep archiving Met Éireann's rainfall forecasts and try them as an input, then check the live forecasts against what actually happens, and only then think about the Dodder. [PROGRESS.md](PROGRESS.md) records what happened at each phase and what didn't work.
