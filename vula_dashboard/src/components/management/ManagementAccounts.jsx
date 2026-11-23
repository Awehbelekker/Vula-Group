/**
 * ManagementAccounts.jsx — Money › Management accounts (23 Nov 2026; Ian: "Management accounts next").
 *
 * How the business did in a month: the month against last month (and the same month last year once
 * the business has traded a year), the year to date, cash per bank account, what's owed both ways,
 * VAT, a few key figures and short notes — all worked out on the server
 * (vula_mind/vula/commerce/management_pack.py). Downloads: the same pack as PDF or Excel. The owners
 * get it on WhatsApp around the 5th where the business has it switched on.
 * Every figure is the server's; this screen only formats.
 */
import { Download } from "lucide-react";
import { ManagementDownloads } from "./ManagementDownloads.jsx";
import { formatMoney } from "../../lib/money.js";
import { Button, Card, DataTable, EmptyState, ErrorCard, PageHeader, Select, Skeleton } from "../ui/index.jsx";
import { monthLabel, monthsFrom, useManagementAccounts } from "./useManagementAccounts.js";
import "./management.css";

const money = (c) => (c === null || c === undefined ? "—" : formatMoney(c, { cents: "always" }));
const BUCKETS = [["current", "Not yet due"], ["days_1_30", "1–30 days"], ["days_31_60", "31–60 days"],
  ["days_61_90", "61–90 days"], ["days_90_plus", "Over 90 days"]];

function Tile({ label, value, tone }) {
  return (
    <div className={`vma-tile${tone ? ` vma-tile--${tone}` : ""}`}>
      <span className="vma-tile-label">{label}</span>
      <span className="vma-tile-value">{value}</span>
    </div>
  );
}

function pnlRows(d) {
  const cols = columnsOf(d.period);
  const t = d.pnl.totals;
  const row = (key, label, amounts, kind) => ({ key, label, kind, amounts });
  const totalOf = (field) => Object.fromEntries(Object.keys(t).map((k) => [k, t[k][field]]));
  const out = [row("h-inc", "Income", {}, "head")];
  d.pnl.income.forEach((r, i) => out.push(row(`i${i}`, r.account, r.amounts)));
  out.push(row("t-inc", "Total income", totalOf("income_cents"), "total"));
  out.push(row("h-exp", "Costs", {}, "head"));
  d.pnl.expenses.forEach((r, i) => out.push(row(`e${i}`, r.account + (r.direct ? " · direct cost" : ""), r.amounts)));
  out.push(row("t-exp", "Total costs", totalOf("expense_cents"), "total"));
  out.push(row("t-gross", "Gross profit (income less direct costs)", totalOf("gross_cents"), "total"));
  out.push(row("t-net", "Net profit (loss)", totalOf("net_cents"), "total"));
  return { rows: out, cols };
}

function columnsOf(p) {
  const out = [{ key: "this", label: p.this.label }];
  if (p.previous) out.push({ key: "previous", label: p.previous.label }, { key: "change", label: "Change" });
  if (p.last_year) out.push({ key: "last_year", label: p.last_year.label });
  out.push({ key: "ytd", label: "Year to date" });
  return out;
}

const changeOf = (a) => (a.this !== undefined || a.previous !== undefined ? (a.this || 0) - (a.previous || 0) : null);

