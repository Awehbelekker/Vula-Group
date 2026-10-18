/**
 * ExpensesV2.jsx — the redesigned Expenses & claims (2026-10-18, UX redesign Phase F). Same data
 * and actions as the current screen (expenses/useExpenses.js). The list shows each expense's
 * allocation read-only and flags what still needs a person ("Needs project", "Needs purpose");
 * opening one shows the same allocation fields (category, purpose, project, section, note) and
 * the same Approve / Mark repaid actions in a Sheet. A table on desktop, cards below 1200px.
 */
import { useState } from "react";
import { Banknote, Check, CircleAlert, CreditCard, MessageCircle, Plus, Receipt, Wallet, X } from "lucide-react";
import { formatMoney } from "../../lib/money";
import {
  Button, Card, DataTable, EmptyState, Field, FilterBar, IconButton, Input, PageHeader, Select, Sheet, Skeleton, StatCard, StatusChip,
} from "../ui/index.jsx";
import { addExpense, useExpenses } from "./useExpenses";
import "./expenses.css";

const STATUS = {
  unconfirmed: { label: "Not booked yet", tone: "muted" },
  pending: { label: "Pending", tone: "muted" },
  submitted: { label: "Submitted", tone: "info" },
  approved: { label: "Approved", tone: "highlight" },
  reimbursed: { label: "Reimbursed", tone: "ok" },
  paid: { label: "Paid", tone: "ok" },
  rejected: { label: "Rejected", tone: "danger" },
  cancelled: { label: "Cancelled", tone: "muted" },
};
const CHIPS = [
  { id: "submitted", label: "Submitted" }, { id: "approved", label: "Approved" },
  { id: "owed", label: "Reimbursable" }, { id: "reimbursed", label: "Reimbursed" },
];
const PAID_WITH = { company_card: "company card", personal: "personal card", cash: "cash" };
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const statusOf = (e) => STATUS[e.status] || { label: cap(e.status || "—"), tone: "muted" };

function who(e) {
  const how = PAID_WITH[e.paid_with];
  const card = e.paid_with === "company_card" && e.card_last4 ? ` •••• ${e.card_last4}` : "";
  return [e.paid_by_name || e.paid_by || "—", how ? `${how}${card}` : null].filter(Boolean).join(" · ");
}

/** What still needs a person on this expense — the amber "set…" of the current screen. */
function Needs({ e }) {
  const need = [e.project == null && "project", !e.purpose_category && "purpose"].filter(Boolean);
  if (!need.length) return null;
  return <StatusChip tone="warn"><CircleAlert size={13} aria-hidden="true" />Needs {need.join(" + ")}</StatusChip>;
}

function Actions({ e, x }) {
  const stop = (ev) => ev.stopPropagation();
  return (
    <span className="vex-actions" onClick={stop} onKeyDown={stop}>
      {e.status === "submitted" && <Button size="sm" variant="secondary" Icon={Check} onClick={() => x.setStatus(e.id, "approved")}>Approve</Button>}
      {e.reimbursable && e.status !== "reimbursed" &&
        <Button size="sm" Icon={Banknote} onClick={() => x.setStatus(e.id, "reimbursed")}>Mark repaid</Button>}
    </span>
  );
}

