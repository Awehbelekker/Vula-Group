/** Customer receipt page (/r/<token>): fetches the safe receipt and prints it out with animation + sound. */
import { useEffect, useState } from "react";
import PrintedSlip, { playPrintSound, reducedMotion } from "./PrintedSlip";
import { getVolume, setVolume, nextVolume } from "./printSound";

const API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const lsGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } };

const token = () => {
  const h = location.hash.slice(1);
  if (h) return h;
  const last = location.pathname.split("/").filter(Boolean).pop() || "";
  return last === "index.html" || last === "r" ? "" : last;
};

const css = `
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:#12171a}
body{color:#e8eee9;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.rp{animation:roomIn .45s ease both;min-height:100vh;min-height:100dvh;display:flex;flex-direction:column;align-items:center;background:radial-gradient(120% 60% at 50% 0%,#26323a 0%,#12171a 62%)}
.rp .dock{position:sticky;bottom:0;margin-top:auto;width:100%;padding:16px 16px calc(16px + env(safe-area-inset-bottom));display:flex;flex-direction:column;align-items:center;gap:10px;background:linear-gradient(rgba(18,23,26,0),#12171a 38%)}
.rp .m{color:#9fb0a8;font-size:13px;text-align:center}.rp .m b{color:#e8eee9}
.rp .btns{display:flex;gap:8px;flex-wrap:wrap;justify-content:center}
.rp button{font:inherit;font-size:15px;font-weight:600;border:1px solid rgba(255,255,255,.22);background:rgba(255,255,255,.08);color:#e8eee9;border-radius:999px;padding:10px 18px;cursor:pointer}
.rp button.p{background:#3f8f73;border-color:#3f8f73;color:#fff}
.rp .e{background:#1c2428;border-radius:14px;padding:24px;text-align:center;margin:90px 16px 0;border:1px solid rgba(255,255,255,.12);max-width:360px}
.rp .e h1{font-size:18px;margin:0 0 6px}
.rp .sheet{position:fixed;inset:0;z-index:20;background:rgba(0,0,0,.6);display:flex;align-items:flex-end;justify-content:center}
.rp .card{width:100%;max-width:440px;background:#1c2428;border-radius:18px 18px 0 0;padding:20px 18px calc(20px + env(safe-area-inset-bottom));display:flex;flex-direction:column;gap:10px}
.rp .card h2{margin:0;font-size:17px}
.rp .card label{font-size:12px;color:#9fb0a8;display:flex;flex-direction:column;gap:4px;text-align:left}
.rp .card input{font:inherit;font-size:16px;padding:11px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.22);background:#12171a;color:#e8eee9}
.rp .card .err{color:#ff9d8f;font-size:13px;text-align:left}
.rp .card .row{display:flex;gap:8px;justify-content:flex-end;margin-top:4px}
@keyframes roomIn{from{opacity:0}to{opacity:1}}
@media (prefers-reduced-motion:reduce){.rp{animation:none}}
@media print{html,body,.rp{background:#fff!important;color:#000!important}.rp .dock,.rp .sheet{display:none}}
`;

function TaxSheet({ tok, onDone, onClose }) {
  const [f, setF] = useState({ company: "", vat: "", address: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const r = await fetch(`${API}/v1/tap/receipt/${encodeURIComponent(tok)}/tax-invoice`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ company: f.company, vat: f.vat, address: f.address || null }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "Please check your company name and VAT number.");
      onDone(j.number);
    } catch (x) { setErr(x.message || "Something went wrong. Please try again."); } finally { setBusy(false); }
  };
  return (
    <div className="sheet" role="dialog" aria-modal="true" aria-label="Get a tax invoice" onClick={onClose}>
      <form className="card" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2>Tax invoice</h2>
        <label>Company name<input value={f.company} onChange={set("company")} required minLength={2} maxLength={120} autoComplete="organization" /></label>
        <label>VAT number (10 digits)<input value={f.vat} onChange={set("vat")} required inputMode="numeric" placeholder="4123456789" autoComplete="off" /></label>
        <label>Company address (optional)<input value={f.address} onChange={set("address")} maxLength={200} autoComplete="street-address" /></label>
        <div className="m" style={{ fontSize: 11, textAlign: "left" }}>Once issued, a tax invoice can't be changed. Any tip is not on the invoice.</div>
        {err && <div className="err" role="alert">{err}</div>}
        <div className="row"><button type="button" onClick={onClose}>Cancel</button><button className="p" disabled={busy}>{busy ? "Making it…" : "Get tax invoice"}</button></div>
      </form>
    </div>
  );
}

export default function ReceiptPage() {
  const [state, setState] = useState({ s: "loading", data: null });
  const [sheet, setSheet] = useState(false);
  const [run, setRun] = useState(0);
  const [volume, setVol] = useState(() => getVolume());
  const still = reducedMotion();

  useEffect(() => {
    const t = token();
    if (!t) { setState({ s: "error" }); return; }
    fetch(`${API}/v1/tap/receipt/${encodeURIComponent(t)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data) => setState({ s: "ok", data }))
      .catch(() => setState({ s: "error" }));
  }, []);

  const pdfUrl = () => `${API}/v1/tap/receipt/${encodeURIComponent(token())}/tax-invoice.pdf`;
  const taxDone = (number) => {
    setState((st) => ({ ...st, data: { ...st.data, tax_invoice_number: number } }));
    setSheet(false);
    window.open(pdfUrl(), "_blank", "noopener");
  };
  const toggle = () => { const n = nextVolume(volume); setVol(n); setVolume(n); if (n !== "off") playPrintSound(0.4); };   // a tap: audio is allowed, so you hear the new level
  const replay = () => setRun((n) => n + 1);          // a tap = a user gesture, so sound is allowed here

  return (
    <div className="rp">
      <style>{css}</style>
      {state.s === "loading" && <div className="m" style={{ marginTop: 120 }}>Fetching your receipt…</div>}
      {state.s === "error" && (
        <div className="e"><h1>Receipt not available</h1><p className="m">This receipt link is no longer available. If you need a copy, ask the business that served you.</p></div>
      )}
      {state.s === "ok" && (
        <>
          <PrintedSlip key={run} data={state.data} sound={volume !== "off" && !still} />
          <div className="dock noprint">
            <div className="m"><b>Payment received</b> · your receipt from {state.data.merchant}</div>
            <div className="btns">
              <button className="p" onClick={replay}>Replay</button>
              <button onClick={toggle} aria-label={`Sound volume: ${volume}`}>{volume === "off" ? "Sound off" : `Sound: ${volume}`}</button>
              <button onClick={() => window.print()}>Save / print</button>
              {state.data.tax_invoice_number
                ? <a href={pdfUrl()} target="_blank" rel="noopener noreferrer"><button>Tax invoice {state.data.tax_invoice_number}</button></a>
                : state.data.can_tax_invoice && <button onClick={() => setSheet(true)}>Get tax invoice</button>}
            </div>
            <div className="m" style={{ fontSize: 11 }}>Powered by Vula</div>
          </div>
          {sheet && <TaxSheet tok={token()} onDone={taxDone} onClose={() => setSheet(false)} />}
        </>
      )}
    </div>
  );
}
