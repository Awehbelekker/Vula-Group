/**
 * money.js — the ONE way the dashboard shows an amount (2026-10-17, UX redesign B4).
 *
 * Amounts arrive from the API as integer cents (CLAUDE.md: money is computed server-side, never
 * by the browser or an LLM); this only formats them. "R 318 400": a non-breaking space after
 * the R and between thousands, so an amount never wraps across lines; cents only when the
 * amount has them ("R 1 250.50"). Before this there were ~38 ad-hoc copies with different
 * separators, decimals and spacing (docs/ux-redesign-audit.md §3). Screens move onto it as they
 * are migrated (Phase F).
 */
const NBSP = " ";
const MINUS = "−";

function groupThousands(digits) {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
}

/**
 * @param {number|string|null|undefined} cents  integer cents
 * @param {{ cents?: "auto"|"always"|"never", empty?: string }} [opts]
 *   cents: "auto" (default) shows decimals only when the amount has them; "always" for
 *   statements and invoices where every line should align; "never" rounds to whole rand.
 *   empty: what to show for a missing amount (default an em dash).
 */
export function formatMoney(cents, { cents: showCents = "auto", empty = "—" } = {}) {
  if (cents === null || cents === undefined || cents === "") return empty;
  const n = Number(cents);
  if (!Number.isFinite(n)) return empty;
  const whole = Math.round(n);
  const neg = whole < 0;
  const abs = Math.abs(whole);
  let rand = Math.floor(abs / 100);
  let part = abs % 100;
  if (showCents === "never") {
    if (part >= 50) rand += 1;
    part = 0;
  }
  const withCents = showCents === "always" || (showCents === "auto" && part !== 0);
  const body = groupThousands(String(rand)) + (withCents ? "." + String(part).padStart(2, "0") : "");
  return `${neg ? MINUS : ""}R${NBSP}${body}`;
}
