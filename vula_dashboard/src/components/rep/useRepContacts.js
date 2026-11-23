/**
 * useRepContacts.js — a sales rep's own contacts (shared by VulaRepCrmContacts and
 * RepContactsV2). Moved verbatim out of VulaRepCrmContacts.jsx.
 */
import { useState, useEffect, useCallback } from "react";
import { tryApi } from "../../lib/api";
import { toast } from "../ui/index.jsx";

export const emptyContact = () => ({ name: "", phone: "", email: "", company: "", title: "", notes: "" });

export function useRepContacts(tenantId, repPhone) {
  const [contacts, setContacts] = useState([]);
  const [search, setSearch] = useState("");
  const [form, setForm] = useState(emptyContact());
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    if (!tenantId) return;
    const params = new URLSearchParams({ created_by: repPhone || "", search });
    // 23 Nov 2026 audit: a failed load showed "No contacts yet"
    const { ok, data: d, error } = await tryApi(`/v1/commerce/${tenantId}/admin/contacts?${params}`);
    if (!ok) { toast(`Your contacts couldn't be loaded: ${error}`, "danger"); return; }
    setContacts(d.contacts || []);
  }, [tenantId, repPhone, search]);
  useEffect(() => { load(); }, [load]);

  // Resolves true once the contact is saved (the redesign closes its sheet on that).
  const save = async () => {
    if (!form.name.trim()) { setMsg("Name is required."); return false; }
    setMsg("");
    const { ok, data: d, error } = await tryApi(`/v1/commerce/${tenantId}/admin/contacts`, {
      method: "POST", body: { ...form, created_by: repPhone || "" },
    });
    if (ok && d.saved) { setForm(emptyContact()); load(); return true; }
    setMsg(ok ? (d.detail || d.error || "Could not save contact.") : error);
    return false;
  };

  return { contacts, search, setSearch, form, setForm, msg, setMsg, save };
}
