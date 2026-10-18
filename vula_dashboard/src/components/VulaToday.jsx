/**
 * VulaToday.jsx — Today: what needs a person today (2026-10-17, UX redesign E3). One list from
 * GET /admin/today — chats waiting, follow-ups due, Vula's draft quotes to approve, documents to
 * check, today's bookings — built from the data that already holds them (no separate task
 * system), filtered on the server to this login. Home shows the first few (TodayList).
 */
import { useCallback, useEffect, useState } from "react";
import { CalendarCheck, CalendarDays, FileText, Mail, MailCheck, MessageCircle, ReceiptText } from "lucide-react";
import { VULA_API } from "../lib/authFetch";
import { formatMoney } from "../lib/money";
import { EmptyState, ErrorCard, ListRow, PageHeader, Skeleton, StatusChip } from "./ui/index.jsx";
import { shortWhen } from "./VulaActivity";

const ICONS = { chat: MessageCircle, follow_up: MailCheck, email: Mail, quote: ReceiptText, document: FileText, booking: CalendarDays };

export function useToday(tenantId) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);
  const load = useCallback(async () => {
    setError(false);
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/today`);
      if (!r.ok) throw new Error(String(r.status));
      setData(await r.json());
    } catch { setError(true); setData((d) => d || { items: [] }); }
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);
  return { items: data?.items || null, error, reload: load };
}

function when(it) {
  if (it.kind === "booking") return new Date(it.at).toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit" });
  return it.at ? shortWhen(it.at) : "";
}

export function TodayList({ items, onNavigate, max }) {
  const list = max ? items.slice(0, max) : items;
  return (
    <div role="list">
      {list.map((it) => (
        <div role="listitem" key={it.id}>
          <ListRow Icon={ICONS[it.kind] || CalendarCheck} title={it.label}
            detail={[it.detail, it.amount_cents != null ? formatMoney(it.amount_cents) : null, it.contact_id].filter(Boolean).join(" · ") || undefined}
            trailing={<>
              {it.label.includes("overdue") && <StatusChip tone="warm">Overdue</StatusChip>}
              <span>{when(it)}</span>
            </>}
            onClick={it.link && onNavigate ? () => onNavigate(it.link.section, it.link.tab) : undefined} />
        </div>))}
    </div>
  );
}

export default function VulaToday({ tenantId, onNavigate }) {
  const { items, error, reload } = useToday(tenantId);
  const date = new Date().toLocaleDateString("en-ZA", { weekday: "long", day: "numeric", month: "long" });
  return (
    <div>
      {/* the shell's top bar already says "Today"; the page's own heading is the date */}
      <PageHeader title={date} sub="What needs a person today. Tap one to open it." />
      {error && <ErrorCard what="today's list" onRetry={reload} />}
      {items === null && <Skeleton lines={5} height={18} />}
      {items && items.length === 0 && !error && (
        <EmptyState Icon={CalendarCheck} title="Nothing waiting on you">Chats, follow-ups and documents that need you show up here.</EmptyState>)}
      {items && items.length > 0 && (
        <div style={{ border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", background: "var(--surface)", padding: 4 }}>
          <TodayList items={items} onNavigate={onNavigate} />
        </div>)}
    </div>
  );
}
