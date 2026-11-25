/**
 * FlowApp.jsx — the FlowCrew customer app (installable PWA at /flow/<business>), 24 Nov 2026.
 *
 * A surf or kite school's customers see today's conditions, ask for a lesson or for gear, and
 * follow their requests. Backend: vula_mind/vula/api/lessons.py (`public`, /v1/flow/<business>).
 * No login: a request made on this phone is opened only by the signed token the server gave back,
 * kept on this phone. Nothing is booked here — the school confirms the time, and says so on
 * WhatsApp. Every price is the server's (the school's own list, worked out there); this screen
 * never adds anything up.
 */
import "./flow.css";
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { myAccount, session } from "./account";
import AccountCard from "./AccountCard.jsx";

const Discover = lazy(() => import("./Discover.jsx"));   // find a school: only loaded where it's shown

const API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const LS = {
  get: (k, d) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode: this visit only */ } },
};
const rand = (cents) => (cents === null || cents === undefined ? "" : `R${Math.floor(cents / 100).toLocaleString("en-ZA").replace(/ |,/g, " ")}${cents % 100 ? "." + String(cents % 100).padStart(2, "0") : ""}`);
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const dayName = (d, i) => (i === 0 ? "Today" : i === 1 ? "Tomorrow" : d.toLocaleDateString("en-ZA", { weekday: "short", day: "numeric", month: "short" }));
const shopFromPath = () => (window.location.pathname.replace(/^\/flow\/?/, "").split("/")[0] || "").toLowerCase();
const RATING = { good: "Good", marginal: "Marginal", poor: "Poor" };
const STATUS = { requested: ["marginal", "Waiting for the school"], confirmed: ["good", "Confirmed"], declined: ["poor", "Declined"], cancelled: ["", "Cancelled"], completed: ["", "Done"], no_show: ["poor", "Missed"] };

async function call(shop, path = "", body, token) {
  let r;
  try {
    r = await fetch(`${API}/v1/flow/${shop}${path}`, body === undefined ? undefined
      : { method: "POST", headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) }, body: JSON.stringify(body) });
  } catch { throw new Error("No connection. Check your signal and try again."); }
  let d = {};
  try { d = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new Error(r.status === 404 ? "This school isn't taking bookings here. Check the link it sent you." : typeof d.detail === "string" ? d.detail : "That didn't go through. Try again.");
  return d;
}

const ICONS = {
  home: <path d="M4 11.5 12 4l8 7.5M6.5 10v9.5h4.5v-6h2v6h4.5V10" />,
  book: <><rect x="4" y="5.5" width="16" height="14.5" rx="2.2" /><path d="M4 10h16M8.3 3.5v4M15.7 3.5v4M12 12.5v4M10 14.5h4" /></>,
  mine: <><path d="M6 4h12v16l-6-3.5L6 20z" /></>,
  chat: <path d="M5 5h14v11H10l-4 3.5V16H5z" />,
};
const Icon = ({ name }) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{ICONS[name]}</svg>;

/** A live camera near the spot. It is its owner's: by default a button to the owner's page; played
 * here only where the owner agreed (the server sends a player address only then), and only once
 * the customer asks, so nobody's data is spent on video they didn't want. */
function Camera({ cam }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="fc-cam">
      <p className="fc-sub"><b>{cam.name}</b> · camera by {cam.owner}</p>
      {cam.kind === "embed" && open && <iframe className="fc-cam-frame" title={cam.name} src={cam.embed_url} allow="autoplay; fullscreen" referrerPolicy="no-referrer" sandbox="allow-scripts allow-same-origin" />}
      <div className="fc-row">
        {cam.kind === "embed" && <button type="button" className="fc-btn alt" onClick={() => setOpen(!open)}>{open ? "Hide the camera" : "Watch live"}</button>}
        <a className={`fc-btn ${cam.kind === "embed" ? "quiet" : "alt"} fc-btn-link`} href={cam.page_url} target="_blank" rel="noreferrer">{cam.kind === "embed" ? "Open their page" : "Watch the live camera"}</a>
      </div>
    </div>
  );
}

