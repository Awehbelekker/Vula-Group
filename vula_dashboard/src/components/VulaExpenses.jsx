/**
 * VulaExpenses.jsx — 💸 Expense claims: every business spend / out-of-pocket claim in one place.
 * Who paid, what for, VAT, which project, and reimbursement status (submitted → approved →
 * reimbursed). Receipts snapped on WhatsApp land here automatically; add manual ones too.
 * Backend: /v1/commerce/{tenant}/admin/expenses (+ /assign, /{id}/status, /reports/expenses).
 */
import { useEffect, useState } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", red: "#A23B2D", amber: "#B7791F", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const R = (c) => `R${((Number(c) || 0) / 100).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const api = (t, p, opts) => fetch(`${VULA_API}/v1/commerce/${t}/admin${p}`, opts).then(r => r.json());

const STATUS = {
  submitted: { label: "Submitted", color: C.muted },
  approved: { label: "Approved", color: C.amber },
  reimbursed: { label: "Reimbursed", color: C.green },
  paid: { label: "Paid", color: C.green },
  rejected: { label: "Rejected", color: C.red },
};

export default function VulaExpenses({ tenantId }) {
  const [rows, setRows] = useState([]);
  const [projects, setProjects] = useState([]);
  const [categories, setCategories] = useState([]);
  const [rep, setRep] = useState(null);
  const [filter, setFilter] = useState("all");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [adding, setAdding] = useState(false);
  const [cards, setCards] = useState([]);
  const [newCard, setNewCard] = useState("");

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 4000); };

  const load = async () => {
    setBusy(true);
    const q = filter === "owed" ? "?reimbursable=true" : filter === "all" ? "" : `?status=${filter}`;
    const [d, r, c] = await Promise.all([
      api(tenantId, `/expenses${q}`).catch(() => ({ expenses: [] })),
      api(tenantId, `/reports/expenses`).catch(() => null),
      api(tenantId, `/cards`).catch(() => ({ cards: [] })),
    ]);
    setRows(d.expenses || []);
    setProjects(d.projects || []);
    setCategories(d.categories || []);
    setRep(r);
    setCards(c.cards || []);
    setBusy(false);
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [tenantId, filter]);

  const addCard = async () => {
    const last4 = (newCard || "").replace(/\D/g, "").slice(-4);
    if (last4.length !== 4) return flash("Enter the card's last 4 digits.");
    await api(tenantId, `/cards`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ last4, label: "Company card" }),
    });
    setNewCard(""); flash("Company card saved — receipts on it are matched to the bank statement."); load();
  };
  const dropCard = async (id) => { await api(tenantId, `/cards/${id}`, { method: "DELETE" }); load(); };

  const setStatus = async (id, status) => {
    await api(tenantId, `/expenses/${id}/status`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
    });
    flash("Updated."); load();
  };
  const assign = async (id, patch) => {
    await api(tenantId, `/expenses/${id}/assign`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
    });
    flash("Allocated + learned."); load();
  };

  const owed = rep?.reimbursable_owed_cents || 0;

  return (
    <div style={{ fontFamily: "system-ui", color: C.text, maxWidth: 960 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h4 style={{ fontSize: 15, fontWeight: 600, margin: "2px 0" }}>💸 Expenses & claims</h4>
        <span style={{ fontSize: 12.5, color: C.muted }}>Receipts sent on WhatsApp appear here automatically.</span>
        <button style={{ ...btn, ...btnOn, marginLeft: "auto" }} onClick={() => setAdding(a => !a)}>{adding ? "Close" : "+ Add expense"}</button>
      </div>

      {/* Summary */}
      {rep && (
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", margin: "10px 0" }}>
          <Stat label="Total spend" value={R(rep.total_cents)} />
          <Stat label="Input VAT" value={R(rep.vat_cents)} />
          <Stat label="Owed to people" value={R(owed)} color={owed > 0 ? C.amber : C.muted} />
          <Stat label="Claims" value={rep.count} />
        </div>
      )}

      {adding && <AddForm tenantId={tenantId} projects={projects} onDone={() => { setAdding(false); load(); }} />}

      {/* Company card register — whose money decides reimbursement + bank matching */}
      <div style={{ ...card, margin: "0 0 10px", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <strong style={{ fontSize: 12.5 }}>💳 Company card(s):</strong>
        {cards.filter(c => c.active !== false).map(c => (
          <span key={c.id} style={{ fontSize: 12, background: C.alt, borderRadius: 12, padding: "3px 10px" }}>
            •••• {c.last4}
            <button style={{ ...miniBtn, marginLeft: 6, padding: "0 6px", border: "none", color: C.red }} onClick={() => dropCard(c.id)}>×</button>
          </span>
        ))}
        <input value={newCard} onChange={e => setNewCard(e.target.value)} placeholder="last 4 digits"
          style={{ ...input, width: 100 }} maxLength={4} />
        <button style={miniBtn} onClick={addCard}>Add</button>
        <span style={{ fontSize: 11, color: C.muted }}>Receipts paid on these are business money — auto-matched to the bank statement, never owed back.</span>
      </div>

      {/* Who's owed (reimbursements) */}
      {rep?.by_person?.some(p => p.owed_cents > 0) && (
        <div style={{ ...card, marginBottom: 10, background: "#FFF8EC" }}>
          <strong style={{ fontSize: 13 }}>Reimbursements owed</strong>
          {rep.by_person.filter(p => p.owed_cents > 0).map((p, i) => (
            <div key={i} style={{ fontSize: 13, marginTop: 4, display: "flex", gap: 8 }}>
              <span>{p.name}</span><strong style={{ color: C.amber }}>{R(p.owed_cents)}</strong>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", margin: "6px 0 10px" }}>
        {["all", "submitted", "approved", "owed", "reimbursed"].map(f => (
          <button key={f} onClick={() => setFilter(f)}
            style={{ ...chip, ...(filter === f ? chipOn : {}) }}>{f === "owed" ? "reimbursable" : f}</button>
        ))}
        {msg && <span style={{ fontSize: 12, color: C.green, alignSelf: "center" }}>{msg}</span>}
      </div>

      {/* Table */}
      <div style={{ ...card, padding: 0, overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
          <thead>
            <tr style={{ textAlign: "left", color: C.muted, background: C.alt }}>
              {["Date", "What", "Who paid", "Amount", "VAT", "Category", "Project", "Notes", "Status", ""].map(h =>
                <th key={h} style={th}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {busy && <tr><td colSpan={10} style={{ ...td, color: C.muted }}>Loading…</td></tr>}
            {!busy && !rows.length && <tr><td colSpan={10} style={{ ...td, color: C.muted }}>No expenses yet. Snap a receipt on WhatsApp or add one.</td></tr>}
            {rows.map(e => {
              const st = STATUS[e.status] || { label: e.status, color: C.muted };
              return (
                <tr key={e.id} style={{ borderTop: `1px solid ${C.border}` }}>
                  <td style={td}>{(e.date || "").slice(0, 10)}</td>
                  <td style={td}>{e.description}{e.supplier ? <span style={{ color: C.muted }}> · {e.supplier}</span> : null}
                    {e.channel === "whatsapp" && <span title="from WhatsApp"> 💬</span>}</td>
                  <td style={td}>
                    {e.paid_by_name || e.paid_by || "—"}
                    {e.paid_with === "company_card" && <span title={`company card ${e.card_last4 ? "•••• " + e.card_last4 : ""}`}> 💳</span>}
                    {e.paid_with === "personal" && <span title="personal card — reimburse"> 👛</span>}
                    {e.paid_with === "cash" && <span title="cash"> 💵</span>}
                    {e.reimbursable && e.status !== "reimbursed" && <span style={{ color: C.amber }}> ⏳</span>}
                  </td>
                  <td style={{ ...td, fontWeight: 600 }}>{R(e.amount_cents)}</td>
                  <td style={{ ...td, color: C.muted }}>{e.vat_cents ? R(e.vat_cents) : "—"}</td>
                  <td style={td}>
                    <select value={e.account_code || ""} onChange={ev => assign(e.id, { account_code: ev.target.value })}
                      style={{ ...miniInput, maxWidth: 130 }}>
                      <option value="">{e.category || "—"}</option>
                      {categories.map(c => <option key={c.code} value={c.code}>{c.name}</option>)}
                    </select>
                  </td>
                  <td style={td}>
                    {/* Distinguish still-pending (NULL, amber warning) from a real, saved "no
                        project" answer (empty string, neutral "No project" label) — both used
                        to look identical here, and "no project" never actually stuck (see
                        expenses.assign()'s 2026-08-12 fix). */}
                    <select value={e.project || ""} onChange={ev => assign(e.id, { project: ev.target.value })}
                      style={{ ...miniInput, maxWidth: 130, borderColor: (e.project == null) ? C.amber : C.border }}>
                      <option value="">{e.project == null ? "⚠ set…" : "No project"}</option>
                      {projects.map(p => <option key={p} value={p}>{p}</option>)}
                    </select>
                  </td>
                  <td style={td}>
                    <input defaultValue={e.notes || ""} placeholder="note for the accountant…"
                      onBlur={ev => { if (ev.target.value !== (e.notes || "")) assign(e.id, { notes: ev.target.value }); }}
                      style={{ ...miniInput, width: 130 }} />
                  </td>
                  <td style={{ ...td, color: st.color, fontWeight: 600 }}>{st.label}</td>
                  <td style={{ ...td, whiteSpace: "nowrap" }}>
                    {e.status === "submitted" && <button style={miniBtn} onClick={() => setStatus(e.id, "approved")}>Approve</button>}
                    {e.reimbursable && e.status !== "reimbursed" && <button style={{ ...miniBtn, color: C.green }} onClick={() => setStatus(e.id, "reimbursed")}>Mark repaid</button>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p style={{ fontSize: 11.5, color: C.muted, marginTop: 8 }}>
        Vula records + categorises the spend and keeps the receipt as your audit trail. It rolls into your VAT return, P&amp;L and project costs. PAYE/tax filing stays with your accountant.
      </p>
    </div>
  );
}

function AddForm({ tenantId, projects, onDone }) {
  const [f, setF] = useState({ description: "", amount: "", supplier: "", date: "", project: "", paid_by_name: "", reimbursable: false });
  const [busy, setBusy] = useState(false);
  const s = (k, v) => setF(o => ({ ...o, [k]: v }));
  const submit = async () => {
    if (!f.amount || !f.description) return;
    setBusy(true);
    await api(tenantId, "/expenses", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        amount_cents: Math.round(parseFloat(f.amount) * 100), description: f.description,
        supplier: f.supplier || null, date: f.date || null, project: f.project || null,
        paid_by_name: f.paid_by_name || null, reimbursable: f.reimbursable,
      }),
    });
    setBusy(false); onDone();
  };
  return (
    <div style={{ ...card, marginBottom: 10, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
      <input placeholder="What for" value={f.description} onChange={e => s("description", e.target.value)} style={{ ...input, flex: 1, minWidth: 140 }} />
      <input placeholder="Amount (R)" value={f.amount} onChange={e => s("amount", e.target.value)} style={{ ...input, width: 100 }} />
      <input placeholder="Supplier" value={f.supplier} onChange={e => s("supplier", e.target.value)} style={{ ...input, width: 130 }} />
      <input type="date" value={f.date} onChange={e => s("date", e.target.value)} style={input} />
      <input placeholder="Paid by" value={f.paid_by_name} onChange={e => s("paid_by_name", e.target.value)} style={{ ...input, width: 110 }} />
      <select value={f.project} onChange={e => s("project", e.target.value)} style={input}>
        <option value="">No project</option>
        {projects.map(p => <option key={p} value={p}>{p}</option>)}
      </select>
      <label style={{ fontSize: 12, color: C.muted, display: "flex", alignItems: "center", gap: 3 }}>
        <input type="checkbox" checked={f.reimbursable} onChange={e => s("reimbursable", e.target.checked)} /> reimburse me
      </label>
      <button style={{ ...btn, ...btnOn }} disabled={busy} onClick={submit}>{busy ? "Saving…" : "Save"}</button>
    </div>
  );
}

const Stat = ({ label, value, color }) => (
  <div style={{ ...card, padding: "8px 14px", minWidth: 110 }}>
    <div style={{ fontSize: 11, color: C.muted }}>{label}</div>
    <div style={{ fontSize: 16, fontWeight: 700, color: color || C.text }}>{value}</div>
  </div>
);

const card = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 12 };
const btn = { padding: "7px 13px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: "pointer" };
const btnOn = { background: C.green, color: "#fff", borderColor: C.green };
const chip = { padding: "5px 11px", border: `1px solid ${C.border}`, borderRadius: 20, background: C.surface, color: C.muted, fontSize: 12, cursor: "pointer", textTransform: "capitalize" };
const chipOn = { background: C.text, color: "#fff", borderColor: C.text };
const miniBtn = { padding: "3px 9px", border: `1px solid ${C.border}`, borderRadius: 5, background: C.surface, color: C.text, fontSize: 11.5, cursor: "pointer", marginRight: 4 };
const input = { padding: "6px 9px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12.5, background: C.surface, color: C.text, fontFamily: "system-ui" };
const miniInput = { padding: "3px 6px", border: `1px solid ${C.border}`, borderRadius: 5, fontSize: 12, background: C.surface, color: C.text };
const th = { padding: "8px 10px", fontWeight: 600, whiteSpace: "nowrap" };
const td = { padding: "8px 10px", verticalAlign: "middle" };
