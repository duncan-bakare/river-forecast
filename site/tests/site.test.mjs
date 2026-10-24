import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { niceTicks, plottedValues, yDomain } from "../assets/scale.js";
import { MAX_FORECAST_AGE_MIN, classify, trustNote } from "../assets/status.js";
import { axisLabel, dublinLabel, dublinOffsetMinutes, formatBoth, formatIrish, formatUTC } from "../assets/time.js";
import { verdict } from "../assets/verification.js";

const sample = JSON.parse(readFileSync(new URL("../data/forecast.sample.json", import.meta.url), "utf8"));
const issued = Date.parse(sample.issued_at);

// ---- time
test("Irish time is IST in summer and GMT in winter", () => {
  assert.equal(dublinOffsetMinutes(new Date("2026-07-01T12:00:00Z")), 60);
  assert.equal(dublinOffsetMinutes(new Date("2026-12-01T12:00:00Z")), 0);
  assert.equal(dublinLabel(new Date("2026-07-01T12:00:00Z")), "IST");
  assert.equal(dublinLabel(new Date("2026-12-01T12:00:00Z")), "GMT");
});

test("clock change on 25 October 2026 (01:00 UTC)", () => {
  assert.equal(dublinLabel(new Date("2026-10-25T00:30:00Z")), "IST"); // 01:30 IST
  assert.equal(dublinLabel(new Date("2026-10-25T01:30:00Z")), "GMT"); // 01:30 GMT, the repeated hour
  assert.match(formatBoth("2026-10-25T00:30:00Z"), /00:30 UTC \(01:30 IST\)/);
  assert.match(formatBoth("2026-10-25T01:30:00Z"), /01:30 UTC \(01:30 GMT\)/);
});

test("both labels are shown and the date is repeated only when it differs", () => {
  assert.equal(formatBoth("2026-10-24T16:00:00Z"), "Sat 24 Oct 16:00 UTC (17:00 IST)");
  assert.equal(formatBoth("2026-10-24T23:30:00Z"), "Sat 24 Oct 23:30 UTC (Sun 25 Oct 00:30 IST)");
  assert.equal(formatUTC("2026-10-24T16:00:00Z"), "Sat 24 Oct 16:00 UTC");
  assert.equal(formatIrish("2026-10-24T16:00:00Z"), "Sat 24 Oct 17:00 IST");
  assert.equal(axisLabel(Date.parse("2026-10-24T16:00:00Z"), "utc"), "Sat 16:00");
  assert.equal(axisLabel(Date.parse("2026-10-24T16:00:00Z"), "irish"), "Sat 17:00");
});

// ---- status: every failure path hides the numbers
test("a fresh ok forecast is shown", () => {
  const v = classify(sample, issued + 10 * 60000);
  assert.equal(v.state, "ok");
  assert.equal(v.showNumbers, true);
});

test("an old forecast is stale and hidden", () => {
  const v = classify(sample, issued + (MAX_FORECAST_AGE_MIN + 1) * 60000);
  assert.equal(v.state, "stale");
  assert.equal(v.showNumbers, false);
  assert.match(v.reasons[0], /hours old/);
});

test("unavailable, degraded, malformed and future-dated documents", () => {
  const now = issued + 5 * 60000;
  assert.equal(classify({ ...sample, status: "unavailable", forecasts: [] }, now).showNumbers, false);
  assert.equal(classify({ ...sample, status: "ok", forecasts: [] }, now).state, "unavailable");
  const deg = classify({ ...sample, status: "degraded", reasons: ["only 8 of 14 upstream stations available"] }, now);
  assert.equal(deg.state, "degraded");
  assert.equal(deg.showNumbers, true);
  assert.deepEqual(deg.reasons, ["only 8 of 14 upstream stations available"]);
  for (const bad of [null, undefined, "x", {}, { schema: 2, issued_at: sample.issued_at }, { schema: 1, issued_at: "garbage" }]) {
    assert.equal(classify(bad, now).state, "unreadable");
    assert.equal(classify(bad, now).showNumbers, false);
  }
  assert.equal(classify(sample, issued - 3 * 3600e3).state, "unreadable"); // clock far behind
  assert.equal(classify({ ...sample, status: "mystery" }, now).showNumbers, false);
});

