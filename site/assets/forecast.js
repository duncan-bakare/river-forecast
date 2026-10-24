import { drawChart } from "./chart.js";
import { classify, trustNote } from "./status.js";
import { ageMinutes, ageText, formatBoth, formatIrish, formatUTC } from "./time.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const URL_ = params.has("sample") ? "data/forecast.sample.json" : "data/forecast.json";
const REFRESH_MS = 5 * 60 * 1000;

let doc = null;
let zone = "utc";

function text(tag, str, cls) {
  const n = document.createElement(tag);
  n.textContent = str;
  if (cls) n.className = cls;
  return n;
}

function setNotice(kind, title, lines) {
  const box = $("status");
  box.replaceChildren();
  box.className = `notice notice-${kind}`;
  box.append(text("strong", title));
  if (lines.length) {
    const ul = document.createElement("ul");
    for (const l of lines) ul.append(text("li", l));
    box.append(ul);
  }
  box.hidden = false;
}

function m(v) {
  return v == null ? "n/a" : `${v.toFixed(2)} m`;
}

function renderFacts(d) {
  const f = $("facts");
  f.replaceChildren();
  const add = (label, value, sub) => {
    const div = document.createElement("div");
    div.className = "fact";
    div.append(text("dt", label), text("dd", value));
    if (sub) div.lastChild.append(text("span", sub, "sub"));
    f.append(div);
  };
  const now = Date.now();
  if (d.latest_observation && d.latest_observation.time) {
    add("Latest reading", m(d.latest_observation.level_m), `${formatBoth(d.latest_observation.time)} · ${ageText(ageMinutes(d.latest_observation.time, now))}`);
  }
  if (d.data_through) {
    add("Forecasts are based on data to", formatBoth(d.data_through), "latest hour with both level and rain data (hourly mean, hour starting)");
  }
  add("Issued", formatBoth(d.issued_at), ageText(ageMinutes(d.issued_at, now)));
  f.hidden = false;
}

function renderTable(d) {
  const body = $("fc-body");
  body.replaceChildren();
  for (const f of d.forecasts) {
    const tr = document.createElement("tr");
    const when = document.createElement("th");
    when.scope = "row";
    when.append(text("span", formatUTC(f.valid_at)), document.createElement("br"), text("span", formatIrish(f.valid_at), "sub"));
    const lead = text("td", `+${f.horizon_h} h`);
    const lg = f.lightgbm;
    const central = text("td", m(lg.level_m), "num strong");
    const interval = text("td", `${lg.lo_m.toFixed(2)} to ${lg.hi_m.toFixed(2)} m`, "num");
    const lin = text("td", m(f.linear && f.linear.level_m), "num");
    const pers = text("td", m(f.persistence.level_m), "num");
    const trust = document.createElement("td");
    for (const line of trustNote(f.verification)) trust.append(text("p", line, "small"));
    tr.append(when, lead, central, interval, lin, pers, trust);
    body.append(tr);
  }
  $("fc-table").hidden = false;
}

function renderWarnings(d) {
  const box = $("warnings");
  box.replaceChildren();
  const w = d.warnings;
  box.append(text("h2", "Met Éireann weather warnings"));
  if (!w || w.status !== "ok") {
    const p = text("p", "The Met Éireann warnings feed could not be read just now. Please check ");
    p.append(link("https://www.met.ie/warnings", "met.ie/warnings"), document.createTextNode(" directly."));
    box.append(p);
    return;
  }
  if (w.items.length === 0) {
    const p = text("p", `No warnings for Tipperary, Waterford, Kilkenny, Limerick or Cork were listed in the feed when it was checked (${formatBoth(w.checked_at)}). This does not mean there is no risk. `);
    p.append(link("https://www.met.ie/warnings", "Check met.ie/warnings"), document.createTextNode("."));
    box.append(p);
    return;
  }
  const ul = document.createElement("ul");
  ul.className = "warnings";
  for (const it of w.items) {
    const li = document.createElement("li");
    li.append(text("strong", `${(it.level || "").toUpperCase()} ${it.type || "warning"}`.trim()), text("span", ` ${it.headline || ""}`));
    if (it.onset && it.expiry) li.append(document.createElement("br"), text("span", `From ${formatBoth(it.onset)} until ${formatBoth(it.expiry)}`, "sub"));
    ul.append(li);
  }
  box.append(ul);
  const p = text("p", "Counties in or beside the Suir catchment. Source: Met Éireann. Full details and the official wording: ", "small");
  p.append(link("https://www.met.ie/warnings", "met.ie/warnings"), document.createTextNode("."));
  box.append(p);
}

function link(href, label) {
  const a = document.createElement("a");
  a.href = href;
  a.textContent = label;
  a.rel = "noopener";
  return a;
}

function render() {
  const verdict = classify(doc, Date.now());
  const show = verdict.showNumbers;
  for (const id of ["facts", "chart-section", "fc-table", "trust"]) $(id).hidden = true;
  $("status").hidden = true;

  if (verdict.state === "ok") {
    // nothing to announce
  } else if (verdict.state === "degraded") {
    setNotice("warn", "Forecast available with reduced inputs", verdict.reasons);
  } else {
    const titles = {
      unavailable: "No forecast right now",
      stale: "The forecast is out of date",
      unreadable: "The forecast could not be loaded",
    };
    setNotice("alert", titles[verdict.state], [...verdict.reasons, "Please use the official sources below."]);
  }
  if (doc && (doc.latest_observation || doc.warnings) && verdict.state !== "stale" && verdict.state !== "unreadable") {
    renderFacts(doc);
  }
  if (show) {
    $("chart-section").hidden = false;
    const { offChart } = drawChart($("chart"), doc, zone);
    $("offchart").textContent = offChart.length
      ? `OPW statistical levels ${offChart.map((t) => `${t.level_m.toFixed(2)} m`).join(" and ")} are above the range of this chart.`
      : "";
    renderTable(doc);
    $("trust").hidden = false;
  }
  if (doc && doc.warnings && verdict.state !== "unreadable" && verdict.state !== "stale") renderWarnings(doc);
  else {
    $("warnings").replaceChildren(text("h2", "Met Éireann weather warnings"));
    const p = text("p", "Please check ");
    p.append(link("https://www.met.ie/warnings", "met.ie/warnings"), document.createTextNode(" for current warnings."));
    $("warnings").append(p);
  }
}

async function load() {
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 15000);
    const res = await fetch(URL_, { cache: "no-store", signal: ctl.signal });
    clearTimeout(timer);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    doc = await res.json();
  } catch (e) {
    doc = null;
  }
  render();
}

for (const r of document.querySelectorAll("input[name=zone]")) {
  r.addEventListener("change", () => {
    zone = r.value;
    if (doc && classify(doc, Date.now()).showNumbers) drawChart($("chart"), doc, zone);
  });
}
load();
setInterval(load, REFRESH_MS);
setInterval(() => doc && render(), 60 * 1000);
