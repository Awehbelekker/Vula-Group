/**
 * JobCostingV2.jsx — the redesigned job costing in Money › Finances › Ledger (2026-10-19, UX
 * redesign Phase F). Same data and actions as VulaJobCosting (finances/useJobCosting.js): the
 * business totals as tiles, one disclosure per project (its six figures, fee % and cost by
 * trade), and "What should I charge?". Nothing renders for a business without projects.
 */
import { ChevronRight, Calculator, HardHat } from "lucide-react";
import { formatMoney } from "../../lib/money";
import { Button, Card, ErrorCard, Field, Input, Skeleton, StatCard, StatusChip } from "../ui/index.jsx";
import { useJobCosting } from "./useJobCosting";
import "./finances.css";

const money = (c) => formatMoney(Number(c) || 0, { cents: "never" });
const STATUS_TONE = { "loss": "danger", "below target": "warn", "on track": "ok" };

export default function JobCostingV2({ tenantId }) {
  const x = useJobCosting(tenantId);
  if (x.hasProjects === false) return null;   // job costing is for project businesses (DIGG)
  if (x.failed) return <ErrorCard what="job costing" onRetry={x.load} />;
  if (!x.data || x.hasProjects === null) return <Card><Skeleton lines={3} /></Card>;
  const d = x.data;
  const projects = d.projects || [];
  return (
    <section className="vfn-stack" aria-labelledby="vjc-h">
      <div className="vfn-head"><h3 id="vjc-h" className="vfn-h3"><HardHat size={18} aria-hidden="true" />Job costing — is each project making its fee?</h3></div>
      <p className="vfn-sub">
        From the bank: money received vs cost on each project, the cost-plus fee it should earn (default {d.fee_default_pct}%), and its share of running costs. Received counts what's in the bank so far.
      </p>
      <div className="vfn-stats vfn-stats--sm">
        <StatCard variant="tile" label="Fees earned" value={money(d.fees_earned_cents)} sub={`target ${money(d.fees_target_cents)}`}
          color={d.fees_earned_cents < d.fees_target_cents ? "var(--danger-text)" : undefined} />
        <StatCard variant="tile" label="Running costs" value={money(d.overheads_cents)}
          sub={d.overhead_rate_pct != null ? `${d.overhead_rate_pct}% of project spend` : undefined} />
        <StatCard variant="tile" label="Business result" value={money(d.business_result_cents)} sub="fees earned − running costs"
          color={d.business_result_cents < 0 ? "var(--danger-text)" : undefined} />
        {d.unallocated_project_spend_cents > 0 &&
          <StatCard variant="tile" label="Not on a project yet" value={money(d.unallocated_project_spend_cents)}
            sub={`${d.unallocated_project_lines} materials/labour lines — allocate in Bank`} color="var(--warn-text)" />}
      </div>

      {projects.length === 0
        ? <p className="vfn-sub">No bank lines are allocated to projects yet. In Bank, import a categorised statement sheet or set a project on each line.</p>
        : <ul className="vfn-projects" aria-label="Job costing per project">{projects.map((p) => <Project key={p.project} p={p} x={x} />)}</ul>}

      <Card className="vfn-ask">
        <h4 className="vfn-h4"><Calculator size={16} aria-hidden="true" style={{ verticalAlign: "-3px", marginRight: 6 }} />What should I charge?</h4>
        <p className="vfn-sub">Starts from what the business actually paid.</p>
        <form className="vfn-form" onSubmit={(e) => { e.preventDefault(); x.getAdvice(); }}>
          <Field label="Item or work"><Input placeholder="e.g. ceiling board, tiling labour" value={x.ask.item} onChange={(e) => x.setAsk({ ...x.ask, item: e.target.value })} /></Field>
          <Field label="Quantity"><Input type="number" value={x.ask.quantity} onChange={(e) => x.setAsk({ ...x.ask, quantity: e.target.value })} /></Field>
          <Field label="Unit"><Input placeholder="m2…" value={x.ask.unit} onChange={(e) => x.setAsk({ ...x.ask, unit: e.target.value })} /></Field>
          <Button type="submit">Price it</Button>
        </form>
        {x.advice && <p className="vfn-summary" role="status">{x.advice.text || x.advice.message}</p>}
      </Card>
    </section>
  );
}

