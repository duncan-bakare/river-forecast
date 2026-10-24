// Time helpers. All data are UTC. Irish time is Europe/Dublin: GMT in winter, IST (UTC+1) in summer.

const DUBLIN = "Europe/Dublin";

function parts(date, timeZone) {
  const f = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  return Object.fromEntries(f.formatToParts(date).map((p) => [p.type, p.value]));
}

/** UTC offset of Irish time at this instant, in minutes (0 in winter, 60 in summer). */
export function dublinOffsetMinutes(date) {
  const p = parts(date, DUBLIN);
  const u = parts(date, "UTC");
  const asMinutes = (x) => Number(x.hour) * 60 + Number(x.minute);
  let diff = asMinutes(p) - asMinutes(u);
  if (diff < -720) diff += 1440;
  if (diff > 720) diff -= 1440;
  return diff;
}

export function dublinLabel(date) {
  return dublinOffsetMinutes(date) === 60 ? "IST" : "GMT";
}

/** "Sat 24 Oct 16:00" */
function stamp(p) {
  return `${p.weekday} ${p.day} ${p.month} ${p.hour}:${p.minute}`;
}

export function formatUTC(iso) {
  return `${stamp(parts(new Date(iso), "UTC"))} UTC`;
}

export function formatIrish(iso) {
  const d = new Date(iso);
  return `${stamp(parts(d, DUBLIN))} ${dublinLabel(d)}`;
}

/** Both labels, UTC first: "Sat 24 Oct 16:00 UTC (17:00 IST)". The date is repeated only if it differs. */
export function formatBoth(iso) {
  const d = new Date(iso);
  const u = parts(d, "UTC");
  const l = parts(d, DUBLIN);
  const sameDay = u.day === l.day && u.month === l.month;
  const local = sameDay ? `${l.hour}:${l.minute}` : `${l.weekday} ${l.day} ${l.month} ${l.hour}:${l.minute}`;
  return `${stamp(u)} UTC (${local} ${dublinLabel(d)})`;
}

/** Short axis label in the chosen zone ("utc" or "irish"): "Sat 18:00" */
export function axisLabel(ms, zone) {
  const d = new Date(ms);
  const p = parts(d, zone === "irish" ? DUBLIN : "UTC");
  return `${p.weekday} ${p.hour}:${p.minute}`;
}

export function ageMinutes(iso, nowMs) {
  return (nowMs - new Date(iso).getTime()) / 60000;
}

export function ageText(minutes) {
  if (minutes < 90) return `${Math.round(minutes)} min ago`;
  const h = minutes / 60;
  return h < 48 ? `${h.toFixed(1)} h ago` : `${Math.round(h / 24)} days ago`;
}