function Conditions({ shop, spot }) {
  const [day, setDay] = useState(0);
  const [d, setD] = useState(null);
  useEffect(() => {
    let live = true;
    const when = new Date(); when.setDate(when.getDate() + day);
    setD(null);
    call(shop, `/conditions?date=${iso(when)}`).then((x) => live && setD(x)).catch(() => live && setD({ hours: [], unavailable: true }));
    return () => { live = false; };
  }, [shop, day]);
  if (!spot) return null;
  const hours = (d?.hours || []).filter((h) => Number(h.hour.slice(0, 2)) % 2 === 0);
  const now = day === 0 ? (d?.hours || []).find((h) => Number(h.hour.slice(0, 2)) === new Date().getHours()) || d?.hours?.[0] : d?.hours?.find((h) => h.hour === "08:00");
  return (
    <section className="fc-card" aria-label={`Conditions at ${spot.name}`}>
      <div className="fc-row" style={{ alignItems: "center", justifyContent: "space-between" }}>
        <h3>Conditions at {spot.name}</h3>
        <div className="fc-row" role="group" aria-label="Day">
          {["Today", "Tomorrow"].map((l, i) => <button key={l} type="button" className={`fc-chip pick${day === i ? " on" : ""}`} aria-pressed={day === i} onClick={() => setDay(i)}>{l}</button>)}
        </div>
      </div>
      {day === 0 && d?.report && <p className="fc-note"><b>Seen from the beach at {d.report.time}{d.report.by ? ` by ${d.report.by}` : ""}:</b> {d.report.text}.{d.report.coach_note ? ` “${d.report.coach_note}”` : ""}</p>}
      {(d?.cameras || []).map((c) => <Camera key={c.id} cam={c} />)}
      {!d ? <p className="fc-sub">Reading the forecast…</p> : !hours.length ? <p className="fc-sub">The forecast couldn't be read just now.</p> : (<>
        {d.summary?.headline && (
          <div className="fc-brief">
            <p className="fc-brief-h">{d.summary.headline}</p>
            {d.summary.parts.map((p) => (
              <p key={p.part} className="fc-brief-p"><b>{p.part}</b> <span className="fc-sub">{p.from}–{p.to}</span><br />{p.text}.</p>))}
            <p className="fc-sub">{d.summary.confidence}</p>
          </div>)}
        {now && <p style={{ margin: 0 }}><span className={`fc-chip ${now.rating}`}>{RATING[now.rating]} for learners{day === 0 ? " now" : " at 08:00"}</span> <span className="fc-sub">{now.text}</span></p>}
        <div className="fc-hours">
          {hours.map((h) => (
            <div key={h.hour} className={`fc-hour ${h.rating}`}>
              <b>{h.hour}</b>
              <span>{h.wave_m !== undefined ? `${h.wave_m.toFixed(1)} m` : "—"}</span>
              <span>{h.wind_kn !== undefined ? `${Math.round(h.wind_kn)} kn` : "—"}</span>
            </div>))}
        </div>
        <p className="fc-sub">Swell and wind from the open-sea forecast. A guide only: your coach decides on the day.</p>
      </>)}
    </section>
  );
}

