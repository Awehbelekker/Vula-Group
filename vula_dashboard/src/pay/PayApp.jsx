/**
 * PayApp.jsx — "Vula Pay": the coach/cashier app for Tap to Pay (installable PWA at /pay/).
 *
 * Flow: enrol once (manager's 6-digit code + your WhatsApp number + a PIN) → unlock with PIN →
 * create a bill → the customer taps your tag → the card flips to PAID live (vibration + sound),
 * with your share. Creating a bill needs a connection; the app shell and your recent bills open
 * offline. Everything money-related comes from the server — nothing is computed here.
 */
import { useState, useEffect, useRef, useCallback } from "react";
import PrintedSlip, { playPrintSound } from "../receipt/PrintedSlip";
import { getVolume, setVolume, nextVolume } from "../receipt/printSound";

const API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const LS = {
  get: (k) => { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  del: (k) => { try { localStorage.removeItem(k); } catch { /* private mode */ } },
};
const SS = {
  get: (k) => { try { return sessionStorage.getItem(k) || ""; } catch { return ""; } },
  set: (k, v) => { try { sessionStorage.setItem(k, v); } catch { /* ignore */ } },
  del: (k) => { try { sessionStorage.removeItem(k); } catch { /* ignore */ } },
};
const rands = (c) => `R ${(c / 100).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`.replace(/ /g, " ");
const RAND_RE = /^\d{1,7}([.,]\d{1,2})?$/;

class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }

