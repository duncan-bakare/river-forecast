// Decide what the page may show. Every failure path ends in "no numbers", never a stale forecast.

export const MAX_FORECAST_AGE_MIN = 180;

/**
 * @returns {{state: "ok"|"degraded"|"unavailable"|"stale"|"unreadable", showNumbers: boolean, reasons: string[]}}
 */
export function classify(doc, nowMs) {
  if (!doc || typeof doc !== "object" || doc.schema !== 1 || typeof doc.issued_at !== "string") {
    return { state: "unreadable", showNumbers: false, reasons: ["The forecast file could not be read."] };
  }
  const issued = Date.parse(doc.issued_at);
  if (Number.isNaN(issued)) {
    return { state: "unreadable", showNumbers: false, reasons: ["The forecast file has no valid issue time."] };
  }
  const reasons = Array.isArray(doc.reasons) ? doc.reasons.slice() : [];
  const ageMin = (nowMs - issued) / 60000;
  if (ageMin > MAX_FORECAST_AGE_MIN) {
    return {
      state: "stale",
      showNumbers: false,
      reasons: [`The last forecast is ${(ageMin / 60).toFixed(1)} hours old, so it is not shown.`, ...reasons],
    };
  }
  if (ageMin < -10) {
    return { state: "unreadable", showNumbers: false, reasons: ["The forecast is dated in the future; check your device clock."] };
  }
  if (doc.status === "unavailable" || !Array.isArray(doc.forecasts) || doc.forecasts.length === 0) {
    return { state: "unavailable", showNumbers: false, reasons };
  }
  if (doc.status === "degraded") return { state: "degraded", showNumbers: true, reasons };
  if (doc.status === "ok") return { state: "ok", showNumbers: true, reasons };
  return { state: "unreadable", showNumbers: false, reasons: ["Unknown forecast status."] };
}

/** Plain-language read of the verification record for one horizon, from the numbers in the document. */
export function trustNote(ver) {
  const out = [];
  if (!ver) return out;
  const cov = ver.interval_coverage && ver.interval_coverage.floods_out_of_fold;
  if (typeof cov === "number") {
    out.push(
      `In the 8 biggest past floods, this 90% interval contained the observed level in about ${Math.round(cov * 100)}% of hours.`,
    );
  }
  const sk = ver.skill_floods;
  if (sk && typeof sk.lightgbm === "number") {
    const noBetter = typeof sk.lightgbm_5 === "number" && sk.lightgbm_5 <= 0;
    out.push(
      noBetter
        ? "In past floods this model was not reliably better than simply assuming the level stays where it is."
        : `In past floods it reduced squared error by about ${Math.round(sk.lightgbm * 100)}% compared with assuming the level stays where it is.`,
    );
  }
  return out;
}
