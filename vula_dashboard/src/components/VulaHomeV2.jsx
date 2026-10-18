/**
 * VulaHomeV2.jsx — the redesigned Home (2026-10-17, UX redesign E1). Shown on the overview tab
 * when the tenant is on the redesign; the current Home (VulaMerchantAdmin OverviewTab) is
 * untouched for everyone else, and a sales rep keeps VulaRepHome.
 *
 * Front house / Back house / All. Every figure comes from GET /admin/home — computed on the
 * server, in integer cents, filtered to this login (a staff login without Money gets no back
 * house) — and every figure opens the list it came from. A figure the business has no data
 * for isn't drawn. All = the merged activity feed and Today.
 */
import { useEffect, useMemo, useState } from "react";
import { ArrowRight } from "lucide-react";
import { VULA_API } from "../lib/authFetch";
import { formatMoney } from "../lib/money";
import { useAuthStore } from "../store/auth";
import { Button, Card, ErrorCard, ListRow, PageHeader, PillTabs, Skeleton, StatCard } from "./ui/index.jsx";
import { ActivityDetail, ActivityList, shortWhen, useActivity } from "./VulaActivity";
import { TodayList, useToday } from "./VulaToday";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

function greeting(name) {
  const h = new Date().getHours();
  const part = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  const first = (name || "").trim().split(/\s+/)[0];
  return first ? `${part}, ${first}` : part;
}

function duration(secs) {
  if (secs == null) return "—";
  if (secs < 90) return `${secs}s`;
  if (secs < 90 * 60) return `${Math.round(secs / 60)} min`;
  return `${Math.round(secs / 3600)} h`;
}

