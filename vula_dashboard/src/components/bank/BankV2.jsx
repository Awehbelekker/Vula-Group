/**
 * BankV2.jsx — the redesigned Bank & matching (2026-10-19, UX redesign Phase F). Same data and
 * actions as the current screen (bank/useBank.js): the reconciliation summary, statement upload
 * and tools, a categorised-sheet import, filters, payee groups, and one card per bank line with
 * its category / project / trade / worker and its match actions. One column at every width; a
 * line's controls wrap under it, so nothing pushes the page sideways on a phone.
 */
import { useState } from "react";
import {
  AlertTriangle, ArrowDownLeft, ArrowUpRight, FileSpreadsheet, HardHat, Landmark, MessageCircle, RefreshCw, Settings2, Upload, Users,
} from "lucide-react";
import { formatMoney } from "../../lib/money";
import { Button, Card, EmptyState, Field, FilterBar, Input, PageHeader, Select, Sheet, StatCard, StatusChip } from "../ui/index.jsx";
import { BANK_FILTERS, fetchCandidates, useBank } from "./useBank";
import "./bank.css";

const money = (c) => formatMoney(c || 0, { cents: "always" });
// The hook's messages are shared with the current look; the redesign shows them without emoji.
const plain = (t) => String(t || "").replace(/[\u{1F300}-\u{1FAFF}☀-➿]️?/gu, "").replace(/\s+([.,])/g, "$1").trim();
const STATUS = { matched: ["Matched", "ok"], ignored: ["Ignored", "muted"], needs_input: ["Needs input", "warn"], unmatched: ["To review", "highlight"] };

export default function BankV2({ tenantId }) {
  const b = useBank(tenantId);
  const [tools, setTools] = useState(false);
  const s = b.sum && !b.sum.error ? b.sum : null;
  const toReview = s ? (s.unmatched_credits || 0) + (s.unmatched_debits || 0) : 0;
  const active = b.noProject ? "no_project" : (b.filter || "all");
  const chips = [...BANK_FILTERS.map(([v, l]) => ({ id: v || "all", label: l })), ...(b.hasProjects ? [{ id: "no_project", label: "Not on a project" }] : [])];

  return (
    <div className="vbr">
      <PageHeader sub="Vula reads your bank statements (and payment-confirmation emails), matches deposits to invoices or orders paid by EFT and marks them paid, and flags the rest."
        actions={<>
          <label className={`vbr-upload${b.busy ? " vbr-upload--busy" : ""}`}>
            <Upload size={16} aria-hidden="true" />{b.busy ? "Working…" : "Upload statement"}
            <input type="file" accept="application/pdf" className="vbr-file" disabled={b.busy} onChange={e => { b.upload(e.target.files[0]); e.target.value = ""; }} />
          </label>
          <Button variant="secondary" Icon={Settings2} onClick={() => setTools(true)}>Tools</Button>
        </>} />
      {b.msg && <p className="vbr-msg" role="status">{plain(b.msg)}</p>}

      {s && (
        <div className="vbr-stats">
          <StatCard variant="tile" label="Money in" value={money(s.money_in_cents)} color="var(--ok-text)" />
          <StatCard variant="tile" label="Money out" value={money(s.money_out_cents)} />
          <StatCard variant="tile" label="Invoices paid by matching" value={s.matched} sub="matched by Vula" />
          <StatCard variant="tile" label="Needs your input" value={s.needs_input || 0} sub="Vula unsure" color={(s.needs_input || 0) > 0 ? "var(--warn-text)" : undefined} />
          <StatCard variant="tile" label="To review" value={toReview} color={toReview > 0 ? "var(--warn-text)" : undefined} />
          <StatCard variant="tile" label="Invoiced, unpaid" value={money(s.invoices_sent_unpaid_cents)} sub="still owed" />
          <StatCard variant="tile" label="Invoiced, paid" value={money(s.invoices_paid_cents)} color="var(--ok-text)" />
        </div>
      )}

      {b.sheet && <SheetImport b={b} />}
      <datalist id="vbr-projects">{b.projectNames.map(p => <option key={p} value={p} />)}</datalist>

      <FilterBar chips={chips} selected={[active]} label="Show"
        onToggle={(id) => (id === "no_project" ? b.chooseNoProject() : b.chooseFilter(id === "all" ? "" : id))}>
        <div className="vbr-filter-extra">
          {b.accounts.length > 0 && (
            <Select aria-label="Category" value={b.cat} onChange={e => b.setCat(e.target.value)}>
              <option value="">All categories</option>
              {b.accounts.map(a => <option key={a.code} value={a.code}>{a.name}</option>)}
            </Select>)}
          <button type="button" className="vui-chip" aria-pressed={b.grouped} onClick={() => b.setGrouped(g => !g)}>
            <Users size={16} aria-hidden="true" />Group by payee
          </button>
        </div>
      </FilterBar>

      {b.grouped && (
        <div className="vbr-list">
          {b.groups.length === 0
            ? <p className="vbr-sub">{b.cat ? "Nothing in this category." : "Nothing waiting for review — pick a category to re-file it."}</p>
            : b.groups.map(g => <GroupCard key={g.key} g={g} b={b} />)}
        </div>
      )}

      {!b.grouped && (b.txns.length === 0
        ? <EmptyState Icon={Landmark} title="No bank lines yet">Upload a statement, or wait for the weekly email.</EmptyState>
        : <ul className="vbr-list" aria-label="Bank lines">{b.shown.map(t => <TxnCard key={t.id} t={t} b={b} tenantId={tenantId} />)}</ul>)}

      <Sheet open={tools} title="Statement tools" onClose={() => setTools(false)}>
        <div className="vbr-tools">
          <Field label="Statement password (ID number)" hint="Stored encrypted, so emailed statements unlock by themselves.">
            <div className="vbr-row">
              <Input type="password" className="vbr-grow" value={b.pw} onChange={e => b.setPw(e.target.value)} autoComplete="off" />
              <Button onClick={b.saveSettings}>Save</Button>
            </div>
          </Field>
          <label className="vbr-upload vbr-upload--secondary">
            <FileSpreadsheet size={16} aria-hidden="true" />Import a categorised sheet
            <input type="file" accept=".xlsx,.xlsm,.csv" className="vbr-file" disabled={b.busy} onChange={e => { b.pickSheet(e.target.files[0]); e.target.value = ""; setTools(false); }} />
          </label>
          <p className="vbr-sub">A statement you've already categorised in Excel — its categories, projects and trades are kept.</p>
          <Button variant="secondary" Icon={RefreshCw} disabled={b.busy} onClick={() => { b.recategorize(); setTools(false); }}>Re-run the AI on unallocated lines</Button>
          <Button variant="secondary" Icon={MessageCircle} onClick={() => { b.reviewOnWhatsApp(); setTools(false); }}>Review on WhatsApp</Button>
          <p className="vbr-sub">Vula asks you about each unallocated line on WhatsApp — reply to allocate it.</p>
          {b.msg && <p className="vbr-msg" role="status">{plain(b.msg)}</p>}
        </div>
      </Sheet>
    </div>
  );
}

