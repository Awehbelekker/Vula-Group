/**
 * FinancesV2.jsx — the redesigned Money › Finances (2026-10-19, UX redesign Phase F). Same data
 * and actions as the current screens (finances/useFinances.js): the insights snapshot on top,
 * then Insights (the sales report and its revenue chart) and Ledger (money per project, progress
 * claims, recent transactions, the trial balance). Job costing and the cross-check are the same
 * components as today, framed in a card.
 */
import { useState } from "react";
import { ChevronRight, Download, FileText, Sparkles, Users } from "lucide-react";
import { formatMoney } from "../../lib/money";
import { downloadCsv } from "../../lib/csv";
import { Button, Card, EmptyState, Field, Input, PillTabs, Skeleton, StatCard, StatusChip } from "../ui/index.jsx";
import VulaJobCosting from "../VulaJobCosting";
import VulaCrossCheck from "../VulaCrossCheck";
import RevenueChart from "./RevenueChart";
import { INSIGHT_PERIODS, REPORT_DAYS, RISK_LABELS, useFinanceInsights, useLedger, useSalesReport } from "./useFinances";
import "./finances.css";

const money = (c) => formatMoney(c || 0, { cents: "always" });
// The project ledger sends rand amounts (server-computed, 2 dp); shown through the same formatter.
const rands = (r) => formatMoney(Math.round((Number(r) || 0) * 100), { cents: "always" });
const Lines = ({ rows }) => (
  <dl className="vfn-lines">{rows.map(([a, b], i) => <div key={i} className="vfn-line"><dt>{a}</dt><dd className="vula-money">{b}</dd></div>)}</dl>
);
const Chips = ({ label, options, value, onChange }) => (
  <div className="vfn-chips" role="group" aria-label={label}>
    {options.map(([v, l]) => <button key={v} type="button" className="vui-chip" aria-pressed={value === v} onClick={() => onChange(v)}>{l}</button>)}
  </div>
);

export default function FinancesV2({ tenantId }) {
  const [tab, setTab] = useState("insights");
  return (
    <div className="vfn">
      <Insights tenantId={tenantId} />
      <PillTabs label="Finances" active={tab} onChange={setTab} tabs={[{ id: "insights", label: "Sales" }, { id: "ledger", label: "Ledger" }]} />
      {tab === "insights" ? <Sales tenantId={tenantId} /> : <Ledger tenantId={tenantId} />}
    </div>
  );
}

