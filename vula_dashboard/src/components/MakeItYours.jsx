/**
 * MakeItYours.jsx — Home's "Make it yours" card (5 Nov 2026). The old Home had one (2026-09-29);
 * it went with the old design, and Ian asked where "the brand kit finder" had gone.
 *
 * For an owner or manager whose business still wears Vula's default look (no logo, Vula green):
 * one website address sets the brand (colours, and the logo when the site has one) and starts
 * "Import from your website" — products, the pages customers ask about, and the business profile
 * drafted for the owner to confirm on WhatsApp (POST /admin/setup-from-website,
 * vula_mind/vula/commerce/website_setup.py). "Not now" hides it on this device.
 */
import { useCallback, useEffect, useState } from "react";
import { Globe, Sparkles } from "lucide-react";
import { VULA_API } from "../lib/authFetch";
import { useAuthStore } from "../store/auth";
import { DEFAULT_BRAND } from "../theme/tokens";
import { Button, Card, Field, Input } from "./ui/index.jsx";

const LEADS = ["owner", "manager", "admin", "master"];

export default function MakeItYours({ tenantId, onNavigate }) {
  const role = useAuthStore((s) => s.role);
  const key = `vula-brand-dismissed-${tenantId}`;
  const [show, setShow] = useState(false);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const [st, setSt] = useState(null);

  useEffect(() => {
    if (!LEADS.includes(role)) return;
    try { if (localStorage.getItem(key) === "1") return; } catch { /* private mode */ }
    fetch(`${VULA_API}/v1/commerce/${tenantId}/brand`).then((r) => (r.ok ? r.json() : null))
      .then((b) => { if (b && !b.logo_url && (!b.accent_color || b.accent_color.toUpperCase() === DEFAULT_BRAND.accent.toUpperCase())) setShow(true); })
      .catch(() => {});
  }, [tenantId, role]);  // eslint-disable-line react-hooks/exhaustive-deps

  const poll = useCallback(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/site-import`).then((r) => r.json())
      .then((d) => setSt(d.status || null)).catch(() => {});
  }, [tenantId]);
  useEffect(() => {
    if (!res?.import_started || (st && !st.running)) return undefined;
    const t = setInterval(poll, 4000);
    poll();
    return () => clearInterval(t);
  }, [res, st?.running, poll]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (!show) return null;
  const dismiss = () => { try { localStorage.setItem(key, "1"); } catch { /* private mode */ } setShow(false); };
  const start = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/setup-from-website`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: url.trim() }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(d.detail || "Couldn't read that address."); return; }
      setRes(d);
      if (d.brand?.applied) window.dispatchEvent(new Event("vula-brand-changed"));
    } catch { setErr("Couldn't reach Vula — try again."); } finally { setBusy(false); }
  };

  const brand = res?.brand || {};
  const done = st && !st.running && st.step === "done";
  return (
    <Card role="region" aria-label="Make it yours" style={{ marginBottom: "var(--space-4)", border: "1px solid var(--brand)" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)" }}>
        <strong style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--brand-text)" }}>
          <Sparkles size={16} aria-hidden="true" />Make it yours
        </strong>
        <Button size="sm" variant="quiet" style={{ marginLeft: "auto" }} onClick={dismiss}>Not now</Button>
      </div>
      {!res ? (
        <form onSubmit={start}>
          <p style={{ margin: "var(--space-2) 0 var(--space-3)", color: "var(--muted)" }}>
            Give your website and Vula takes your colours and logo, reads in your products and the pages customers ask about,
            and drafts your business profile — you just confirm it on WhatsApp.
          </p>
          <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", alignItems: "flex-end" }}>
            <Field label="Your website" style={{ flex: "1 1 240px" }}>
              <Input type="text" inputMode="url" autoComplete="url" placeholder="yourshop.co.za" value={url} onChange={(e) => setUrl(e.target.value)} />
            </Field>
            <Button type="submit" Icon={Globe} disabled={busy || !url.trim()}>{busy ? "Reading your site…" : "Set it up"}</Button>
          </div>
          {err && <p role="alert" style={{ color: "var(--danger-text)", margin: "var(--space-2) 0 0" }}>{err}</p>}
          <p style={{ margin: "var(--space-2) 0 0", color: "var(--muted)", fontSize: "var(--fs-caption)" }}>
            No website? <Button size="sm" variant="quiet" type="button" onClick={() => onNavigate?.("settings", "brand")}>Upload your logo instead</Button>
          </p>
        </form>
      ) : (
        <div role="status" style={{ marginTop: "var(--space-2)" }}>
          <p style={{ margin: 0 }}>
            {brand.applied ? `Your colours are set from ${res.url}${brand.logo_url ? ", and your logo" : ""}. `
              : brand.reason === "brand_already_set" ? "Your brand was already set, so it's unchanged. "
              : "No brand colours found on the site — set them in Settings › Brand. "}
            {res.import_started || st?.running ? (done ? "" : `Reading your website… ${st?.step || ""}`) : ""}
          </p>
          {done && (
            <p style={{ margin: "var(--space-2) 0 0" }}>
              Done: {st.products_found || 0} products and {st.kb_documents || 0} pages read
              {st.profile_drafts ? `, and ${st.profile_drafts} business-profile answers drafted — Vula will ask you to confirm them on WhatsApp` : ""}.
            </p>)}
          <div style={{ display: "flex", gap: "var(--space-2)", marginTop: "var(--space-3)", flexWrap: "wrap" }}>
            <Button size="sm" variant="secondary" onClick={() => onNavigate?.("settings", "brand")}>Check my brand</Button>
            <Button size="sm" variant="quiet" onClick={dismiss}>Close</Button>
          </div>
        </div>
      )}
    </Card>
  );
}
