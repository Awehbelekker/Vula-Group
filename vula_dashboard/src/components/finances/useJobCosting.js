/**
 * useJobCosting.js — is each project making its fee? (2026-10-19, UX redesign Phase F). Shared by
 * VulaJobCosting.jsx and finances/JobCostingV2.jsx; moved out unchanged — same
 * /admin/projects/costing, /terms and /price-advice calls (vula/commerce/job_costing.py).
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";

export function useJobCosting(tenantId) {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(null);
  const [feeEdit, setFeeEdit] = useState({});
  const [ask, setAsk] = useState({ item: "", quantity: "", unit: "" });
  const [advice, setAdvice] = useState(null);

  const [hasProjects, setHasProjects] = useState(null);
  const base = `${VULA_API}/v1/commerce/${tenantId}/admin/projects`;
  useEffect(() => {
    if (!tenantId) return;
    fetch(`${VULA_API}/v1/tenants/${tenantId}`).then(r => r.json())
      .then(d => { const t = d.tenant || d; const p = t.profile;   // the backend's tenant profile decides
        setHasProjects(p ? !!p.uses_projects : (t.modules || []).includes("projects")); })
      .catch(() => setHasProjects(false));
  }, [tenantId]);
  const [failed, setFailed] = useState(false);
  const load = useCallback(async () => {
    if (!tenantId) return;
    setFailed(false);
    try {
      const r = await fetch(`${base}/costing`);
      if (!r.ok) throw new Error(String(r.status));
      setData(await r.json());
    } catch { setData(null); setFailed(true); }
  }, [tenantId, base]);
  useEffect(() => { load(); }, [load]);

  const saveFee = async (project) => {
    const v = parseFloat(feeEdit[project]);
    if (Number.isNaN(v)) return;
    await fetch(`${base}/terms`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, fee_pct: v }) });
    setFeeEdit({ ...feeEdit, [project]: undefined });
    load();
  };
  const getAdvice = async () => {
    if (!ask.item.trim()) return;
    const q = new URLSearchParams({ item: ask.item });
    if (ask.quantity) q.set("quantity", ask.quantity);
    if (ask.unit) q.set("unit", ask.unit);
    try { setAdvice(await (await fetch(`${base}/price-advice?${q}`)).json()); } catch { setAdvice(null); }
  };

  return { data, open, setOpen, feeEdit, setFeeEdit, ask, setAsk, advice, hasProjects, failed, load, saveFee, getAdvice };
}
