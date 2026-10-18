/**
 * VulaActivity.jsx — All activity: everything that happened in the business, one feed
 * (2026-10-17, UX redesign E2). GET /admin/activity is filtered on the server to what this
 * login may see, and carries labels and references only — opening a chat goes to the Inbox,
 * which applies its own rules and records the view. Desktop: feed + a side panel for the
 * picked item; phone: feed + a bottom sheet (ui/Sheet does both). Home embeds ActivityList.
 */
import { useCallback, useEffect, useState } from "react";
import {
  Activity, Bot, CalendarDays, FileText, MessageCircle, Package, ReceiptText, UserX, Wallet, MailCheck, ArrowRight,
} from "lucide-react";
import { VULA_API } from "../lib/authFetch";
import { formatMoney } from "../lib/money";
import { Button, EmptyState, ErrorCard, FilterBar, ListRow, PageHeader, Sheet, Skeleton, StatusChip } from "./ui/index.jsx";

const ICONS = {
  escalation: MessageCircle, chat: MessageCircle, order: Package, booking: CalendarDays, follow_up: MailCheck,
  consent: UserX, invoice: ReceiptText, quote: ReceiptText, credit_note: ReceiptText, proforma: ReceiptText,
  delivery_note: ReceiptText, expense: Wallet, document: FileText, vula_action: Bot,
};
const ACTOR = { vula: "Vula", team: "Your team", customer: "Customer" };

/** "17m", "3h", "Mon", "12 Oct" — the trailing time on a row. */
export function shortWhen(at, now = Date.now()) {
  const t = new Date(at).getTime();
  if (!Number.isFinite(t)) return "";
  const mins = Math.max(0, Math.round((now - t) / 60000));
  if (mins < 60) return `${mins}m`;
  if (mins < 24 * 60) return `${Math.round(mins / 60)}h`;
  const d = new Date(t);
  if (mins < 7 * 24 * 60) return d.toLocaleDateString("en-ZA", { weekday: "short" });
  return d.toLocaleDateString("en-ZA", { day: "numeric", month: "short" });
}

/** One row per feed item. onPick(item) opens the detail. */
export function ActivityList({ items, onPick }) {
  return (
    <div role="list">
      {items.map((it) => (
        <div role="listitem" key={it.id}>
          <ListRow Icon={ICONS[it.type] || ICONS[it.source] || Activity} title={it.label}
            detail={[it.detail, it.amount_cents != null ? formatMoney(it.amount_cents) : null,
              it.actor === "vula" ? null : ACTOR[it.actor]].filter(Boolean).join(" · ") || undefined}
            trailing={<>
              {it.actor === "vula" && <StatusChip tone="info">Vula</StatusChip>}
              <time dateTime={it.at}>{shortWhen(it.at)}</time>
            </>}
            onClick={onPick ? () => onPick(it) : undefined} />
        </div>))}
    </div>
  );
}

const CHIPS = [
  { id: "front", label: "Front house" }, { id: "back", label: "Back house" },
  { id: "vula", label: "Vula" }, { id: "team", label: "Your team" }, { id: "customer", label: "Customers" },
];