function Home({ shop, info, go }) {
  const both = info.lessons.length > 0 && info.hire.length > 0;
  const [show, setShow] = useState(info.lessons.length ? "lesson" : "hire");      // what the list below shows
  return (<>
    <section className="fc-card fc-hero">
      <div className="fc-glass">
        <h2>Ready to get in the water?</h2>
        <p>{info.note || `Ask ${info.name} here for a lesson or gear, and they'll confirm your time.`}</p>
      </div>
      <div className="fc-row">
        {info.lessons.length > 0 && <button type="button" className="fc-btn white" onClick={() => go("book", { kind: "lesson" })}>Book a lesson</button>}
        {info.hire.length > 0 && <button type="button" className="fc-btn white" onClick={() => go("book", { kind: "hire" })}>Hire gear</button>}
      </div>
    </section>
    <Conditions shop={shop} spot={info.spot} />
    {info.coaches?.length > 0 && <section className="fc-card">
      <h3>Your coaches</h3>
      {info.coaches.map((c, i) => (
        <div key={i} className="fc-coach">
          {c.photo_url ? <img src={c.photo_url} alt="" /> : <span className="fc-face">{c.name.slice(0, 1)}</span>}
          <span><b>{c.name}</b>{c.levels.length > 0 && <span className="fc-sub">Teaches: {c.levels.join(", ").toLowerCase()}</span>}{c.bio && <span className="fc-sub">{c.bio}</span>}</span>
        </div>))}
    </section>}
    {both && <div className="fc-tabs" role="tablist" aria-label="What the school offers">
      {[["lesson", "Lessons"], ["hire", "Gear hire"]].map(([k, label]) => (
        <button key={k} type="button" role="tab" aria-selected={show === k} className={show === k ? "on" : ""} onClick={() => setShow(k)}>{label}</button>))}
    </div>}
    {show === "hire" && info.hire.map((g) => (
      <section key={g.id} className="fc-card">
        <h3>{g.name}</h3>
        {g.about && <p className="fc-sub">{g.about}</p>}
        <ul className="fc-lines">
          {Object.entries(g.prices || {}).map(([k, cents]) => <li key={k}><span>{info.durations[k] || k}</span><b>{rand(cents)}</b></li>)}
          {g.extra_day_cents != null && <li><span>Per day, from 4 days</span><b>{rand(g.extra_day_cents)}</b></li>}
        </ul>
        <button type="button" className="fc-btn alt" onClick={() => go("book", { kind: "hire", item_id: g.id })}>Hire this</button>
      </section>))}
    {show === "lesson" && info.lessons.map((l) => {
      const from = Math.min(...l.group.map((t) => t.single_cents), ...(l.private ? [l.private.single_cents] : []));
      return (
        <section key={l.id} className="fc-card">
          <h3>{l.name}</h3>
          {l.about && <p className="fc-sub">{l.about}</p>}
          <ul className="fc-lines">
            {l.group.map((t) => <li key={t.min}><span>Group, {t.max === t.min ? `${t.min} ${t.min === 1 ? "person" : "people"}` : t.max === null ? `${t.min} or more` : `${t.min} to ${t.max} people`}</span><b>{rand(t.single_cents)}{(t.max ?? 2) > 1 ? " each" : ""}</b></li>)}
            {l.private && <li><span>Private, one on one</span><b>{rand(l.private.single_cents)}</b></li>}
            {l.extras.map((e) => <li key={e.id}><span>{e.name}</span><b>{rand(e.cents)} each</b></li>)}
          </ul>
          <button type="button" className="fc-btn alt" onClick={() => go("book", { kind: "lesson", item_id: l.id })}>Book from {rand(from)}</button>
        </section>);
    })}
  </>);
}

const Opt = ({ title, sub, pick }) => <button type="button" className="fc-opt" onClick={pick}><b>{title}</b>{sub && <span>{sub}</span>}</button>;

const BLANK = { kind: "", item_id: "", level: "", format: "", people: 1, package: null, extras: null, duration: "", wanted_date: "", wanted_part: "" };

