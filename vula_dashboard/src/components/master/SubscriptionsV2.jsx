/**
 * SubscriptionsV2.jsx — the redesigned Master HQ › Subscriptions (2026-10-20, UX redesign
 * Phase F). Same call as VulaSubscriptions (master/useSignupsHQ.js): trial health and estimated
 * MRR, by urgency (expired, expiring in 3 days, this week, paid, healthy trials) or as one list.
 */
import { useEffect, useState } from "react";
import { authFetch } from "../../lib/authFetch";
import { rand } from "../settings/AiUsage.jsx";
import { ChevronDown, ChevronUp, CreditCard, RefreshCw } from "lucide-react";
import { formatMoney } from "../../lib/money";
import { Button, Card, EmptyState, PillTabs, Skeleton, StatCard, StatusChip } from "../ui/index.jsx";
import { PLAN_PRICE, daysUntil, subStateOf, useSubscriptions } from "./useSignupsHQ";
import { saDate } from "./SignupsV2";
import "./hq.css";

const VIEWS = [{ id: "urgency", label: "By urgency" }, { id: "all", label: "All tenants" }];

function Row({ t, selected, onSelect }) {
  const st = subStateOf(t);
  const d = daysUntil(t.trial_ends);
  const pct = Math.max(0, Math.min(100, ((30 - Math.max(0, d ?? 0)) / 30) * 100));
  const bar = d === null || t.paid ? null : d <= 3 ? " vhq-bar--danger" : d <= 7 ? " vhq-bar--warn" : "";
  return (
    <li className="vhq-item">
      <button type="button" className="vhq-toggle" aria-expanded={selected} onClick={() => onSelect(selected ? null : t)}>
        <span className="vhq-grow"><b>{t.company_name}</b><span className="vhq-muted">{t.contact_name}</span></span>
        <StatusChip outline>{t.plan}</StatusChip>
        <StatusChip tone={st.tone}>{st.label}</StatusChip>
        {t.paid && <span className="vhq-muted">{formatMoney((PLAN_PRICE[t.plan] || 0) * 100, { cents: "never" })}/mo</span>}
        {selected ? <ChevronUp size={18} aria-hidden="true" /> : <ChevronDown size={18} aria-hidden="true" />}
      </button>
      {bar !== null && <div className={`vhq-bar${bar}`} role="progressbar" aria-label={`${t.company_name} trial used`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}><span style={{ width: `${pct}%` }} /></div>}
      {selected && (
        <dl className="vhq-detail">
          <div><dt>Email</dt><dd>{t.email}</dd></div>
          <div><dt>WhatsApp</dt><dd>{t.whatsapp || "Not given"}</dd></div>
          <div><dt>Trial ends</dt><dd>{saDate(t.trial_ends)}</dd></div>
          <div><dt>Payment</dt><dd>{t.paid ? "Paid" : "Unpaid"}</dd></div>
          <div><dt>Status</dt><dd>{t.status}</dd></div>
          <div><dt>Signed up</dt><dd>{saDate(t.created_at)}</dd></div>
        </dl>)}
    </li>
  );
}

// AI top-ups an owner requested (vula/ai_allowance.py): invoice them, then mark them paid on the
// business's Usage tab. Shown only when there are some.
function AiTopUpsWaiting() {
  const [rows, setRows] = useState(null);
  useEffect(() => { authFetch("/v1/master/ai-credits").then((d) => setRows(d.waiting || [])).catch(() => setRows([])); }, []);
  if (!rows || rows.length === 0) return null;
  return (
    <Card>
      <h2 className="vhq-sub"><b>AI top-ups waiting</b> — invoice them, then mark them paid on the business's Usage tab.</h2>
      <ul className="vhq-list" aria-label="AI top-ups waiting">
        {rows.map((c) => (
          <li key={c.id} className="vhq-item"><span className="vhq-grow"><b>{c.tenant_id}</b> <span className="vhq-muted">· {String(c.requested_at || "").slice(0, 10)} · ref {c.id.slice(0, 8)}</span></span>
            <StatusChip tone="warn">{rand(c.amount_cents)}</StatusChip></li>))}
      </ul>
    </Card>
  );
}

export default function SubscriptionsV2() {
  const x = useSubscriptions();
  const [open, setOpen] = useState(null);
  const select = (t) => setOpen(t ? t.email : null);
  const list = (rows) => (
    <Card pad={4}><ul className="vhq-list">{rows.map((t) => <Row key={t.email} t={t} selected={open === t.email} onSelect={select} />)}</ul></Card>);
  return (
    <section className="vhq" aria-label="Subscriptions">
      <div className="vhq-row">
        <p className="vhq-sub vhq-grow">Trial health, estimated monthly revenue and who to follow up.</p>
        <Button variant="secondary" Icon={RefreshCw} disabled={x.loading} onClick={x.load}>{x.loading ? "Loading…" : "Refresh"}</Button>
      </div>
      <AiTopUpsWaiting />
      <div className="vhq-tiles">
        <StatCard variant="tile" label="MRR (list prices)" value={formatMoney(x.mrr * 100, { cents: "never" })} sub={`${x.paid.length} paid ${x.paid.length === 1 ? "client" : "clients"}`} />
        <StatCard variant="tile" label="Tenants" value={String(x.tenants.length)} sub="all plans" />
        <StatCard variant={x.soonCount ? "hot" : "tile"} label="Expiring soon" value={String(x.soonCount)} sub="within 7 days" />
        <StatCard variant={x.expiredCount ? "hot" : "tile"} label="Expired" value={String(x.expiredCount)} sub="needs follow-up" />
      </div>
      <PillTabs label="View" tabs={VIEWS} active={x.view} onChange={x.setView} />
      {x.error && <p className="vhq-error" role="alert">Couldn't load: {x.error}</p>}
      {x.loading ? <Card><Skeleton lines={4} /></Card>
        : x.error ? null
        : x.tenants.length === 0 ? <EmptyState Icon={CreditCard} title="No tenants yet">Onboard your first client to see subscriptions here.</EmptyState>
        : x.view === "all" ? list(x.sorted)
        : x.sections.map((s) => {
            const rows = x.sorted.filter(s.filter);
            if (!rows.length) return null;
            return (
              <div key={s.title} className="vhq" style={{ gap: "var(--space-2)" }}>
                <h2 className="vhq-overline"><StatusChip tone={s.tone}>{rows.length}</StatusChip>{s.title.replace(" — ", ": ")}</h2>
                {list(rows)}
              </div>);
          })}
    </section>
  );
}