async function call(path, { token, method = "GET", body } = {}) {
  let r;
  try {
    r = await fetch(`${API}${path}`, {
      method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: body ? JSON.stringify(body) : undefined });
  } catch { throw new ApiError("No connection. Check your signal and try again.", 0); }
  let data = {};
  try { data = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new ApiError(typeof data.detail === "string" ? data.detail : "Something went wrong.", r.status);
  return data;
}

const buzz = () => { try { navigator.vibrate?.([120, 60, 120]); } catch { /* unsupported */ } };

function urlB64ToUint8(s) {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const raw = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

// ── styles (self-contained: this page does not load the dashboard's CSS) ─────────────────────
const C = { bg: "#F7F4EE", ink: "#1d2b25", green: "#2C5545", soft: "#e8efe9", muted: "#6b756f", line: "rgba(0,0,0,.09)", ok: "#15803d", warn: "#b45309", danger: "#b91c1c", card: "#fff" };
const css = `
*{box-sizing:border-box} body{margin:0;background:${C.bg};color:${C.ink};font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;-webkit-text-size-adjust:100%}
.wrap{max-width:480px;margin:0 auto;padding:16px 16px calc(24px + env(safe-area-inset-bottom))}
h1{font-size:22px;margin:4px 0 2px} h2{font-size:16px;margin:0 0 8px}
.card{background:${C.card};border:1px solid ${C.line};border-radius:14px;padding:14px;margin:12px 0}
.muted{color:${C.muted};font-size:13px}
input,select{width:100%;font:inherit;font-size:17px;padding:13px 12px;border:1px solid ${C.line};border-radius:10px;background:#fff;margin:6px 0}
button{font:inherit;font-size:17px;font-weight:600;border:0;border-radius:12px;padding:14px 16px;background:${C.green};color:#fff;width:100%;margin:6px 0;cursor:pointer}
button.ghost{background:transparent;color:${C.green};border:1px solid ${C.line}} button.small{width:auto;font-size:14px;padding:8px 12px;margin:0}
button:disabled{opacity:.5;cursor:not-allowed}
.err{background:#fee2e2;color:${C.danger};border-radius:10px;padding:10px 12px;margin:10px 0;font-size:14px}
.banner{background:#fef3c7;color:${C.warn};border-radius:10px;padding:8px 12px;margin:8px 0;font-size:13px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 6px} .chip{background:${C.soft};color:${C.green};border-radius:999px;padding:7px 12px;font-size:14px;border:0;width:auto;font-weight:500;margin:0}
.row{display:flex;gap:10px;align-items:center;justify-content:space-between}
.pill{border-radius:999px;padding:3px 10px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.03em}
.pill.open{background:#e5e7eb;color:#374151}.pill.claimed{background:#fef3c7;color:${C.warn}}.pill.paid{background:#dcfce7;color:${C.ok}}.pill.cancelled,.pill.expired{background:#f3f4f6;color:#9ca3af}.pill.abandoned{background:#ffedd5;color:#c2410c}.pill.needs_follow_up{background:#fee2e2;color:#b91c1c}
.paid-card{border-color:${C.ok};background:#f0fdf4;animation:pop .5s ease-out} @keyframes pop{0%{transform:scale(.97)}60%{transform:scale(1.02)}100%{transform:scale(1)}}
.big{font-size:28px;font-weight:800}.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px}
.pin{letter-spacing:.5em;text-align:center;font-size:28px}
@keyframes roomIn{from{opacity:0}to{opacity:1}}
@media (prefers-reduced-motion:reduce){.ov{animation:none}}
.ov{animation:roomIn .4s ease both;position:fixed;inset:0;background:radial-gradient(120% 60% at 50% 0%,#26323a 0%,#12171a 62%);z-index:50;display:flex;flex-direction:column;align-items:center;overflow:auto}
.ov .bar{position:sticky;bottom:0;margin-top:auto;width:100%;display:flex;gap:8px;justify-content:center;flex-wrap:wrap;padding:16px 12px calc(16px + env(safe-area-inset-bottom));background:linear-gradient(rgba(18,23,26,0),#12171a 40%)}
.ov .bar button{width:auto;margin:0;font-size:15px;padding:10px 20px}
.ov .bar button.ghost{background:rgba(255,255,255,.1);color:#fff;border-color:rgba(255,255,255,.28)}
`;

export default function PayApp() {
  const [tenant, setTenant] = useState(() => new URLSearchParams(location.search).get("t") || LS.get("vp.tenant"));
  const [deviceToken, setDeviceToken] = useState(() => LS.get("vp.device"));
  const [token, setToken] = useState(() => SS.get("vp.access"));
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (tenant) LS.set("vp.tenant", tenant); }, [tenant]);

  const signedIn = (d, dev) => { setToken(d.access_token); SS.set("vp.access", d.access_token); if (dev) { setDeviceToken(dev); LS.set("vp.device", dev); } setErr(""); };
  const lock = useCallback(() => { setToken(""); SS.del("vp.access"); }, []);
  const forgetDevice = useCallback(() => { lock(); setDeviceToken(""); LS.del("vp.device"); }, [lock]);

  let screen;
  if (token) screen = <Home token={token} onLock={lock} onExpired={() => { lock(); setErr("Please enter your PIN again."); }} />;
  else if (deviceToken) screen = <Unlock deviceToken={deviceToken} err={err} setErr={setErr} busy={busy} setBusy={setBusy} onIn={(d) => signedIn(d)} onForget={forgetDevice} />;
  else screen = <Enrol tenant={tenant} setTenant={setTenant} err={err} setErr={setErr} busy={busy} setBusy={setBusy} onIn={(d) => signedIn(d, d.device_token)} />;

  return <><style>{css}</style><div className="wrap">{screen}</div></>;
}

