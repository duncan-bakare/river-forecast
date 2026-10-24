"""Build site/data/verification.json from the Phase 2 re-run CSVs (docs/phase2/rerun), so the verification page
shows the repo's actual results. Run in CI before the site is synced.

Usage: python scripts/build_site_data.py [output path]
"""

import json
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
RERUN = ROOT / "docs" / "phase2" / "rerun"
HORIZONS = [6, 12, 24, 48]
MODELS = {"persistence": "persistence", "upstream_linear": "linear", "lightgbm": "lightgbm"}
SHORT = {
    "test_checked": "everyday conditions",
    "floods": "the eight biggest floods",
    "provisional_2026": "2026 unchecked data",
}
SETS = [
    (
        "test_checked",
        "test, checked labels (2022-2026)",
        "Everyday conditions: January 2022 onwards, OPW-checked data, flood windows excluded",
    ),
    (
        "floods",
        "flood backtests (8 events)",
        "The eight biggest floods since 2006 (held out of training; 5 days before to 3 days after each peak)",
    ),
    (
        "provisional_2026",
        "test, provisional labels (2026)",
        "2026 data that OPW has not yet checked (no flood in this period)",
    ),
]


def num(x, nd=3):
    return None if pd.isna(x) else round(float(x), nd)


def skill_sets(skill):
    out = []
    for sid, key, label in SETS:
        rows = []
        for h in HORIZONS:
            g = skill[(skill.set == key) & (skill.h == h)].set_index("model")
            row = {"h": h, "n": int(g.n.iloc[0])}
            for src, dst in MODELS.items():
                r = g.loc[src]
                cell = {"nse": num(r.NSE), "kge": num(r.KGE)}
                if src != "persistence":
                    cell |= {"skill": num(r.skill), "skill_lo": num(r["5%"]), "skill_hi": num(r["95%"])}
                row[dst] = cell
            rows.append(row)
        out.append({"id": sid, "label": label, "short": SHORT[sid], "rows": rows})
    return out


def peak_rows(peaks):
    rows = []
    for h in HORIZONS:
        g = peaks[peaks.h == h]
        row = {"h": h, "events": len(g)}
        for src, dst in MODELS.items():
            row[dst] = {
                "timing_h": num(g[f"{src}_timing_h"].abs().median(), 1),
                "size_m": num(g[f"{src}_size_m"].abs().median(), 3),
            }
        rows.append(row)
    return rows


def exceedance_rows(ex):
    out = []
    for thr, g in ex.groupby("threshold", sort=False):
        level = float(thr.split("(")[1].split(" m")[0])
        models = {}
        for src, dst in MODELS.items():
            r = g[g.model == src].iloc[0]
            models[dst] = {
                "caught": {str(h): int(r[f"caught@{h}h"]) for h in HORIZONS},
                "median_lead_h": num(r.median_lead_h, 1),
                "false_alarms": {"12": int(r["false_alarms@12h"]), "24": int(r["false_alarms@24h"])},
                "csi": {"12": num(r["CSI@12h"]), "24": num(r["CSI@24h"])},
            }
        out.append({"threshold": thr, "level_m": level, "onsets": int(g.onsets.iloc[0]), "models": models})
    return out


def interval_rows(ints):
    names = {
        "test, checked": "Everyday conditions (2022 onwards, checked data)",
        "high flow (issue level >= 15.74 m)": "All hours when the level was 15.74 m or higher (partly in-sample)",
        "flood events (out-of-fold recal.)": "The eight biggest floods, recalibrated out-of-fold (honest estimate)",
        "flood events (all-events recal., in-sample)": (
            "The eight biggest floods, final calibration (in-sample, optimistic)"
        ),
        "test, provisional": "2026 unchecked data",
    }
    out = []
    for key, label in names.items():
        rows = []
        for h in HORIZONS:
            r = ints[(ints.set == key) & (ints.h == h)].iloc[0]
            rows.append(
                {
                    "h": h,
                    "coverage_first_run": num(r.cov_original),
                    "coverage_final": num(r.cov_shipped),
                    "width_final_m": num(r.width_shipped),
                }
            )
        out.append({"id": key, "label": label, "rows": rows})
    return out


def build():
    skill = pd.read_csv(RERUN / "skill.csv")
    return {
        "schema": 1,
        "source": "docs/phase2/rerun (analysis/phase2_rerun.py, analysis/phase2_intervals.py)",
        "model_version": "v1-2026-10-24",
        "nominal_interval": 0.9,
        "sets": skill_sets(skill),
        "peaks": peak_rows(pd.read_csv(RERUN / "peaks.csv")),
        "exceedance": exceedance_rows(pd.read_csv(RERUN / "exceedance.csv")),
        "intervals": interval_rows(pd.read_csv(RERUN / "intervals.csv")),
    }


if __name__ == "__main__":
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "site" / "data" / "verification.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build(), indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out}")
