/**
 * VulaUiKit.jsx — #/ui-kit (master only): every shared component in its states, on demo data
 * (2026-10-17, UX redesign Phase C). It is what the Playwright visual + axe tests screenshot,
 * and where a new primitive is checked in both themes before a screen uses it. The redesign
 * look is on while this page is open (with a switch to compare the current look).
 */
import { useEffect, useState } from "react";
import {
  Bell, Calendar, FilePlus2, FileText, Inbox, MessageSquare, Plus, ReceiptText, ScanLine,
  Search, Settings, UserPlus, Users, Megaphone, Wallet, Menu,
} from "lucide-react";
import {
  Button, IconButton, Card, StatCard, PillTabs, SectionTabs, StatusChip, ListRow, DataTable, FilterBar,
  EmptyState, Skeleton, Sheet, CommandPalette, useCommandPaletteHotkey, FloatingToolbar, PageHeader, toast,
} from "./ui/index.jsx";
import { formatMoney } from "../lib/money";
import { getThemeMode, setThemeMode } from "../theme/tokens";

const INVOICES = [
  { id: "i1", no: "INV-2026-0118", who: "Thandi Mokoena", cents: 482050, due: "2026-10-12", status: "overdue" },
  { id: "i2", no: "INV-2026-0121", who: "Sipho Ndlovu", cents: 1250000, due: "2026-10-24", status: "sent" },
  { id: "i3", no: "INV-2026-0119", who: "Nomsa Dlamini", cents: 72450, due: "2026-10-31", status: "draft" },
  { id: "i4", no: "INV-2026-0109", who: "Pieter van der Berg", cents: 3184000, due: "2026-10-02", status: "paid" },
];
const STATUS = { overdue: ["warm", "Overdue"], sent: ["info", "Sent"], draft: ["muted", "Draft"], paid: ["ok", "Paid"] };

function Section({ title, children }) {
  return (
    <section style={{ marginBottom: "var(--space-8)" }}>
      <h2 style={{ font: "600 var(--fs-overline)/var(--lh-overline) var(--font-sans)", letterSpacing: ".06em", textTransform: "uppercase",
        color: "var(--muted)", margin: "0 0 var(--space-3)" }}>{title}</h2>
      {children}
    </section>
  );
}
const row = { display: "flex", flexWrap: "wrap", gap: "var(--space-3)", alignItems: "center" };
const grid = { display: "grid", gap: "var(--space-3)", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))" };