// ── enrol ────────────────────────────────────────────────────────────────────────────────────
function Enrol({ tenant, setTenant, err, setErr, busy, setBusy, onIn }) {
  const [f, setF] = useState({ phone: "", code: "", pin: "", pin2: "" });
  const submit = async (e) => {
    e.preventDefault(); setErr("");
    if (!tenant) return setErr("Open the link your manager sent you, or type your business name.");
    if (!/^\d{4,6}$/.test(f.pin)) return setErr("Choose a PIN of 4 to 6 digits.");
    if (f.pin !== f.pin2) return setErr("The two PINs don't match.");
    setBusy(true);
    try { onIn(await call("/v1/tap/app/enrol", { method: "POST", body: { tenant: tenant.trim(), phone: f.phone, code: f.code.trim(), pin: f.pin, label: navigator.userAgent.slice(0, 50) } })); }
    catch (x) { setErr(x.message); } finally { setBusy(false); }
  };
  return (
    <form onSubmit={submit}>
      <h1>Vula Pay</h1>
      <p className="muted">Set up this phone. Ask your manager for a 6-digit code (valid for 10 minutes).</p>
      {err && <div className="err" role="alert">{err}</div>}
      {!new URLSearchParams(location.search).get("t") && <input aria-label="Business" placeholder="Business name or ID" value={tenant} onChange={(e) => setTenant(e.target.value)} autoCapitalize="none" />}
      <input aria-label="Your WhatsApp number" placeholder="Your WhatsApp number" inputMode="tel" autoComplete="tel" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} />
      <input aria-label="Code from your manager" placeholder="6-digit code" inputMode="numeric" maxLength={6} value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} />
      <input aria-label="Choose a PIN" placeholder="Choose a PIN (4–6 digits)" type="password" inputMode="numeric" maxLength={6} autoComplete="new-password" value={f.pin} onChange={(e) => setF({ ...f, pin: e.target.value })} />
      <input aria-label="Repeat PIN" placeholder="Repeat PIN" type="password" inputMode="numeric" maxLength={6} autoComplete="new-password" value={f.pin2} onChange={(e) => setF({ ...f, pin2: e.target.value })} />
      <button type="submit" disabled={busy}>{busy ? "Setting up…" : "Set up this phone"}</button>
    </form>
  );
}

// ── unlock ───────────────────────────────────────────────────────────────────────────────────
function Unlock({ deviceToken, err, setErr, busy, setBusy, onIn, onForget }) {
  const [pin, setPin] = useState("");
  const submit = async (e) => {
    e.preventDefault(); setErr(""); setBusy(true);
    try { onIn(await call("/v1/tap/app/login", { method: "POST", body: { device_token: deviceToken, pin } })); }
    catch (x) { setErr(x.message); setPin(""); if (x.status === 401 && /no longer signed in/i.test(x.message)) onForget(); }
    finally { setBusy(false); }
  };
  return (
    <form onSubmit={submit} style={{ paddingTop: 40 }}>
      <h1>Vula Pay</h1>
      <p className="muted">Enter your PIN</p>
      {err && <div className="err" role="alert">{err}</div>}
      <input className="pin" aria-label="PIN" type="password" inputMode="numeric" maxLength={6} autoFocus autoComplete="current-password" value={pin} onChange={(e) => setPin(e.target.value)} />
      <button type="submit" disabled={busy || pin.length < 4}>{busy ? "Checking…" : "Unlock"}</button>
      <button type="button" className="ghost" onClick={onForget}>Not you? Set up again</button>
    </form>
  );
}

