/**
 * SignupsV2.jsx — the redesigned Master HQ › Signups (2026-10-20, UX redesign Phase F). Same call
 * as VulaAdmin (master/useSignupsHQ.js): the latest client signups to follow up within a day,
 * filtered by status or plan, each opening its contact details.
 */
import { useState } from "react";
import { ChevronDown, ChevronUp, ClipboardList, RefreshCw } from "lucide-react";
import { SA_TZ } from "../../lib/dates";
import { formatMoney } from "../../lib/money";
import { Button, Card, EmptyState, PillTabs, Skeleton, StatCard, StatusChip } from "../ui/index.jsx";
import { useSignups } from "./useSignupsHQ";
import "./hq.css";

const FILTERS = ["all", "active", "provisioning", "business", "growth", "starter"].map((id) => ({ id, label: id[0].toUpperCase() + id.slice(1) }));
const statusTone = (s) => (s === "active" ? "ok" : s === "provisioning" ? "warn" : "muted");
export const saDate = (ts) => (ts ? new Date(ts).toLocaleDateString("en-ZA", { day: "numeric", month: "short", year: "numeric", timeZone: SA_TZ }) : "—");

export default function SignupsV2() {
  const x = useSignups();
  const [open, setOpen] = useState(null);
  return (
    <section className="vhq" aria-label="Client signups">
      <div className="vhq-row">
        <p className="vhq-sub vhq-grow">Your follow-up list: call new signups within 24 hours.</p>
        <Button variant="secondary" Icon={RefreshCw} disabled={x.loading} onClick={x.load}>{x.loading ? "Loading…" : "Refresh"}</Button>
      </div>
      <div className="vhq-tiles">
        <StatCard variant="tile" label="Signups" value={String(x.signups.length)} />
        <StatCard variant="tile" label="Active clients" value={String(x.active)} />
        <StatCard variant="tile" label="MRR (list prices)" value={formatMoney(x.mrr * 100, { cents: "never" })} sub="active clients only" />
        <StatCard variant="tile" label="Business plan" value={String(x.byPlan.business || 0)} sub={`Growth ${x.byPlan.growth || 0} · Starter ${x.byPlan.starter || 0}`} />
      </div>
      <PillTabs label="Show" tabs={FILTERS} active={x.filter} onChange={x.setFilter} />
      {x.error && <p className="vhq-error" role="alert">Couldn't load signups: {x.error}</p>}
      {x.loading ? <Card><Skeleton lines={4} /></Card>
        : !x.error && x.filtered.length === 0
          ? <EmptyState Icon={ClipboardList} title={x.filter === "all" ? "No signups yet" : `No ${x.filter} clients`}>
              {x.filter === "all" ? "Clients appear here once they sign up through Onboard." : "Try another filter."}</EmptyState>
          : x.filtered.length > 0 && (
            <Card pad={4}>
              <ul className="vhq-list" aria-label="Signups">
                {x.filtered.map((s, i) => {
                  const key = s.email || i;
                  const isOpen = open === key;
                  const days = s.trial_ends ? Math.max(0, Math.ceil((new Date(s.trial_ends) - Date.now()) / 86400000)) : null;
                  return (
                    <li key={key} className="vhq-item">
                      <button type="button" className="vhq-toggle" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : key)}>
                        <span className="vhq-grow"><b>{s.company_name}</b><span className="vhq-muted">{s.contact_name}</span></span>
                        <StatusChip outline>{s.plan}</StatusChip>
                        <StatusChip tone={statusTone(s.status)}>{s.status}</StatusChip>
                        {days !== null && <StatusChip tone={days < 7 ? "danger" : "muted"}>{days}d trial left</StatusChip>}
                        <span className="vhq-muted">{saDate(s.created_at)}</span>
                        {isOpen ? <ChevronUp size={18} aria-hidden="true" /> : <ChevronDown size={18} aria-hidden="true" />}
                      </button>
                      {isOpen && (
                        <dl className="vhq-detail">
                          <div><dt>Email</dt><dd>{s.email}</dd></div>
                          <div><dt>WhatsApp</dt><dd>{s.whatsapp || "Not given"}</dd></div>
                          <div><dt>Industry</dt><dd>{s.industry}</dd></div>
                          <div><dt>Trial ends</dt><dd>{saDate(s.trial_ends)}</dd></div>
                        </dl>)}
                    </li>);
                })}
              </ul>
            </Card>)}
    </section>
  );
}
