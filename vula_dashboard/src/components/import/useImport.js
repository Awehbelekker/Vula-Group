/**
 * useImport.js — Sell › Import, shared by VulaImport (current look) and import/ImportV2 (the
 * redesign, 2026-10-20). Moved verbatim: paste or upload old WhatsApp chats / emails and Vula pulls
 * out the past orders, or an old invoice list (Xero export) so bank payments match past invoice
 * numbers — reviewed and edited before anything is committed. And learning tone + common answers
 * from WhatsApp chat exports (vula/commerce/chat_learn.py); answers wait for review in Settings.
 */
import { useState } from "react";
import { VULA_API } from "../../lib/authFetch";

export function useImport(tenantId) {
  const [mode, setMode] = useState("orders");          // 'orders' | 'invoices'
  const [text, setText] = useState("");
  const [orders, setOrders] = useState(null);
  const [invoices, setInvoices] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 5000); };

  const onFile = async (file) => {
    if (!file) return;
    const t = await file.text();
    setText(t.slice(0, 200000));
    flash(`Loaded ${file.name}`);
  };

  const extract = async () => {
    if (!text.trim()) return flash("Paste or upload something first.");
    setBusy(true); setOrders(null); setInvoices(null); flash("Reading your history…");
    if (mode === "invoices") {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/import/invoices/extract`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
      }).then(r => r.json()).catch(() => ({ invoices: [] }));
      setBusy(false); setInvoices(r.invoices || []);
      return flash(`Found ${(r.invoices || []).length} invoice(s) — review, then commit.`);
    }
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/import/extract`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
    }).then(r => r.json()).catch(() => ({ orders: [] }));
    setBusy(false);
    setOrders(r.orders || []);
    flash(`Found ${(r.orders || []).length} order(s) — review below, then commit.`);
  };

  const commitInvoices = async () => {
    if (!invoices?.length) return;
    setBusy(true); flash("Registering invoices…");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/import/invoices/commit`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ invoices }),
    }).then(r => r.json()).catch(() => ({ error: "network" }));
    setBusy(false);
    if (r.error) return flash(r.error);
    flash(`✓ ${r.created} invoice(s) registered${r.skipped ? `, ${r.skipped} skipped` : ""} — bank payments will now match them.`);
    setInvoices(null); setText("");
  };

  const upd = (i, patch) => setOrders(os => os.map((o, j) => j === i ? { ...o, ...patch } : o));
  const drop = (i) => setOrders(os => os.filter((_, j) => j !== i));

  const commit = async () => {
    if (!orders?.length) return;
    setBusy(true); flash("Adding to Vula…");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/import/commit`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ orders }),
    }).then(r => r.json()).catch(() => ({ error: "network" }));
    setBusy(false);
    if (r.error) return flash(r.error);
    flash(`✓ ${r.created} order(s) added${r.skipped ? `, ${r.skipped} skipped (duplicates)` : ""}.`);
    setOrders(null); setText("");
  };

  const switchMode = (v) => { setMode(v); setOrders(null); setInvoices(null); };
  const dropInvoice = (i) => setInvoices(vs => vs.filter((_, j) => j !== i));
  return { mode, switchMode, text, setText, orders, setOrders, invoices, setInvoices, busy, msg, onFile, extract, commitInvoices, upd, drop, commit, dropInvoice };
}

export function useChatLearn(tenantId) {
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");

  const pick = async (list) => {
    setRes(null); setErr("");
    const out = [];
    for (const f of Array.from(list || []).slice(0, 40)) {
      if (f.size > 4_000_000) { setErr(`${f.name} is too big — export "Without media".`); continue; }
      if (f.name.toLowerCase().endsWith(".zip")) {
        const buf = new Uint8Array(await f.arrayBuffer());
        let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
        out.push({ name: f.name, b64: btoa(bin) });
      } else {
        out.push({ name: f.name, text: await f.text() });
      }
    }
    setFiles(out);
  };

  const learn = async (business) => {
    if (!files.length) return setErr("Choose one or more chat exports first.");
    setBusy(true); setErr(""); setRes(null);
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/import/chats/learn`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ files, business: business || null }),
    }).then(r => r.json()).catch(() => ({ error: "Couldn't reach Vula — try again." }));
    setBusy(false);
    if (r.error || r.detail) return setErr(r.error || r.detail);
    setRes(r);
  };

  return { files, busy, res, err, pick, learn };
}