function Book({ shop, info, start, onDone, acct }) {
  const [w, setW] = useState({ ...BLANK, ...start });
  const [me, setMe] = useState(() => LS.get("fc.me", { name: "", phone: "" }));
  const [quote, setQuote] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [hist, setHist] = useState([]);              // what was chosen before each answer, for Back
  const set = (patch) => { setError(""); setHist((h) => [...h, w]); setW((cur) => ({ ...cur, ...patch })); };
  const fill = (patch) => setW((cur) => ({ ...cur, ...patch }));   // an answer nobody had to give (one lesson, one format): not a step back
  const lesson = info.lessons.find((l) => l.id === w.item_id);
  const gear = info.hire.find((g) => g.id === w.item_id);
  const extra = lesson?.extras?.[0];
  const hasPackage = lesson && (w.format === "private" ? lesson.private?.package_cents != null : lesson.group.some((t) => t.package_cents != null));
  const days = useMemo(() => Array.from({ length: 10 }, (_, i) => { const d = new Date(); d.setDate(d.getDate() + i); return [iso(d), dayName(d, i)]; }), []);

  // the one question still to answer, in order; "review" when none is
  const step = !w.kind ? "kind" : !w.item_id ? "item"
    : w.kind === "lesson" ? (!w.level ? "level" : !w.format ? "format" : !w.peopleSet ? "people" : hasPackage && w.package === null ? "package" : extra && w.extras === null ? "extras" : !w.wanted_date ? "day" : !w.wanted_part ? "part" : "review")
      : (!w.duration ? "duration" : !w.peopleSet ? "people" : !w.wanted_date ? "day" : !w.wanted_part ? "part" : "review");
  const order = w.kind === "hire" ? ["kind", "item", "duration", "people", "day", "part", "review"] : ["kind", "item", "level", "format", "people", "package", "extras", "day", "part", "review"];

  // a lesson with only one format: no need to ask
  useEffect(() => { if (lesson && !w.format && (!lesson.group.length || !lesson.private)) fill({ format: lesson.group.length ? "group" : "private" }); }, [lesson, w.format]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (w.kind && !w.item_id) { const list = w.kind === "lesson" ? info.lessons : info.hire; if (list.length === 1) fill({ item_id: list[0].id }); } }, [w.kind, w.item_id, info]); // eslint-disable-line react-hooks/exhaustive-deps

  const wanted = useMemo(() => ({ kind: w.kind, item_id: w.item_id, format: w.format || "group", people: w.people, package: !!w.package,
    extras: extra && w.extras ? { [extra.id]: w.people } : null, duration: w.duration }), [w, extra]);
  useEffect(() => {
    if (step !== "review") return undefined;
    let live = true;
    setQuote(null);
    call(shop, "/quote", wanted).then((q) => live && setQuote(q)).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [shop, step, wanted]);

  const back = () => { setError(""); setW(hist[hist.length - 1] || { ...BLANK }); setHist((h) => h.slice(0, -1)); };
  const send = async (e) => {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const d = await call(shop, "/requests", { ...wanted, level: w.level, wanted_date: w.wanted_date, wanted_part: w.wanted_part, name: me.name, phone: me.phone }, acct?.token);
      LS.set("fc.me", me);
      LS.set(`fc.tokens.${shop}`, [d.request.token, ...LS.get(`fc.tokens.${shop}`, [])].slice(0, 20));
      onDone(d.request);
    } catch (err) { setError(err.message); }
    setBusy(false);
  };
  return (
    <section className="fc-card">
      <div className="fc-steps" aria-hidden="true">{order.map((s) => <i key={s} className={order.indexOf(s) <= order.indexOf(step) ? "on" : ""} />)}</div>
      {step === "kind" && (<><h2>What would you like?</h2><div className="fc-opts">
        {info.lessons.length > 0 && <Opt title="A lesson" sub={info.lessons.map((l) => l.name).join(", ")} pick={() => set({ kind: "lesson" })} />}
        {info.hire.length > 0 && <Opt title="Hire gear" sub={info.hire.map((g) => g.name).slice(0, 4).join(", ")} pick={() => set({ kind: "hire" })} />}</div></>)}
      {step === "item" && (<><h2>{w.kind === "lesson" ? "Which lesson?" : "What do you need?"}</h2><div className="fc-opts">
        {(w.kind === "lesson" ? info.lessons : info.hire).map((x) => <Opt key={x.id} title={x.name} sub={x.about || (x.prices ? `from ${rand(Math.min(...Object.values(x.prices)))}` : "")} pick={() => set({ item_id: x.id })} />)}</div></>)}
      {step === "level" && (<><h2>How much have you done before?</h2><div className="fc-opts">
        {Object.entries(info.levels).map(([k, label]) => <Opt key={k} title={label.split(" (")[0]} sub={(label.match(/\((.+)\)/) || [])[1]} pick={() => set({ level: k })} />)}</div></>)}
      {step === "format" && lesson && (<><h2>Group or private?</h2><div className="fc-opts">
        <Opt title="Group" sub={`With others, from ${rand(Math.min(...lesson.group.map((t) => t.single_cents)))} each. At most ${lesson.max_per_coach} per coach.`} pick={() => set({ format: "group" })} />
        <Opt title="Private" sub={`One on one, ${rand(lesson.private.single_cents)}`} pick={() => set({ format: "private" })} /></div></>)}
      {step === "duration" && gear && (<><h2>For how long?</h2><div className="fc-opts">
        {gear.durations.map((d) => <Opt key={d} title={info.durations[d]} sub={gear.prices[d] != null ? `${rand(gear.prices[d])} each` : "priced per day"} pick={() => set({ duration: d })} />)}</div></>)}
      {step === "people" && (<><h2>{w.kind === "hire" ? "How many do you need?" : "How many people?"}</h2><div className="fc-opts">
        {[1, 2, 3, 4, 5, 6, 7, 8].slice(0, w.kind === "hire" ? 6 : 8).map((n) => <Opt key={n} title={w.kind === "hire" ? String(n) : n === 1 ? "Just me" : `${n} people`} pick={() => set({ people: n, peopleSet: true })} />)}</div>
        {w.kind === "lesson" && <p className="fc-sub">More than 8? Message the school and they'll arrange it.</p>}</>)}
      {step === "package" && lesson && (<><h2>One lesson or a package?</h2><div className="fc-opts">
        <Opt title="One lesson" sub="Try it out" pick={() => set({ package: false })} />
        <Opt title={`${lesson.package_sessions} lessons`} sub="Costs less per lesson. Use them whenever it suits you." pick={() => set({ package: true })} /></div></>)}
      {step === "extras" && extra && (<><h2>Need a {extra.name.toLowerCase()}?</h2><p className="fc-sub">{rand(extra.cents)} per person per lesson.</p><div className="fc-opts">
        <Opt title={w.people === 1 ? "Yes please" : "Yes, for everyone"} pick={() => set({ extras: true })} /><Opt title="No thanks" pick={() => set({ extras: false })} /></div></>)}
      {step === "day" && (<><h2>Which day?</h2><div className="fc-opts">{days.map(([d, label]) => <Opt key={d} title={label} pick={() => set({ wanted_date: d })} />)}</div>
        <label className="fc-field">Or another date<input className="fc-in" type="date" min={days[0][0]} onChange={(e) => e.target.value && set({ wanted_date: e.target.value })} /></label></>)}
      {step === "part" && (<><h2>What time of day?</h2><p className="fc-sub">The school confirms the exact time.</p><div className="fc-opts">
        {Object.entries(info.parts).map(([k, label]) => <Opt key={k} title={label} sub={k === "any" ? "Whenever conditions are best" : ""} pick={() => set({ wanted_part: k })} />)}</div></>)}
      {step === "review" && (
        <form onSubmit={send} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <h2>Check and send</h2>
          <p className="fc-sub">{(lesson || gear)?.name}{w.kind === "lesson" ? `, ${w.format}, ${info.levels[w.level]?.split(" (")[0] || ""}` : `, ${info.durations[w.duration]?.toLowerCase()}`} · {new Date(`${w.wanted_date}T12:00:00`).toLocaleDateString("en-ZA", { weekday: "long", day: "numeric", month: "long" })}, {info.parts[w.wanted_part]?.toLowerCase()}</p>
          {!quote ? <p className="fc-sub">Working out the price…</p> : quote.quote_cents === null ? <p className="fc-note">The school will send you the price when they confirm.</p> : (<>
            <ul className="fc-lines">{quote.lines.map((ln, i) => <li key={i}><span>{ln.quantity} × {ln.description}</span><b>{rand(ln.quantity * ln.unit_price_cents)}</b></li>)}</ul>
            <div className="fc-total"><span>Total</span><span>{rand(quote.quote_cents)}</span></div></>)}
          <label className="fc-field">Your name<input className="fc-in" required autoComplete="name" maxLength={80} value={me.name} onChange={(e) => setMe({ ...me, name: e.target.value })} /></label>
          <label className="fc-field">Your WhatsApp number<input className="fc-in" required type="tel" inputMode="tel" autoComplete="tel" placeholder="082 123 4567" value={me.phone} onChange={(e) => setMe({ ...me, phone: e.target.value })} /></label>
          <p className="fc-note">This is a request, not a booking yet. {info.name} will confirm the exact time with you on WhatsApp.</p>
          {error && <p className="fc-err" role="alert">{error}</p>}
          <button type="submit" className="fc-btn" disabled={busy || !quote}>{busy ? "Sending…" : "Send my request"}</button>
        </form>)}
      {step !== "review" && error && <p className="fc-err" role="alert">{error}</p>}
      {hist.length > 0 && <button type="button" className="fc-btn quiet" style={{ flex: "none" }} onClick={back}>Back</button>}
    </section>
  );
}

function Mine({ shop, info, fresh, go, acct, onSignedOut }) {
  const [list, setList] = useState(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    const tokens = LS.get(`fc.tokens.${shop}`, []);
    if (!tokens.length) { setList([]); return; }
    call(shop, "/mine", { tokens }).then((d) => { setList(d.requests); setError(""); }).catch((e) => setError(e.message));
  }, [shop]);
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);
  const cancel = async (r) => {
    if (!window.confirm(`Cancel ${r.summary}?`)) return;
    try { await call(shop, "/cancel", { token: r.token }); load(); } catch (e) { setError(e.message); }
  };
  if (error && !list) return <section className="fc-card"><p className="fc-err" role="alert">{error}</p><button type="button" className="fc-btn alt" onClick={load}>Try again</button></section>;
  if (!list) return <section className="fc-card"><p className="fc-sub">Loading your bookings…</p></section>;
  if (!list.length) return <><section className="fc-card"><h2>Nothing booked yet</h2><p className="fc-sub">Requests you make on this phone show here, with the time once {info.name} confirms.</p><button type="button" className="fc-btn" onClick={() => go("book", {})}>Book a lesson</button></section><AccountCard acct={acct} onSignedOut={onSignedOut} /></>;
  return (<>
    {fresh && <p className="fc-note" role="status">Sent. {info.name} will confirm the time with you on WhatsApp. Reference {fresh.ref}.</p>}
    {error && <p className="fc-err" role="alert">{error}</p>}
    {list.map((r) => {
      const [tone, label] = STATUS[r.status] || STATUS.requested;
      return (
        <section key={r.token} className="fc-card">
          <span className={`fc-chip ${tone}`}>{label}</span>
          <h3>{r.summary}</h3>
          <p className="fc-sub">{r.when ? `${r.when}${r.coach ? ` with ${r.coach}` : ""}` : `Asked for ${r.wanted}`} · {r.ref}</p>
          {r.decline_reason && <p className="fc-note">{r.decline_reason}</p>}
          {r.amount && <div className="fc-total"><span>Total</span><span>{rand(r.amount_cents)}</span></div>}
          {r.status === "confirmed" && info.pay_note && <p className="fc-sub">{info.pay_note}</p>}
          {r.status === "confirmed" && info.form_url && <a className="fc-link" href={info.form_url} target="_blank" rel="noreferrer">Complete the indemnity form</a>}
          {(r.status === "requested" || r.status === "confirmed") && <button type="button" className="fc-btn danger" style={{ flex: "none" }} onClick={() => cancel(r)}>Cancel</button>}
        </section>);
    })}
    <AccountCard acct={acct} onSignedOut={onSignedOut} />
  </>);
}