// ── The snapshot ──
function Insights({ tenantId }) {
  const x = useFinanceInsights(tenantId);
  const d = x.d;
  return (
    <section className="vfn-stack" aria-labelledby="vfn-insights">
      <div className="vfn-head">
        <h2 id="vfn-insights" className="vfn-h2">Financial insights</h2>
        <Chips label="Period" options={INSIGHT_PERIODS.map((p) => [p.d, p.l])} value={x.days} onChange={x.setDays} />
      </div>
      {x.loading ? <Card><Skeleton lines={3} /></Card> : (!d || d.error) ? <EmptyState Icon={FileText} title="No financial data yet">It fills in as orders are paid and expenses are logged.</EmptyState> : (
        <>
          <div className="vfn-stats">
            <StatCard variant="tile" label="Revenue" value={money(d.revenue_cents)} sub={`${d.orders_count} paid orders`} color="var(--ok-text)" />
            <StatCard variant="tile" label="Expenses" value={money(d.expenses_cents)} />
            <StatCard variant="tile" label={`Gross profit · ${d.margin_pct}%`} value={money(d.gross_profit_cents)}
              color={d.gross_profit_cents >= 0 ? "var(--ok-text)" : "var(--danger-text)"} />
            <StatCard variant="tile" label="VAT collected" value={money(d.vat.collected_cents)} sub="on paid invoices" />
            <StatCard variant="tile" label="Owed to you" value={money(d.receivables.outstanding_cents)} sub={`${d.receivables.count} unpaid invoices`} />
            <StatCard variant="tile" label="Overdue" value={money(d.receivables.overdue_cents)} sub="chase these"
              color={d.receivables.overdue_cents > 0 ? "var(--danger-text)" : undefined} />
            {d.payment_behavior?.overall?.on_time_pct != null && (
              <StatCard variant="tile" label="Paid on time" value={`${d.payment_behavior.overall.on_time_pct}%`}
                sub={`across ${d.payment_behavior.overall.sample_size} paid invoices`}
                color={d.payment_behavior.overall.on_time_pct >= 70 ? "var(--ok-text)" : "var(--danger-text)"} />)}
          </div>

          <Card className="vfn-ask">
            <div className="vfn-head">
              <h3 className="vfn-h3"><Sparkles size={18} aria-hidden="true" />Ask Vula</h3>
              <Button size="sm" onClick={x.askVula} disabled={x.asking}>{x.asking ? "Thinking…" : "Explain my numbers"}</Button>
            </div>
            {x.summary && <p className="vfn-summary" role="status">{x.summary}</p>}
          </Card>

          <div className="vfn-two">
            {d.receivables.aging && Object.values(d.receivables.aging).some((v) => v > 0) && (
              <Card pad={0} className="vfn-card">
                <h3 className="vfn-card-h">Who to chase first</h3>
                <Lines rows={[["Not yet due", money(d.receivables.aging.current)], ["1–30 days overdue", money(d.receivables.aging.days_1_30)],
                  ["31–60 days overdue", money(d.receivables.aging.days_31_60)], ["61–90 days overdue", money(d.receivables.aging.days_61_90)],
                  ["90+ days overdue", money(d.receivables.aging.days_90_plus)]]} />
              </Card>)}
            {d.payment_behavior?.watch_list?.length > 0 && (
              <Card pad={0} className="vfn-card">
                <h3 className="vfn-card-h"><Users size={16} aria-hidden="true" />Customers to watch</h3>
                <ul className="vfn-list">
                  {d.payment_behavior.watch_list.map((w, i) => (
                    <li key={i} className="vfn-line">
                      <span className="vfn-name">{w.customer_name || w.customer_phone}</span>
                      <StatusChip tone={w.label === "high_risk" ? "danger" : "warn"}>{RISK_LABELS[w.label] || w.label}</StatusChip>
                      <span className="vfn-sub">{w.on_time_pct}% on time · {w.avg_days_late} days late on average</span>
                    </li>))}
                </ul>
              </Card>)}
            {d.top_customers?.length > 0 && (
              <Card pad={0} className="vfn-card">
                <h3 className="vfn-card-h">Top customers</h3>
                <Lines rows={d.top_customers.map((c) => [c.name, money(c.revenue_cents)])} />
              </Card>)}
            {d.top_expense_categories?.length > 0 && (
              <Card pad={0} className="vfn-card">
                <h3 className="vfn-card-h">Top expense categories</h3>
                <Lines rows={d.top_expense_categories.map((c) => [c.category, money(c.amount_cents)])} />
              </Card>)}
          </div>
          {d.vat.note && <p className="vfn-sub">{d.vat.note}</p>}
        </>
      )}
    </section>
  );
}