export default function VulaUiKit() {
  const [v2, setV2] = useState(true);
  const [mode, setMode] = useState(getThemeMode());
  const [house, setHouse] = useState("front");
  const [sub, setSub] = useState("orders");
  const [query, setQuery] = useState("");
  const [chips, setChips] = useState(["overdue"]);
  const [sheet, setSheet] = useState(false);
  const [palette, setPalette] = useState(false);
  useCommandPaletteHotkey(setPalette);

  useEffect(() => {
    const root = document.documentElement;
    const before = root.dataset.ui;
    if (v2) root.dataset.ui = "v2"; else delete root.dataset.ui;
    return () => { if (before) root.dataset.ui = before; else delete root.dataset.ui; };
  }, [v2]);

  const rows = INVOICES.filter((r) => (!query || `${r.no} ${r.who}`.toLowerCase().includes(query.toLowerCase()))
    && (!chips.length || chips.includes(r.status)));

  return (
    <main style={{ maxWidth: 1080, margin: "0 auto", padding: "var(--space-6) var(--space-4)", color: "var(--ink)" }}>
      <PageHeader headline eyebrow="Vula design system" title="UI kit"
        sub="Every shared component, on demo data. Master only."
        actions={<>
          <Button variant="secondary" size="sm" onClick={() => setV2((x) => !x)}>{v2 ? "Show current look" : "Show redesign"}</Button>
          <Button variant="secondary" size="sm" onClick={() => { const n = mode === "dark" ? "light" : "dark"; setThemeMode(n); setMode(n); }}>
            {mode === "dark" ? "Light" : "Dark"} theme</Button>
        </>} />

      <Section title="Buttons">
        <div style={row}>
          <Button Icon={Plus}>New quote</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="quiet">Quiet</Button>
          <Button variant="danger">Delete</Button>
          <Button disabled>Disabled</Button>
          <IconButton Icon={Search} label="Search" />
          <IconButton Icon={Inbox} label="Inbox" count={9} />
          <IconButton Icon={Bell} label="Alerts" count={2} quiet />
        </div>
      </Section>

      <Section title="Tabs">
        <div style={{ display: "grid", gap: "var(--space-3)" }}>
          <PillTabs label="Home view" active={house} onChange={setHouse}
            tabs={[{ id: "front", label: "Front house" }, { id: "back", label: "Back house" }, { id: "all", label: "All" }]} />
          <SectionTabs label="Sell" active={sub} onChange={setSub}
            tabs={[{ id: "orders", label: "Orders" }, { id: "products", label: "Products" }, { id: "stock", label: "Stock" }]} />
        </div>
      </Section>

      <Section title="Stat cards">
        <div style={{ display: "grid", gap: "var(--space-3)", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 260px), 1fr))" }}>
          <StatCard variant="hero" label="Won this month" value={formatMoney(31840000)} sub="Target R 450 000"
            chip={`needs ${formatMoney(820000)} a day`} progress={0.71} onClick={() => toast("Opens the won deals")} />
          <StatCard variant="tile" label="Open pipeline" value={formatMoney(126500000)} sub="23 deals" onClick={() => {}} />
          <StatCard variant="hot" label="Chats waiting on you" value="9" sub="oldest 2h ago" onClick={() => {}} />
          <StatCard label="Current KPI tile" value="33" sub="orders (7 days)" />
        </div>
      </Section>

      <Section title="Status chips">
        <div style={row}>
          <StatusChip tone="ok">Paid</StatusChip>
          <StatusChip tone="warm">Overdue</StatusChip>
          <StatusChip tone="highlight">{formatMoney(482050)} due</StatusChip>
          <StatusChip tone="info">Vula answered</StatusChip>
          <StatusChip tone="info">Vula filed</StatusChip>
          <StatusChip tone="warn">Needs review</StatusChip>
          <StatusChip tone="muted">Draft</StatusChip>
          <StatusChip outline tone="brand">Front house</StatusChip>
          <StatusChip outline>Back house</StatusChip>
          <StatusChip outline tone="brand">Visible to customers</StatusChip>
          <StatusChip outline>Staff only</StatusChip>
        </div>
      </Section>

      <Section title="List rows">
        <Card pad={6}>
          <ListRow initials="TM" title="Thandi Mokoena" detail="Do you have gluten-free bread for a party of 12?" trailing="17m" onClick={() => {}} />
          <ListRow Icon={ReceiptText} title="INV-2026-0118 · Thandi Mokoena" detail="Due 12 Oct" trailing={<StatusChip tone="warm">Overdue</StatusChip>} onClick={() => {}} />
          <ListRow Icon={FileText} title="Atlantic Dairy invoice BILL-0031" detail="Filed by Vula · 96% sure" trailing={<StatusChip tone="info">Vula filed</StatusChip>} onClick={() => {}} />
        </Card>
      </Section>

      <Section title="Filter bar + data table (stacked rows under 768px)">
        <FilterBar query={query} onQuery={setQuery} placeholder="Search invoices"
          chips={[{ id: "overdue", label: "Overdue", count: 1 }, { id: "sent", label: "Sent" }, { id: "draft", label: "Draft" }, { id: "paid", label: "Paid" }]}
          selected={chips} onToggle={(id) => setChips((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]))} />
        <DataTable caption="Invoices" rows={rows} onRowClick={() => setSheet(true)} empty="No invoices match these filters."
          initialSort={{ key: "due", dir: "asc" }}
          stack={{ title: (r) => `${r.no} · ${r.who}`, detail: (r) => `Due ${r.due}`, Icon: () => ReceiptText,
            trailing: (r) => <span className="vula-money">{formatMoney(r.cents)}</span> }}
          columns={[
            { key: "no", label: "Invoice", sortable: true, render: (r) => <span className="vula-mono">{r.no}</span> },
            { key: "who", label: "Customer", sortable: true },
            { key: "due", label: "Due", sortable: true },
            { key: "status", label: "Status", render: (r) => <StatusChip tone={STATUS[r.status][0]}>{STATUS[r.status][1]}</StatusChip> },
            { key: "cents", label: "Amount", align: "right", sortable: true, sortValue: (r) => r.cents,
              render: (r) => <span className="vula-money">{formatMoney(r.cents)}</span> },
          ]} />
      </Section>

      <Section title="Empty, loading">
        <div style={grid}>
          <EmptyState Icon={Users} title="No customers yet" action={<Button Icon={UserPlus}>Add a contact</Button>}>
            They appear here after their first chat.
          </EmptyState>
          <Card><Skeleton height={14} width="40%" /><Skeleton lines={3} style={{ marginTop: 12 }} /></Card>
        </div>
      </Section>

      <Section title="Sheet, command palette, toast">
        <div style={row}>
          <Button variant="secondary" Icon={Menu} onClick={() => setSheet(true)}>Open sheet</Button>
          <Button variant="secondary" Icon={Search} onClick={() => setPalette(true)}>Search (Ctrl K)</Button>
          <Button variant="secondary" onClick={() => toast("Invoice archived", "ok", { action: { label: "Undo", onClick: () => toast("Restored") } })}>
            Toast with undo</Button>
        </div>
      </Section>
      <div className="vui-toolbar-pad" />

      <Sheet open={sheet} title="Add" onClose={() => setSheet(false)}>
        <ListRow Icon={FilePlus2} title="New quote" detail="Drafted for you to approve" onClick={() => setSheet(false)} />
        <ListRow Icon={Wallet} title="Add expense" onClick={() => setSheet(false)} />
        <ListRow Icon={ScanLine} title="Scan a document" onClick={() => setSheet(false)} />
        <ListRow Icon={UserPlus} title="New contact" onClick={() => setSheet(false)} />
        <ListRow Icon={Megaphone} title="Broadcast" detail="Template messages only" onClick={() => setSheet(false)} />
      </Sheet>
      <CommandPalette open={palette} onClose={() => setPalette(false)} onAsk={(q) => toast(`Asked Vula: ${q}`)}
        sources={[
          { group: "Pages", items: [
            { id: "inbox", label: "Inbox", Icon: Inbox, onSelect: () => {} },
            { id: "invoices", label: "Invoices", Icon: ReceiptText, keywords: ["money", "billing"], onSelect: () => {} },
            { id: "settings", label: "Settings", Icon: Settings, onSelect: () => {} },
          ] },
          { group: "Contacts", items: [
            { id: "c1", label: "Thandi Mokoena", detail: "+27 82 000 0101", Icon: MessageSquare, onSelect: () => {} },
            { id: "c2", label: "Sipho Ndlovu", detail: "+27 82 000 0102", Icon: MessageSquare, onSelect: () => {} },
          ] },
        ]} />
      <FloatingToolbar items={[
        { id: "search", label: "Search", Icon: Search, onClick: () => setPalette(true) },
        { id: "add", label: "Add", Icon: Plus, onClick: () => setSheet(true), primary: true },
        { id: "today", label: "Today", Icon: Calendar, onClick: () => {} },
        { id: "inbox", label: "Inbox", Icon: Inbox, onClick: () => {}, count: 9 },
        { id: "more", label: "More", Icon: Menu, onClick: () => setSheet(true) },
      ]} />
    </main>
  );
}