function Project({ p, x }) {
  const open = x.open === p.project;
  const id = `vjc-${p.project.replace(/[^a-z0-9]+/gi, "-")}`;
  const editing = x.feeEdit[p.project] !== undefined;
  return (
    <li className="vfn-proj">
      <button type="button" className="vfn-proj-head" aria-expanded={open} aria-controls={id} onClick={() => x.setOpen(open ? null : p.project)}>
        <ChevronRight size={18} className="vfn-chev" aria-hidden="true" />
        <span className="vfn-proj-name">{p.project}</span>
        <StatusChip tone={STATUS_TONE[p.status] || "muted"}>{p.status}</StatusChip>
        <span className="vfn-sub" style={{ marginLeft: "auto" }}>{p.first} – {p.last}</span>
      </button>
      <dl className="vfn-proj-nums vjc-nums">
        <div><dt>Received</dt><dd className="vula-money">{money(p.received_cents)}</dd></div>
        <div><dt>Cost</dt><dd className="vula-money">{money(p.cost_cents)}</dd></div>
        <div><dt>Should bring in (+{p.fee_pct}%)</dt><dd className="vula-money">{money(p.target_received_cents)}</dd></div>
        <div><dt>Fee earned</dt><dd className={`vula-money${p.fee_earned_cents < p.fee_target_cents ? " vfn-neg" : ""}`}>{money(p.fee_earned_cents)}</dd>
          <dd className="vfn-sub">of {money(p.fee_target_cents)}</dd></div>
        <div><dt>Overhead share</dt><dd className="vula-money">{money(p.overhead_share_cents)}</dd></div>
        <div><dt>Profit</dt><dd className={`vula-money${p.profit_cents < 0 ? " vfn-neg" : ""}`}>{money(p.profit_cents)}</dd></div>
      </dl>
      {open && (
        <div id={id} className="vfn-detail">
          <form className="vfn-row" onSubmit={(e) => { e.preventDefault(); x.saveFee(p.project); }}>
            <Field label={`Fee % for ${p.project}`}>
              <Input type="number" value={x.feeEdit[p.project] ?? p.fee_pct} style={{ width: 120 }}
                onChange={(e) => x.setFeeEdit({ ...x.feeEdit, [p.project]: e.target.value })} />
            </Field>
            {editing && <Button type="submit" size="sm" style={{ alignSelf: "flex-end" }}>Save fee</Button>}
          </form>
          {(p.unallocated_trade_cents > 0 || p.variations) && (
            <div className="vfn-row">
              {p.unallocated_trade_cents > 0 && <StatusChip tone="warn" style={{ whiteSpace: "normal" }}>{money(p.unallocated_trade_cents)} not yet allocated to a trade</StatusChip>}
              {p.variations && <StatusChip tone="warn" style={{ whiteSpace: "normal" }}>
                Variations over BOQ: {p.variations.documents} · claimed {money(p.variations.claimed_cents)} · extra costs {money(p.variations.extra_cost_cents)}</StatusChip>}
            </div>)}
          <h5 className="vfn-h4">Cost by trade</h5>
          {p.trades.length === 0 ? <p className="vfn-sub">No costs on this project yet.</p> : (
            <ul className="vfn-list vfn-list--boxed" aria-label={`Cost by trade for ${p.project}`}>
              {p.trades.map((t) => (
                <li key={t.trade} className="vfn-line">
                  <span className="vfn-name">{t.trade}</span>
                  <span className="vfn-sub">{p.cost_cents ? Math.round(100 * t.cents / p.cost_cents) : 0}%</span>
                  <span className="vula-money vfn-amt">{money(t.cents)}</span>
                </li>))}
            </ul>)}
        </div>)}
    </li>
  );
}
