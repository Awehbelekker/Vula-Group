/**
 * RepHomeV2.jsx — the redesigned sales rep Home (2026-10-20, UX redesign Phase F). Same calls as
 * VulaRepHome (rep/useRepHome.js): the rep's own day, open reminders and this week's call sheet,
 * each opening its My Work tab. Nothing tenant-wide.
 */
import { BookOpen, CalendarDays, ChevronRight, Contact, Link2, Receipt } from "lucide-react";
import { Card, ListRow, PageHeader, StatCard } from "../ui/index.jsx";
import RepTodayV2 from "./RepTodayV2";
import { useRepHome } from "./useRepHome";
import "./rep.css";

const LINKS = [
  ["rep-bookings", "Bookings", "Today's and this week's", CalendarDays],
  ["rep-contacts", "Contacts", "People you've met", Contact],
  ["rep-documents", "Product knowledge", "Brochures, price lists, specs", BookOpen],
  ["rep-expenses", "Expenses", "Receipts you've scanned", Receipt],
  ["rep-crm", "CRM sync", "Dynamics 365", Link2],
];

export default function RepHomeV2({ tenantId, repPhone, onNavigate }) {
  const x = useRepHome(tenantId, repPhone);
  const go = (id) => () => onNavigate?.("my-work", id);
  const n = x.list.length;
  const entries = (x.callSheet?.entries || []).length;
  const recipient = x.callSheet?.config?.call_sheet_recipient_email;
  return (
    <div className="vrp">
      <PageHeader headline title="Today" sub="Your own contacts, call sheet, reminders and bookings. Nothing tenant-wide." />
      <RepTodayV2 tenantId={tenantId} repPhone={repPhone} />
      <div className="vrp-grid">
        <StatCard variant={x.overdue.length ? "hot" : "tile"} label="Open reminders" onClick={go("rep-reminders")}
          value={x.reminders === null ? "…" : String(n)}
          sub={x.reminders === null ? "Loading" : x.overdue.length ? `${x.overdue.length} overdue` : n ? "none overdue" : "Nothing due"} />
        <StatCard variant={x.callSheet && !recipient ? "hot" : "tile"} label="Call sheet this week" onClick={go("rep-callsheet")}
          value={x.callSheet ? String(entries) : "…"}
          sub={!x.callSheet ? "Loading" : `${entries === 1 ? "entry" : "entries"} · ${recipient ? `goes to ${recipient}` : "no recipient set yet"}`} />
      </div>
      {n > 0 && (
        <>
          <h2 className="vrp-overline">Open reminders</h2>
          <Card pad={4}>
            {x.list.slice(0, 3).map((r) => <ListRow key={r.id} title={r.text} trailing={<ChevronRight size={16} aria-hidden="true" />} onClick={go("rep-reminders")} />)}
          </Card>
        </>)}
      <h2 className="vrp-overline">More of your work</h2>
      <Card pad={4} className="vrp-links">
        {LINKS.map(([id, title, detail, Icon]) => (
          <ListRow key={id} Icon={Icon} title={title} detail={detail} trailing={<ChevronRight size={16} aria-hidden="true" />} onClick={go(id)} />))}
      </Card>
    </div>
  );
}
