// Chart maths, kept free of the DOM so it can be tested.

export function niceTicks(min, max, target = 5) {
  const span = max - min;
  const raw = span / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const first = Math.ceil(min / step) * step;
  const ticks = [];
  for (let v = first; v <= max + 1e-9; v += step) ticks.push(Math.round(v / step) * step);
  return { ticks, step };
}

export function linear(d0, d1, r0, r1) {
  const k = (r1 - r0) / (d1 - d0);
  return (v) => r0 + (v - d0) * k;
}

/**
 * Y domain covering the observations and every forecast interval. A threshold line is included only if it is
 * within `near` metres of the data (otherwise the chart would be mostly empty); the others are reported so the
 * page can say they are off the chart.
 */
export function yDomain(values, thresholds, near = 1.0, pad = 0.08) {
  const finite = values.filter(Number.isFinite);
  let lo = Math.min(...finite);
  let hi = Math.max(...finite);
  const shown = [];
  const offChart = [];
  for (const t of thresholds) {
    if (t.level_m - hi <= near && lo - t.level_m <= near) {
      shown.push(t);
      lo = Math.min(lo, t.level_m);
      hi = Math.max(hi, t.level_m);
    } else {
      offChart.push(t);
    }
  }
  const span = Math.max(hi - lo, 0.2);
  return { min: lo - span * pad, max: hi + span * pad, shown, offChart };
}

/** Collect plotted values from a forecast document. */
export function plottedValues(doc) {
  const v = (doc.observed || []).map((p) => p.v);
  for (const f of doc.forecasts || []) {
    v.push(f.lightgbm.lo_m, f.lightgbm.hi_m, f.lightgbm.level_m);
    if (f.linear && f.linear.level_m != null) v.push(f.linear.level_m);
    v.push(f.persistence.level_m);
  }
  return v;
}
