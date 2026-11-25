/**
 * Discover.jsx — find a school (the customer app at /flow/ and /flow/find), 24 Nov 2026.
 *
 * The schools that asked to be listed (vula_mind/vula/commerce/lesson_directory.py,
 * GET /v1/flow/directory): by name, or nearest first once the customer taps "Near me". Their
 * position is sent once to sort the list and isn't kept, here or on the server. Every figure on a
 * card (the from-price, the distance, the conditions) is the server's.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { myAccount } from "./account";
import AccountCard from "./AccountCard.jsx";
import { Rated } from "./Progress.jsx";

const STATE = { requested: "Waiting for the school", confirmed: "Confirmed", declined: "Declined", cancelled: "Cancelled", completed: "Done", no_show: "Missed" };

const SPORT = { surf: "Surf", sup: "SUP", kite: "Kite", windsurf: "Windsurf", wing: "Wing foil", foil: "Foil", bodyboard: "Bodyboard", kayak: "Kayak", skate: "Skate", other: "Other" };
const RATING = { good: "Good now", marginal: "Marginal now", poor: "Poor now" };

function SchoolMap({ schools }) {
  const ref = useRef(null);
  useEffect(() => {
    let dead = false, map = null;
    Promise.all([import("leaflet"), import("leaflet/dist/leaflet.css")]).then(([mod]) => {
      if (dead || !ref.current) return;
      const L = mod.default || mod;
      map = L.map(ref.current, { scrollWheelZoom: false });
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap contributors", maxZoom: 17 }).addTo(map);
      const css = getComputedStyle(document.documentElement);     // the app's own colours, from flow.css
      const ink = css.getPropertyValue("--fc-blue-text").trim(), fill = css.getPropertyValue("--fc-teal").trim();
      const points = schools.map((s) => {
        const at = [s.spot.lat, s.spot.lng];
        const link = document.createElement("a");                  // built as elements: a school's name is never read as HTML
        link.href = `/flow/${s.shop}?via=find`; link.textContent = s.name; link.className = "fc-link";
        const box = document.createElement("div"); box.append(link, document.createElement("br"), s.spot.name);
        L.circleMarker(at, { radius: 10, weight: 3, color: ink, fillColor: fill, fillOpacity: 0.9 }).addTo(map).bindPopup(box);
        return at;
      });
      if (points.length === 1) map.setView(points[0], 12);
      else if (points.length) map.fitBounds(points, { padding: [32, 32], maxZoom: 13 });
      else map.setView([-30.5, 24.5], 5);                          // nothing listed: all of South Africa
    }).catch(() => {});
    return () => { dead = true; if (map) map.remove(); };
  }, [schools]);
  return <div ref={ref} className="fc-map" role="img" aria-label="Map of schools" />;
}

function Card({ s }) {
  return (
    <a className="fc-card fc-school" href={`/flow/${s.shop}?via=find`}>
      <span className="fc-school-top">
        <img src={s.logo_url || "/partners/flowcrew-mark.png"} alt="" />
        <span><b>{s.name} <Rated rating={s.rating} /></b><span className="fc-sub">{[s.spot.name, s.town].filter(Boolean).join(" · ")}</span></span>
        {s.distance_km !== undefined && <span className="fc-km">{s.distance_km < 100 ? s.distance_km.toFixed(1) : Math.round(s.distance_km)} km</span>}
      </span>
      {s.about && <span className="fc-sub">{s.about}</span>}
      <span className="fc-row fc-chips">
        {s.now && <span className={`fc-chip ${s.now.rating}`}>{RATING[s.now.rating]}</span>}
        {s.sports.map((k) => <span key={k} className="fc-chip">{SPORT[k] || k}</span>)}
        {s.hire && <span className="fc-chip">Gear hire</span>}
      </span>
      {s.now && <span className="fc-sub">{s.now.text}</span>}
      {s.coach_count > 0 && <span className="fc-faces">
        {s.coaches.map((c, i) => (c.photo_url ? <img key={i} src={c.photo_url} alt="" /> : <span key={i} className="fc-face">{c.name.slice(0, 1)}</span>))}
        <span className="fc-sub">{s.coaches.map((c) => c.name).slice(0, 3).join(", ")}{s.coach_count > 3 ? ` and ${s.coach_count - 3} more` : ""}</span>
      </span>}
      {s.from_cents !== null && <span className="fc-from">Lessons from <b>R{Math.floor(s.from_cents / 100).toLocaleString("en-ZA").replace(/[\u00a0,]/g, " ")}</b></span>}
    </a>
  );
}

export default function Discover({ api, acct, onSignedOut }) {
  const [mine, setMine] = useState([]);           // the signed-in customer's bookings, at every school
  useEffect(() => {
    let live = true;
    if (!acct) { setMine([]); return undefined; }
    myAccount(acct.token).then((a) => live && setMine(a.requests.filter((r) => r.status === "requested" || r.status === "confirmed"))).catch(() => {});
    return () => { live = false; };
  }, [acct]);
  const [d, setD] = useState(null);
  const [error, setError] = useState("");
  const [at, setAt] = useState(null);             // {lat, lng} once the customer asked for "near me"
  const [locating, setLocating] = useState(false);
  const [q, setQ] = useState("");
  const [sport, setSport] = useState("");
  const [view, setView] = useState("list");
  useEffect(() => {
    let live = true;
    setError("");
    api("directory", at ? `?lat=${at.lat}&lng=${at.lng}` : "").then((x) => live && setD(x)).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [api, at]);
  useEffect(() => { document.title = "Find a school · FlowCrew"; }, []);
  const near = () => {
    if (!navigator.geolocation) { setError("This phone can't share its location. Search by town instead."); return; }
    setLocating(true); setError("");
    navigator.geolocation.getCurrentPosition(
      (p) => { setLocating(false); setAt({ lat: p.coords.latitude.toFixed(3), lng: p.coords.longitude.toFixed(3) }); },   // to about 100 m: enough to sort by
      () => { setLocating(false); setError("Your location wasn't shared. Search by town instead."); },
      { maximumAge: 300000, timeout: 10000 });
  };
  const all = d?.schools || [];
  const sports = useMemo(() => [...new Set(all.flatMap((s) => s.sports))], [all]);
  const shown = useMemo(() => {
    const words = q.trim().toLowerCase();
    return all.filter((s) => (!sport || s.sports.includes(sport))
      && (!words || [s.name, s.town, s.spot.name, s.about, ...s.lessons].join(" ").toLowerCase().includes(words)));
  }, [all, q, sport]);
  return (
    <main className="fc fc-find">
      <header className="fc-top"><img src="/partners/flowcrew-mark.png" alt="" /><div><h1>FlowCrew</h1><p>Find a surf or kite school</p></div></header>
      {view !== "mine" && <section className="fc-card">
        <label className="fc-field">Search<input className="fc-in" type="search" value={q} placeholder="School, town or beach" onChange={(e) => setQ(e.target.value)} /></label>
        <div className="fc-row">
          <button type="button" className={`fc-btn${at ? " alt" : ""}`} onClick={near} disabled={locating}>{locating ? "Finding you…" : at ? "Nearest first" : "Near me"}</button>
        </div>
        {sports.length > 1 && <div className="fc-row fc-chips">
          {["", ...sports].map((k) => <button key={k || "all"} type="button" className={`fc-chip fc-pick${sport === k ? " on" : ""}`} aria-pressed={sport === k} onClick={() => setSport(k)}>{k ? SPORT[k] || k : "All"}</button>)}
        </div>}
        <p className="fc-sub">{at ? "Your location was used once to put the nearest school first. It isn't kept." : "Tap Near me to see the closest school first."}</p>
        {error && <p className="fc-err" role="alert">{error}</p>}
      </section>}
      {!d && !error && view !== "mine" && <p className="fc-sub">Loading…</p>}
      {d && view === "map" && <SchoolMap schools={shown} />}
      {d && view === "list" && shown.map((s) => <Card key={s.shop} s={s} />)}
      {d && view !== "mine" && shown.length === 0 && <section className="fc-card"><p className="fc-sub">{all.length === 0 ? "No schools are listed yet. If your school sent you a link, open that." : "No school matches that. Try another town or clear the search."}</p></section>}
      {view === "mine" && mine.length === 0 && <section className="fc-card"><h3>Your bookings</h3><p className="fc-sub">{acct ? "Nothing booked yet. Pick a school to ask for a lesson or gear." : "Sign in to see your bookings from every school on any phone. On this phone they also show on each school's own page."}</p></section>}
      {view === "mine" && mine.length > 0 && <section className="fc-card">
        <h3>Your bookings</h3>
        <ul className="fc-lines">{mine.map((r) => (
          <li key={r.token}><a className="fc-link" href={`/flow/${r.shop}?via=find`}>{r.school}</a><span className="fc-sub">{r.summary} · {r.when || `asked for ${r.wanted}`} · {STATE[r.status] || r.status}</span></li>))}</ul>
      </section>}
      {view === "mine" && <AccountCard acct={acct} onSignedOut={onSignedOut} />}
      {view === "mine" && <p className="fc-foot"><a className="fc-link" href="/signin">Run a school? Sign in</a></p>}
      <p className="fc-foot">Powered by FlowCrew<img src="/partners/flowcrew-mark.png" alt="" /></p>
      <nav className="fc-bar" aria-label="Main">
        {[["list", "Schools"], ["map", "Map"], ["mine", "Bookings"]].map(([id, label]) => (
          <button key={id} type="button" className="fc-tab" aria-current={view === id ? "page" : undefined} onClick={() => { setView(id); window.scrollTo(0, 0); }}>
            <span className="fc-bead"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{BAR[id]}</svg></span>{label}
            {id === "mine" && mine.length > 0 && <span className="fc-count">{mine.length}</span>}
          </button>))}
      </nav>
    </main>
  );
}

const BAR = {
  list: <path d="M5 7h14M5 12h14M5 17h14" />,
  map: <><path d="M12 21s6-5.4 6-10.2A6 6 0 0 0 6 10.8C6 15.6 12 21 12 21z" /><circle cx="12" cy="10.5" r="2.2" /></>,
  mine: <path d="M6 4h12v16l-6-3.5L6 20z" />,
};