function Chat({ info }) {
  return (
    <section className="fc-card">
      <h2>Talk to {info.name}</h2>
      <p className="fc-sub">Questions about a lesson, the conditions or your booking go straight to the school on WhatsApp.</p>
      {info.whatsapp ? <a className="fc-btn" style={{ display: "grid", placeItems: "center", textDecoration: "none" }} href={`https://wa.me/${info.whatsapp}`} target="_blank" rel="noreferrer">Open WhatsApp</a>
        : <p className="fc-note">This school hasn't added its WhatsApp number yet.</p>}
      {info.form_url && <a className="fc-link" href={info.form_url} target="_blank" rel="noreferrer">Indemnity form</a>}
    </section>
  );
}

/** The school's own colours, once a customer is on its page: its colour for the buttons and the
 * banner, with text that reads on it. Nothing when the school has chosen none — FlowCrew's look. */
function schoolLook(own) {
  const hex = (c) => (/^#[0-9a-fA-F]{6}$/.test(c || "") ? c : null);
  const a = hex(own?.accent_color);
  if (!a) return undefined;
  const lin = (i) => { const v = parseInt(a.slice(i, i + 2), 16) / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const lum = 0.2126 * lin(1) + 0.7152 * lin(3) + 0.0722 * lin(5);
  const b = hex(own?.secondary_color) || a;
  const dark = lum < 0.4;                                    // white reads on it
  return { "--fc-green": a, "--fc-on": dark ? "white" : "var(--fc-ink)", "--fc-grad": `linear-gradient(135deg, ${a} 0%, ${b} 100%)`,
    "--fc-blue": a, ...(dark ? { "--fc-blue-text": a } : {}) };
}

export default function FlowApp() {
  // came from the directory: a visit, not "my school" — the installed app keeps opening where it did
  const [viaFind] = useState(() => new URLSearchParams(window.location.search).get("via") === "find");
  const [shop] = useState(() => {
    const fromPath = shopFromPath();
    if (fromPath === "find") return "";                     // /flow/find: the directory, whatever school is remembered
    if (fromPath) { if (!viaFind) LS.set("fc.shop", fromPath); return fromPath; }
    const kept = LS.get("fc.shop", "");                     // the installed app opens at /flow/: back to the school it was added from
    if (kept) window.history.replaceState(null, "", `/flow/${kept}`);
    return kept;
  });
  const [info, setInfo] = useState(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("home");
  const [acct, setAcct] = useState(null);        // the signed-in customer: {token, email} — optional
  const [acctKey, setAcctKey] = useState(0);
  useEffect(() => {
    let live = true;
    session().then(async (s) => {
      if (!live || !s) return;
      setAcct(s);
      if (!shop) return;
      try {                                       // their requests at this school, made on any phone, show here too
        const a = await myAccount(s.token);
        const theirs = a.requests.filter((r) => r.shop === shop).map((r) => r.token);
        LS.set(`fc.tokens.${shop}`, [...new Set([...theirs, ...LS.get(`fc.tokens.${shop}`, [])])].slice(0, 20));
        if (!LS.get("fc.me", {}).phone && a.phone) LS.set("fc.me", { name: a.name, phone: a.phone });
        if (live) setAcctKey((k) => k + 1);
      } catch { /* the app still works from this phone's own tokens */ }
    });
    return () => { live = false; };
  }, [shop]);
  const [start, setStart] = useState({});
  const [bookKey, setBookKey] = useState(0);
  const [fresh, setFresh] = useState(null);
  const load = useCallback(() => { if (shop) { setError(""); call(shop).then(setInfo).catch((e) => setError(e.message)); } }, [shop]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (info?.name) document.title = `${info.name} · FlowCrew`; }, [info]);
  // the school's ready-made look, on <html> so the page behind the cards follows; ?theme= lets the
  // school try one on before saving it (only the names the app knows)
  const theme = useMemo(() => {
    const tried = new URLSearchParams(window.location.search).get("theme");
    return ["ocean", "sunset", "sand", "kelp", "night"].includes(tried) ? tried : info?.theme || "";
  }, [info]);
  useEffect(() => {
    if (theme && theme !== "ocean") document.documentElement.dataset.fcTheme = theme; else delete document.documentElement.dataset.fcTheme;
    return () => { delete document.documentElement.dataset.fcTheme; };
  }, [theme]);
  const go = (to, s) => { if (to === "book") { setStart(s || {}); setBookKey((k) => k + 1); } setFresh(null); setTab(to); window.scrollTo(0, 0); };
  const waiting = LS.get(`fc.tokens.${shop}`, []).length;

  if (!shop) return <Suspense fallback={<main className="fc"><p className="fc-sub">Loading…</p></main>}><Discover api={call} acct={acct} onSignedOut={() => setAcct(null)} /></Suspense>;
  if (error && !info) return <main className="fc"><section className="fc-card"><p className="fc-err" role="alert">{error}</p><button type="button" className="fc-btn alt" onClick={load}>Try again</button></section></main>;
  if (!info) return <main className="fc"><p className="fc-sub">Loading…</p></main>;
  return (
    <main className="fc" style={theme ? undefined : schoolLook(info.own_look)}>
      {viaFind && <a className="fc-back" href="/flow/find">‹ All schools</a>}
      <header className="fc-top">
        <img src={info.brand?.logo_url || "/partners/flowcrew-mark.png"} alt="" />
        <div><h1>{info.name}</h1><p>{info.brand?.tagline || (info.spot ? `Lessons and gear at ${info.spot.name}` : "Lessons and gear")}</p></div>
      </header>
      {tab === "home" && <Home shop={shop} info={info} go={go} />}
      {tab === "book" && <Book key={bookKey} shop={shop} info={info} start={start} acct={acct} onDone={(r) => { setFresh(r); setTab("mine"); window.scrollTo(0, 0); }} />}
      {tab === "mine" && <Mine key={acctKey} shop={shop} info={info} fresh={fresh} go={go} acct={acct} onSignedOut={() => setAcct(null)} />}
      {tab === "chat" && <Chat info={info} />}
      <p className="fc-foot">Powered by {info.partner?.name || "FlowCrew"}<img src="/partners/flowcrew-mark.png" alt="" /></p>
      <nav className="fc-bar" aria-label="Main">
        {[["home", "Home"], ["book", "Book"], ["mine", "Bookings"], ["chat", "Chat"]].map(([id, label]) => (
          <button key={id} type="button" className="fc-tab" aria-current={tab === id ? "page" : undefined} onClick={() => (id === "book" ? go("book", {}) : go(id))}>
            <span className="fc-bead"><Icon name={id} /></span>{label}
            {id === "mine" && waiting > 0 && tab !== "mine" && <span className="fc-count" aria-label={`${waiting} on this phone`}>{waiting}</span>}
          </button>))}
      </nav>
    </main>
  );
}
