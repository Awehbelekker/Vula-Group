/**
 * RevenueChart.jsx — daily revenue as columns (2026-10-19, UX redesign Phase F). One series, so no
 * legend: the card's title names it. Brand-coloured columns (contrast checked against both
 * surfaces), at most 24px wide with a 4px rounded top and a 2px gap; a hairline grid at clean
 * rand ticks; a tooltip on hover (the whole day's slot is the target, not just the bar); and a
 * "Show as table" view with every value — the keyboard and screen-reader route (up to 90 days
 * would be 90 tab stops), so nothing is colour- or hover-only.
 */
import { useMemo, useState } from "react";
import { formatMoney } from "../../lib/money";

const money = (c) => formatMoney(c || 0, { cents: "always" });
const compact = (cents) => {
  const r = cents / 100;
  return r >= 1000 ? `R${(r / 1000).toLocaleString("en-ZA", { maximumFractionDigits: 1 })}K` : `R${Math.round(r)}`;
};
// A clean axis top: 1, 2, 2.5 or 5 × a power of ten, at or above the largest day.
function niceMax(v) {
  if (v <= 0) return 100;
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((n) => n >= v);
}
const dayLabel = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString("en-ZA", { day: "numeric", month: "short" });

export default function RevenueChart({ data, title }) {
  const [hover, setHover] = useState(null);
  const [table, setTable] = useState(false);
  const top = useMemo(() => niceMax(Math.max(0, ...data.map((d) => d.revenue_cents))), [data]);
  const ticks = [top, top / 2, 0];
  const total = data.reduce((a, d) => a + (d.revenue_cents || 0), 0);
  const best = data.reduce((b, d) => (d.revenue_cents > (b?.revenue_cents || 0) ? d : b), null);
  const h = hover != null ? data[hover] : null;

  return (
    <figure className="vrc">
      <div className="vrc-head">
        <figcaption className="vrc-title">{title}</figcaption>
        <button type="button" className="vrc-toggle" aria-pressed={table} onClick={() => setTable((t) => !t)}>
          {table ? "Show as chart" : "Show as table"}
        </button>
      </div>
      {table ? (
        <div className="vrc-table-wrap" role="region" aria-label={`${title} — table`} tabIndex={0}>
          <table className="vrc-table">
            <thead><tr><th scope="col">Day</th><th scope="col">Orders</th><th scope="col">Revenue</th></tr></thead>
            <tbody>{data.map((d) => (
              <tr key={d.date}><th scope="row">{dayLabel(d.date)}</th><td>{d.orders}</td><td className="vula-money">{money(d.revenue_cents)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      ) : (
        <div className="vrc-plot" role="img"
          aria-label={`${title}: ${money(total)} over ${data.length} days${best ? `, best day ${dayLabel(best.date)} at ${money(best.revenue_cents)}` : ""}. Use "Show as table" for every day.`}>
          <div className="vrc-axis" aria-hidden="true">
            {ticks.map((t) => <span key={t} className="vrc-tick" style={{ bottom: `${(t / top) * 100}%` }}>{compact(t)}</span>)}
          </div>
          <div className="vrc-area" onMouseLeave={() => setHover(null)}>
            {ticks.map((t) => <span key={t} className="vrc-grid" style={{ bottom: `${(t / top) * 100}%` }} aria-hidden="true" />)}
            {data.map((d, i) => (
              <div key={d.date} className={`vrc-col${hover === i ? " vrc-col--on" : ""}`} onMouseEnter={() => setHover(i)}>
                <span className="vrc-bar" style={{ height: d.revenue_cents > 0 ? `max(${(d.revenue_cents / top) * 100}%, 2px)` : 0 }} />
              </div>
            ))}
            {h && (
              <div className="vrc-tip" role="status" style={{ left: `${((hover + 0.5) / data.length) * 100}%` }}>
                <strong>{dayLabel(h.date)}</strong>
                <span className="vula-money">{money(h.revenue_cents)}</span>
                <span>{h.orders} order{h.orders === 1 ? "" : "s"}</span>
              </div>
            )}
          </div>
          <div className="vrc-x" aria-hidden="true">
            <span>{data[0] && dayLabel(data[0].date)}</span>
            <span>{data[data.length - 1] && dayLabel(data[data.length - 1].date)}</span>
          </div>
        </div>
      )}
    </figure>
  );
}
