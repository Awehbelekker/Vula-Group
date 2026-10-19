/**
 * dates.js — calendar days in South African time (Africa/Johannesburg, UTC+2, no DST), the zone
 * the backend books, delivers and invoices in (2026-10-19). `toISOString().slice(0, 10)` is the
 * UTC day, which is still yesterday between midnight and 02:00 SAST.
 */
export const SA_TZ = "Africa/Johannesburg";

// en-CA formats as YYYY-MM-DD
const ymd = new Intl.DateTimeFormat("en-CA", { timeZone: SA_TZ, year: "numeric", month: "2-digit", day: "2-digit" });

/** The SA calendar day of a Date / timestamp / ISO string, as YYYY-MM-DD. */
export const saDay = (when) => ymd.format(new Date(when));

/** Today in SA as YYYY-MM-DD; `offsetDays` moves it (−1 = yesterday). */
export const todaySA = (offsetDays = 0) => saDay(Date.now() + offsetDays * 86400000);

/** Is an API timestamp on SA day `d`? A bare "2026-10-19T09:00:00" is already SA wall time. */
export const isOnSADay = (iso, d) => {
  const s = String(iso || "");
  if (!s) return false;
  return /([zZ]|[+-]\d\d:?\d\d)$/.test(s) ? saDay(s) === d : s.slice(0, 10) === d;
};

/** The last calendar day of `month` (1–12) of `year`, as YYYY-MM-DD. */
export const lastDayOfMonth = (year, month) =>
  `${year}-${String(month).padStart(2, "0")}-${String(new Date(Date.UTC(year, month, 0)).getUTCDate()).padStart(2, "0")}`;
