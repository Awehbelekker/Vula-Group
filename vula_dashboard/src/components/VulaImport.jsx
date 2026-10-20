/**
 * VulaImport.jsx — 📥 historical onboarding: paste/upload old WhatsApp chats or emails, Vula
 * extracts the past orders, you review + edit, then commit them into the system.
 * Backend: /v1/commerce/{tenant}/admin/import/extract + /commit.
 */
import { useImport, useChatLearn } from "./import/useImport";
import ImportV2 from "./import/ImportV2";

const C = { surface: "var(--surface)", border: "var(--border)", green: "var(--accent)", red: "var(--danger)", text: "var(--text)", muted: "var(--muted)", alt: "var(--surface-alt)" };
const R = (n) => `R${(Number(n) || 0).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function VulaImport({ v2 = false, ...props }) {
  return v2 ? <ImportV2 {...props} /> : <ImportV1 {...props} />;
}

function ImportV1({ tenantId }) {
  const { mode, switchMode, text, setText, orders, setOrders, invoices, setInvoices, busy, msg, onFile, extract, commitInvoices, upd, drop, commit, dropInvoice } = useImport(tenantId);

  return (
    <div style={{ color: C.text, maxWidth: 820 }}>
      <h4 style={{ fontSize: 15, fontWeight: 600, margin: "2px 0 2px" }}>📥 Import your history</h4>
      <div style={{ display: "flex", gap: 6, margin: "6px 0" }}>
        {[["orders", "💬 Orders (chats/emails)"], ["invoices", "🧾 Old invoices (Xero export)"],
          ["learn", "🧠 Learn from WhatsApp chats"]].map(([v, l]) => (
          <button key={v} onClick={() => switchMode(v)}
            style={{ ...btn, ...(mode === v ? btnOn : {}) }}>{l}</button>
        ))}
      </div>
      {mode === "learn" ? <ChatLearn tenantId={tenantId} /> : <>
      <p style={{ color: C.muted, fontSize: 13, marginTop: 0 }}>
        {mode === "invoices"
          ? "Paste or upload your old invoice list (Xero: Business → Invoices → Export, or any pasted table). Vula registers your past invoice numbers so bank payments like “INVOICE 003983” match up — unpaid ones stay open, paid ones go straight to history."
          : "Paste an old WhatsApp chat or emails (or upload a WhatsApp chat export). Vula pulls out the past orders — you review and confirm before anything is added. Nothing is created until you commit."}
      </p>

      {!orders && !invoices && (
        <div style={card}>
          <textarea value={text} onChange={e => setText(e.target.value)} placeholder="Paste WhatsApp chat / emails here…"
            style={{ width: "100%", minHeight: 180, boxSizing: "border-box", padding: 10, border: `1px solid ${C.border}`, borderRadius: 8, fontFamily: "monospace", fontSize: 12.5, resize: "vertical" }} />
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8, flexWrap: "wrap" }}>
            <label style={{ ...btn, cursor: "pointer" }}>⬆ Upload {mode === "invoices" ? ".csv export" : ".txt export"}
              <input type="file" accept=".txt,.csv,text/plain,text/csv" style={{ display: "none" }} onChange={e => onFile(e.target.files[0])} />
            </label>
            <button style={{ ...btn, ...btnOn }} disabled={busy} onClick={extract}>{busy ? "Reading…" : mode === "invoices" ? "Extract invoices" : "Extract orders"}</button>
            {msg && <span style={{ fontSize: 12, color: C.green }}>{msg}</span>}
          </div>
        </div>
      )}

      {orders && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "4px 0 10px", flexWrap: "wrap" }}>
            <strong style={{ fontSize: 13 }}>{orders.length} order(s) found</strong>
            <button style={btn} onClick={() => setOrders(null)}>← Back to text</button>
            <button style={{ ...btn, ...btnOn, marginLeft: "auto" }} disabled={busy || !orders.length} onClick={commit}>
              {busy ? "Adding…" : `Commit ${orders.length} order(s)`}
            </button>
            {msg && <span style={{ fontSize: 12, color: C.green, width: "100%" }}>{msg}</span>}
          </div>
          {orders.length === 0 && <div style={{ color: C.muted, fontSize: 13 }}>No orders detected in that text.</div>}
          {orders.map((o, i) => (
            <div key={i} style={{ ...card, marginBottom: 8 }}>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <input value={o.customer_name || ""} onChange={e => upd(i, { customer_name: e.target.value })} placeholder="Customer" style={{ ...input, flex: 1, minWidth: 120 }} />
                <input value={o.customer_phone || ""} onChange={e => upd(i, { customer_phone: e.target.value })} placeholder="Phone" style={{ ...input, width: 120 }} />
                <input type="date" value={o.date || ""} onChange={e => upd(i, { date: e.target.value })} style={input} />
                <label style={chk}><input type="checkbox" checked={!!o.paid} onChange={e => upd(i, { paid: e.target.checked })} /> paid</label>
                <label style={chk}><input type="checkbox" checked={!!o.delivered} onChange={e => upd(i, { delivered: e.target.checked })} /> delivered</label>
                <button style={{ ...miniBtn, color: C.red, marginLeft: "auto" }} onClick={() => drop(i)}>Remove</button>
              </div>
              <div style={{ fontSize: 12, color: C.muted, marginTop: 6 }}>
                {(o.items || []).map((it, k) => `${it.quantity || 1}× ${it.name}${it.unit_price_rands ? ` @ ${R(it.unit_price_rands)}` : ""}`).join(", ") || "—"}
                {o.total_rands != null && <strong style={{ color: C.text }}> · {R(o.total_rands)}</strong>}
                {o.delivery_address && ` · ${o.delivery_address}`}
              </div>
            </div>
          ))}
        </>
      )}

      {invoices && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "4px 0 10px", flexWrap: "wrap" }}>
            <strong style={{ fontSize: 13 }}>{invoices.length} invoice(s) found</strong>
            <button style={btn} onClick={() => setInvoices(null)}>← Back</button>
            <button style={{ ...btn, ...btnOn, marginLeft: "auto" }} disabled={busy || !invoices.length} onClick={commitInvoices}>
              {busy ? "Registering…" : `Register ${invoices.length} invoice(s)`}
            </button>
            {msg && <span style={{ fontSize: 12, color: C.green, width: "100%" }}>{msg}</span>}
          </div>
          {invoices.length === 0 && <div style={{ color: C.muted, fontSize: 13 }}>No invoices detected in that text.</div>}
          <div style={{ ...card, padding: 0, overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
              <thead><tr style={{ textAlign: "left", color: C.muted, background: C.alt }}>
                {["Invoice #", "Customer", "Date", "Total", "Status", ""].map(h => <th key={h} style={{ padding: "8px 10px", fontWeight: 600 }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {invoices.map((v, i) => (
                  <tr key={i} style={{ borderTop: `1px solid ${C.border}` }}>
                    <td style={{ padding: "7px 10px", fontWeight: 600 }}>{v.invoice_number}</td>
                    <td style={{ padding: "7px 10px" }}>{v.customer_name || "—"}</td>
                    <td style={{ padding: "7px 10px" }}>{v.date || "—"}</td>
                    <td style={{ padding: "7px 10px" }}>{R(v.total_rands)}</td>
                    <td style={{ padding: "7px 10px", color: v.status === "paid" ? 'var(--ok)' : C.red, fontWeight: 600 }}>{v.status || "unpaid"}</td>
                    <td style={{ padding: "7px 10px" }}><button style={miniBtn} onClick={() => dropInvoice(i)}>Remove</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      </>}
    </div>
  );
}

/** Learn tone + common answers from WhatsApp exports (backend: vula/commerce/chat_learn.py).
 *  Answers wait for review in Settings › What Vula has learned; the chats aren't kept. */
function ChatLearn({ tenantId }) {
  const { files, busy, res, err, pick, learn } = useChatLearn(tenantId);

  return (
    <div>
      <p style={{ color: C.muted, fontSize: 13, marginTop: 0 }}>
        Upload exported WhatsApp chats with your customers (in WhatsApp: open a chat › More › Export chat ›
        Without media). Vula learns how you write and the questions you answer most. You review every answer
        before it's used, the tone is only a suggestion, and the chats themselves aren't kept — customers'
        names and numbers are taken out.
      </p>
      <div style={card}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <label style={{ ...btn, cursor: "pointer" }}>⬆ Choose chat exports (.txt or .zip)
            <input type="file" multiple accept=".txt,.zip,text/plain,application/zip" style={{ display: "none" }}
              onChange={e => pick(e.target.files)} />
          </label>
          <span style={{ fontSize: 12.5, color: C.muted }}>{files.length ? `${files.length} chat(s) ready` : "No chats chosen yet"}</span>
          <button style={{ ...btn, ...btnOn, marginLeft: "auto" }} disabled={busy || !files.length} onClick={() => learn()}>
            {busy ? "Reading your chats… (about a minute)" : "Learn from these chats"}
          </button>
        </div>
        {err && <div style={{ marginTop: 8, fontSize: 12.5, color: C.red }}>{err}</div>}
      </div>

      {res?.need_sender && (
        <div style={{ ...card, marginTop: 10 }}>
          <div style={{ fontSize: 13, marginBottom: 8 }}>Which of these is <b>you</b> (the business)?</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {res.need_sender.map(s => <button key={s} style={btn} disabled={busy} onClick={() => learn(s)}>{s}</button>)}
          </div>
        </div>
      )}

      {res && !res.need_sender && (
        <div style={{ ...card, marginTop: 10, fontSize: 13, lineHeight: 1.6 }}>
          <div>✓ Read <b>{res.chats}</b> chat(s), {res.messages} messages — you're “{res.business}”.</div>
          <div>🗣️ {res.voice_samples} of your messages added to learn your tone.
            {res.tone_suggested ? " A new tone suggestion is waiting in Settings › What Vula has learned." : " Not enough yet for a tone suggestion — add more chats."}</div>
          <div>💬 {res.answers_to_review} answer(s) to review (from {res.pairs_found} questions customers asked).
            Keep the right ones in <b>Settings › What Vula has learned</b> — only kept answers are used.</div>
        </div>
      )}
    </div>
  );
}

const card = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14 };
const btn = { padding: "8px 14px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: "pointer" };
const btnOn = { background: C.green, color: "var(--on-accent)", borderColor: C.green };
const miniBtn = { padding: "4px 10px", border: `1px solid ${C.border}`, borderRadius: 5, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
const input = { padding: "6px 9px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 12.5, background: C.surface, color: C.text};
const chk = { fontSize: 12, color: C.muted, display: "flex", alignItems: "center", gap: 3 };
