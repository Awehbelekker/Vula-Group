/**
 * VulaBankRec.jsx — bank reconciliation from the emailed statement (move off Xero, no API).
 * Upload/auto-ingest a Capitec statement → transactions auto-match to invoices (paid) + expenses.
 * Backend: /v1/commerce/{tenant}/admin/bank/* (migration 057).
 */
import { useState } from "react";
import { BANK_FILTERS, R, fetchCandidates, useBank } from "./bank/useBank";
import BankV2 from "./bank/BankV2";

const C = { surface: "var(--surface)", border: "var(--border)", green: "var(--accent)", red: "var(--danger)", text: "var(--text)", muted: "var(--muted)", alt: "var(--surface-alt)" };

export default function VulaBankRec({ tenantId, v2 = false }) {
  if (v2) return <BankV2 tenantId={tenantId} />;
  return <BankRecV1 tenantId={tenantId} />;
}

function BankRecV1({ tenantId }) {
  const {
    sum, txns, shown, pendingOrders, accounts, workers, vatReg, filter, chooseFilter, noProject, chooseNoProject,
    pw, setPw, busy, msg, cat, setCat, grouped, setGrouped, groups, hasProjects, projectNames, sheet, setSheet,
    categorize, assignWorker, workerName, allocate, applyGroup, pickSheet, importSheet, saveSettings, upload, recategorize,
    reviewOnWhatsApp, act,
  } = useBank(tenantId);

  return (
    <div style={{ color: C.text }}>
      <h4 style={{ fontSize: 15, fontWeight: 600, margin: "2px 0 2px" }}>🏦 Bank reconciliation</h4>
      <p style={{ color: C.muted, fontSize: 13, marginTop: 0 }}>
        Vula reads your bank statements (and payment-confirmation emails) and matches deposits to invoices or orders paid by EFT (marks them paid), flagging the rest. No accounting software needed.
      </p>

      {/* Summary */}
      {sum && !sum.error && (
        <div style={grid}>
          <Stat label="Money in" value={R(sum.money_in_cents)} color={'var(--ok)'} />
          <Stat label="Money out" value={R(sum.money_out_cents)} color={C.red} />
          <Stat label="Invoices → paid" value={sum.matched} sub="auto-matched" />
          <Stat label="Needs your input" value={sum.needs_input || 0} color={(sum.needs_input || 0) > 0 ? C.red : C.text} sub="Vula unsure" />
          <Stat label="To review" value={sum.unmatched_credits + sum.unmatched_debits} color={(sum.unmatched_credits + sum.unmatched_debits) > 0 ? C.red : C.text} />
          <Stat label="Invoiced, unpaid" value={R(sum.invoices_sent_unpaid_cents)} sub="still owed" />
          <Stat label="Invoiced, paid" value={R(sum.invoices_paid_cents)} color={'var(--ok)'} />
        </div>
      )}

      {/* Settings + upload */}
      <div style={{ ...card, background: C.alt, marginTop: 14, display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Statement password (ID number)</span>
        <input type="password" value={pw} onChange={e => setPw(e.target.value)} placeholder="stored encrypted" style={input} />
        <button style={{ ...btn, ...btnOn }} onClick={saveSettings}>Save</button>
        <label style={{ ...btn, cursor: "pointer" }}>
          {busy ? "Working…" : "⬆ Upload statement"}
          <input type="file" accept="application/pdf" style={{ display: "none" }} onChange={e => upload(e.target.files[0])} disabled={busy} />
        </label>
        <label style={{ ...btn, cursor: "pointer" }} title="A statement you've already categorised in Excel — categories, projects and trades are kept">
          ⬆ Import categorised sheet
          <input type="file" accept=".xlsx,.xlsm,.csv" style={{ display: "none" }} onChange={e => pickSheet(e.target.files[0])} disabled={busy} />
        </label>
        <button style={btn} onClick={recategorize} disabled={busy} title="Re-run the AI over everything still unallocated">🔁 Re-run AI</button>
        <button style={btn} onClick={reviewOnWhatsApp} title="Vula asks you about each unallocated item on WhatsApp — reply to allocate">📲 Review on WhatsApp</button>
        {msg && <span style={{ fontSize: 12, color: C.green, width: "100%" }}>{msg}</span>}
      </div>

      {sheet && (
        <div style={{ ...card, flexDirection: "column", alignItems: "stretch", marginTop: 12 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>{sheet.name}: {sheet.preview.lines} lines, {sheet.preview.first} – {sheet.preview.last} · in {R(sheet.preview.money_in_cents)} · out {R(sheet.preview.money_out_cents)}</div>
          {(sheet.preview.projects || []).length > 0 && <div style={{ fontSize: 12, color: C.muted, margin: "4px 0 6px" }}>Which of your projects is each one? (Vula suggested; change a name to merge it with an existing project.)</div>}
          {(sheet.preview.projects || []).map(p => (
            <div key={p.label} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 12.5, marginBottom: 4 }}>
              <span style={{ minWidth: 140 }}>{p.label} <span style={{ color: C.muted }}>({p.lines} lines · out {R(p.out)} · in {R(p.in)})</span></span>
              <span>→</span>
              <input list="bank-projects" value={sheet.map[p.label] || ""} style={{ ...input, fontSize: 12, padding: "4px 8px" }}
                onChange={e => setSheet({ ...sheet, map: { ...sheet.map, [p.label]: e.target.value } })} />
            </div>))}
          {sheet.preview.existing_lines_in_period > 0 && (
            <label style={{ fontSize: 12.5, display: "flex", gap: 6, alignItems: "flex-start", marginTop: 6 }}>
              <input type="checkbox" checked={!!sheet.replace} onChange={e => setSheet({ ...sheet, replace: e.target.checked })} />
              <span>Vula already has {sheet.preview.existing_lines_in_period} bank lines for {sheet.preview.first} – {sheet.preview.last} (read from PDF statements). Set them aside and use this sheet instead, so nothing is counted twice. They're kept, not deleted.</span>
            </label>)}
          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            <button style={{ ...btn, ...btnOn }} disabled={busy} onClick={importSheet}>{busy ? "Importing…" : "Import"}</button>
            <button style={btn} onClick={() => setSheet(null)}>Cancel</button>
          </div>
        </div>
      )}
      <datalist id="bank-projects">{projectNames.map(p => <option key={p} value={p} />)}</datalist>

      {/* Filter + transactions */}
      <div style={{ display: "flex", gap: 6, margin: "16px 0 8px" }}>
        {BANK_FILTERS.map(([v, l]) => (
          <button key={v} onClick={() => chooseFilter(v)} style={{ ...chip, ...(filter === v && !noProject ? chipOn : {}) }}>{l}</button>
        ))}
        {hasProjects && <button onClick={chooseNoProject} style={{ ...chip, ...(noProject ? chipOn : {}) }} title="Materials and labour paid but not put on a project yet">Not on a project</button>}
        {accounts.length > 0 && (
          <select value={cat} onChange={e => setCat(e.target.value)} title="Show one category"
            style={{ ...input, fontSize: 12, padding: "3px 6px", maxWidth: 190 }}>
            <option value="">All categories</option>
            {accounts.map(a => <option key={a.code} value={a.code}>{a.name}</option>)}
          </select>
        )}
        <button onClick={() => setGrouped(g => !g)} style={{ ...chip, ...(grouped ? chipOn : {}) }}
          title="Group payments by payee and set the category, project and trade for the whole group at once">Group by payee</button>
      </div>
      {grouped && (
        <div style={{ marginBottom: 12 }}>
          {groups.length === 0
            ? <div style={{ color: C.muted, fontSize: 13 }}>{cat ? "Nothing in this category." : "Nothing waiting for review — pick a category to re-file it."}</div>
            : groups.map(g => <GroupRow key={g.key} g={g} accounts={accounts} hasProjects={hasProjects} busy={busy} onApply={applyGroup} />)}
        </div>
      )}
      {txns.length === 0 ? <div style={{ color: C.muted, fontSize: 13 }}>No transactions yet — upload a statement or wait for the weekly email.</div>
        : grouped ? null : shown.map(t => (
          <div key={t.id} style={card}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: 13 }}>
                <span style={{ color: t.direction === "in" ? C.green : C.red }}>{t.direction === "in" ? "▲" : "▼"} {R(t.amount_cents)}</span>
                <span style={{ color: C.muted, fontWeight: 400 }}> · {t.txn_date || ""}</span>
                {vatReg && t.vat_cents > 0 && <span style={{ color: C.muted, fontWeight: 400, fontSize: 11 }}> · VAT {R(t.vat_cents)}</span>}
              </div>
              <div style={{ fontSize: 12, color: C.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.description}</div>
              <div style={{ display: "flex", gap: 6, marginTop: 3, flexWrap: "wrap", alignItems: "center" }}>
                {t.categorized_by === "default" && <span style={{ fontSize: 11, color: C.red }} title="Vula wasn't sure — please confirm the category">⚠ confirm</span>}
                {accounts.length > 0 && (
                  <select value={t.account_code || ""} onChange={e => categorize(t.id, e.target.value)}
                    style={{ ...input, fontSize: 11, padding: "3px 6px", maxWidth: 180, borderColor: t.categorized_by === "default" ? C.red : C.border }}
                    title={t.categorized_by ? `allocated by ${t.categorized_by}` : "allocate account"}>
                    <option value="">— category —</option>
                    {accounts.map(a => <option key={a.code} value={a.code}>{a.name}</option>)}
                  </select>
                )}
                {hasProjects && !t.worker_id && (
                  <>
                    <input list="bank-projects" defaultValue={t.project || ""} placeholder="project"
                      title={t.direction === "in" ? "Money in on a project, e.g. a payment certificate" : "Which project this was spent on"}
                      onBlur={e => e.target.value !== (t.project || "") && allocate(t.id, e.target.value, t.trade)}
                      style={{ ...input, fontSize: 11, padding: "3px 6px", width: 120 }} />
                    {t.direction === "out" && (
                      <input defaultValue={t.trade || ""} placeholder="trade"
                        onBlur={e => e.target.value !== (t.trade || "") && allocate(t.id, t.project, e.target.value)}
                        style={{ ...input, fontSize: 11, padding: "3px 6px", width: 120 }} />)}
                  </>
                )}
                {t.direction === "out" && workers.length > 0 && (
                  t.worker_id
                    ? <span style={{ fontSize: 11, color: C.green, alignSelf: "center" }}>👷 {workerName(t.worker_id)}{t.project ? ` · ${t.project}` : ""}</span>
                    : <select defaultValue="" onChange={e => e.target.value && assignWorker(t.id, e.target.value)}
                        style={{ ...input, fontSize: 11, padding: "3px 6px", maxWidth: 150 }}>
                        <option value="">— worker —</option>
                        {workers.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
                      </select>
                )}
              </div>
            </div>
            {t.match_status === "matched" ? <span style={{ ...pill, color: 'var(--ok)', borderColor: 'var(--ok)' }}>matched</span>
              : t.match_status === "ignored" ? <span style={{ ...pill, color: C.muted, borderColor: C.border }}>ignored</span>
                : (
                  <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                    <MatchPicker tenantId={tenantId} txn={t} onPick={(id) => act(t.id, "match", id)} />
                    {t.direction === "in" && (
                      <select defaultValue="" onChange={e => e.target.value && act(t.id, "match", null, e.target.value)} style={{ ...input, maxWidth: 150 }}>
                        <option value="">Match order…</option>
                        {pendingOrders.map(o => <option key={o.id} value={o.id}>{o.display_id} · {o.customer_name || "?"} · {R(o.total_cents)}</option>)}
                      </select>
                    )}
                    {t.direction === "out" && <button style={miniBtn} onClick={() => act(t.id, "expense")}>Log expense</button>}
                    <button style={miniBtn} onClick={() => act(t.id, "ignore")}>Ignore</button>
                  </div>
                )}
          </div>
        ))}
    </div>
  );
}

const Stat = ({ label, value, sub, color }) => (
  <div style={{ ...card, flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
    <div style={{ fontSize: 10, textTransform: "uppercase", color: C.muted, marginBottom: 3 }}>{label}</div>
    <div style={{ fontSize: 18, fontWeight: 700, color: color || C.text }}>{value}</div>
    {sub && <div style={{ fontSize: 10, color: C.muted }}>{sub}</div>}
  </div>
);

const grid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 10 };
const card = { display: "flex", alignItems: "center", gap: 10, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 12px", marginBottom: 8 };
const btn = { padding: "7px 12px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: "pointer" };
const btnOn = { background: C.green, color: "var(--on-accent)", borderColor: C.green };
const miniBtn = { padding: "4px 10px", border: `1px solid ${C.border}`, borderRadius: 5, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
const chip = { padding: "5px 12px", border: `1px solid ${C.border}`, borderRadius: 16, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
// The invoice (money in) or supplier bill (money out) a line settles, closest amount first —
// for the lines Vula couldn't match itself. Only the amount on the line is booked as paid.
function MatchPicker({ tenantId, txn, onPick }) {
  const [opts, setOpts] = useState(null);
  const load = () => {
    if (opts) return;
    fetchCandidates(tenantId, txn.id).then(setOpts);
  };
  const label = txn.direction === "in" ? "Match invoice…" : "Match supplier bill…";
  return (
    <select defaultValue="" onFocus={load} onMouseDown={load}
      onChange={e => e.target.value && onPick(e.target.value)} style={{ ...input, maxWidth: 190 }}>
      <option value="">{label}</option>
      {opts === null && <option disabled>Loading…</option>}
      {opts && opts.length === 0 && <option disabled>Nothing open to match</option>}
      {(opts || []).map(c => (
        <option key={c.id} value={c.id}>
          {c.exact ? "✓ " : ""}{c.number || "—"} · {c.party || "?"} · {R(c.balance_cents)} owed{c.status === "part_paid" ? " (part paid)" : ""}
        </option>
      ))}
    </select>
  );
}

function GroupRow({ g, accounts, hasProjects, busy, onApply }) {
  const [form, setForm] = useState({ account_code: g.account_code || "", project: "", trade: "" });
  const set = (k) => (e) => setForm(f => ({ ...f, [k]: e.target.value }));
  return (
    <div style={{ ...card, alignItems: "center", flexWrap: "wrap", gap: 8 }}>
      <div style={{ flex: 1, minWidth: 180 }}>
        <div style={{ fontWeight: 600, fontSize: 13 }}>{g.key}</div>
        <div style={{ fontSize: 12, color: C.muted }}>{g.count} payment{g.count === 1 ? "" : "s"} · {R(g.total_cents)}{g.oldest ? ` · ${g.oldest} – ${g.newest}` : ""}</div>
      </div>
      <select value={form.account_code} onChange={set("account_code")} style={{ ...input, fontSize: 11, padding: "3px 6px", maxWidth: 180 }}>
        <option value="">— category —</option>
        {accounts.map(a => <option key={a.code} value={a.code}>{a.name}</option>)}
      </select>
      {hasProjects && (
        <>
          <input list="bank-projects" value={form.project} onChange={set("project")} placeholder="project"
            style={{ ...input, fontSize: 11, padding: "3px 6px", width: 120 }} />
          <input value={form.trade} onChange={set("trade")} placeholder="trade"
            style={{ ...input, fontSize: 11, padding: "3px 6px", width: 120 }} />
        </>
      )}
      <button style={{ ...btn, ...btnOn }} disabled={busy} onClick={() => onApply(g, form)}>Apply to all {g.count}</button>
    </div>
  );
}

const chipOn = { background: C.green, color: "var(--on-accent)", borderColor: C.green };
const input = { padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, background: C.surface, color: C.text };
const pill = { fontSize: 11, padding: "2px 8px", borderRadius: 10, border: "1px solid", fontWeight: 600 };
