/**
 * VulaWATemplates.jsx — 📨 WhatsApp template manager: create, submit, and track approval status
 * for the templates Vula needs to message people outside the 24h conversation window (proactive
 * notifications, opt-in campaigns). Backend: /v1/commerce/{tenant}/admin/wa-templates.
 */
import { useEffect, useState } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", red: "#A23B2D", amber: "#B7791F", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const api = (t, p, opts) => fetch(`${VULA_API}/v1/commerce/${t}/admin${p}`, opts).then(r => r.json());

const STATUS = {
  APPROVED: { label: "Approved", color: C.green },
  PENDING: { label: "Pending review", color: C.amber },
  REJECTED: { label: "Rejected", color: C.red },
  UNKNOWN: { label: "Unknown", color: C.muted },
};

export default function VulaWATemplates({ tenantId }) {
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [adding, setAdding] = useState(false);

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 6000); };

  const load = async () => {
    setBusy(true);
    const d = await api(tenantId, "/wa-templates").catch(() => ({ templates: [] }));
    setRows(d.templates || []);
    setBusy(false);
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [tenantId]);

  const remove = async (name) => {
    if (!window.confirm(`Delete template "${name}"? This can't be undone.`)) return;
    const r = await api(tenantId, `/wa-templates/${name}`, { method: "DELETE" }).catch(() => ({ error: "network" }));
    flash(r.error || `Deleted "${name}".`);
    load();
  };

  return (
    <div style={{ fontFamily: "system-ui", color: C.text, maxWidth: 900 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h4 style={{ fontSize: 15, fontWeight: 600, margin: "2px 0" }}>📨 WhatsApp templates</h4>
        <span style={{ fontSize: 12.5, color: C.muted }}>
          Needed to message someone who hasn't texted you recently — Meta reviews each one (usually minutes–24h).
        </span>
        <button style={{ ...btn, ...btnOn, marginLeft: "auto" }} onClick={() => setAdding(a => !a)}>{adding ? "Close" : "+ New template"}</button>
      </div>

      {adding && <AddForm tenantId={tenantId} onDone={() => { setAdding(false); load(); flash("Submitted — Meta is reviewing it."); }} flash={flash} />}
      {msg && <div style={{ fontSize: 12.5, color: C.green, margin: "8px 0" }}>{msg}</div>}

      <div style={{ ...card, padding: 0, overflowX: "auto", marginTop: 10 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
          <thead>
            <tr style={{ textAlign: "left", color: C.muted, background: C.alt }}>
              {["Name", "Category", "Body", "Rich media", "Status", "Created", ""].map(h => <th key={h} style={th}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {busy && <tr><td colSpan={6} style={{ ...td, color: C.muted }}>Loading…</td></tr>}
            {!busy && !rows.length && <tr><td colSpan={7} style={{ ...td, color: C.muted }}>No templates yet — create one for any message that needs to reach someone cold.</td></tr>}
            {rows.map(r => {
              const st = STATUS[r.status] || STATUS.UNKNOWN;
              return (
                <tr key={r.name} style={{ borderTop: `1px solid ${C.border}` }}>
                  <td style={{ ...td, fontFamily: "monospace" }}>{r.name}</td>
                  <td style={td}>{r.category}</td>
                  <td style={{ ...td, maxWidth: 280, whiteSpace: "normal", color: C.muted }}>{r.body_text}</td>
                  <td style={{ ...td, color: C.muted, fontSize: 12 }}>
                    {r.header_type && <div>🖼 {r.header_type}</div>}
                    {(r.buttons || []).map((b, i) => <div key={i}>🔘 {b.text}</div>)}
                    {!r.header_type && !(r.buttons || []).length && "—"}
                  </td>
                  <td style={{ ...td, color: st.color, fontWeight: 600 }} title={r.rejected_reason || ""}>{st.label}</td>
                  <td style={{ ...td, color: C.muted }}>{(r.created_at || "").slice(0, 10)}</td>
                  <td style={td}><button style={{ ...miniBtn, color: C.red }} onClick={() => remove(r.name)}>Delete</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p style={{ fontSize: 11.5, color: C.muted, marginTop: 8 }}>
        Use <code>{"{{1}}"}</code>, <code>{"{{2}}"}</code>… in the body for the parts that change each send (name, order number, amount). Keep wording plain and non-promotional for faster approval unless this is a marketing message.
      </p>
    </div>
  );
}

function AddForm({ tenantId, onDone, flash }) {
  const [name, setName] = useState("");
  const [category, setCategory] = useState("UTILITY");
  const [bodyText, setBodyText] = useState("");
  const [examples, setExamples] = useState("");
  const [headerType, setHeaderType] = useState("");       // "" | IMAGE | VIDEO | DOCUMENT
  const [headerUrl, setHeaderUrl] = useState("");
  const [buttons, setButtons] = useState([]);              // [{type,text,url}]
  const [busy, setBusy] = useState(false);

  const paramCount = (bodyText.match(/\{\{(\d+)\}\}/g) || []).length;

  const addButton = () => { if (buttons.length < 2) setButtons(b => [...b, { type: "URL", text: "", url: "" }]); };
  const updButton = (i, patch) => setButtons(b => b.map((x, j) => j === i ? { ...x, ...patch } : x));
  const dropButton = (i) => setButtons(b => b.filter((_, j) => j !== i));

  const submit = async () => {
    if (!name.trim() || !bodyText.trim()) return flash("Name and body text are required.");
    if (headerType && !headerUrl.trim()) return flash("Header type needs a sample media URL for Meta to review.");
    setBusy(true);
    const example_params = examples.split(",").map(s => s.trim()).filter(Boolean);
    const cleanButtons = buttons.filter(b => b.text.trim() && (b.type === "QUICK_REPLY" || b.url.trim()));
    const r = await api(tenantId, "/wa-templates", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name, body_text: bodyText, category, example_params,
        header_type: headerType || undefined, header_example_url: headerType ? headerUrl : undefined,
        buttons: cleanButtons.length ? cleanButtons : undefined,
      }),
    }).then(r => r.json ? r.json() : r).catch(() => ({ error: "network" }));
    setBusy(false);
    if (r.error) return flash(r.error);
    onDone();
  };

  return (
    <div style={{ ...card, margin: "10px 0", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input placeholder="template_name (lowercase_snake_case)" value={name} onChange={e => setName(e.target.value)}
          style={{ ...input, flex: 1, minWidth: 200, fontFamily: "monospace" }} />
        <select value={category} onChange={e => setCategory(e.target.value)} style={input}>
          <option value="UTILITY">Utility (order/account updates)</option>
          <option value="MARKETING">Marketing (promos, campaigns)</option>
        </select>
      </div>
      <textarea placeholder="Hi {{1}}, your order {{2}} is ready. Reply if you have any questions."
        value={bodyText} onChange={e => setBodyText(e.target.value)}
        style={{ width: "100%", minHeight: 90, boxSizing: "border-box", padding: 10, border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13, resize: "vertical" }} />
      {paramCount > 0 && (
        <input placeholder={`Example values for {{1}}..{{${paramCount}}}, comma-separated (e.g. Sarah, OFF-00042)`}
          value={examples} onChange={e => setExamples(e.target.value)} style={input} />
      )}

      {/* Rich media: header image + up to 2 buttons */}
      <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <select value={headerType} onChange={e => setHeaderType(e.target.value)} style={input}>
            <option value="">No header image</option>
            <option value="IMAGE">Image header</option>
            <option value="VIDEO">Video header</option>
          </select>
          {headerType && (
            <input placeholder="Public URL of a sample image/video for Meta to review"
              value={headerUrl} onChange={e => setHeaderUrl(e.target.value)}
              style={{ ...input, flex: 1, minWidth: 220 }} />
          )}
        </div>

        {buttons.map((b, i) => (
          <div key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <select value={b.type} onChange={e => updButton(i, { type: e.target.value })} style={{ ...input, width: 130 }}>
              <option value="URL">Link button</option>
              <option value="QUICK_REPLY">Quick reply</option>
            </select>
            <input placeholder="Button text (e.g. Shop Now)" value={b.text} onChange={e => updButton(i, { text: e.target.value })}
              style={{ ...input, width: 160 }} />
            {b.type === "URL" && (
              <input placeholder="https://yoursite.com/... (or …/l/{{1}} for a click-tracked link)"
                value={b.url} onChange={e => updButton(i, { url: e.target.value })}
                style={{ ...input, flex: 1, minWidth: 200 }} />
            )}
            <button style={{ ...miniBtn, color: C.red }} onClick={() => dropButton(i)}>Remove</button>
          </div>
        ))}
        {buttons.length < 2 && <button style={{ ...miniBtn, alignSelf: "flex-start" }} onClick={addButton}>+ Add button</button>}
      </div>

      <div>
        <button style={{ ...btn, ...btnOn }} disabled={busy} onClick={submit}>{busy ? "Submitting…" : "Submit to Meta for review"}</button>
      </div>
    </div>
  );
}

const card = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 12 };
const btn = { padding: "7px 13px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: "pointer" };
const btnOn = { background: C.green, color: "#fff", borderColor: C.green };
const miniBtn = { padding: "3px 9px", border: `1px solid ${C.border}`, borderRadius: 5, background: C.surface, fontSize: 11.5, cursor: "pointer" };
const input = { padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, background: C.surface, color: C.text, fontFamily: "system-ui" };
const th = { padding: "8px 10px", fontWeight: 600, whiteSpace: "nowrap" };
const td = { padding: "8px 10px", verticalAlign: "top" };
