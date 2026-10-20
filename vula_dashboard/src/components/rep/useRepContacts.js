/**
 * useRepContacts.js — a sales rep's own contacts (shared by VulaRepCrmContacts and
 * RepContactsV2). Moved verbatim out of VulaRepCrmContacts.jsx.
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../../lib/authFetch";

export const emptyContact = () => ({ name: "", phone: "", email: "", company: "", title: "", notes: "" });

export function useRepContacts(tenantId, repPhone) {
  const [contacts, setContacts] = useState([]);
  const [search, setSearch] = useState("");
  const [form, setForm] = useState(emptyContact());
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    if (!tenantId) return;
    const params = new URLSearchParams({ created_by: repPhone || "", search });
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/contacts?${params}`);
    const d = await r.json().catch(() => ({}));
    setContacts(d.contacts || []);
  }, [tenantId, repPhone, search]);
  useEffect(() => { load(); }, [load]);

  // Resolves true once the contact is saved (the redesign closes its sheet on that).
  const save = async () => {
    if (!form.name.trim()) { setMsg("Name is required."); return false; }
    setMsg("");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/contacts`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...form, created_by: repPhone || "" }),
    });
    const d = await r.json().catch(() => ({}));
    if (d.saved) { setForm(emptyContact()); load(); return true; }
    setMsg(d.detail || d.error || "Could not save contact.");
    return false;
  };

  return { contacts, search, setSearch, form, setForm, msg, setMsg, save };
}
