/** Customer receipt page (/r/<token>): fetches the safe receipt and prints it out with animation + sound. */
import { useEffect, useState } from "react";
import PrintedSlip, { playPrintSound, reducedMotion } from "./PrintedSlip";

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
*{box-sizing:border-box}body{margin:0;background:#F7F4EE;color:#1d2b25;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.rp{max-width:420px;margin:0 auto;padding:22px 16px calc(28px + env(safe-area-inset-bottom));display:flex;flex-direction:column;align-items:center;gap:14px;min-height:100vh}
.rp h1{font-size:18px;margin:6px 0 0;text-align:center}.rp .m{color:#6b756f;font-size:13px;text-align:center}
.rp .btns{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-top:6px}
.rp button{font:inherit;font-size:15px;font-weight:600;border:1px solid rgba(0,0,0,.12);background:#fff;color:#2C5545;border-radius:999px;padding:10px 16px;cursor:pointer}
.rp button.p{background:#2C5545;color:#fff;border-color:#2C5545}
.rp .e{background:#fff;border-radius:14px;padding:22px;text-align:center;margin-top:60px;border:1px solid rgba(0,0,0,.08)}
`;

export default function ReceiptPage() {
  const [state, setState] = useState({ s: "loading", data: null });
  const [run, setRun] = useState(0);
  const [sound, setSound] = useState(() => lsGet("vp.sound") !== "off");
  const still = reducedMotion();

  useEffect(() => {
    const t = token();
    if (!t) { setState({ s: "error" }); return; }
    fetch(`${API}/v1/tap/receipt/${encodeURIComponent(t)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data) => setState({ s: "ok", data }))
      .catch(() => setState({ s: "error" }));
  }, []);

  const toggle = () => { const n = !sound; setSound(n); lsSet("vp.sound", n ? "on" : "off"); if (n) playPrintSound(0.4); };
  const replay = () => setRun((n) => n + 1);          // a tap = a user gesture, so sound is allowed here

  return (
    <div className="rp">
      <style>{css}</style>
      {state.s === "loading" && <div className="m" style={{ marginTop: 80 }}>Fetching your receipt…</div>}
      {state.s === "error" && (
        <div className="e"><h1>Receipt not available</h1><p className="m">This receipt link is no longer available. If you need a copy, ask the business that served you.</p></div>
      )}
      {state.s === "ok" && (
        <>
          <h1 className="noprint">Payment received</h1>
          <div className="m noprint">Your receipt from {state.data.merchant}</div>
          <PrintedSlip key={run} data={state.data} sound={sound && !still} />
          <div className="btns noprint">
            <button className="p" onClick={replay}>Replay</button>
            <button onClick={toggle} aria-pressed={sound}>{sound ? "Sound on" : "Sound off"}</button>
            <button onClick={() => window.print()}>Save / print</button>
          </div>
          <div className="m noprint" style={{ marginTop: 10 }}>Powered by Vula</div>
        </>
      )}
    </div>
  );
}
