/**
 * FinancialPosition.jsx — Home › Back house "Financial position" (23 Nov 2026): where the business
 * stands now — money in the bank (each account's last statement balance), what customers owe, what's
 * owed to suppliers, profit so far this month and, if VAT registered, the month's VAT. From
 * GET /admin/reports/position (vula_mind/vula/commerce/management_pack.py::position), loaded apart from
 * Home's own figures so Home stays quick. Hidden for a login without Money (403). Formats only.
 */
import { useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";
import { formatMoney } from "../../lib/money";
import { Skeleton, StatCard } from "../ui/index.jsx";

const grid = { display: "grid", gap: "var(--space-3)", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 220px), 1fr))" };
const section = { margin: "0 0 var(--space-3)", font: "600 var(--fs-overline)/var(--lh-overline) var(--font-sans)",
  letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" };

const day = (iso) => {
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-ZA", { day: "numeric", month: "short" });
};

export default function FinancialPosition({ tenantId, onOpen }) {
  const [p, setP] = useState(undefined);       // undefined = loading, null = nothing to show
  useEffect(() => {
    let live = true;
    setP(undefined);
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reports/position`)
      .then((r) => (r.ok ? r.json() : null)).catch(() => null)
      .then((d) => { if (live) setP(d); });
    return () => { live = false; };
  }, [tenantId]);

  if (p === null) return null;
  if (p === undefined) return <div style={{ marginBottom: "var(--space-5)" }}><Skeleton lines={2} height={22} /></div>;
  const latest = (p.bank?.accounts || []).map((a) => a.as_at).filter(Boolean).sort().pop();
  const tiles = [
    { label: "In the bank", value: p.bank?.total_cents == null ? "—" : formatMoney(p.bank.total_cents),
      sub: latest ? `last statement line ${day(latest)}` : "no bank account on file" },
    { label: "Customers owe you", value: formatMoney(p.owed_to_you_cents), sub: "today" },
    { label: "You owe suppliers", value: formatMoney(p.you_owe_cents), sub: "today" },
    { label: p.profit_month_cents < 0 ? `Loss so far in ${p.month.split(" ")[0]}` : `Profit so far in ${p.month.split(" ")[0]}`,
      value: formatMoney(p.profit_month_cents), sub: `on sales of ${formatMoney(p.sales_month_cents)}` },
  ];
  if (p.vat_month_cents != null) {
    tiles.push({ label: `VAT for ${p.month.split(" ")[0]} so far`, value: formatMoney(p.vat_month_cents),
      sub: p.vat_month_cents < 0 ? "to claim back" : "to pay SARS" });
  }
  return (
    <section aria-label="Financial position" style={{ marginBottom: "var(--space-5)" }}>
      <h2 style={section}>Financial position</h2>
      <div style={grid}>
        {tiles.map((t) => (
          <StatCard key={t.label} variant="tile" label={t.label} value={t.value} sub={t.sub} onClick={onOpen} />))}
      </div>
    </section>
  );
}
