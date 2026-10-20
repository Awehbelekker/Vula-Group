/**
 * useQSRates.js — the tenant's own QS rate library plus rates learned from its documents (shared
 * by VulaQSRates and QSRatesV2). Moved verbatim out of VulaQSRates.jsx; see its header.
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../../lib/authFetch";

export const UNITS = ["m2", "m3", "m", "each", "no", "kg", "item", "sum", "day", "hour", "bag", "litre"];
export const KINDS = [["", "All"], ["material", "Materials"], ["labour", "Labour"], ["plant", "Plant hire"], ["delivery", "Delivery"]];
export const blank = { id: null, description: "", unit: "m2", rate: "", code: "", category: "", source: "" };

export function useQSRates({ tenantId }) {
  const [own, setOwn] = useState([]);
  const [learned, setLearned] = useState([]);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [form, setForm] = useState(blank);
  const [busy, setBusy] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(async () => {
    if (!tenantId) return;
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (kind) params.set("kind", kind);
    try {
      const r = await fetch(`${VULA_API}/v1/qs/rates/${tenantId}?${params}`);
      const d = await r.json();
      setOwn(d.own || d.rates || []);
      setLearned(d.learned || []);
    } catch { setOwn([]); setLearned([]); }
  }, [tenantId, q, kind]);
  useEffect(() => { load(); }, [load]);

  const post = async (body) => {
    setBusy(true);
    try {
      await fetch(`${VULA_API}/v1/qs/rates/${tenantId}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
    } finally { setBusy(false); load(); }
  };
  const save = async () => {
    if (!form.description.trim() || form.rate === "") return;
    const { id, ...rest } = form;
    await post({ ...rest, ...(id ? { id } : {}), rate: parseFloat(form.rate) });
    setForm(blank);
  };
  const edit = (r) => setForm({ id: r.id, description: r.description || "", unit: r.unit || "m2", rate: r.rate ?? "",
    code: r.code || "", category: r.category || "", source: r.source || "" });
  const del = async (id) => { await fetch(`${VULA_API}/v1/qs/rates/${tenantId}/${id}`, { method: "DELETE" }); load(); };
  const adopt = (r) => post({ description: r.description, unit: r.unit || "each", rate: r.rate,
    category: r.kind === "material" ? "" : r.kind, source: r.source });
  const updateToLearned = (r) => post({ id: r.id, description: r.description, unit: r.unit, rate: r.drift.learned_rate,
    source: r.drift.source });

  const shown = showAll ? learned : learned.slice(0, 60);
  return { own, setOwn, learned, setLearned, q, setQ, kind, setKind, form, setForm, busy, setBusy, showAll, setShowAll, load, post, save, edit, del, adopt, updateToLearned, shown };
}
