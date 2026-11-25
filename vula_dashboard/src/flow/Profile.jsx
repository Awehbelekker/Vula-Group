/**
 * Profile.jsx — the customer's own profile (25 Nov 2026): who they are, what fits them, and for
 * each sport their level and what they can already do; and the coaches who taught them. It needs
 * a sign-in, because it follows the customer to every school. The school reads only what a coach
 * needs, and only through a booking the customer made with it.
 */
import { useEffect, useState } from "react";
import { deleteProfile, getProfile, saveProfile } from "./account";
import { Stars } from "./Progress.jsx";

const num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));

function Edit({ token, data, onSaved, onCancel }) {
  const { profile: p, catalog, account_photo: hasPhoto } = data;
  const [f, setF] = useState(() => ({ name: p.name, phone: p.phone ? `+${p.phone}` : "", birth_year: p.birth_year ?? "", can_swim: p.can_swim,
    height_cm: p.gear.height_cm ?? "", weight_kg: p.gear.weight_kg ?? "", wetsuit: p.gear.wetsuit || "",
    e_name: p.emergency.name, e_phone: p.emergency.phone ? `+${p.emergency.phone}` : "", medical: p.medical, sports: p.sports, photo: !!p.photo_url || (!p.saved && hasPhoto) }));
  const [open, setOpen] = useState(() => Object.keys(p.sports)[0] || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const sport = (id, patch) => setF((cur) => ({ ...cur, sports: { ...cur.sports, [id]: { skills: [], ...cur.sports[id], ...patch } } }));
  const drop = (id) => setF((cur) => { const { [id]: _gone, ...rest } = cur.sports; return { ...cur, sports: rest }; });
  const flip = (id, k) => { const has = (f.sports[id]?.skills || []).includes(k); sport(id, { skills: has ? f.sports[id].skills.filter((x) => x !== k) : [...(f.sports[id]?.skills || []), k] }); };
  const save = async (e) => {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const d = await saveProfile(token, { name: f.name, phone: f.phone, birth_year: num(f.birth_year), can_swim: f.can_swim,
        gear: { height_cm: num(f.height_cm), weight_kg: num(f.weight_kg), wetsuit: f.wetsuit || null },
        emergency: { name: f.e_name, phone: f.e_phone }, medical: f.medical, sports: f.sports, photo: f.photo });
      onSaved(d.profile);
    } catch (err) { setError(err.message); setBusy(false); }
  };
  return (
    <form className="fc-card" onSubmit={save}>
      <h2>Your profile</h2>
      <label className="fc-field">Your name<input className="fc-in" autoComplete="name" maxLength={60} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      <label className="fc-field">Cellphone<input className="fc-in" type="tel" autoComplete="tel" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></label>
      <label className="fc-field">Year of birth<input className="fc-in" inputMode="numeric" maxLength={4} value={f.birth_year} onChange={(e) => setF({ ...f, birth_year: e.target.value.replace(/\D/g, "") })} /></label>
      {hasPhoto && <label className="fc-check"><input type="checkbox" checked={f.photo} onChange={(e) => setF({ ...f, photo: e.target.checked })} /><span>Use my Google picture, so my coach knows who to look for</span></label>}

      <h3>Your sports</h3>
      <p className="fc-sub">Pick each one you do, your level, and what you can already do. After a lesson your coach can confirm the level.</p>
      <div className="fc-row fc-chips">
        {catalog.sports.map((s) => <button key={s.id} type="button" className={`fc-chip fc-pick${f.sports[s.id]?.level ? " on" : ""}`} aria-pressed={open === s.id} onClick={() => setOpen(open === s.id ? "" : s.id)}>{s.name}</button>)}
      </div>
      {catalog.sports.filter((s) => s.id === open).map((s) => {
        const mine = f.sports[s.id] || {};
        return (
          <div key={s.id} className="fc-rate">
            <b>{s.name}</b>
            <label className="fc-field">My level
              <select className="fc-in" value={mine.level || ""} onChange={(e) => (e.target.value ? sport(s.id, { level: e.target.value }) : drop(s.id))}>
                <option value="">I don't do this</option>
                {Object.entries(catalog.levels).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
              </select></label>
            {mine.confirmed && <p className="fc-sub">Confirmed as {catalog.levels[mine.confirmed.level]} by {mine.confirmed.school} on {mine.confirmed.at}.</p>}
            {mine.level && s.skills.map((k) => <label key={k.id} className="fc-check"><input type="checkbox" checked={(mine.skills || []).includes(k.id)} onChange={() => flip(s.id, k.id)} /><span>{k.name}</span></label>)}
          </div>);
      })}

      <h3>What fits you</h3>
      <div className="fc-two">
        <label className="fc-field">Height (cm)<input className="fc-in" inputMode="numeric" maxLength={3} value={f.height_cm} onChange={(e) => setF({ ...f, height_cm: e.target.value.replace(/\D/g, "") })} /></label>
        <label className="fc-field">Weight (kg)<input className="fc-in" inputMode="numeric" maxLength={3} value={f.weight_kg} onChange={(e) => setF({ ...f, weight_kg: e.target.value.replace(/\D/g, "") })} /></label>
      </div>
      <label className="fc-field">Wetsuit size<select className="fc-in" value={f.wetsuit} onChange={(e) => setF({ ...f, wetsuit: e.target.value })}><option value="">Not sure</option>{catalog.wetsuits.map((w) => <option key={w}>{w}</option>)}</select></label>

      <h3>Safety</h3>
      <fieldset className="fc-set"><legend>Can you swim 50 metres without help?</legend>
        {[[true, "Yes"], [false, "No"], [null, "Rather not say"]].map(([v, label]) => <label key={label} className="fc-check"><input type="radio" name="swim" checked={f.can_swim === v} onChange={() => setF({ ...f, can_swim: v })} /><span>{label}</span></label>)}
      </fieldset>
      <label className="fc-field">Emergency contact's name<input className="fc-in" maxLength={60} value={f.e_name} onChange={(e) => setF({ ...f, e_name: e.target.value })} /></label>
      <label className="fc-field">Their cellphone<input className="fc-in" type="tel" value={f.e_phone} onChange={(e) => setF({ ...f, e_phone: e.target.value })} /></label>
      <label className="fc-field">Anything your coach should know? (optional)
        <textarea className="fc-in fc-area" rows={2} maxLength={300} value={f.medical} onChange={(e) => setF({ ...f, medical: e.target.value })} /></label>
      <p className="fc-sub">Only a school you have a booking with sees this, and only for that lesson. The emergency contact and your note are stored encrypted.</p>
      {error && <p className="fc-err" role="alert">{error}</p>}
      <div className="fc-row"><button type="submit" className="fc-btn" disabled={busy}>{busy ? "Saving…" : "Save my profile"}</button>
        <button type="button" className="fc-btn quiet" onClick={onCancel}>Cancel</button></div>
    </form>);
}

/** The profile card on the Me tab, and the coaches below it. Nothing for someone not signed in
 * but one line saying what signing in gives them. */
export default function Profile({ acct, go, onLevels }) {
  const [d, setD] = useState(null);
  const [edit, setEdit] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!acct) { setD(null); return; }
    getProfile(acct.token).then((x) => { setD(x); onLevels?.(x.profile.sports); }).catch((e) => setError(e.message));
  }, [acct]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!acct) return <section className="fc-card"><h3>Your profile</h3><p className="fc-sub">Sign in below to keep a profile: your level in each sport, your sizes and the coaches you've had. It follows you to every school.</p></section>;
  if (error && !d) return <section className="fc-card"><p className="fc-err" role="alert">{error}</p></section>;
  if (!d) return <section className="fc-card"><p className="fc-sub">Loading your profile…</p></section>;
  if (edit) return <Edit token={acct.token} data={d} onCancel={() => setEdit(false)} onSaved={(profile) => { setD({ ...d, profile }); onLevels?.(profile.sports); setEdit(false); window.scrollTo(0, 0); }} />;
  const p = d.profile;
  const name = (id) => d.catalog.sports.find((s) => s.id === id)?.name || id;
  const remove = async () => {
    if (!window.confirm("Delete your profile? Your bookings stay with the schools.")) return;
    try { await deleteProfile(acct.token); setD({ ...d, profile: { ...p, saved: false, name: "", sports: {}, gear: {}, photo_url: null, emergency: { name: "", phone: "" }, medical: "", can_swim: null, birth_year: null, phone: "" } }); onLevels?.({}); }
    catch (e) { setError(e.message); }
  };
  return (<>
    <section className="fc-card" aria-label="Your profile">
      <div className="fc-coach">
        {p.photo_url ? <img src={p.photo_url} alt="" referrerPolicy="no-referrer" /> : <span className="fc-face">{(p.name || acct.email || "?").slice(0, 1).toUpperCase()}</span>}
        <span><b>{p.name || "Your profile"}</b><span className="fc-sub">{p.saved ? [p.gear.wetsuit && `Wetsuit ${p.gear.wetsuit}`, p.can_swim === true && "Swims 50 m", p.emergency.name && "Emergency contact added"].filter(Boolean).join(" · ") || "Add your sizes and an emergency contact" : "Tell your coach who is coming: your level, your sizes, what you can already do."}</span></span>
      </div>
      {Object.keys(p.sports).length > 0 && <ul className="fc-lines">
        {Object.entries(p.sports).map(([id, s]) => (
          <li key={id}><span><b>{name(id)}</b>{s.skills?.length > 0 && <span className="fc-sub"> · {s.skills.length} {s.skills.length === 1 ? "skill" : "skills"} ticked</span>}</span>
            <span className={`fc-chip${s.confirmed ? " good" : ""}`}>{d.catalog.levels[s.level]}{s.confirmed ? " ✓" : ""}</span></li>))}
      </ul>}
      {Object.values(p.sports).some((s) => s.confirmed) && <p className="fc-sub">✓ confirmed by a coach after a lesson.</p>}
      {error && <p className="fc-err" role="alert">{error}</p>}
      <div className="fc-row"><button type="button" className={`fc-btn${p.saved ? " alt" : ""}`} onClick={() => setEdit(true)}>{p.saved ? "Edit my profile" : "Fill in my profile"}</button>
        {p.saved && <button type="button" className="fc-btn quiet" onClick={remove}>Delete</button>}</div>
    </section>
    {d.coaches.length > 0 && <section className="fc-card" aria-label="Your coaches">
      <h3>Your coaches</h3>
      {d.coaches.map((c, i) => (
        <div key={i} className="fc-coach">
          {c.photo_url ? <img src={c.photo_url} alt="" /> : <span className="fc-face">{c.name.slice(0, 1)}</span>}
          <span><b>{c.name}</b><span className="fc-sub">{c.school} · {c.lessons} {c.lessons === 1 ? "lesson" : "lessons"}{c.last ? ` · last ${c.last}` : ""}</span>
            {c.my_stars && <Stars value={c.my_stars} />}
            {go ? <button type="button" className="fc-linkbtn" onClick={() => go(c)}>Book again with {c.name}</button> : <a className="fc-link" href={`/flow/${c.shop}?via=find`}>Book again at {c.school}</a>}</span>
        </div>))}
    </section>}
  </>);
}