// ── home ─────────────────────────────────────────────────────────────────────────────────────
function Home({ token, onLock, onExpired }) {
  const [me, setMe] = useState(null);
  const [bills, setBills] = useState(() => { try { return JSON.parse(LS.get("vp.bills") || "[]"); } catch { return []; } });
  const [online, setOnline] = useState(navigator.onLine);
  const [live, setLive] = useState(false);
  const [err, setErr] = useState("");
  const [form, setForm] = useState({ desc: "", amount: "", phone: "", more: false });
  const [recents, setRecents] = useState(() => { try { return JSON.parse(LS.get("vp.recents") || "[]"); } catch { return []; } });
  const [creating, setCreating] = useState(false);
  const [note, setNote] = useState("");
  const [installEvt, setInstallEvt] = useState(null);
  const [alerts, setAlerts] = useState("unknown");
  const [slip, setSlip] = useState(null);           // paid bill whose slip is printing on screen
  const [slipRun, setSlipRun] = useState(0);
  const [volume, setVol] = useState(() => getVolume());
  const seenPaid = useRef(new Set());   // bills already celebrated (seeded by the FIRST load only)
  const seeded = useRef(false);
  const cursor = useRef("");

  const guard = useCallback((x) => { if (x.status === 401) onExpired(); else setErr(x.message); }, [onExpired]);
  const persist = (list) => { LS.set("vp.bills", JSON.stringify(list.slice(0, 20))); return list; };

  const upsert = useCallback((b) => setBills((cur) => {
    const i = cur.findIndex((x) => x.id === b.id);
    const next = i >= 0 ? cur.map((x, j) => (j === i ? { ...x, ...b } : x)) : [b, ...cur];
    return persist(next);
  }), []);

  const loadBills = useCallback(async () => {
    try {
      const d = await call("/v1/tap/app/bills", { token });
      // Payments that happened before the app opened shouldn't beep. Reconnect reloads must NOT
      // seed this, or a payment made during the gap would be swallowed silently.
      if (!seeded.current) { seeded.current = true; d.bills.forEach((b) => { if (b.status === "paid") seenPaid.current.add(b.id); }); }
      setBills(persist(d.bills));
      cursor.current = cursor.current || d.server_time;
    } catch (x) { guard(x); }
  }, [token, guard]);

  useEffect(() => {
    (async () => { try { setMe(await call("/v1/tap/app/me", { token })); } catch (x) { guard(x); } })();
    loadBills();
  }, [token, loadBills, guard]);

  useEffect(() => {
    if (!slip) return undefined;
    const k = (e) => { if (e.key === "Escape") setSlip(null); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [slip]);

  useEffect(() => {
    const on = () => setOnline(true), off = () => setOnline(false);
    window.addEventListener("online", on); window.addEventListener("offline", off);
    const bip = (e) => { e.preventDefault(); setInstallEvt(e); };
    window.addEventListener("beforeinstallprompt", bip);
    return () => { window.removeEventListener("online", on); window.removeEventListener("offline", off); window.removeEventListener("beforeinstallprompt", bip); };
  }, []);

  // live status: fetch-based SSE so the Authorization header can be sent; reconnect with backoff
  useEffect(() => {
    let stop = false, ctrl, delay = 1000;
    const run = async () => {
      while (!stop) {
        ctrl = new AbortController();
        try {
          const q = cursor.current ? `?since=${encodeURIComponent(cursor.current)}` : "";
          const r = await fetch(`${API}/v1/tap/app/events${q}`, { headers: { Authorization: `Bearer ${token}` }, signal: ctrl.signal });
          if (r.status === 401) { onExpired(); return; }
          if (!r.ok || !r.body) throw new Error("stream");
          setLive(true); delay = 1000;
          const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "";
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += dec.decode(value, { stream: true });
            let i;
            while ((i = buf.indexOf("\n\n")) >= 0) {
              const block = buf.slice(0, i); buf = buf.slice(i + 2);
              const ev = /^event: (.+)$/m.exec(block)?.[1], data = /^data: (.+)$/m.exec(block)?.[1];
              if (!ev || !data) continue;
              const d = JSON.parse(data);
              if (d.cursor) cursor.current = d.cursor;
              if (ev === "bill") {
                if (d.status === "paid" && !d.is_test && !seenPaid.current.has(d.id)) { seenPaid.current.add(d.id); buzz(); setSlip(d); setSlipRun((n) => n + 1); }
                upsert(d);
              }
            }
          }
        } catch { /* fall through to reconnect */ }
        setLive(false);
        if (stop) return;
        await new Promise((res) => setTimeout(res, delay)); delay = Math.min(delay * 2, 15000);
        if (!stop) loadBills();
      }
    };
    run();
    return () => { stop = true; ctrl?.abort(); };
  }, [token, upsert, onExpired, loadBills]);

  useEffect(() => { (async () => {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return setAlerts("unsupported");
    if (Notification.permission === "denied") return setAlerts("blocked");
    const reg = await navigator.serviceWorker.getRegistration("/pay/"); const sub = await reg?.pushManager.getSubscription();
    setAlerts(sub ? "on" : "off");
  })().catch(() => setAlerts("unsupported")); }, []);

  const enableAlerts = async () => {
    setErr("");
    try {
      if (!me?.push_public_key) return setErr("Alerts aren't set up for this business yet.");
      const perm = await Notification.requestPermission();
      if (perm !== "granted") return setAlerts("blocked");
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8(me.push_public_key) });
      const j = sub.toJSON();
      await call("/v1/tap/app/push", { token, method: "POST", body: { endpoint: j.endpoint, keys: j.keys } });
      setAlerts("on");
    } catch (x) { guard(x instanceof ApiError ? x : new ApiError("Couldn't turn on alerts on this phone.", 0)); }
  };

  const create = async (e) => {
    e.preventDefault(); setErr(""); setNote("");
    if (!form.desc.trim()) return setErr("What is the bill for? For example “Beginner lesson”.");
    if (!RAND_RE.test(form.amount.trim())) return setErr("Enter the amount in rands, for example 500 or 500.50.");
    setCreating(true);
    try {
      const cents = Math.round(Number(form.amount.replace(",", ".")) * 100);
      const b = await call("/v1/tap/app/bills", { token, method: "POST", body: { amount_cents: cents, description: form.desc.trim(), customer_phone: form.phone.trim() || null } });
      upsert(b);
      const rec = [{ d: form.desc.trim(), a: form.amount.trim() }, ...recents.filter((r) => r.d !== form.desc.trim() || r.a !== form.amount.trim())].slice(0, 6);
      setRecents(rec); LS.set("vp.recents", JSON.stringify(rec));
      setNote(b.bill_code ? `Bill created. If they pay from a different phone, give them code ${b.bill_code}.` : "Bill created — ask the customer to tap your tag.");
      setForm({ desc: "", amount: "", phone: "", more: false });
    } catch (x) { guard(x); } finally { setCreating(false); }
  };

  const act = async (id, action) => { setErr(""); try { await call(`/v1/tap/app/bills/${id}/${action}`, { token, method: "POST" }); await loadBills(); } catch (x) { guard(x); } };
  const open = bills.find((b) => b.status === "open" || b.status === "claimed");
  const isIos = /iphone|ipad/i.test(navigator.userAgent) && !window.matchMedia("(display-mode: standalone)").matches;

  return (
    <>
      <div className="row"><div><h1>{me?.merchant || "Vula Pay"}</h1><div className="muted"><span className="dot" style={{ background: live ? C.ok : "#9ca3af" }} />{me ? `${me.name}${me.sees_all ? " · manager" : ""}` : ""} · {live ? "live" : "connecting…"}</div></div>
        <button className="ghost small" onClick={onLock}>Lock</button></div>
      {!online && <div className="banner">You're offline. You can see recent bills, but creating a bill needs a connection.</div>}
      {me && me.mode === "off" && <div className="banner">Tap to Pay is switched off for this business.</div>}
      {err && <div className="err" role="alert">{err}</div>}

      <form className="card" onSubmit={create}>
        <h2>New bill</h2>
        {recents.length > 0 && <div className="chips">{recents.map((r) => <button type="button" key={r.d + r.a} className="chip" onClick={() => setForm({ ...form, desc: r.d, amount: r.a })}>{r.d} · R{r.a}</button>)}</div>}
        <input aria-label="What for" placeholder="What for? e.g. Beginner lesson" value={form.desc} onChange={(e) => setForm({ ...form, desc: e.target.value })} maxLength={120} />
        <input aria-label="Amount in rands" placeholder="Amount (R)" inputMode="decimal" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />
        {form.more
          ? <input aria-label="Customer WhatsApp number" placeholder="Customer's WhatsApp number" inputMode="tel" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          : <button type="button" className="ghost small" onClick={() => setForm({ ...form, more: true })}>+ Customer's number (optional)</button>}
        <button type="submit" disabled={creating || !online || !me?.tag || me?.mode === "off"}>{creating ? "Creating…" : "Create bill"}</button>
        {me && !me.tag && <div className="muted">You don't have a tag yet — ask your manager to create one.</div>}
        {note && <div className="muted" style={{ color: C.ok }}>{note}</div>}
      </form>

      {open && me?.tag && (
        <div className="card" style={{ textAlign: "center" }}>
          <div className="muted">Waiting for the customer — they tap your tag, or scan this</div>
          <img src={me.tag.qr} alt="Your QR code" width={190} height={190} style={{ background: "#fff", margin: "8px auto", display: "block" }} />
        </div>
      )}

      <h2 style={{ marginTop: 18 }}>Bills</h2>
      {bills.length === 0 && <div className="muted">No bills yet.</div>}
      {bills.map((b) => (b.status === "paid" && !b.is_test
        ? <div key={b.id} className="card paid-card">
            <div className="row"><span className="pill paid">Paid</span><span className="muted">{b.description}</span></div>
            <div className="big">{rands(b.total_cents ?? b.subtotal_cents)}</div>
            <div className="muted">Bill {rands(b.subtotal_cents)}{b.tip_cents ? ` + ${rands(b.tip_cents)} tip` : ""}{b.share_cents != null ? ` · your share ${rands(b.share_cents)}` : ""}</div>
            <button className="ghost small" style={{ marginTop: 8 }} onClick={() => { setSlip(b); setSlipRun((n) => n + 1); }}>View slip</button>
          </div>
        : <div key={b.id} className="card">
            <div className="row"><span style={{ fontWeight: 600 }}>{b.description}{b.is_test ? " (test)" : ""}</span><span className={`pill ${b.status}`}>{b.status === "abandoned" ? "unpaid" : b.status === "needs_follow_up" ? "follow up" : b.status}</span></div>
            <div className="big" style={{ fontSize: 22 }}>{rands(b.subtotal_cents)}</div>
            {(b.status === "abandoned" || b.status === "needs_follow_up") && (
              <div className="row" style={{ marginTop: 6 }}>
                <span className="muted" style={{ fontSize: 12 }}>{b.status === "abandoned" ? "Customer left without paying" : "Reminders finished — follow up yourself"}</span>
                <button className="ghost small" onClick={() => act(b.id, "remind")}>Resend link</button>
              </div>)}
            {(b.status === "open" || b.status === "claimed") && (
              <div className="row" style={{ marginTop: 6 }}>
                {b.status === "claimed" && <button className="ghost small" onClick={() => act(b.id, "release")}>Release</button>}
                <button className="ghost small" onClick={() => act(b.id, "cancel")}>Cancel bill</button>
              </div>)}
          </div>))}

      <div className="card">
        <h2>This phone</h2>
        {alerts === "off" && <button className="ghost" onClick={enableAlerts}>Turn on payment alerts</button>}
        {alerts === "on" && <div className="muted">Payment alerts are on.</div>}
        {alerts === "blocked" && <div className="muted">Alerts are blocked in your phone's settings for this app.</div>}
        {alerts === "unsupported" && <div className="muted">{isIos ? "On iPhone: tap Share → Add to Home Screen, then open Vula Pay from your home screen to get alerts." : "This browser can't show payment alerts. You'll still get a WhatsApp message."}</div>}
        <button className="ghost" aria-label={`Slip sound volume: ${volume}`} onClick={() => { const n = nextVolume(volume); setVol(n); setVolume(n); if (n !== "off") playPrintSound(0.4); }}>{volume === "off" ? "Slip sound: off" : `Slip sound: ${volume}`}</button>
        {installEvt && <button className="ghost" onClick={async () => { installEvt.prompt(); await installEvt.userChoice; setInstallEvt(null); }}>Install the app</button>}
      </div>
      {slip && (
        <div className="ov" role="dialog" aria-modal="true" aria-label="Payment slip">
          <PrintedSlip key={slipRun} sound={volume !== "off"} data={{
            merchant: me?.merchant, description: slip.description, served_by: me && !me.sees_all ? me.name : null,
            bill_cents: slip.subtotal_cents, tip_cents: slip.tip_cents || 0, total_cents: slip.total_cents ?? slip.subtotal_cents,
            paid_at: slip.paid_at, ref: slip.ref }}
            extra={slip.share_cents != null ? [{ label: "Your share", value: rands(slip.share_cents), strong: true }] : []} />
          <div className="bar">
            <button onClick={() => setSlipRun((n) => n + 1)} className="ghost">Replay</button>
            <button onClick={() => setSlip(null)}>Done</button>
          </div>
        </div>
      )}
    </>
  );
}
