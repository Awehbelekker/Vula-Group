/** Customer receipt page (/r/<token>): fetches the safe receipt and prints it out with animation + sound. */
import { useEffect, useState } from "react";
import "./ReceiptPage.css";
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