// ── Sales ──
function Sales({ tenantId }) {
  const s = useSalesReport(tenantId);
  const data = s.data;
  const exportCsv = () => downloadCsv(`sales-report-${s.days}d`, data?.revenue_trend || [], [
    { key: "date", label: "Date" }, { key: "orders", label: "Orders" },
    { label: "Revenue (R)", get: (d) => (d.revenue_cents / 100).toFixed(2) },
  ]);
  const maxRev = data?.top_products?.[0]?.revenue_cents || 1;
  return (
    <section className="vfn-stack" aria-labelledby="vfn-sales">
      <div className="vfn-head">
        <h2 id="vfn-sales" className="vfn-h2">Sales</h2>
        <Chips label="Days" options={REPORT_DAYS.map((n) => [n, `${n} days`])} value={s.days} onChange={s.setDays} />
        <Button size="sm" variant="secondary" Icon={Download} onClick={exportCsv} disabled={!data?.revenue_trend?.length}>Export CSV</Button>
      </div>
      {s.error && <p className="vfn-error" role="alert">Couldn't load the sales report ({s.error}).</p>}
      {s.loading && <Card><Skeleton lines={4} /></Card>}
      {!s.loading && data && (
        <>
          <div className="vfn-stats">
            <StatCard variant="tile" label="Revenue" value={money(data.total_revenue_cents)} sub={`last ${s.days} days`} />
            <StatCard variant="tile" label="Orders" value={data.total_orders} sub="paid, confirmed or dispatched" />
            <StatCard variant="tile" label="Average order" value={money(s.avgCents)} sub="per order" />
          </div>
          <Card>
            {data.revenue_trend?.length
              ? <RevenueChart data={data.revenue_trend} title={`Revenue per day — last ${s.days} days`} />
              : <p className="vfn-empty">No revenue in this period.</p>}
          </Card>
          <Card pad={0} className="vfn-card">
            <h3 className="vfn-card-h">Top products by revenue</h3>
            {data.top_products?.length ? (
              <ul className="vfn-list">
                {data.top_products.map((p, i) => (
                  <li key={i} className="vfn-prod">
                    <span className="vfn-name">{p.name}</span>
                    <span className="vfn-sub">{p.units} sold</span>
                    <span className="vula-money vfn-amt">{money(p.revenue_cents)}</span>
                    <span className="vfn-meter" aria-hidden="true"><span style={{ width: `${Math.round((p.revenue_cents / maxRev) * 100)}%` }} /></span>
                  </li>))}
              </ul>
            ) : <p className="vfn-empty">No product sales in this period.</p>}
          </Card>
        </>
      )}
    </section>
  );
}

// ── Ledger ──
function Ledger({ tenantId }) {
  const L = useLedger(tenantId);
  const { data } = L;
  const netCents = Math.round((Number(data.total_in) || 0) * 100) - Math.round((Number(data.total_out) || 0) * 100);
  return (
    <section className="vfn-stack" aria-labelledby="vfn-ledger">
      <div className="vfn-head"><h2 id="vfn-ledger" className="vfn-h2">Money per project</h2></div>
      <p className="vfn-sub">Built from the invoices and payments Vula files. Payments are matched to invoices, not guessed.</p>
      <Card><VulaJobCosting tenantId={tenantId} /></Card>
      <Card><VulaCrossCheck tenantId={tenantId} /></Card>
      <div className="vfn-stats">
        <StatCard variant="tile" label="Money in" value={rands(data.total_in)} color="var(--ok-text)" />
        <StatCard variant="tile" label="Money out" value={rands(data.total_out)} />
        <StatCard variant="tile" label="Net" value={formatMoney(netCents, { cents: "always" })} />
      </div>

      {data.projects.length === 0 ? <EmptyState Icon={FileText} title="No projects yet">No financial documents are filed under a project yet.</EmptyState> : (
        <ul className="vfn-projects" aria-label="Projects">
          {data.projects.map((p) => <ProjectItem key={p.project} p={p} L={L} />)}
        </ul>
      )}

      <Card pad={0} className="vfn-card">
        <h3 className="vfn-card-h">Recent transactions</h3>
        <ul className="vfn-list">
          {data.transactions.slice(0, 25).map((t) => (
            <li key={t.id} className="vfn-txn">
              <StatusChip tone={t.direction === "in" ? "ok" : "muted"}>{t.direction === "in" ? "In" : t.direction === "out" ? "Out" : "Other"}</StatusChip>
              <span className="vfn-name">{t.counterparty || t.filename}{t.description ? <span className="vfn-sub"> · {String(t.description).slice(0, 40)}</span> : null}</span>
              <span className="vfn-sub">{t.project || "No project"}</span>
              <span className="vula-money vfn-amt">{rands(t.amount)}</span>
              <span className="vfn-sub">{t.reconciled ? "Matched" : String(t.kind || "").replace(/_/g, " ")}</span>
            </li>))}
          {data.transactions.length === 0 && <li className="vfn-empty">Nothing filed yet.</li>}
        </ul>
      </Card>

      <TrialBalance L={L} />
    </section>
  );
}

function ProjectItem({ p, L }) {
  const open = L.openProj === p.project;
  const d = L.detail;
  return (
    <li className="vfn-proj">
      <button type="button" className="vfn-proj-head" aria-expanded={open} onClick={() => L.openDetail(p.project)}>
        <ChevronRight size={18} className="vfn-chev" aria-hidden="true" />
        <span className="vfn-proj-name">{p.project}<span className="vfn-sub"> · {p.count} document{p.count === 1 ? "" : "s"}</span></span>
      </button>
      <dl className="vfn-proj-nums">
        <div><dt>In</dt><dd className="vula-money">{rands(p.in)}</dd></div>
        <div><dt>Out</dt><dd className="vula-money">{rands(p.out)}</dd></div>
        <div><dt>Budget</dt><dd>
          {L.editing === p.project
            ? <Input autoFocus aria-label={`Budget for ${p.project} (R)`} inputMode="decimal" value={L.budget} onChange={(e) => L.setBudget(e.target.value)}
                onBlur={() => L.saveBudget(p.project)} onKeyDown={(e) => e.key === "Enter" && L.saveBudget(p.project)} />
            : <button type="button" className="vfn-linkish" onClick={() => L.startBudget(p)} aria-label={`${p.budget ? "Change" : "Set"} the budget for ${p.project}`}>
                {p.budget ? <span className="vula-money">{rands(p.budget)}</span> : "Set"}</button>}
        </dd></div>
        <div><dt>Remaining</dt><dd className={`vula-money${p.remaining != null && p.remaining < 0 ? " vfn-neg" : ""}`}>{p.remaining != null ? rands(p.remaining) : "—"}</dd></div>
      </dl>

      {open && (
        <div className="vfn-detail">
          {!d ? <Skeleton lines={2} /> : (
            <div className="vfn-stats vfn-stats--sm">
              <StatCard variant="tile" label="Contract / budget" value={rands(d.contract)} />
              <StatCard variant="tile" label="Invoiced" value={rands(d.invoiced)} sub={`${d.invoice_count} invoice(s)`} />
              <StatCard variant="tile" label="Outstanding" value={rands(d.outstanding)} sub="billed, unpaid" color={d.outstanding > 0 ? "var(--danger-text)" : undefined} />
              <StatCard variant="tile" label="Paid in" value={rands(d.paid_in)} color="var(--ok-text)" />
              <StatCard variant="tile" label="Spent" value={rands(d.spent)} sub="cash out + expenses" />
              <StatCard variant="tile" label="Net" value={rands(d.net)} />
              <StatCard variant="tile" label="Budget left" value={d.remaining != null ? rands(d.remaining) : "—"}
                color={d.remaining != null && d.remaining < 0 ? "var(--danger-text)" : undefined} />
            </div>)}
          <div className="vfn-form">
            <Field label="Contract value from your BoQ (R)"><Input inputMode="decimal" value={L.contractDraft} onChange={(e) => L.setContractDraft(e.target.value)} /></Field>
            <Button onClick={() => L.saveContract(p.project)}>Save</Button>
          </div>
          <p className="vfn-sub">Unified from invoices, expenses and filed payments. The contract value is kept and drives the budget left; link invoices to a project on the invoice form.</p>
          <Claims project={p.project} L={L} />
        </div>
      )}
    </li>
  );
}

function Claims({ project, L }) {
  return (
    <div className="vfn-claims">
      <h4 className="vfn-h4">Progress claims (interim payment certificates)</h4>
      {L.claims.length > 0 && (
        <ul className="vfn-list vfn-list--boxed">
          {L.claims.map((c) => (
            <li key={c.id} className="vfn-claim">
              <span className="vfn-claim-n">#{c.claim_number}</span>
              <dl className="vfn-claim-nums">
                <div><dt>Work to date</dt><dd className="vula-money">{money(c.cumulative_value_cents)}</dd></div>
                <div><dt>Retention ({c.retention_pct}%)</dt><dd className="vula-money">{money(c.retention_cents)}</dd></div>
                <div><dt>Certified</dt><dd className="vula-money">{money(c.certified_to_date_cents)}</dd></div>
                <div><dt>This payment</dt><dd className="vula-money vfn-strong">{money(c.this_payment_cents)}</dd></div>
              </dl>
              <span className="vfn-claim-act">
                <StatusChip tone={c.status === "certified" ? "ok" : "muted"}>{c.status === "certified" ? "Certified" : c.status === "draft" ? "Draft" : c.status}</StatusChip>
                {c.status === "draft" && <Button size="sm" variant="secondary" onClick={() => L.certifyClaim(project, c.id)}>Certify</Button>}
                {c.status === "certified" && !c.linked_invoice_id && (
                  <Button size="sm" onClick={() => L.setInvoicing({ claimId: c.id, name: "", phone: "", email: "", address: "" })}>Invoice</Button>)}
                {c.linked_invoice_id && <StatusChip tone="ok">Invoiced</StatusChip>}
              </span>
            </li>))}
        </ul>)}

      {L.invoicing && (
        <Card className="vfn-card-in">
          <p className="vfn-sub">Create an invoice for this claim's net payment:</p>
          <div className="vfn-form">
            <Field label="Customer name"><Input value={L.invoicing.name} onChange={(e) => L.setInvoicing({ ...L.invoicing, name: e.target.value })} /></Field>
            <Field label="Phone (optional)"><Input value={L.invoicing.phone} onChange={(e) => L.setInvoicing({ ...L.invoicing, phone: e.target.value })} /></Field>
            <Field label="Email (optional)"><Input type="email" value={L.invoicing.email} onChange={(e) => L.setInvoicing({ ...L.invoicing, email: e.target.value })} /></Field>
          </div>
          <div className="vfn-row">
            <Button disabled={L.claimBusy || !L.invoicing.name} onClick={() => L.submitInvoice(project)}>{L.claimBusy ? "Creating…" : "Create invoice"}</Button>
            <Button variant="secondary" onClick={() => L.setInvoicing(null)}>Cancel</Button>
          </div>
        </Card>)}

      <div className="vfn-form">
        <Field label="New claim — work done to date (R)" hint="The cumulative value of all work so far, not just this period.">
          <Input inputMode="decimal" value={L.newClaim.value} onChange={(e) => L.setNewClaim({ ...L.newClaim, value: e.target.value })} />
        </Field>
        <Field label="Retention %"><Input inputMode="decimal" value={L.newClaim.retention} onChange={(e) => L.setNewClaim({ ...L.newClaim, retention: e.target.value })} /></Field>
        <Button disabled={L.claimBusy || !L.newClaim.value} onClick={() => L.addClaim(project)}>{L.claimBusy ? "Saving…" : "Add claim"}</Button>
      </div>
      <p className="vfn-sub">Retention and the payment due are worked out on the server from the previous claim.</p>
    </div>
  );
}

function TrialBalance({ L }) {
  const tb = L.tb;
  return (
    <Card pad={0} className="vfn-card">
      <button type="button" className="vfn-proj-head vfn-tb-head" aria-expanded={L.tbOpen} onClick={L.toggleTrialBalance}>
        <ChevronRight size={18} className="vfn-chev" aria-hidden="true" /><span className="vfn-proj-name">General ledger (trial balance)</span>
      </button>
      {L.tbOpen && (
        !tb ? <div className="vfn-pad"><Skeleton lines={3} /></div>
          : tb.error ? <p className="vfn-empty">{tb.error}</p>
            : tb.accounts.length === 0 ? <p className="vfn-empty">No journal entries yet — these post by themselves as orders and invoices are paid and expenses are logged.</p>
              : (
                <div className="vfn-tb-wrap" role="region" aria-label="Trial balance" tabIndex={0}>
                  <table className="vfn-tb">
                    <thead><tr><th scope="col">Account</th><th scope="col">Debit</th><th scope="col">Credit</th></tr></thead>
                    <tbody>{tb.accounts.map((a) => (
                      <tr key={a.code}><th scope="row">{a.name} <span className="vfn-sub">· {a.type}</span></th>
                        <td className="vula-money">{a.debit_cents ? money(a.debit_cents) : "—"}</td>
                        <td className="vula-money">{a.credit_cents ? money(a.credit_cents) : "—"}</td></tr>))}
                    </tbody>
                    <tfoot><tr><th scope="row">Total</th><td className="vula-money">{money(tb.total_debit_cents)}</td>
                      <td className="vula-money">{money(tb.total_credit_cents)}{tb.total_debit_cents !== tb.total_credit_cents && <StatusChip tone="danger">Doesn't balance</StatusChip>}</td></tr></tfoot>
                  </table>
                </div>
              )
      )}
    </Card>
  );
}