function SheetImport({ b }) {
  const { sheet, setSheet } = b;
  const p = sheet.preview;
  return (
    <Card className="vbr-card">
      <h3 className="vbr-h"><FileSpreadsheet size={18} aria-hidden="true" />{sheet.name}</h3>
      <p className="vbr-sub">{p.lines} lines, {p.first} – {p.last} · in <span className="vula-money">{money(p.money_in_cents)}</span> · out <span className="vula-money">{money(p.money_out_cents)}</span></p>
      {(p.projects || []).length > 0 && <p className="vbr-sub">Which of your projects is each one? Vula suggested a name; change it to merge with an existing project.</p>}
      {(p.projects || []).map(pr => (
        <Field key={pr.label} label={`${pr.label} — ${pr.lines} lines · out ${money(pr.out)} · in ${money(pr.in)}`}>
          <Input list="vbr-projects" value={sheet.map[pr.label] || ""} onChange={e => setSheet({ ...sheet, map: { ...sheet.map, [pr.label]: e.target.value } })} />
        </Field>))}
      {p.existing_lines_in_period > 0 && (
        <label className="vbr-check">
          <input type="checkbox" checked={!!sheet.replace} onChange={e => setSheet({ ...sheet, replace: e.target.checked })} />
          <span>Vula already has {p.existing_lines_in_period} bank lines for {p.first} – {p.last} (read from PDF statements). Set them aside and use this sheet instead, so nothing is counted twice. They're kept, not deleted.</span>
        </label>)}
      <div className="vbr-row">
        <Button disabled={b.busy} onClick={b.importSheet}>{b.busy ? "Importing…" : "Import"}</Button>
        <Button variant="secondary" onClick={() => setSheet(null)}>Cancel</Button>
      </div>
    </Card>
  );
}