export default function ExpensesV2({ tenantId, defaultPaidBy }) {
  const x = useExpenses(tenantId, defaultPaidBy);
  const [adding, setAdding] = useState(false);
  const [openId, setOpenId] = useState(null);
  const open = x.rows.find((r) => r.id === openId) || null;
  const owedPeople = (x.rep?.by_person || []).filter((p) => p.owed_cents > 0);

  const columns = [
    { key: "date", label: "Date", sortable: true, render: (e) => <span className="vex-sub">{(e.date || "").slice(0, 10)}</span> },
    { key: "description", label: "What", sortable: true, render: (e) => (
      <span className="vex-what">
        <span>{e.description}{e.channel === "whatsapp" && <MessageCircle size={14} aria-label="from WhatsApp" className="vex-wa" />}</span>
        <span className="vex-sub">{[e.supplier, who(e)].filter(Boolean).join(" · ")}</span>
      </span>) },
    { key: "alloc", label: "Allocated to", render: (e) => (
      <span className="vex-what">
        <span>{[e.category, e.purpose_category && cap(e.purpose_category)].filter(Boolean).join(" · ") || "—"}</span>
        <span className="vex-sub">{e.project == null ? "" : e.project || "No project"}{e.section ? ` · ${e.section}` : ""}</span>
        <Needs e={e} />
      </span>) },
    { key: "status", label: "Status", render: (e) => <StatusChip tone={statusOf(e).tone}>{statusOf(e).label}</StatusChip> },
    { key: "amount_cents", label: "Amount", align: "right", sortable: true, render: (e) => (
      <span className="vex-amt"><span className="vula-money">{formatMoney(e.amount_cents, { cents: "always" })}</span>
        {e.vat_cents ? <span className="vex-sub">VAT {formatMoney(e.vat_cents, { cents: "always" })}</span> : null}</span>) },
    { key: "actions", label: <span className="vex-sr">Actions</span>, align: "right", render: (e) => <Actions e={e} x={x} /> },
  ];

  return (
    <div>
      <PageHeader sub="Receipts sent on WhatsApp appear here automatically."
        actions={<Button size="sm" Icon={adding ? X : Plus} variant={adding ? "secondary" : "primary"} onClick={() => setAdding((a) => !a)}>
          {adding ? "Close" : "Add expense"}</Button>} />

      {x.rep && (
        <div className="vex-stats">
          <StatCard variant="tile" label="Total spend" value={formatMoney(x.rep.total_cents || 0)} />
          <StatCard variant="tile" label="Input VAT" value={formatMoney(x.rep.vat_cents || 0)} />
          <StatCard variant={x.owed > 0 ? "hot" : "tile"} label="Owed to people" value={formatMoney(x.owed)} />
          <StatCard variant="tile" label="Claims" value={String(x.rep.count ?? 0)} />
        </div>
      )}

      {adding && <AddFormV2 tenantId={tenantId} projects={x.projects} sections={x.sections} onDone={() => { setAdding(false); x.load(); }} />}

      {owedPeople.length > 0 && (
        <Card pad={0} className="vex-owed">
          <h2 className="vex-h">Reimbursements owed</h2>
          {owedPeople.map((p, i) => (
            <div key={i} className="vex-owed-row"><span>{p.name}</span><span className="vula-money">{formatMoney(p.owed_cents, { cents: "always" })}</span></div>
          ))}
        </Card>
      )}

      <Card className="vex-cards">
        <span className="vex-cards-label"><CreditCard size={16} aria-hidden="true" />Company cards</span>
        {x.cards.filter((c) => c.active !== false).map((c) => (
          <span key={c.id} className="vex-card-chip">•••• {c.last4}
            <IconButton Icon={X} quiet label={`Remove card ending ${c.last4}`} className="vex-card-x" onClick={() => x.dropCard(c.id)} />
          </span>
        ))}
        <span className="vex-card-add">
          <Input value={x.newCard} onChange={(e) => x.setNewCard(e.target.value)} placeholder="Last 4 digits" maxLength={4} inputMode="numeric" aria-label="Company card last 4 digits" />
          <Button size="sm" variant="secondary" onClick={x.addCard}>Add</Button>
        </span>
        <p className="vex-sub vex-cards-note">Receipts paid on these are business money — matched to the bank statement, never owed back.</p>
      </Card>

      <FilterBar label="Show" chips={CHIPS} selected={x.filter === "all" ? [] : [x.filter]}
        onToggle={(id) => x.setFilter(x.filter === id ? "all" : id)} />
      {x.msg && <p className="vex-msg" role="status">{x.msg}</p>}

      {x.busy && !x.rows.length ? <Skeleton lines={5} height={18} /> : !x.rows.length ? (
        <EmptyState Icon={Receipt} title="No expenses yet">Snap a receipt on WhatsApp or add one.</EmptyState>
      ) : (
        <>
          <div className="vex-table">
            <DataTable columns={columns} rows={x.rows} caption="Expenses" onRowClick={(e) => setOpenId(e.id)} />
          </div>
          <div className="vex-list" role="list" aria-label="Expenses">
            {x.rows.map((e) => (
              <Card key={e.id} pad={0} role="listitem" className="vex-item">
                <button type="button" className="vex-item-main" onClick={() => setOpenId(e.id)}>
                  <span className="vex-item-top">
                    <span className="vex-item-what">{e.description}</span>
                    <span className="vula-money vex-item-amt">{formatMoney(e.amount_cents, { cents: "always" })}</span>
                  </span>
                  <span className="vex-sub">{[(e.date || "").slice(0, 10), e.supplier, who(e)].filter(Boolean).join(" · ")}</span>
                  <span className="vex-chips"><StatusChip tone={statusOf(e).tone}>{statusOf(e).label}</StatusChip><Needs e={e} /></span>
                </button>
                {(e.status === "submitted" || (e.reimbursable && e.status !== "reimbursed")) &&
                  <div className="vex-item-foot"><Actions e={e} x={x} /></div>}
              </Card>
            ))}
          </div>
        </>
      )}

      <Sheet open={!!open} title={open ? open.description : ""} onClose={() => setOpenId(null)}
        footer={open && (open.status === "submitted" || (open.reimbursable && open.status !== "reimbursed")) ? <Actions e={open} x={x} /> : null}>
        {open && <Allocate e={open} x={x} />}
      </Sheet>

      <p className="vex-sub vex-foot">
        Vula records and categorises the spend and keeps the receipt as your audit trail. It rolls into your VAT return, P&amp;L and project costs. PAYE and tax filing stay with your accountant.
      </p>
    </div>
  );
}

