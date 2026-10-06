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
@keyframes roomIn{from{opacity:0}to{opacity:1}}
@media (prefers-reduced-motion:reduce){.rp{animation:none}}
@media print{html,body,.rp{background:#fff!important;color:#000!important}.rp .dock{display:none}}
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
      {state.s === "loading" && <div className="m" style={{ marginTop: 120 }}>Fetching your receipt…</div>}
      {state.s === "error" && (
        <div className="e"><h1>Receipt not available</h1><p className="m">This receipt link is no longer available. If you need a copy, ask the business that served you.</p></div>
      )}
      {state.s === "ok" && (
        <>
          <PrintedSlip key={run} data={state.data} sound={sound && !still} />
          <div className="dock noprint">
            <div className="m"><b>Payment received</b> · your receipt from {state.data.merchant}</div>
            <div className="btns">
              <button className="p" onClick={replay}>Replay</button>
              <button onClick={toggle} aria-pressed={sound}>{sound ? "Sound on" : "Sound off"}</button>
              <button onClick={() => window.print()}>Save / print</button>
            </div>
            <div className="m" style={{ fontSize: 11 }}>Powered by Vula</div>
          </div>
        </>
      )}
    </div>
  );
}
