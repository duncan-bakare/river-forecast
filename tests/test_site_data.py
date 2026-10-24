import importlib.util
import json
import pathlib

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_site_data", ROOT / "scripts" / "build_site_data.py")
bsd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bsd)

RERUN = ROOT / "docs" / "phase2" / "rerun"


def test_verification_json_matches_the_csvs():
    d = bsd.build()
    json.dumps(d, allow_nan=False)  # no NaN / Infinity in the published file
    assert [s["id"] for s in d["sets"]] == ["test_checked", "floods", "provisional_2026"]
    skill = pd.read_csv(RERUN / "skill.csv")
    flood12 = next(s for s in d["sets"] if s["id"] == "floods")["rows"][1]
    src = skill[(skill.set == "flood backtests (8 events)") & (skill.h == 12) & (skill.model == "lightgbm")].iloc[0]
    assert flood12["h"] == 12 and flood12["lightgbm"]["skill"] == round(src.skill, 3)
    assert flood12["lightgbm"]["skill_lo"] == round(src["5%"], 3)
    assert "skill" not in flood12["persistence"]


def test_headline_numbers_are_the_ones_in_decisions():
    d = bsd.build()
    floods = next(s for s in d["sets"] if s["id"] == "floods")["rows"]
    skill = {r["h"]: (r["linear"]["skill"], r["lightgbm"]["skill"]) for r in floods}
    assert skill[6] == (0.353, 0.85) and skill[24] == (0.322, 0.342) and skill[48] == (0.155, 0.083)
    # the plain-language claim on the forecast page: intervals cover roughly 65-80% of hours in floods
    oof = next(i for i in d["intervals"] if i["id"] == "flood events (out-of-fold recal.)")["rows"]
    assert all(0.6 <= r["coverage_final"] <= 0.82 for r in oof)


def test_exceedance_and_peaks_shapes():
    d = bsd.build()
    assert [e["onsets"] for e in d["exceedance"]] == [16, 13]
    p1 = d["exceedance"][0]["models"]
    assert p1["persistence"]["caught"] == {"6": 0, "12": 0, "24": 0, "48": 0}
    assert p1["lightgbm"]["caught"]["6"] == 16
    assert [r["h"] for r in d["peaks"]] == [6, 12, 24, 48]
    assert d["peaks"][0]["persistence"]["timing_h"] == 6.0 and d["peaks"][0]["persistence"]["size_m"] == 0.0