test("track-record text reports the numbers and does not overclaim", () => {
  const lines = trustNote({ interval_coverage: { floods_out_of_fold: 0.711 }, skill_floods: { lightgbm: 0.342, lightgbm_5: 0.231 } });
  assert.match(lines[0], /about 71% of hours/);
  assert.match(lines[1], /about 34%/);
  const weak = trustNote({ interval_coverage: { floods_out_of_fold: 0.65 }, skill_floods: { lightgbm: 0.083, lightgbm_5: -0.051 } });
  assert.match(weak[1], /not reliably better/);
  assert.deepEqual(trustNote(null), []);
});

test("every horizon in the live document carries a track record", () => {
  for (const f of sample.forecasts) assert.ok(trustNote(f.verification).length >= 1, `h=${f.horizon_h}`);
});

// ---- chart maths
test("niceTicks are round numbers inside the range", () => {
  const { ticks, step } = niceTicks(15.03, 16.97, 5);
  assert.ok(ticks.every((t) => t >= 15.03 && t <= 16.97));
  assert.ok([0.25, 0.5, 1].includes(step));
});

test("thresholds far from the data are reported, not drawn", () => {
  const th = [{ level_m: 16.702 }, { level_m: 16.914 }];
  const far = yDomain([14.2, 14.4], th);
  assert.equal(far.shown.length, 0);
  assert.equal(far.offChart.length, 2);
  assert.ok(far.max < 15);
  const near = yDomain([15.9, 16.3], th);
  assert.equal(near.shown.length, 2);
  assert.ok(near.max > 16.914 && near.min < 15.9);
});

test("plotted values cover observations, intervals, and baselines", () => {
  const vals = plottedValues(sample);
  assert.ok(vals.length >= sample.observed.length + 4 * 5);
  assert.ok(vals.every(Number.isFinite));
});

// ---- verification flags
test("verification verdicts", () => {
  assert.equal(verdict({ skill: -0.087, skill_lo: -1, skill_hi: 0.1 }), "worse");
  assert.equal(verdict({ skill: -0.001, skill_lo: -0.037, skill_hi: 0.122 }), "same"); // indistinguishable, not "worse"
  assert.equal(verdict({ skill: 0.083, skill_lo: -0.051, skill_hi: 0.212 }), "same");
  assert.equal(verdict({ skill: 0.85, skill_lo: 0.82, skill_hi: 0.88 }), null);
  assert.equal(verdict({ nse: 0.9 }), null);
});

// ---- static pages carry the required notices
for (const page of ["index.html", "verification.html", "method.html", "404.html"]) {
  test(`${page} states it is not an official flood warning, in static HTML`, () => {
    const html = readFileSync(new URL(`../${page}`, import.meta.url), "utf8");
    assert.match(html, /not an official flood warning/i);
    assert.ok(!/style=/.test(html), "inline styles are blocked by the CSP");
    assert.ok(!/<script(?![^>]*src=)/.test(html), "inline scripts are blocked by the CSP");
  });
}

for (const page of ["index.html", "verification.html", "method.html"]) {
  test(`${page} attributes OPW and Met Éireann`, () => {
    const html = readFileSync(new URL(`../${page}`, import.meta.url), "utf8");
    assert.match(html, /Contains Irish Public Sector Information licensed under a Creative Commons Attribution 4\.0 International \(CC BY 4\.0\) licence \(source <a href="http:\/\/waterlevel\.ie\/hydro-data\/">/);
    assert.match(html, /Source: Met Éireann, licensed under <a [^>]*>CC BY 4\.0<\/a>/);
  });
}

for (const page of ["index.html", "method.html"]) {
  test(`${page} explains UTC and Irish time`, () => {
    const html = readFileSync(new URL(`../${page}`, import.meta.url), "utf8");
    assert.match(html, /UTC/);
    assert.match(html, /IST/);
  });
}