const grid = { display: "grid", gap: "var(--space-3)", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 220px), 1fr))" };
const section = { margin: "var(--space-6) 0 var(--space-3)", font: "600 var(--fs-overline)/var(--lh-overline) var(--font-sans)",
  letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" };

function Tile({ label, value, sub, hot, onClick }) {
  return <StatCard variant={hot ? "hot" : "tile"} label={label} value={value} sub={sub} onClick={onClick} />;
}

export default function VulaHomeV2({ tenantId, onNavigate }) {
  const user = useAuthStore((s) => s.user);
  const [home, setHome] = useState(null);
  const [error, setError] = useState(false);
  const [view, setView] = useState("front");
  const [picked, setPicked] = useState(null);
  const today = useToday(tenantId);
  const feed = useActivity(tenantId, { limit: 12 });

  const load = () => {
    setError(false);
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/home`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setHome).catch(() => { setError(true); setHome({ front: null, back: null }); });
  };
  useEffect(load, [tenantId]);  // eslint-disable-line react-hooks/exhaustive-deps

  const f = home?.front || {};
  const b = home?.back || {};
  const go = (link) => () => link && onNavigate?.(link.section, link.tab);
  const tabs = useMemo(() => [
    ...(home?.front ? [{ id: "front", label: "Front house" }] : []),
    ...(home?.back ? [{ id: "back", label: "Back house" }] : []),
    { id: "all", label: "All" },
  ], [home]);
  useEffect(() => { if (home && !tabs.some((t) => t.id === view)) setView(tabs[0].id); }, [tabs]);  // eslint-disable-line

  const status = [
    home?.month_days_left != null ? `${home.month_days_left} days left in ${MONTHS[new Date().getMonth()]}` : null,
    f.open_orders ? `${f.open_orders.value} open orders` : null,
    f.chats_waiting ? `${f.chats_waiting.value} ${f.chats_waiting.value === 1 ? "chat" : "chats"} waiting on you` : null,
  ].filter(Boolean).join(" · ");

  return (
    <div>
      <PageHeader headline title={greeting(user?.name)} sub={status || undefined} />
      {error && <ErrorCard what="your Home figures" onRetry={load} />}
      {!home && <Skeleton lines={4} height={22} />}
      {home && <PillTabs label="Home view" tabs={tabs} active={view} onChange={setView} />}

      {home && view === "front" && (
        <>
          {f.won_month && (
            <div style={{ marginBottom: "var(--space-3)" }}>
              <StatCard variant="hero" label={`Won in ${MONTHS[new Date().getMonth()]}`} value={formatMoney(f.won_month.cents)}
                sub={`${f.won_month.count} paid ${f.won_month.count === 1 ? "order" : "orders"}`}
                progress={f.won_month.target_cents ? Math.min(1, f.won_month.cents / f.won_month.target_cents) : undefined}
                onClick={go(f.won_month.link)} />
            </div>)}
          <div style={grid}>
            {f.open_orders && <Tile label="Open orders" value={String(f.open_orders.value)} sub="to pay, pack or send" onClick={go(f.open_orders.link)} />}
            {f.chats_waiting && <Tile label="Chats waiting on you" value={String(f.chats_waiting.value)} hot={f.chats_waiting.value > 0}
              sub={f.chats_waiting.hot?.[0] ? `oldest ${shortWhen(f.chats_waiting.hot[0].since)} ago` : "none waiting"} onClick={go(f.chats_waiting.link)} />}
            {f.first_reply && <Tile label="First reply" value={duration(f.first_reply.seconds)} sub="typical, last 7 days" onClick={go(f.first_reply.link)} />}
            {f.new_contacts && <Tile label="New contacts" value={String(f.new_contacts.value)} sub="last 7 days" onClick={go(f.new_contacts.link)} />}
          </div>
          {f.chats_waiting?.hot?.length > 0 && (
            <>
              <h2 style={section}>Waiting longest</h2>
              <Card pad={4}>
                {f.chats_waiting.hot.map((h) => (
                  <ListRow key={`${h.contact_id}${h.since}`} title={h.contact_id || "A customer"} detail="Needs a person"
                    trailing={shortWhen(h.since)} onClick={go(f.chats_waiting.link)} />))}
              </Card>
            </>)}
        </>)}

      {home && view === "back" && (
        <div style={grid}>
          {b.money_in && <Tile label="Money in this month" value={formatMoney(b.money_in.cents)} sub="from the bank" onClick={go(b.money_in.link)} />}
          {b.money_out && <Tile label="Money out this month" value={formatMoney(b.money_out.cents)} sub="from the bank" onClick={go(b.money_out.link)} />}
          {b.invoices_overdue && <Tile label="Invoices overdue" value={formatMoney(b.invoices_overdue.cents)} hot={b.invoices_overdue.value > 0}
            sub={`${b.invoices_overdue.value} to chase`} onClick={go(b.invoices_overdue.link)} />}
          {b.bills_due && <Tile label="Bills due this week" value={formatMoney(b.bills_due.cents)} sub={`${b.bills_due.value} to pay`} onClick={go(b.bills_due.link)} />}
          {b.documents_to_review && <Tile label="Documents to check" value={String(b.documents_to_review.value)} sub="Vula wasn't sure" onClick={go(b.documents_to_review.link)} />}
          {b.bank_unmatched && <Tile label="Bank lines to match" value={String(b.bank_unmatched.value)} sub="not linked yet" onClick={go(b.bank_unmatched.link)} />}
          {b.stock_alerts && <Tile label="Low stock" value={String(b.stock_alerts.value)} sub="under 5 left" onClick={go(b.stock_alerts.link)} />}
        </div>)}

      {home && view === "all" && feed.items && feed.items.length > 0 && (
        <>
          <h2 style={section}>Latest</h2>
          <Card pad={4}><ActivityList items={feed.items} onPick={setPicked} /></Card>
          <div style={{ marginTop: 8 }}><Button variant="quiet" Icon={ArrowRight} onClick={() => onNavigate?.("activity")}>All activity</Button></div>
        </>)}

      {home && today.items && today.items.length > 0 && (
        <>
          <h2 style={section}>Today</h2>
          <Card pad={4}><TodayList items={today.items} onNavigate={onNavigate} max={5} /></Card>
          {today.items.length > 5 && <div style={{ marginTop: 8 }}>
            <Button variant="quiet" Icon={ArrowRight} onClick={() => onNavigate?.("today")}>All {today.items.length} for today</Button></div>}
        </>)}
      <ActivityDetail item={picked} onClose={() => setPicked(null)} onNavigate={onNavigate} />
    </div>
  );
}
