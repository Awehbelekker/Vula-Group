/**
 * PayApp.jsx — "Vula Pay": the coach/cashier app for Tap to Pay (installable PWA at /pay/).
 *
 * Flow: enrol once (manager's 6-digit code + your WhatsApp number + a PIN) → unlock with PIN →
 * create a bill → the customer taps your tag → the card flips to PAID live (vibration + sound),
 * with your share. Creating a bill needs a connection; the app shell and your recent bills open
 * offline. Everything money-related comes from the server — nothing is computed here.
 */
import "./pay.css";
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

/** Is this #rrggbb dark enough for white text on it (contrast of 4.5:1 or better)? */
export function darkEnough(hex) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return false;
  const ch = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  const lum = 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  return 1.05 / (lum + 0.05) >= 4.5;
}

const buzz = () => { try { navigator.vibrate?.([120, 60, 120]); } catch { /* unsupported */ } };

function urlB64ToUint8(s) {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const raw = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

// ── styles (self-contained: this page does not load the dashboard's CSS) ─────────────────────
const C = { ok: "var(--vp-ok)", off: "var(--vp-off)" };      // colours live in pay.css

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

  return <><div className="wrap">{screen}</div></>;
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
    (async () => {
      try {
        const m = await call("/v1/tap/app/me", { token });
        setMe(m);
        // the business's own colour, so the app looks like theirs (pay.css derives the tints from it)
        // …unless it is too pale to carry white text (the hero card and the buttons sit on it)
        if (m.brand?.accent && darkEnough(m.brand.accent)) document.documentElement.style.setProperty("--vp-green", m.brand.accent);
      } catch (x) { guard(x); }
    })();
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
  const liveTab = (b) => b.bill_type === "tab" && (b.status === "open" || b.status === "claimed");
  const shown = bills.filter((b) => !liveTab(b));     // open tabs live in the Tabs card
  const open = shown.find((b) => b.status === "open" || b.status === "claimed");
  const isIos = /iphone|ipad/i.test(navigator.userAgent) && !window.matchMedia("(display-mode: standalone)").matches;

  return (
    <>
      <div className="row head">
        {me?.brand?.logo && <img className="logo" src={me.brand.logo} alt="" />}
        <div className="grow"><h1>{me?.merchant || "Vula Pay"}</h1><div className="muted"><span className="dot" style={{ background: live ? C.ok : C.off }} />{me ? `${me.name}${me.sees_all ? " · manager" : ""}` : ""} · {live ? "live" : "connecting…"}</div></div>
        <button className="ghost small" onClick={onLock}>Lock</button></div>
      {!online && <div className="banner">You're offline. You can see recent bills, but creating a bill needs a connection.</div>}
      {me && me.mode === "off" && <div className="banner">Tap to Pay is switched off for this business.</div>}
      {err && <div className="err" role="alert">{err}</div>}

      <Income token={token} guard={guard} bump={bills} name={me?.name} />
      <Scores token={token} guard={guard} bump={bills} />
      <MyShifts token={token} guard={guard} online={online} />
      <Floor token={token} guard={guard} online={online} />
      <Tabs token={token} guard={guard} online={online} bump={bills} />

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
          <img src={me.tag.qr} alt="Your QR code" width={190} height={190} className="qr" style={{ margin: "8px auto", display: "block" }} />
        </div>
      )}

      <h2 style={{ marginTop: 18 }}>Bills</h2>
      {shown.length === 0 && <div className="muted">No bills yet.</div>}
      {shown.map((b) => (b.status === "paid" && !b.is_test
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

// ── bar tabs: the customer opens a tab by tapping a tag (WhatsApp); staff add what they order ──
const TAB_RE = /^\d{1,5}([.,]\d{1,2})?$/;

function Tabs({ token, guard, online, bump }) {
  const [tabs, setTabs] = useState(null);           // null = loading; "off" = tabs switched off
  const [openId, setOpenId] = useState("");
  const [f, setF] = useState({ desc: "", qty: "1", price: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [quick, setQuick] = useState(() => { try { return JSON.parse(LS.get("vp.tabquick") || "[]"); } catch { return []; } });

  const load = useCallback(async () => {
    try { setTabs((await call("/v1/tap/app/tabs", { token })).tabs); }
    catch (x) { if (x.status === 409) setTabs("off"); else if (x.status) guard(x); }
  }, [token, guard]);

  // reload when a bill event arrives (a tab opened, was claimed or paid) and every 30s
  useEffect(() => { load(); }, [load, bump]);
  useEffect(() => { const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);

  if (tabs === null || tabs === "off") return null;

  const replace = (t) => setTabs((cur) => cur.map((x) => (x.id === t.id ? { ...x, ...t } : x)));
  const add = async (e, tab) => {
    e.preventDefault(); setErr("");
    const qty = Number(f.qty);
    if (!f.desc.trim()) return setErr("What did they order?");
    if (!Number.isInteger(qty) || qty < 1 || qty > 99) return setErr("Quantity is 1 to 99.");
    if (!TAB_RE.test(f.price.trim())) return setErr("Enter the price in rands, for example 45 or 45.50.");
    setBusy(true);
    try {
      const unit = Math.round(Number(f.price.trim().replace(",", ".")) * 100);
      replace(await call(`/v1/tap/app/tabs/${tab.id}/items`, { token, method: "POST", body: { description: f.desc.trim(), quantity: qty, unit_cents: unit } }));
      const q = [{ d: f.desc.trim(), p: f.price.trim() }, ...quick.filter((r) => r.d !== f.desc.trim())].slice(0, 8);
      setQuick(q); LS.set("vp.tabquick", JSON.stringify(q));
      setF({ desc: "", qty: "1", price: "" });
    } catch (x) { if (x.status === 401) guard(x); else setErr(x.message); await load(); } finally { setBusy(false); }
  };
  const voidItem = async (tab, it) => {
    if (!window.confirm(`Remove ${it.quantity} × ${it.description} from ${tab.name}'s tab?`)) return;
    setErr("");
    try { replace(await call(`/v1/tap/app/tabs/${tab.id}/items/${it.id}/void`, { token, method: "POST" })); }
    catch (x) { if (x.status === 401) guard(x); else setErr(x.message); }
  };

  return (
    <div className="card">
      <div className="row"><h2 style={{ margin: 0 }}>Open tabs</h2><button className="ghost small" onClick={load}>Refresh</button></div>
      {err && <div className="err" role="alert">{err}</div>}
      {tabs.length === 0 && <div className="muted" style={{ marginTop: 6 }}>No open tabs. A customer taps a tag and chooses “Open a tab”.</div>}
      {tabs.map((t) => (
        <div key={t.id} className="tab">
          <button type="button" className="tab-head" aria-expanded={openId === t.id} onClick={() => { setOpenId(openId === t.id ? "" : t.id); setErr(""); }}>
            <span><strong>{t.name}</strong> <span className="muted">{t.number}</span></span>
            <span>{t.status === "claimed" && <span className="pill claimed">paying</span>} {rands(t.total_cents)}</span>
          </button>
          {openId === t.id && (
            <div className="tab-body">
              {t.items.length === 0 && <div className="muted">Nothing on this tab yet.</div>}
              {t.items.map((it) => (
                <div key={it.id} className="row tab-line">
                  <span>{it.quantity} × {it.description}</span>
                  <span>{rands(it.line_cents)} {t.status === "open" && <button type="button" className="ghost small" aria-label={`Remove ${it.description}`} onClick={() => voidItem(t, it)}>✕</button>}</span>
                </div>))}
              {t.status === "claimed"
                ? <div className="muted" style={{ marginTop: 6 }}>The customer is paying now. Add new orders after, or they open a new tab.</div>
                : <form onSubmit={(e) => add(e, t)}>
                    {quick.length > 0 && <div className="chips">{quick.map((r) => <button type="button" key={r.d} className="chip" onClick={() => setF({ ...f, desc: r.d, price: r.p })}>{r.d} · R{r.p}</button>)}</div>}
                    <input aria-label="Item" placeholder="What? e.g. Castle Lite" value={f.desc} maxLength={80} onChange={(e) => setF({ ...f, desc: e.target.value })} />
                    <div className="row">
                      <input aria-label="Quantity" placeholder="Qty" inputMode="numeric" value={f.qty} style={{ width: 80 }} onChange={(e) => setF({ ...f, qty: e.target.value })} />
                      <input aria-label="Price each in rands" placeholder="Price each (R)" inputMode="decimal" value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} />
                    </div>
                    <button type="submit" disabled={busy || !online}>{busy ? "Adding…" : `Add to ${t.name}'s tab`}</button>
                  </form>}
            </div>)}
        </div>))}
    </div>
  );
}

// ── service at the table: who is calling, the orders to confirm, and each table's bill ────────
// (vula_mind/vula/tap/tableside.py). Everything shown is the server's; this only asks and taps.
export function Floor({ token, guard, online }) {
  const [floor, setFloor] = useState(null);
  const [menu, setMenu] = useState(null);
  const [openId, setOpenId] = useState("");
  const [line, setLine] = useState({ product: "", qty: "1", desc: "", price: "", note: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const seen = useRef(null);

  const load = useCallback(async () => {
    try {
      const d = await call("/v1/tap/app/floor", { token });
      const waiting = [...d.calls.filter((c) => c.status === "open").map((c) => c.id), ...d.orders.map((o) => o.id)];
      if (seen.current && waiting.some((id) => !seen.current.has(id))) buzz();     // something new is waiting
      seen.current = new Set(waiting);
      setFloor(d);
    } catch (x) { if (x.status) guard(x); }
  }, [token, guard]);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);

  if (!floor || !(floor.settings.calls || floor.settings.orders)) return null;

  const run = async (path, body) => {
    setBusy(true); setErr("");
    try { await call(path, { token, method: "POST", body: body || {} }); await load(); return true; }
    catch (x) { if (x.status === 401) guard(x); else setErr(x.message); await load(); return false; }
    finally { setBusy(false); }
  };
  const openTable = async (id) => {
    setOpenId(openId === id ? "" : id); setErr("");
    if (!menu) { try { setMenu((await call("/v1/tap/app/menu", { token })).menu); } catch (x) { guard(x); } }
  };
  const addLine = async (e, t) => {
    e.preventDefault();
    const qty = Number(line.qty);
    if (!Number.isInteger(qty) || qty < 1) return setErr("How many? Type a whole number.");
    let body;
    if (line.product) body = { product_id: line.product, quantity: qty, note: line.note.trim() };
    else {
      if (!line.desc.trim()) return setErr("Choose something from the menu, or type what it was.");
      if (!TAB_RE.test(line.price.trim())) return setErr("Type the price in rands, like 60 or 60.50.");
      const [r, c = ""] = line.price.trim().replace(",", ".").split(".");
      body = { description: line.desc.trim(), quantity: qty, unit_cents: Number(r) * 100 + Number(c.padEnd(2, "0")), note: line.note.trim() };
    }
    if (await run(`/v1/tap/app/tables/${t.id}/items`, body)) setLine({ product: "", qty: "1", desc: "", price: "", note: "" });
  };

  return (
    <div className="card">
      <h2>Tables</h2>
      {err && <div className="err" role="alert">{err}</div>}
      {floor.calls.map((c) => (
        <div key={c.id} className={`call${c.status === "open" ? " call-open" : ""}`}>
          <div><strong>{c.table}</strong> · {c.what}
            <div className="muted">{c.status === "accepted" ? `${c.accepted_by || "Someone"} is on it` : c.waited_minutes ? `Waiting ${c.waited_minutes} min` : "Just now"}</div></div>
          {c.status === "open"
            ? <button className="small" disabled={busy || !online} onClick={() => run(`/v1/tap/app/calls/${c.id}/accept`)}>I'm going</button>
            : <button className="ghost small" disabled={busy || !online} onClick={() => run(`/v1/tap/app/calls/${c.id}/done`)}>Done</button>}
        </div>))}
      {floor.orders.map((o) => (
        <div key={o.id} className="call call-open">
          <div><strong>{o.table}</strong> · new order · {rands(o.total_cents)}
            {o.items.map((i) => <div key={i.product_id} className="tab-line">{i.quantity} x {i.name}</div>)}
            {o.note && <div className="muted">Note: {o.note}</div>}
            <div className="row" style={{ marginTop: 6, justifyContent: "flex-start" }}>
              <button className="small" disabled={busy || !online} onClick={() => run(`/v1/tap/app/orders/${o.id}/confirm`)}>Confirm</button>
              <button className="ghost small" disabled={busy || !online} onClick={() => run(`/v1/tap/app/orders/${o.id}/reject`, { reason: "" })}>Can't do it</button>
            </div></div>
        </div>))}
      {floor.tables.length === 0 && <div className="muted">No tables are yours yet. Ask your manager to add them.</div>}
      {floor.tables.map((t) => (
        <div key={t.id} className="tab">
          <button className="tab-head" onClick={() => openTable(t.id)} aria-expanded={openId === t.id}>
            <span>{t.label}<span className="muted"> · {t.waiter || "nobody yet"}</span></span>
            <span>{t.bill ? rands(t.bill.total_cents) : "No bill"}{t.bill?.status === "claimed" ? " · paying" : ""}</span>
          </button>
          {openId === t.id && (
            <div className="tab-body">
              {!t.waiter_id && <button className="ghost small" disabled={busy} onClick={() => run(`/v1/tap/app/tables/${t.id}/take`)}>This is my table</button>}
              {(t.bill?.items || []).map((i) => (
                <div key={i.id} className="row tab-line"><span>{i.quantity} x {i.description}</span>
                  <span>{rands(i.line_cents)}{t.bill.status === "open" && <button className="ghost small" style={{ marginLeft: 8 }} disabled={busy} aria-label={`Remove ${i.description}`} onClick={() => run(`/v1/tap/app/tables/${t.id}/items/${i.id}/void`)}>Remove</button>}</span></div>))}
              {t.bill?.status === "claimed"
                ? <div className="banner">The customer is paying now. Add more once they've paid.</div>
                : (
                  <form onSubmit={(e) => addLine(e, t)}>
                    <select value={line.product} onChange={(e) => setLine({ ...line, product: e.target.value })} aria-label="Menu item">
                      <option value="">Not on the menu (type it)</option>
                      {(menu || []).map((m) => <option key={m.id} value={m.id}>{m.name} · {rands(m.price_cents)}</option>)}
                    </select>
                    {!line.product && <>
                      <input value={line.desc} maxLength={80} placeholder="What was it?" aria-label="Description" onChange={(e) => setLine({ ...line, desc: e.target.value })} />
                      <input value={line.price} inputMode="decimal" placeholder="Price in rands" aria-label="Price in rands" onChange={(e) => setLine({ ...line, price: e.target.value })} />
                    </>}
                    <input value={line.qty} inputMode="numeric" aria-label="How many" onChange={(e) => setLine({ ...line, qty: e.target.value })} />
                    <input value={line.note} maxLength={40} placeholder="Note for the kitchen (optional)" aria-label="Note for the kitchen" onChange={(e) => setLine({ ...line, note: e.target.value })} />
                    <button disabled={busy || !online}>Add to {t.label}</button>
                  </form>)}
            </div>)}
        </div>))}
    </div>
  );
}

// ── my numbers and the team's board (vula_mind/vula/tap/scoreboard.py). All counted by the server.
// My own tips show here; the board everyone sees never carries tips.
const wait = (s) => (s == null ? "–" : s < 60 ? `${s}s` : `${Math.round(s / 60)} min`);
export function Scores({ token, guard, bump }) {
  const [period, setPeriod] = useState("today");
  const [d, setD] = useState(null);
  const [open, setOpen] = useState(false);
  const load = useCallback(async () => {
    try { setD(await call(`/v1/tap/app/scores?period=${period}`, { token })); } catch (x) { if (x.status) guard(x); }
  }, [token, guard, period]);
  useEffect(() => { load(); }, [load, bump]);
  if (!d) return null;
  const me = d.me;
  return (
    <div className="card">
      <div className="row"><h2 style={{ margin: 0 }}>Where I stand</h2>
        <div className="chips" style={{ margin: 0 }}>{[["today", "Today"], ["week", "7 days"], ["month", "30 days"]].map(([k, l]) => (
          <button key={k} className="chip" aria-pressed={period === k} onClick={() => setPeriod(k)}>{l}</button>))}</div></div>
      <div className="stats">
        <div><span className="muted">Sales</span><strong>{rands(me.sales_cents)}</strong></div>
        <div><span className="muted">My tips</span><strong>{rands(me.tips_cents)}</strong></div>
        <div><span className="muted">Bills</span><strong>{me.bills}</strong></div>
        <div><span className="muted">Tables answered</span><strong>{me.calls}{me.calls ? ` · ${wait(me.avg_wait_seconds)}` : ""}</strong></div>
      </div>
      <div className="muted">{me.rank ? `You're number ${me.rank} of ${me.of} with ${me.points} points.` : "No points yet for this period."}
        {me.streak_days > 1 ? ` ${me.streak_days} days in a row.` : ""}{me.badges.length ? ` ${me.badges.join(" · ")}.` : ""}</div>
      {d.board.length > 0 && <button className="ghost small" style={{ marginTop: 8 }} aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide the team board" : "Show the team board"}</button>}
      {open && (
        <div style={{ marginTop: 8 }}>
          {d.board.map((r) => (
            <div key={r.member_id} className="row tab-line"><span><strong>{r.rank}. {r.name}</strong>{r.badges.length ? <span className="muted"> · {r.badges.join(" · ")}</span> : null}
              <div className="muted">{rands(r.sales_cents)}{r.tips_cents != null ? ` · ${rands(r.tips_cents)} tips` : ""} · {r.bills} bills · {r.calls} {r.calls === 1 ? "table" : "tables"}{r.calls ? ` in ${wait(r.avg_wait_seconds)}` : ""}</div></span>
              <strong>{r.points}</strong></div>))}
          <div className="muted" style={{ marginTop: 6 }}>Points: {d.points_rule}{d.tips_on_board ? "" : " Tips are not shown here."}</div>
        </div>)}
    </div>
  );
}

// ── what I've earned, my target and the last two weeks (vula_mind/vula/tap/scoreboard.py income).
// Every amount is the server's. The target is my own, kept on this phone only; the ring just
// compares the server's figure with it.
const TARGET_RE = /^\d{1,6}([.,]\d{1,2})?$/;
export function Income({ token, guard, bump, name }) {
  const [d, setD] = useState(null);
  const [target, setTarget] = useState(() => { try { return JSON.parse(LS.get("vp.target") || "null"); } catch { return null; } });
  const [edit, setEdit] = useState(null);          // null | { period, amount }
  const [err, setErr] = useState("");
  const load = useCallback(async () => {
    try { setD(await call("/v1/tap/app/income?days=14", { token })); } catch (x) { if (x.status) guard(x); }
  }, [token, guard]);
  useEffect(() => { load(); }, [load, bump]);
  if (!d) return null;

  const period = target?.period === "week" ? "this_week" : "today";
  const earned = d[period].earned_cents;
  const goal = target?.cents || 0;
  const pct = goal ? Math.max(0, Math.min(100, Math.round((earned * 100) / goal))) : 0;
  const R = 34, CIRC = 2 * Math.PI * R;
  const top = Math.max(1, ...d.days.map((x) => x.earned_cents));
  const saveTarget = (e) => {
    e.preventDefault();
    if (!TARGET_RE.test(edit.amount.trim())) return setErr("Type your target in rands, like 800.");
    const [r, c = ""] = edit.amount.trim().replace(",", ".").split(".");
    const t = { period: edit.period, cents: Number(r) * 100 + Number(c.padEnd(2, "0")) };
    setTarget(t); LS.set("vp.target", JSON.stringify(t)); setEdit(null); setErr("");
  };
  const first = (name || "").split(" ")[0];

  return (
    <div className="card hero">
      <div className="hero-top">
        <div className="grow">
          <div className="hero-hi">{first ? `Hi ${first}` : "Today"}</div>
          <div className="muted hero-lbl">Earned today</div>
          <div className="hero-big">{rands(d.today.earned_cents)}</div>
          <div className="muted hero-lbl">{rands(d.today.tips_cents)} in tips · {d.today.bills} bill{d.today.bills === 1 ? "" : "s"} · {rands(d.today.sales_cents)} sold</div>
        </div>
        {goal > 0 && (
          <div className="ring" role="img" aria-label={`${pct}% of my ${target.period === "week" ? "weekly" : "daily"} target of ${rands(goal)}`}>
            <svg viewBox="0 0 80 80" width="84" height="84">
              <circle cx="40" cy="40" r={R} className="ring-bg" />
              <circle cx="40" cy="40" r={R} className="ring-fg" strokeDasharray={CIRC} strokeDashoffset={CIRC * (1 - pct / 100)} transform="rotate(-90 40 40)" />
            </svg>
            <span>{pct}%</span>
          </div>)}
      </div>
      {goal > 0 && <div className="hero-lbl">{pct >= 100 ? "Target reached." : `${rands(Math.max(0, goal - earned))} to go`} · my {target.period === "week" ? "week" : "day"} target {rands(goal)}</div>}
      <div className="bars" role="img" aria-label="What I earned each day, last 14 days">
        {d.days.map((x) => <span key={x.date} title={`${x.date}: ${rands(x.earned_cents)}`} style={{ height: `${Math.max(4, Math.round((x.earned_cents * 100) / top))}%` }} className={x.earned_cents ? "" : "zero"} />)}
      </div>
      <div className="hero-foot">
        <div><span className="hero-lbl">This week</span><strong>{rands(d.this_week.earned_cents)}</strong></div>
        <div><span className="hero-lbl">Last 7 days</span><strong>{rands(d.last_7.earned_cents)}</strong></div>
        <div><span className="hero-lbl">Last 14 days</span><strong>{rands(d.days.reduce((n, x) => n + x.earned_cents, 0))}</strong></div>
      </div>
      {edit ? (
        <form onSubmit={saveTarget} className="hero-form">
          <select value={edit.period} aria-label="Target for" onChange={(e) => setEdit({ ...edit, period: e.target.value })}><option value="day">Each day</option><option value="week">Each week</option></select>
          <input value={edit.amount} inputMode="decimal" placeholder="Target in rands" aria-label="Target in rands" onChange={(e) => setEdit({ ...edit, amount: e.target.value })} />
          {err && <div className="err" role="alert">{err}</div>}
          <div className="row"><button type="submit">Save my target</button>
            {target && <button type="button" className="ghost" onClick={() => { setTarget(null); LS.del("vp.target"); setEdit(null); }}>Remove</button>}</div>
          <div className="hero-lbl">Your target is your own. It stays on this phone.</div>
        </form>
      ) : <button className="hero-btn" onClick={() => setEdit({ period: target?.period || "day", amount: target ? String(target.cents / 100) : "" })}>{target ? "Change my target" : "Set a target for myself"}</button>}
    </div>
  );
}

// ── my shifts and when I can work (vula_mind/vula/tap/shifts.py). All of it is my own. ─────────
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const SHIFT_LABEL = { published: "Please confirm", confirmed: "Confirmed", declined: "You can't make it", sick: "Off sick" };
export function MyShifts({ token, guard, online }) {
  const [d, setD] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [askId, setAskId] = useState("");
  const [reason, setReason] = useState("");
  const [open, setOpen] = useState(false);
  const [w, setW] = useState({ weekday: "4", start: "17:00", end: "23:00" });
  const [off, setOff] = useState({ from_date: "", to_date: "", note: "" });
  const load = useCallback(async () => {
    try { setD(await call("/v1/tap/app/shifts", { token })); } catch (x) { if (x.status === 401) guard(x); }
  }, [token, guard]);
  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t); }, [load]);
  if (!d) return null;
  const nothing = !d.shifts.length && !d.open.length && !d.availability.weekly.length && !d.availability.off.length;

  const run = async (path, method = "POST", body) => {
    setBusy(true); setErr("");
    try { const out = await call(path, { token, method, body: body || (method === "POST" ? {} : undefined) }); await load(); return out; }
    catch (x) { if (x.status === 401) guard(x); else setErr(x.message); return null; }
    finally { setBusy(false); }
  };
  const cant = async (id, sick) => { if (await run(`/v1/tap/app/shifts/${id}/cant`, "POST", { sick, reason: sick ? "" : reason.trim() })) { setAskId(""); setReason(""); } };

  return (
    <div className="card">
      <div className="row"><h2 style={{ margin: 0 }}>My shifts</h2>{d.hours_this_week > 0 && <span className="muted">{d.hours_this_week} h this week</span>}</div>
      {err && <div className="err" role="alert">{err}</div>}
      {d.shifts.length === 0 && <div className="muted" style={{ marginTop: 6 }}>{nothing ? "No shifts yet. Tell your manager when you can work, below." : "No shifts coming up."}</div>}
      {d.shifts.map((s) => (
        <div key={s.id} className={`call${s.status === "published" ? " call-open" : ""}`}>
          <div className="grow"><strong>{s.when}</strong>{s.section ? ` · ${s.section}` : ""}
            <div className="muted">{SHIFT_LABEL[s.status] || s.status}{s.note ? ` · ${s.note}` : ""}</div>
            {(s.status === "published" || s.status === "confirmed") && askId !== s.id && (
              <div className="row" style={{ marginTop: 6, justifyContent: "flex-start" }}>
                {s.status === "published" && <button className="small" disabled={busy || !online} onClick={() => run(`/v1/tap/app/shifts/${s.id}/confirm`)}>Confirm</button>}
                <button className="ghost small" disabled={busy} onClick={() => { setAskId(s.id); setReason(""); }}>I can't work this</button>
              </div>)}
            {askId === s.id && (
              <div style={{ marginTop: 6 }}>
                <input value={reason} maxLength={120} placeholder="Why? (your manager sees this)" aria-label="Why you can't work this shift" onChange={(e) => setReason(e.target.value)} />
                <div className="row" style={{ justifyContent: "flex-start" }}>
                  <button className="small" disabled={busy || !online} onClick={() => cant(s.id, false)}>Can't make it</button>
                  <button className="small" disabled={busy || !online} onClick={() => cant(s.id, true)}>I'm sick</button>
                  <button className="ghost small" onClick={() => setAskId("")}>Back</button>
                </div>
              </div>)}
          </div>
        </div>))}
      {d.open.length > 0 && <h2 style={{ marginTop: 12 }}>Shifts that need someone</h2>}
      {d.open.map((s) => (
        <div key={s.id} className="call"><div><strong>{s.when}</strong>{s.section ? ` · ${s.section}` : ""}<div className="muted">{s.hours} hours</div></div>
          <button className="small" disabled={busy || !online} onClick={() => run(`/v1/tap/app/shifts/${s.id}/take`)}>I'll take it</button></div>))}
      <button className="ghost small" style={{ marginTop: 10 }} aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide when I can work" : "When I can work"}</button>
      {open && (
        <div style={{ marginTop: 8 }}>
          <div className="muted">Your manager plans around these. They can still ask you outside them.</div>
          {d.availability.weekly.map((r) => (
            <div key={r.id} className="row tab-line"><span>{WEEKDAYS[r.weekday]} · {r.start}–{r.end}</span>
              <button className="ghost small" disabled={busy} aria-label={`Remove ${WEEKDAYS[r.weekday]} ${r.start}`} onClick={() => run(`/v1/tap/app/availability/${r.id}`, "DELETE")}>Remove</button></div>))}
          <form onSubmit={(e) => { e.preventDefault(); run("/v1/tap/app/availability/weekly", "POST", { weekday: Number(w.weekday), start: w.start, end: w.end }); }}>
            <div className="trio">
              <select value={w.weekday} aria-label="Day" onChange={(e) => setW({ ...w, weekday: e.target.value })}>{WEEKDAYS.map((n, i) => <option key={n} value={i}>{n}</option>)}</select>
              <input type="time" value={w.start} aria-label="From" onChange={(e) => setW({ ...w, start: e.target.value })} />
              <input type="time" value={w.end} aria-label="Until" onChange={(e) => setW({ ...w, end: e.target.value })} />
            </div>
            <button className="ghost" disabled={busy || !online}>Add a time I can work</button>
          </form>
          <h2 style={{ marginTop: 12 }}>Days I'm away</h2>
          {d.availability.off.map((r) => (
            <div key={r.id} className="row tab-line"><span>{r.from === r.to ? r.from : `${r.from} to ${r.to}`}{r.note ? ` · ${r.note}` : ""}</span>
              <button className="ghost small" disabled={busy} aria-label={`Remove days away from ${r.from}`} onClick={() => run(`/v1/tap/app/availability/${r.id}`, "DELETE")}>Remove</button></div>))}
          <form onSubmit={async (e) => { e.preventDefault(); if (await run("/v1/tap/app/availability/off", "POST", { from_date: off.from_date, to_date: off.to_date || off.from_date, note: off.note })) setOff({ from_date: "", to_date: "", note: "" }); }}>
            <div className="trio">
              <input type="date" required value={off.from_date} aria-label="First day away" onChange={(e) => setOff({ ...off, from_date: e.target.value })} />
              <input type="date" value={off.to_date} aria-label="Last day away" onChange={(e) => setOff({ ...off, to_date: e.target.value })} />
              <input value={off.note} maxLength={80} placeholder="Why (optional)" aria-label="Why" onChange={(e) => setOff({ ...off, note: e.target.value })} />
            </div>
            <button className="ghost" disabled={busy || !online}>Add days away</button>
          </form>
        </div>)}
    </div>
  );
}
