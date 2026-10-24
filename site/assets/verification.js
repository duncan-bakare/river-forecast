const $ = (id) => document.getElementById(id);
const HOURS = [6, 12, 24, 48];
const MODEL_NAMES = { persistence: "Persistence", linear: "Linear baseline", lightgbm: "LightGBM" };

function node(tag, content, cls) {
  const n = document.createElement(tag);
  if (content !== undefined) n.textContent = content;
  if (cls) n.className = cls;
  return n;
}
const f = (v, nd = 2) => (v == null ? "n/a" : (Math.abs(v) < 0.5 * 10 ** -nd ? 0 : v).toFixed(nd)); // never show "-0.00"
const pct = (v) => (v == null ? "n/a" : `${Math.round(v * 100)}%`);

/** "worse" | "same" | null, from a model's skill against persistence and its 5-95% interval. A skill within
 * 0.005 of zero is "same", not "worse": it is indistinguishable from persistence. */
export function verdict(cell) {
  if (!cell || cell.skill == null) return null;
  if (cell.skill < -0.005) return "worse";
  if (cell.skill_lo != null && cell.skill_lo <= 0) return "same";
  return null;
}

function tag(kind) {
  return node("span", kind === "worse" ? "worse than persistence" : "not reliably better than persistence", `tag ${kind === "worse" ? "worse" : ""}`);
}

function table(caption, head, rows) {
  const wrap = node("div", undefined, "scroll");
  wrap.setAttribute("role", "region");
  wrap.setAttribute("tabindex", "0");
  wrap.setAttribute("aria-label", `${caption} (scrolls sideways on small screens)`);
  const t = document.createElement("table");
  t.append(node("caption", caption));
  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  for (const h of head) {
    const th = node("th", h.text, h.num ? "num" : "");
    th.scope = "col";
    hr.append(th);
  }
  thead.append(hr);
  const tb = document.createElement("tbody");
  for (const r of rows) {
    const tr = document.createElement("tr");
    r.forEach((c, i) => {
      const isHead = i === 0;
      const cell = isHead ? node("th", undefined, "") : node("td", undefined, head[i].num ? "num" : "");
      if (isHead) cell.scope = "row";
      if (c instanceof Node) cell.append(c);
      else cell.textContent = c;
      tr.append(cell);
    });
    tb.append(tr);
  }
  t.append(thead, tb);
  wrap.append(t);
  return wrap;
}

function skillCell(c) {
  const box = document.createElement("span");
  box.append(`${f(c.skill)} [${f(c.skill_lo)}, ${f(c.skill_hi)}]`);
  const v = verdict(c);
  if (v) {
    box.append(document.createElement("br"), tag(v));
  }
  return box;
}

function renderSkill(d) {
  const root = $("skill");
  root.replaceChildren();
  const flagged = [];
  for (const s of d.sets) {
    const rows = s.rows.map((r) => {
      for (const m of ["linear", "lightgbm"]) {
        const v = verdict(r[m]);
        if (v) flagged.push({ set: s.short, h: r.h, model: MODEL_NAMES[m], kind: v, skill: r[m].skill });
      }
      return [
        `${r.h} h`,
        r.n.toLocaleString("en-IE"),
        `${f(r.persistence.nse, 3)} / ${f(r.persistence.kge, 3)}`,
        `${f(r.linear.nse, 3)} / ${f(r.linear.kge, 3)}`,
        skillCell(r.linear),
        `${f(r.lightgbm.nse, 3)} / ${f(r.lightgbm.kge, 3)}`,
        skillCell(r.lightgbm),
      ];
    });
    root.append(
      table(
        s.label,
        [
          { text: "Lead" },
          { text: "Hours", num: true },
          { text: "Persistence NSE / KGE", num: true },
          { text: "Linear NSE / KGE", num: true },
          { text: "Linear skill [5%, 95%]", num: true },
          { text: "LightGBM NSE / KGE", num: true },
          { text: "LightGBM skill [5%, 95%]", num: true },
        ],
        rows,
      ),
    );
  }
  const box = $("not-better");
  box.replaceChildren();
  if (!flagged.length) {
    box.append(node("p", "In every comparison below, both models beat persistence with a 5 to 95% range above zero."));
    return;
  }
  const ul = document.createElement("ul");
  for (const x of flagged) {
    ul.append(node("li", `${x.model}, ${x.h} h ahead, ${x.set}: skill ${f(x.skill)}, ${x.kind === "worse" ? "worse than" : "not reliably better than"} persistence.`));
  }
  box.append(ul);
}

function renderPeaks(d) {
  const rows = d.peaks.map((r) => [
    `${r.h} h`,
    ...["persistence", "linear", "lightgbm"].flatMap((m) => [f(r[m].timing_h, 1), f(r[m].size_m, 3)]),
  ]);
  $("peaks").append(
    table(
      "Peak timing and size error, median absolute value over the 8 floods",
      [
        { text: "Lead" },
        { text: "Persistence timing (h)", num: true },
        { text: "size (m)", num: true },
        { text: "Linear timing (h)", num: true },
        { text: "size (m)", num: true },
        { text: "LightGBM timing (h)", num: true },
        { text: "size (m)", num: true },
      ],
      rows,
    ),
  );
}

function renderExceedance(d) {
  for (const e of d.exceedance) {
    const rows = ["persistence", "linear", "lightgbm"].map((m) => {
      const c = e.models[m];
      return [
        MODEL_NAMES[m],
        ...HOURS.map((h) => `${c.caught[h]} of ${e.onsets}`),
        f(c.median_lead_h, 0),
        String(c.false_alarms["12"]),
        String(c.false_alarms["24"]),
        f(c.csi["12"], 2),
      ];
    });
    $("exceedance").append(
      table(
        `Crossings of ${e.level_m.toFixed(3)} m (${e.onsets} in the test period and flood windows) caught at least this far ahead`,
        [
          { text: "Model" },
          ...HOURS.map((h) => ({ text: `≥ ${h} h`, num: true })),
          { text: "Median lead (h)", num: true },
          { text: "False alarms at 12 h", num: true },
          { text: "at 24 h", num: true },
          { text: "Hourly CSI at 12 h", num: true },
        ],
        rows,
      ),
    );
  }
}

function renderIntervals(d) {
  const rows = [];
  for (const s of d.intervals) {
    for (const r of s.rows) rows.push([`${s.label}: ${r.h} h`, pct(r.coverage_first_run), pct(r.coverage_final), `${f(r.width_final_m, 2)} m`]);
  }
  $("intervals").append(
    table(
      "How often the 90% interval contained the observed level",
      [{ text: "Situation and lead" }, { text: "First version", num: true }, { text: "Final", num: true }, { text: "Mean width", num: true }],
      rows,
    ),
  );
}

async function load() {
  try {
    const res = await fetch("data/verification.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const d = await res.json();
    renderSkill(d);
    renderPeaks(d);
    renderExceedance(d);
    renderIntervals(d);
    $("model-version").textContent = d.model_version;
  } catch (e) {
    $("skill").replaceChildren(node("div", "The accuracy figures could not be loaded. The full report is on GitHub: github.com/duncan-bakare/river-forecast (docs/phase2/rerun/report.md).", "notice notice-alert"));
  }
}

if (typeof document !== "undefined" && document.getElementById("skill")) load();