export default function ManagementAccounts({ tenantId }) {
  const x = useManagementAccounts(tenantId);
  const d = x.data;
  const options = monthsFrom(d?.period?.first_month);
  const picker = options.length > 0 && (
    <label className="vma-month"><span className="vma-sr">Month</span>
      <Select aria-label="Month" value={x.month} onChange={(e) => x.setMonth(e.target.value)}>
        {options.map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
      </Select>
    </label>);
  const header = (
    <PageHeader title="Management accounts"
      sub="How the business did in the month, against last month and the year so far. Worked out from your books; the notes are written from the figures."
      actions={picker} />);

  if (x.state === "failed") return <div className="vma-page">{header}<ErrorCard what="the management accounts" onRetry={x.reload} /></div>;
  if (x.state === "off") {
    return (
      <EmptyState title="Management accounts need Money access">
        Ask the business owner to give you access to Money.
      </EmptyState>);
  }
  if (x.state === "empty") {
    return (
      <div className="vma-page">{header}
        <EmptyState title="No management accounts for that month">{x.why || "There's nothing in the books for it yet."}</EmptyState>
      </div>);
  }
  if (x.state === "loading" || !d) {
    return <div className="vma-page">{header}<Card><Skeleton lines={6} /></Card></div>;
  }

  const p = d.period;
  const t = d.pnl.totals.this;
  const k = d.kpis;
  const { rows, cols } = pnlRows(d);
  const pnlColumns = [
    { key: "label", label: "Account", render: (r) => <span className={r.kind ? `vma-row-${r.kind}` : undefined}>{r.label}</span> },
    ...cols.map((c) => ({
      key: c.key, label: c.label, align: "right",
      render: (r) => (r.kind === "head" ? "" : money(c.key === "change" ? changeOf(r.amounts) : (r.amounts[c.key] ?? 0))),
    })),
  ];
  const cashColumns = [
    { key: "account", label: "Account" },
    { key: "opening", label: "Opening", align: "right", render: (a) => money(a.opening_cents) },
    { key: "in", label: "Money in", align: "right", render: (a) => money(a.in_cents) },
    { key: "out", label: "Money out", align: "right", render: (a) => money(a.out_cents) },
    { key: "closing", label: "Closing", align: "right", render: (a) => money(a.closing_cents) },
    { key: "as_at", label: "Last line", render: (a) => a.as_at },
  ];
  const owedColumns = [
    { key: "label", label: "Past due" },
    { key: "c", label: "Customers owe", align: "right", render: (r) => money(r.c) },
    { key: "s", label: "You owe suppliers", align: "right", render: (r) => money(r.s) },
  ];
  const cu = d.owed.customers, su = d.owed.suppliers;
  const owedRows = BUCKETS.map(([key, label]) => ({ id: key, label, c: cu.aging[key] || 0, s: su.aging[key] || 0 }))
    .concat([{ id: "total", label: "Total", c: cu.total_cents, s: su.total_cents }]);

  return (
    <div className="vma-page">
      {header}
      <Card className="vma-panel">
        <div className="vma-head">
          <div className="vma-text">
            <h3 className="vma-h">{p.this.label}{p.ended ? "" : " (to date)"}</h3>
            <p className="vma-sub">
              {d.source === "journal" ? "From the books (accrual view)." : "From the bank statements: money in and out, by category."}
              {" "}Year to date: {p.ytd.label}.
            </p>
          </div>
          <ManagementDownloads tenantId={tenantId} month={p.month} />
        </div>
        <div className="vma-tiles">
          <Tile label="Sales" value={money(t.income_cents)} />
          <Tile label="Gross profit" value={money(t.gross_cents)} />
          <Tile label={t.net_cents < 0 ? "Loss for the month" : "Profit for the month"} value={money(t.net_cents)}
            tone={t.net_cents < 0 ? "bad" : "good"} />
          <Tile label="In the bank" value={money(d.cash.closing_cents)} />
          <Tile label="Customers owe" value={money(cu.total_cents)} />
          <Tile label="You owe suppliers" value={money(su.total_cents)} />
        </div>
        <div className="vma-tiles vma-tiles--small">
          <Tile label="Gross margin" value={k.gross_margin_pct === null ? "—" : `${k.gross_margin_pct}%`} />
          <Tile label="Net margin" value={k.net_margin_pct === null ? "—" : `${k.net_margin_pct}%`} />
          <Tile label="Debtor days" value={k.debtor_days === null ? "—" : String(k.debtor_days)} />
          <Tile label="Cash runway" value={k.cash_runway_months === null ? "—" : `${k.cash_runway_months} months`} />
        </div>
      </Card>

      <Card className="vma-panel">
        <h3 className="vma-h">Notes</h3>
        <ul className="vma-notes">{d.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
      </Card>

      <Card className="vma-panel">
        <h3 className="vma-h">Profit and loss</h3>
        <DataTable caption="Profit and loss" columns={pnlColumns} rows={rows} rowKey="key"
          stack={{ title: (r) => r.label,
            detail: (r) => (r.kind === "head" ? undefined : cols.filter((c) => c.key !== "this" && c.key !== "change")
              .map((c) => `${c.key === "ytd" ? "Year" : c.label.split(" ")[0]} ${money(r.amounts[c.key] ?? 0)}`).join(" · ")),
            trailing: (r) => (r.kind === "head" ? "" : money(r.amounts.this ?? 0)) }} />
      </Card>

      <Card className="vma-panel">
        <h3 className="vma-h">Cash at the month end</h3>
        <DataTable caption="Cash per bank account" columns={cashColumns} rows={d.cash.accounts} rowKey="account"
          empty="No bank accounts on file."
          stack={{ title: (a) => a.account, detail: (a) => `In ${money(a.in_cents)} · out ${money(a.out_cents)}`,
            trailing: (a) => money(a.closing_cents) }} />
        <p className="vma-sub vma-total">Total in the bank: <strong>{money(d.cash.closing_cents)}</strong></p>
      </Card>

      <Card className="vma-panel">
        <h3 className="vma-h">What's owed at the month end</h3>
        <DataTable caption="What's owed by how far past due" columns={owedColumns} rows={owedRows}
          stack={{ title: (r) => r.label, detail: (r) => `Customers owe ${money(r.c)}`, trailing: (r) => money(r.s) }} />
        <div className="vma-two">
          {[["Largest amounts customers owe", cu], ["Largest amounts owed to suppliers", su]].map(([title, who]) => (
            <div key={title} className="vma-text">
              <h4 className="vma-h4">{title}</h4>
              {who.top.length ? (
                <ul className="vma-list">{who.top.map((w) => (
                  <li key={w.name} className="vma-li"><span className="vma-text">{w.name}</span><span>{money(w.owed_cents)}</span></li>))}
                </ul>) : <p className="vma-sub">Nothing owed.</p>}
            </div>))}
        </div>
      </Card>

      {d.vat && (
        <Card className="vma-panel">
          <h3 className="vma-h">VAT for {p.this.label}</h3>
          <ul className="vma-list">
            <li className="vma-li"><span>VAT on sales</span><span>{money(d.vat.output_cents)}</span></li>
            <li className="vma-li"><span>VAT on costs</span><span>{money(d.vat.input_cents)}</span></li>
            <li className="vma-li vma-strong"><span>Net to SARS</span><span>{money(d.vat.net_cents)}</span></li>
          </ul>
        </Card>)}
      <p className="vma-sub"><Download size={14} aria-hidden="true" /> The owners get this pack on WhatsApp around the 5th of each month where it's switched on.</p>
    </div>
  );
}