/** The allocation fields — each change saves straight away, as on the current screen. */
function Allocate({ e, x }) {
  return (
    <div className="vex-alloc">
      <dl className="vex-dl">
        <dt>Amount</dt><dd className="vula-money">{formatMoney(e.amount_cents, { cents: "always" })}{e.vat_cents ? ` (VAT ${formatMoney(e.vat_cents, { cents: "always" })})` : ""}</dd>
        <dt>Date</dt><dd>{(e.date || "").slice(0, 10) || "—"}</dd>
        {e.supplier && <><dt>Supplier</dt><dd>{e.supplier}</dd></>}
        <dt>Paid by</dt><dd>{who(e)}{e.reimbursable && e.status !== "reimbursed" ? " — to repay" : ""}</dd>
        <dt>Status</dt><dd><StatusChip tone={statusOf(e).tone}>{statusOf(e).label}</StatusChip></dd>
      </dl>
      <Field label="Category">
        <Select value={e.account_code || ""} onChange={(ev) => x.assign(e.id, { account_code: ev.target.value })} aria-label="Category">
          <option value="">{e.category || "—"}</option>
          {x.categories.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
        </Select>
      </Field>
      <Field label="Purpose" hint={e.purpose_detail || (e.purpose_category ? null : "Not set yet")}>
        <Select value={e.purpose_category || ""} onChange={(ev) => ev.target.value && x.assign(e.id, { purpose_category: ev.target.value })} aria-label="Purpose">
          <option value="">{e.purpose_category ? cap(e.purpose_category) : "Set a purpose…"}</option>
          {x.purposeCategories.filter((p) => p !== e.purpose_category).map((p) => <option key={p} value={p}>{cap(p)}</option>)}
        </Select>
      </Field>
      <Field label="Project" hint={e.project == null ? "Not set yet — choose one, or No project" : null}>
        <Select value={e.project || ""} onChange={(ev) => x.assign(e.id, { project: ev.target.value })} aria-label="Project">
          <option value="">{e.project == null ? "Set a project…" : "No project"}</option>
          {x.projects.map((p) => <option key={p} value={p}>{p}</option>)}
        </Select>
      </Field>
      <Field label="Section">
        <Select value={e.section || ""} onChange={(ev) => x.assign(e.id, { section: ev.target.value })} aria-label="Section">
          <option value="">No section</option>
          {x.sections.map((s) => <option key={s} value={s}>{s}</option>)}
        </Select>
      </Field>
      <Field label="Note for the accountant">
        <Input key={`n-${e.id}`} defaultValue={e.notes || ""} aria-label="Note for the accountant"
          onBlur={(ev) => { if (ev.target.value !== (e.notes || "")) x.assign(e.id, { notes: ev.target.value }); }} />
      </Field>
    </div>
  );
}

function AddFormV2({ tenantId, projects, sections, onDone }) {
  const [f, setF] = useState({ description: "", amount: "", supplier: "", date: "", project: "", section: "", paid_by_name: "", reimbursable: false });
  const [busy, setBusy] = useState(false);
  const s = (k, v) => setF((o) => ({ ...o, [k]: v }));
  const submit = async () => {
    if (!f.amount || !f.description) return;
    setBusy(true);
    await addExpense(tenantId, f);
    setBusy(false); onDone();
  };
  return (
    <Card className="vex-add">
      <Field label="What for"><Input value={f.description} onChange={(e) => s("description", e.target.value)} /></Field>
      <Field label="Amount (R)"><Input value={f.amount} onChange={(e) => s("amount", e.target.value)} inputMode="decimal" /></Field>
      <Field label="Supplier"><Input value={f.supplier} onChange={(e) => s("supplier", e.target.value)} /></Field>
      <Field label="Date"><Input type="date" value={f.date} onChange={(e) => s("date", e.target.value)} /></Field>
      <Field label="Paid by"><Input value={f.paid_by_name} onChange={(e) => s("paid_by_name", e.target.value)} /></Field>
      <Field label="Project">
        <Select value={f.project} onChange={(e) => s("project", e.target.value)}>
          <option value="">No project</option>{projects.map((p) => <option key={p} value={p}>{p}</option>)}
        </Select>
      </Field>
      <Field label="Section">
        <Select value={f.section} onChange={(e) => s("section", e.target.value)}>
          <option value="">No section</option>{sections.map((sec) => <option key={sec} value={sec}>{sec}</option>)}
        </Select>
      </Field>
      <label className="vex-check"><input type="checkbox" checked={f.reimbursable} onChange={(e) => s("reimbursable", e.target.checked)} /> Reimburse me</label>
      <div className="vex-add-go"><Button Icon={Wallet} disabled={busy || !f.amount || !f.description} onClick={submit}>{busy ? "Saving…" : "Save expense"}</Button></div>
    </Card>
  );
}