export function useActivity(tenantId, { side = "", actor = "", limit = 30 } = {}) {
  const [items, setItems] = useState(null);
  const [cursor, setCursor] = useState(null);
  const [error, setError] = useState(false);
  const load = useCallback(async (after = "") => {
    setError(false);
    try {
      const q = new URLSearchParams({ limit: String(limit), ...(side ? { side } : {}), ...(actor ? { actor } : {}), ...(after ? { cursor: after } : {}) });
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/activity?${q}`);
      if (!r.ok) throw new Error(String(r.status));
      const d = await r.json();
      setItems((prev) => (after ? [...(prev || []), ...(d.items || [])] : d.items || []));
      setCursor(d.next_cursor || null);
    } catch { setError(true); setItems((prev) => prev || []); }
  }, [tenantId, side, actor, limit]);
  useEffect(() => { setItems(null); load(""); }, [load]);
  return { items, cursor, error, more: () => cursor && load(cursor), reload: () => load("") };
}

export function ActivityDetail({ item, onClose, onNavigate }) {
  return (
    <Sheet open={!!item} title={item?.label || ""} onClose={onClose}
      footer={item?.link && onNavigate ? (
        <Button Icon={ArrowRight} onClick={() => { onClose(); onNavigate(item.link.section, item.link.tab); }}>
          {item.link.section === "inbox" ? "Open in Inbox" : "Open the list"}
        </Button>) : null}>
      {item && (
        <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "8px 16px", margin: 0, fontSize: "var(--fs-body)" }}>
          {item.detail && <><dt style={{ color: "var(--muted)" }}>What</dt><dd style={{ margin: 0 }}>{item.detail}</dd></>}
          {item.amount_cents != null && <><dt style={{ color: "var(--muted)" }}>Amount</dt>
            <dd style={{ margin: 0 }} className="vula-money">{formatMoney(item.amount_cents)}</dd></>}
          <dt style={{ color: "var(--muted)" }}>Who</dt><dd style={{ margin: 0 }}>{ACTOR[item.actor] || "—"}</dd>
          {item.contact_id && <><dt style={{ color: "var(--muted)" }}>Contact</dt><dd style={{ margin: 0 }} className="vula-mono">{item.contact_id}</dd></>}
          <dt style={{ color: "var(--muted)" }}>When</dt>
          <dd style={{ margin: 0 }}>{new Date(item.at).toLocaleString("en-ZA", { dateStyle: "medium", timeStyle: "short" })}</dd>
          <dt style={{ color: "var(--muted)" }}>Side</dt>
          <dd style={{ margin: 0 }}><StatusChip outline tone={item.side === "front" ? "brand" : "muted"}>
            {item.side === "front" ? "Front house" : "Back house"}</StatusChip></dd>
        </dl>)}
    </Sheet>
  );
}

export default function VulaActivity({ tenantId, onNavigate }) {
  const [chips, setChips] = useState([]);
  const side = ["front", "back"].find((c) => chips.includes(c)) || "";
  const actor = ["vula", "team", "customer"].find((c) => chips.includes(c)) || "";
  const { items, cursor, error, more, reload } = useActivity(tenantId, { side, actor });
  const [picked, setPicked] = useState(null);
  // one side and one actor at a time: picking another in the same group replaces it
  const toggle = (id) => setChips((cur) => {
    if (cur.includes(id)) return cur.filter((c) => c !== id);
    const group = ["front", "back"].includes(id) ? ["front", "back"] : ["vula", "team", "customer"];
    return [...cur.filter((c) => !group.includes(c)), id];
  });
  return (
    <div>
      {/* the shell's top bar already says "All activity" */}
      <PageHeader sub="Chats, orders, money, documents and what Vula did — newest first. Only what your login may see." />
      <FilterBar chips={CHIPS} selected={chips} onToggle={toggle} label="Show" />
      {error && <ErrorCard what="the activity" onRetry={reload} />}
      {items === null && <Skeleton lines={6} height={18} />}
      {items && items.length === 0 && !error && (
        <EmptyState Icon={Activity} title="Nothing here yet">New chats, orders and documents appear here as they happen.</EmptyState>)}
      {items && items.length > 0 && (
        <div style={{ border: "1px solid var(--line)", borderRadius: "var(--radius-lg)", background: "var(--surface)", padding: 4 }}>
          <ActivityList items={items} onPick={setPicked} />
        </div>)}
      {cursor && <div style={{ textAlign: "center", marginTop: 12 }}><Button variant="secondary" onClick={more}>Show older</Button></div>}
      <ActivityDetail item={picked} onClose={() => setPicked(null)} onNavigate={onNavigate} />
    </div>
  );
}
