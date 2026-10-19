/**
 * useContacts.js — the email-contact library's data and actions (2026-10-18, UX redesign Phase
 * F). Shared by VulaContacts.jsx and contacts/ContactsV2.jsx; moved out unchanged.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { downloadCsv } from "../../lib/csv";
import { VULA_API } from "../../lib/authFetch";

export const KIND_LABEL = { internal: "Co-worker", supplier: "Supplier", client: "Client", external: "External" };
export const KINDS = ["internal", "client", "supplier", "external"];

export function parseCsv(text) {
  const lines = text.split(/\r?\n/).filter(l => l.trim());
  if (!lines.length) return [];
  const header = lines[0].split(",").map(h => h.trim().toLowerCase());
  return lines.slice(1).map(line => {
    const cells = line.split(",").map(c => c.trim().replace(/^"|"$/g, ""));
    const row = {};
    header.forEach((h, i) => { row[h] = cells[i] || ""; });
    return row;
  });
}

export function useContacts(tenantId) {
  const [contacts, setContacts] = useState([]);
  const [q, setQ] = useState("");
  const [tab, setTab] = useState("all");
  const [syncing, setSyncing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [msg, setMsg] = useState("");
  const fileRef = useRef(null);

  const load = useCallback(async () => {
    if (!tenantId) return;
    const r = await fetch(`${VULA_API}/v1/email/contacts/${tenantId}`);
    const d = await r.json();
    setContacts(d.contacts || []);
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const syncNow = async () => {
    setSyncing(true); setMsg("");
    try {
      const r = await fetch(`${VULA_API}/v1/email/sync/${tenantId}`, { method: "POST" });
      const d = await r.json();
      setMsg(d.synced != null ? `Synced ${d.synced} new email(s) · ${d.filed_attachments || 0} attachment(s) filed` : (d.error || "No mailbox connected"));
      load();
    } catch (e) { setMsg("Sync failed"); } finally { setSyncing(false); }
  };
  const retag = async (id, kind) => {
    await fetch(`${VULA_API}/v1/email/contacts/${tenantId}/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind }),
    });
    load();
  };

  const importCsv = async (file) => {
    if (!file) return;
    setImporting(true); setMsg("");
    try {
      const text = await file.text();
      const rows = parseCsv(text).filter(r => r.email);
      if (!rows.length) { setMsg("No 'email' column found — headers should include name,email,kind"); setImporting(false); return; }
      const r = await fetch(`${VULA_API}/v1/email/contacts/${tenantId}/import`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ contacts: rows }),
      }).then(r => r.json());
      setMsg(`Imported ${r.imported || 0} contact(s)${r.skipped ? ` · ${r.skipped} skipped` : ""}`);
      load();
    } catch (e) { setMsg("Import failed — check the CSV format"); }
    setImporting(false);
    if (fileRef.current) fileRef.current.value = "";
  };

  const exportCsv = () => downloadCsv("contacts", contacts, [
    { key: "name", label: "Name" }, { key: "email", label: "Email" },
    { key: "kind", label: "Kind" }, { key: "message_count", label: "Messages" },
  ]);

  const counts = contacts.reduce((a, c) => { a[c.kind] = (a[c.kind] || 0) + 1; return a; }, {});
  let rows = tab === "all" ? contacts : contacts.filter(c => c.kind === tab);
  if (q) { const s = q.toLowerCase(); rows = rows.filter(c => (c.name || "").toLowerCase().includes(s) || (c.email || "").includes(s)); }

  return { contacts, rows, counts, q, setQ, tab, setTab, syncing, importing, msg, fileRef, syncNow, retag, importCsv, exportCsv };
}
