import { axisLabel } from "./time.js";
import { linear, niceTicks, plottedValues, yDomain } from "./scale.js";

const NS = "http://www.w3.org/2000/svg";
const W = 920;
const H = 420;
const M = { l: 58, r: 20, t: 18, b: 56 };
const HOUR = 3600e3;

function el(name, attrs = {}, text) {
  const n = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  if (text !== undefined) n.textContent = text;
  return n;
}

/**
 * Draw observed levels (last 72 h) and the forecasts as points with 90% interval bars.
 * The four forecast points are joined by a thin dotted line only to show their order; it is not a forecast
 * of the shape of the river between them.
 * @returns {{offChart: object[]}} thresholds that were too far from the data to draw
 */
export function drawChart(container, doc, zone) {
  container.replaceChildren();
  const obs = (doc.observed || []).map((p) => ({ t: Date.parse(p.t), v: p.v }));
  const fcs = (doc.forecasts || []).map((f) => ({ ...f, t: Date.parse(f.valid_at) }));
  const through = doc.data_through ? Date.parse(doc.data_through) : obs.at(-1)?.t;
  const t1 = (fcs.at(-1)?.t ?? through) + 3 * HOUR;
  const t0 = Math.max(obs[0]?.t ?? through - 72 * HOUR, through - 72 * HOUR);
  const visibleObs = obs.filter((p) => p.t >= t0);
  const dom = yDomain(plottedValues({ ...doc, observed: visibleObs }), doc.thresholds || []);

  const x = linear(t0, t1, M.l, W - M.r);
  const y = linear(dom.min, dom.max, H - M.b, M.t);

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-labelledby": "chart-title chart-desc", class: "chart" });
  svg.append(
    el("title", { id: "chart-title" }, "Clonmel river level: recent observations and forecasts"),
    el(
      "desc",
      { id: "chart-desc" },
      "Line chart of the observed hourly river level at Clonmel for the last three days, followed by forecast points at 6, 12, 24 and 48 hours after the latest data hour, each with a 90 percent interval. The same numbers are in the table below the chart.",
    ),
  );

  const ty = niceTicks(dom.min, dom.max, 6);
  for (const v of ty.ticks) {
    svg.append(el("line", { x1: M.l, x2: W - M.r, y1: y(v), y2: y(v), class: "grid" }));
    svg.append(el("text", { x: M.l - 8, y: y(v) + 4, class: "tick", "text-anchor": "end" }, v.toFixed(ty.step < 0.1 ? 2 : ty.step < 1 ? 2 : 1)));
  }
  svg.append(el("text", { x: 14, y: (M.t + H - M.b) / 2, class: "axis-title", transform: `rotate(-90 14 ${(M.t + H - M.b) / 2})`, "text-anchor": "middle" }, "Level (m OD)"));

  const tickStep = (t1 - t0) > 100 * HOUR ? 24 * HOUR : 12 * HOUR;
  for (let t = Math.ceil(t0 / tickStep) * tickStep; t <= t1; t += tickStep) {
    svg.append(el("line", { x1: x(t), x2: x(t), y1: M.t, y2: H - M.b, class: "grid" }));
    svg.append(el("text", { x: x(t), y: H - M.b + 18, class: "tick", "text-anchor": "middle" }, axisLabel(t, zone)));
  }
  svg.append(el("text", { x: (M.l + W - M.r) / 2, y: H - 8, class: "axis-title", "text-anchor": "middle" }, zone === "irish" ? "Time (Irish time: GMT in winter, IST in summer)" : "Time (UTC)"));

  for (const th of dom.shown) {
    svg.append(el("line", { x1: M.l, x2: W - M.r, y1: y(th.level_m), y2: y(th.level_m), class: "threshold" }));
    svg.append(el("text", { x: W - M.r - 4, y: y(th.level_m) - 5, class: "threshold-label", "text-anchor": "end" }, `${th.level_m.toFixed(2)} m: OPW statistical level (not a warning level)`));
  }

  if (through) {
    svg.append(el("line", { x1: x(through), x2: x(through), y1: M.t, y2: H - M.b, class: "now" }));
    svg.append(el("text", { x: x(through) - 5, y: M.t + 12, class: "now-label", "text-anchor": "end" }, "latest data hour"));
  }

  if (visibleObs.length) {
    svg.append(el("polyline", { points: visibleObs.map((p) => `${x(p.t)},${y(p.v)}`).join(" "), class: "obs" }));
  }

  if (fcs.length && through) {
    const y0 = fcs[0].persistence.level_m;
    svg.append(el("line", { x1: x(through), x2: x(fcs.at(-1).t), y1: y(y0), y2: y(y0), class: "persistence" }));
    svg.append(el("polyline", { points: fcs.map((f) => `${x(f.t)},${y(f.lightgbm.level_m)}`).join(" "), class: "join" }));
    for (const f of fcs) {
      const cx = x(f.t);
      svg.append(el("line", { x1: cx, x2: cx, y1: y(f.lightgbm.lo_m), y2: y(f.lightgbm.hi_m), class: "interval" }));
      for (const edge of [f.lightgbm.lo_m, f.lightgbm.hi_m]) svg.append(el("line", { x1: cx - 6, x2: cx + 6, y1: y(edge), y2: y(edge), class: "interval" }));
      if (f.linear && f.linear.level_m != null) {
        const ly = y(f.linear.level_m);
        svg.append(el("rect", { x: cx - 4.5 + 9, y: ly - 4.5, width: 9, height: 9, class: "linear", transform: `rotate(45 ${cx + 9} ${ly})` }));
      }
      svg.append(el("circle", { cx, cy: y(f.lightgbm.level_m), r: 5, class: "lgbm" }));
      svg.append(el("text", { x: cx, y: y(f.lightgbm.hi_m) - 8, class: "tick", "text-anchor": "middle" }, `+${f.horizon_h} h`));
    }
  }
  container.append(svg);
  return { offChart: dom.offChart };
}
