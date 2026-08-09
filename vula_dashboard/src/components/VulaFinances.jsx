/**
 * VulaFinances.jsx — money in/out per project, budget-vs-actual, built from filed
 * invoices/payments. Payments reconciled to invoices (matched, not guessed).
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", red: "#A23B2D", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const rand = (n) => "R" + (Number(n) || 0).toLocaleString("en-ZA", { maximumFractionDigits: 0 });

function Cell({ label, value, sub, color }) {
  const C = { surface: "#FFFFFF", border: "#DDD8CE", muted: "#8A8680", text: "#2A2A2A" };
  return (
    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 10px" }}>
      <div style={{ fontSize: 10, textTransform: "uppercase", color: C.muted, marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 700, color: color || C.text }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: C.muted, marginTop: 1 }}>{sub}</div>}
    </div>
  );
}

export default function VulaFinances({ tenantId }) {
  const [data, setData] = useState({ projects: [], transactions: [], total_in: 0, total_out: 0 });
  const [editing, setEditing] = useState(null);
  const [budget, setBudget] = useState("");
  const [openProj, setOpenProj] = useState(null);
  const [detail, setDetail] = useState(null);
  const [tb, setTb] = useState(null);
  const [tbOpen, setTbOpen] = useState(false);

  const loadTrialBalance = useCallback(async () => {
    if (!tenantId) return;
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/reports/trial-balance`);
    setTb(await r.json());
  }, [tenantId]);

  const load = useCallback(async () => {
    if (!tenantId) return;
    const r = await fetch(`${VULA_API}/v1/projects/${tenantId}/finances`);
    setData(await r.json());
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const [contractDraft, setContractDraft] = useState("");
  const [claims, setClaims] = useState([]);
  const [newClaim, setNewClaim] = useState({ value: "", retention: "5" });
  const [claimBusy, setClaimBusy] = useState(false);
  const [invoicing, setInvoicing] = useState(null); // { claimId, name, phone, email, address }

  const loadClaims = async (project) => {
    const r = await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/claims`).then(r => r.json()).catch(() => null);
    setClaims(r?.claims || []);
  };

  const openDetail = async (project) => {
    if (openProj === project) { setOpenProj(null); return; }
    setOpenProj(project); setDetail(null); setContractDraft(""); setClaims([]); setInvoicing(null);
    const r = await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/financials`).then(r => r.json()).catch(() => null);
    setDetail(r);
    loadClaims(project);
  };

  const refreshProject = async (project) => {
    const r = await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/financials`).then(r => r.json()).catch(() => null);
    setDetail(r);
    loadClaims(project);
  };

  const addClaim = async (project) => {
    const value = Number(newClaim.value);
    if (!value) return;
    setClaimBusy(true);
    const d = await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/claims`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cumulative_value: value, retention_pct: Number(newClaim.retention) || 5 }),
    }).then(r => r.json()).catch(() => ({}));
    setClaimBusy(false);
    if (d.claim) { setNewClaim({ value: "", retention: "5" }); refreshProject(project); }
    else alert(d.detail || d.error || "Could not create claim.");
  };

  const certifyClaim = async (project, claimId) => {
    await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/claims/${claimId}/certify`, { method: "POST" });
    refreshProject(project);
  };

  const submitInvoice = async (project) => {
    if (!invoicing?.name) return;
    setClaimBusy(true);
    const d = await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/claims/${invoicing.claimId}/invoice`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        customer_name: invoicing.name, customer_phone: invoicing.phone || undefined,
        customer_email: invoicing.email || undefined, customer_address: invoicing.address || undefined,
      }),
    }).then(r => r.json()).catch(() => ({}));
    setClaimBusy(false);
    if (d.invoice) { alert(`Invoice ${d.invoice.invoice_number || ""} created for this claim.`); setInvoicing(null); refreshProject(project); }
    else alert(d.detail || "Could not create invoice.");
  };

  const saveContract = async (project) => {
    const total = Number(contractDraft);
    if (!total) return;
    await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/boq`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ total }),
    });
    setContractDraft("");
    const r = await fetch(`${VULA_API}/v1/projects/${tenantId}/p/${encodeURIComponent(project)}/financials`).then(r => r.json()).catch(() => null);
    setDetail(r); load();
  };

  const saveBudget = async (project) => {
    await fetch(`${VULA_API}/v1/projects/${tenantId}/budget`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, budget: Number(budget) || 0 }),
    });
    setEditing(null); setBudget(""); load();
  };

  return (
    <div style={{ maxWidth: 1000, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 28, fontWeight: 700, color: C.text, margin: "0 0 2px" }}>Finances</h1>
      <p style={{ fontSize: 13, color: C.muted, margin: "0 0 18px" }}>Money in/out per project — built from invoices & payments Vula files. Payments are matched to invoices, not guessed.</p>

      <div style={{ display: "flex", gap: 12, marginBottom: 18 }}>
        <div style={{ flex: 1, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16 }}>
          <div style={{ fontSize: 12, color: C.muted }}>Money in</div>
          <div style={{ fontSize: 24, fontWeight: 700, color: C.green }}>{rand(data.total_in)}</div>
        </div>
        <div style={{ flex: 1, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16 }}>
          <div style={{ fontSize: 12, color: C.muted }}>Money out</div>
          <div style={{ fontSize: 24, fontWeight: 700, color: C.red }}>{rand(data.total_out)}</div>
        </div>
        <div style={{ flex: 1, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16 }}>
          <div style={{ fontSize: 12, color: C.muted }}>Net</div>
          <div style={{ fontSize: 24, fontWeight: 700, color: C.text }}>{rand(data.total_in - data.total_out)}</div>
        </div>
      </div>

      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden", marginBottom: 20 }}>
        <div style={{ padding: "10px 16px", display: "grid", gridTemplateColumns: "1.6fr 1fr 1fr 1fr 1.2fr", gap: 8, fontSize: 11, color: C.muted, textTransform: "uppercase", background: C.alt }}>
          <span>Project</span><span>In</span><span>Out</span><span>Budget</span><span>Remaining</span>
        </div>
        {data.projects.length === 0 && <div style={{ padding: 16, fontSize: 13, color: C.muted }}>No financial documents filed yet.</div>}
        {data.projects.map((p) => (
          <div key={p.project}>
            <div style={{ padding: "11px 16px", borderTop: `1px solid ${C.alt}`, display: "grid", gridTemplateColumns: "1.6fr 1fr 1fr 1fr 1.2fr", gap: 8, alignItems: "center", fontSize: 13 }}>
              <span onClick={() => openDetail(p.project)} style={{ fontWeight: 600, color: C.text, cursor: "pointer" }}>
                <span style={{ color: C.muted, marginRight: 4, display: "inline-block", transform: openProj === p.project ? "rotate(90deg)" : "none", transition: "transform .15s" }}>›</span>
                {p.project} <span style={{ color: C.muted, fontWeight: 400, fontSize: 11 }}>· {p.count}</span>
              </span>
              <span style={{ color: C.green }}>{rand(p.in)}</span>
              <span style={{ color: C.red }}>{rand(p.out)}</span>
              <span>
                {editing === p.project
                  ? <input autoFocus value={budget} onChange={(e) => setBudget(e.target.value)} onBlur={() => saveBudget(p.project)} onKeyDown={(e) => e.key === "Enter" && saveBudget(p.project)} placeholder="0" style={{ width: 80, padding: "3px 6px", border: `1px solid ${C.border}`, borderRadius: 5, fontSize: 12 }} />
                  : <span onClick={() => { setEditing(p.project); setBudget(p.budget || ""); }} style={{ cursor: "pointer", color: p.budget ? C.text : C.muted, borderBottom: `1px dotted ${C.muted}` }}>{p.budget ? rand(p.budget) : "set"}</span>}
              </span>
              <span style={{ color: p.remaining != null && p.remaining < 0 ? C.red : C.text }}>{p.remaining != null ? rand(p.remaining) : "—"}</span>
            </div>
            {openProj === p.project && (
              <div style={{ padding: "12px 16px 16px", background: C.alt, borderTop: `1px solid ${C.border}` }}>
                {!detail ? <span style={{ fontSize: 12, color: C.muted }}>Loading…</span> : (
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8 }}>
                    <Cell label="Contract / budget" value={rand(detail.contract)} />
                    <Cell label="Invoiced" value={rand(detail.invoiced)} sub={`${detail.invoice_count} invoice(s)`} />
                    <Cell label="Outstanding" value={rand(detail.outstanding)} color={detail.outstanding > 0 ? C.red : C.text} sub="billed, unpaid" />
                    <Cell label="Paid in" value={rand(detail.paid_in)} color={C.green} />
                    <Cell label="Spent" value={rand(detail.spent)} color={C.red} sub="cash out + expenses" />
                    <Cell label="Net" value={rand(detail.net)} />
                    <Cell label="Budget left" value={detail.remaining != null ? rand(detail.remaining) : "—"} color={detail.remaining != null && detail.remaining < 0 ? C.red : C.text} />
                  </div>
                )}
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10 }}>
                  <span style={{ fontSize: 12, color: C.muted }}>Set contract value (from your BoQ):</span>
                  <input value={contractDraft} onChange={e => setContractDraft(e.target.value)} placeholder="R total"
                    style={{ width: 120, padding: "5px 8px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12 }} />
                  <button onClick={() => saveContract(p.project)} style={{ padding: "5px 12px", border: "none", borderRadius: 6, background: C.green, color: "#fff", fontSize: 12, cursor: "pointer" }}>Save</button>
                </div>
                <div style={{ fontSize: 11, color: C.muted, marginTop: 8 }}>Unified from invoices, expenses and filed payments. Contract value persists (survives restarts) and drives budget-remaining; link invoices to a project on the invoice form.</div>

                <div style={{ marginTop: 16, paddingTop: 12, borderTop: `1px solid ${C.border}` }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 8 }}>Progress claims (interim payment certificates)</div>
                  {claims.length > 0 && (
                    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden", marginBottom: 10 }}>
                      <div style={{ padding: "6px 10px", display: "grid", gridTemplateColumns: "40px 1fr 1fr 1fr 1fr 1.4fr", gap: 6, fontSize: 10, color: C.muted, textTransform: "uppercase", background: C.alt }}>
                        <span>#</span><span>Work to date</span><span>Retention</span><span>Certified</span><span>This payment</span><span></span>
                      </div>
                      {claims.map(c => (
                        <div key={c.id} style={{ padding: "8px 10px", borderTop: `1px solid ${C.alt}`, display: "grid", gridTemplateColumns: "40px 1fr 1fr 1fr 1fr 1.4fr", gap: 6, alignItems: "center", fontSize: 12 }}>
                          <span>{c.claim_number}</span>
                          <span>{rand(c.cumulative_value_cents / 100)}</span>
                          <span style={{ color: C.muted }}>{rand(c.retention_cents / 100)} ({c.retention_pct}%)</span>
                          <span>{rand(c.certified_to_date_cents / 100)}</span>
                          <span style={{ fontWeight: 600, color: C.green }}>{rand(c.this_payment_cents / 100)}</span>
                          <span style={{ display: "flex", gap: 4, justifyContent: "flex-end", alignItems: "center" }}>
                            <span style={{ fontSize: 10, color: C.muted, textTransform: "uppercase" }}>{c.status}</span>
                            {c.status === "draft" && (
                              <button onClick={() => certifyClaim(p.project, c.id)} style={{ padding: "3px 8px", border: `1px solid ${C.border}`, borderRadius: 5, background: "#fff", fontSize: 11, cursor: "pointer" }}>Certify</button>
                            )}
                            {c.status === "certified" && !c.linked_invoice_id && (
                              <button onClick={() => setInvoicing({ claimId: c.id, name: "", phone: "", email: "", address: "" })} style={{ padding: "3px 8px", border: "none", borderRadius: 5, background: C.green, color: "#fff", fontSize: 11, cursor: "pointer" }}>Invoice</button>
                            )}
                            {c.linked_invoice_id && <span style={{ fontSize: 10, color: C.green }}>✓ invoiced</span>}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}

                  {invoicing && (
                    <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: 10, marginBottom: 10, display: "flex", flexDirection: "column", gap: 6 }}>
                      <span style={{ fontSize: 11, color: C.muted }}>Create an invoice for this claim's net payment:</span>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        <input placeholder="Customer name" value={invoicing.name} onChange={e => setInvoicing({ ...invoicing, name: e.target.value })} style={{ flex: 1, minWidth: 120, padding: "5px 8px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12 }} />
                        <input placeholder="Phone (optional)" value={invoicing.phone} onChange={e => setInvoicing({ ...invoicing, phone: e.target.value })} style={{ flex: 1, minWidth: 100, padding: "5px 8px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12 }} />
                        <input placeholder="Email (optional)" value={invoicing.email} onChange={e => setInvoicing({ ...invoicing, email: e.target.value })} style={{ flex: 1, minWidth: 120, padding: "5px 8px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12 }} />
                      </div>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button disabled={claimBusy || !invoicing.name} onClick={() => submitInvoice(p.project)} style={{ padding: "5px 12px", border: "none", borderRadius: 6, background: C.green, color: "#fff", fontSize: 12, cursor: "pointer" }}>{claimBusy ? "Creating…" : "Create invoice"}</button>
                        <button onClick={() => setInvoicing(null)} style={{ padding: "5px 12px", border: `1px solid ${C.border}`, borderRadius: 6, background: "#fff", fontSize: 12, cursor: "pointer" }}>Cancel</button>
                      </div>
                    </div>
                  )}

                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <span style={{ fontSize: 12, color: C.muted }}>New claim — total value of work done to date:</span>
                    <input value={newClaim.value} onChange={e => setNewClaim({ ...newClaim, value: e.target.value })} placeholder="R total" style={{ width: 110, padding: "5px 8px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12 }} />
                    <span style={{ fontSize: 12, color: C.muted }}>Retention %</span>
                    <input value={newClaim.retention} onChange={e => setNewClaim({ ...newClaim, retention: e.target.value })} style={{ width: 50, padding: "5px 8px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12 }} />
                    <button disabled={claimBusy || !newClaim.value} onClick={() => addClaim(p.project)} style={{ padding: "5px 12px", border: "none", borderRadius: 6, background: C.green, color: "#fff", fontSize: 12, cursor: "pointer" }}>{claimBusy ? "Saving…" : "Add claim"}</button>
                  </div>
                  <div style={{ fontSize: 11, color: C.muted, marginTop: 6 }}>Enter the CUMULATIVE value of all work completed to date, not just this period — retention and the actual payment due are calculated automatically from the previous claim.</div>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      <h3 style={{ fontSize: 14, color: C.text, margin: "0 0 8px" }}>Recent transactions</h3>
      <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden" }}>
        {data.transactions.slice(0, 25).map((t) => (
          <div key={t.id} style={{ padding: "9px 16px", borderTop: `1px solid ${C.alt}`, display: "grid", gridTemplateColumns: "70px 1.4fr 1fr 90px 70px", gap: 8, alignItems: "center", fontSize: 12.5 }}>
            <span style={{ color: t.direction === "in" ? C.green : t.direction === "out" ? C.red : C.muted, fontWeight: 600 }}>{t.direction === "in" ? "▲ in" : t.direction === "out" ? "▼ out" : "•"}</span>
            <span style={{ color: C.text }}>{t.counterparty || t.filename} <span style={{ color: C.muted }}>{t.description ? `· ${String(t.description).slice(0, 40)}` : ""}</span></span>
            <span style={{ color: C.muted }}>{t.project || "—"}</span>
            <span style={{ fontWeight: 600, color: C.text }}>{rand(t.amount)}</span>
            <span style={{ fontSize: 10, color: t.reconciled ? C.green : C.muted }}>{t.reconciled ? "✓ matched" : t.kind}</span>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 20 }}>
        <span onClick={() => { setTbOpen(!tbOpen); if (!tb) loadTrialBalance(); }} style={{ fontSize: 12, color: C.muted, cursor: "pointer", borderBottom: `1px dotted ${C.muted}` }}>
          {tbOpen ? "▾" : "▸"} General ledger (trial balance)
        </span>
        {tbOpen && (
          <div style={{ marginTop: 8, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden" }}>
            {tb?.error && <div style={{ padding: 12, fontSize: 12, color: C.muted }}>{tb.error}</div>}
            {tb && !tb.error && tb.accounts.length === 0 && <div style={{ padding: 12, fontSize: 12, color: C.muted }}>No journal entries yet — these post automatically as orders/invoices get paid and expenses are logged.</div>}
            {tb && !tb.error && tb.accounts.length > 0 && (
              <>
                <div style={{ padding: "8px 16px", display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr", gap: 8, fontSize: 11, color: C.muted, textTransform: "uppercase", background: C.alt }}>
                  <span>Account</span><span>Debit</span><span>Credit</span>
                </div>
                {tb.accounts.map((a) => (
                  <div key={a.code} style={{ padding: "7px 16px", borderTop: `1px solid ${C.alt}`, display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr", gap: 8, fontSize: 12.5 }}>
                    <span>{a.name} <span style={{ color: C.muted, fontSize: 10 }}>· {a.type}</span></span>
                    <span>{a.debit_cents ? rand(a.debit_cents) : "—"}</span>
                    <span>{a.credit_cents ? rand(a.credit_cents) : "—"}</span>
                  </div>
                ))}
                <div style={{ padding: "8px 16px", borderTop: `1px solid ${C.border}`, display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr", gap: 8, fontSize: 12.5, fontWeight: 700, background: C.alt }}>
                  <span>Total</span>
                  <span>{rand(tb.total_debit_cents)}</span>
                  <span style={{ color: tb.total_debit_cents === tb.total_credit_cents ? C.green : C.red }}>{rand(tb.total_credit_cents)}</span>
                </div>
              </>
            )}
          </div>
        )}
      </div>
      <p style={{ textAlign: "center", fontSize: 11, color: "#B5B0A8", marginTop: 20 }}>Powered by Vula · figures from filed invoices & payments</p>
    </div>
  );
}