function TxnCard({ t, b, tenantId }) {
  const into = t.direction === "in";
  const [st, tone] = STATUS[t.match_status] || STATUS.unmatched;
  const open = t.match_status !== "matched" && t.match_status !== "ignored";
  const what = t.description || "this line";
  return (
    <li className="vbr-txn">
      <div className="vbr-top">
        <span className={`vbr-dir vbr-dir--${into ? "in" : "out"}`}>
          {into ? <ArrowDownLeft size={18} aria-hidden="true" /> : <ArrowUpRight size={18} aria-hidden="true" />}
          <span className="vbr-sr">{into ? "Money in" : "Money out"}</span>
          <strong className="vula-money">{money(t.amount_cents)}</strong>
        </span>
        <span className="vbr-sub">{t.txn_date || ""}{b.vatReg && t.vat_cents > 0 ? ` · VAT ${money(t.vat_cents)}` : ""}</span>
        <span className="vbr-status"><StatusChip tone={tone}>{st}</StatusChip></span>
      </div>
      <p className="vbr-desc">{t.description}</p>

      <div className="vbr-controls">
        {t.categorized_by === "default" && <StatusChip tone="warn"><AlertTriangle size={14} aria-hidden="true" />Confirm the category</StatusChip>}
        {b.accounts.length > 0 && (
          <Select aria-label={`Category for ${what}`} value={t.account_code || ""} onChange={e => b.categorize(t.id, e.target.value)}
            className={t.categorized_by === "default" ? "vbr-confirm" : ""}>
            <option value="">— category —</option>
            {b.accounts.map(a => <option key={a.code} value={a.code}>{a.name}</option>)}
          </Select>)}
        {b.hasProjects && !t.worker_id && (
          <>
            <Input list="vbr-projects" defaultValue={t.project || ""} placeholder="Project" aria-label={`Project for ${what}`}
              onBlur={e => e.target.value !== (t.project || "") && b.allocate(t.id, e.target.value, t.trade)} />
            {!into && <Input defaultValue={t.trade || ""} placeholder="Trade" aria-label={`Trade for ${what}`}
              onBlur={e => e.target.value !== (t.trade || "") && b.allocate(t.id, t.project, e.target.value)} />}
          </>)}
        {!into && b.workers.length > 0 && (t.worker_id
          ? <span className="vbr-worker"><HardHat size={16} aria-hidden="true" />{b.workerName(t.worker_id)}{t.project ? ` · ${t.project}` : ""}</span>
          : <Select aria-label={`Worker for ${what}`} defaultValue="" onChange={e => e.target.value && b.assignWorker(t.id, e.target.value)}>
              <option value="">— worker —</option>
              {b.workers.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
            </Select>)}
      </div>

      {open && (
        <div className="vbr-actions">
          <MatchPicker tenantId={tenantId} txn={t} onPick={(id) => b.act(t.id, "match", id)} />
          {into && (
            <Select aria-label={`Match ${what} to an order`} defaultValue="" onChange={e => e.target.value && b.act(t.id, "match", null, e.target.value)}>
              <option value="">Match order…</option>
              {b.pendingOrders.map(o => <option key={o.id} value={o.id}>{o.display_id} · {o.customer_name || "?"} · {money(o.total_cents)}</option>)}
            </Select>)}
          {!into && <Button size="sm" variant="secondary" onClick={() => b.act(t.id, "expense")}>Log expense</Button>}
          <Button size="sm" variant="quiet" onClick={() => b.act(t.id, "ignore")}>Ignore</Button>
        </div>
      )}
    </li>
  );
}

// The invoice (money in) or supplier bill (money out) a line settles, closest amount first —
// loaded when the box is opened. Only the amount on the line is booked as paid.
function MatchPicker({ tenantId, txn, onPick }) {
  const [opts, setOpts] = useState(null);
  const load = () => { if (!opts) fetchCandidates(tenantId, txn.id).then(setOpts); };
  const label = txn.direction === "in" ? "Match invoice…" : "Match supplier bill…";
  return (
    <Select aria-label={`${label.replace("…", "")} for ${txn.description || "this line"}`} defaultValue="" onFocus={load} onMouseDown={load}
      onChange={e => e.target.value && onPick(e.target.value)}>
      <option value="">{label}</option>
      {opts === null && <option disabled>Loading…</option>}
      {opts && opts.length === 0 && <option disabled>Nothing open to match</option>}
      {(opts || []).map(c => (
        <option key={c.id} value={c.id}>
          {c.exact ? "Exact · " : ""}{c.number || "—"} · {c.party || "?"} · {money(c.balance_cents)} owed{c.status === "part_paid" ? " (part paid)" : ""}
        </option>
      ))}
    </Select>
  );
}

function GroupCard({ g, b }) {
  const [form, setForm] = useState({ account_code: g.account_code || "", project: "", trade: "" });
  const set = (k) => (e) => setForm(f => ({ ...f, [k]: e.target.value }));
  return (
    <Card className="vbr-card">
      <div className="vbr-top">
        <strong className="vbr-desc">{g.key}</strong>
        <span className="vbr-sub">{g.count} payment{g.count === 1 ? "" : "s"} · <span className="vula-money">{money(g.total_cents)}</span>{g.oldest ? ` · ${g.oldest} – ${g.newest}` : ""}</span>
      </div>
      <div className="vbr-controls">
        <Select aria-label={`Category for ${g.key}`} value={form.account_code} onChange={set("account_code")}>
          <option value="">— category —</option>
          {b.accounts.map(a => <option key={a.code} value={a.code}>{a.name}</option>)}
        </Select>
        {b.hasProjects && (
          <>
            <Input list="vbr-projects" value={form.project} onChange={set("project")} placeholder="Project" aria-label={`Project for ${g.key}`} />
            <Input value={form.trade} onChange={set("trade")} placeholder="Trade" aria-label={`Trade for ${g.key}`} />
          </>)}
        <Button disabled={b.busy} onClick={() => b.applyGroup(g, form)}>Apply to all {g.count}</Button>
      </div>
    </Card>
  );
}
