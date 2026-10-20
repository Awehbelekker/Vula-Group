/**
 * useWATemplates.js — Marketing › Templates (WhatsApp message templates), shared by
 * VulaWATemplates (current look) and marketing/WATemplatesV2 (the redesign, 2026-10-20). Moved
 * verbatim: the account's templates with Meta's review status, submitting the standard set Vula's
 * own reminders need, deleting one, and the new-template form (body with {{1}} parts, optional
 * header image / video and up to two buttons).
 */
import { useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";
import { confirmDialog } from "../ui/index.jsx";

const api = (t, p, opts) => fetch(`${VULA_API}/v1/commerce/${t}/admin${p}`, opts).then(r => r.json());

export function useWATemplates(tenantId) {
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

  // 2026-10-12: the templates Vula's own scheduled messages need (unpaid-order reminder, sales
  // summary…), named the way the jobs look them up — one tap for a new business.
  const submitStandard = async () => {
    setBusy(true);
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/wa-templates/standard`, { method: "POST" })
      .then(async (x) => ({ ok: x.ok, d: await x.json().catch(() => ({})) })).catch(() => ({ ok: false, d: {} }));
    setBusy(false);
    if (!r.ok) { flash(r.d.detail || "Couldn't submit the standard templates."); return; }
    const { submitted = [], already_there = [], failed = [] } = r.d;
    flash(`${submitted.length} submitted for review, ${already_there.length} already on the account`
      + (failed.length ? `, ${failed.length} refused: ${failed.map(f => `${f.name} (${f.error})`).join("; ")}` : "") + ".");
    load();
  };

  const remove = async (name) => {
    if (!(await confirmDialog(`Delete template "${name}"? This can't be undone.`, { danger: true, confirmLabel: 'Yes' }))) return;
    const r = await api(tenantId, `/wa-templates/${name}`, { method: "DELETE" }).catch(() => ({ error: "network" }));
    flash(r.error || `Deleted "${name}".`);
    load();
  };

  return { rows, busy, msg, flash, load, adding, setAdding, submitStandard, remove };
}

export function useTemplateForm(tenantId, { onDone, flash }) {
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

  return { name, setName, category, setCategory, bodyText, setBodyText, examples, setExamples, headerType, setHeaderType, headerUrl, setHeaderUrl, buttons, busy, paramCount, addButton, updButton, dropButton, submit };
}
