/**
 * Progress.jsx — the customer's own side of the app (25 Nov 2026): what they have done, the badges
 * that earned, the next good day at this school's beach, and rating a lesson once it is done.
 * Every figure comes from the server, counted from lessons the school marked done. It is the
 * customer's own: nobody is compared with anyone.
 */
import { useEffect, useState } from "react";

const BADGE = { first: "1", five: "5", ten: "10", dawn: "☀", weeks: "3w", sports: "+" };

export function Stars({ value, onPick, label }) {
  return (
    <span className="fc-stars" role={onPick ? "radiogroup" : "img"} aria-label={onPick ? label : `${value} out of 5 stars`}>
      {[1, 2, 3, 4, 5].map((n) => (onPick
        ? <button key={n} type="button" role="radio" aria-checked={value === n} aria-label={`${n} star${n > 1 ? "s" : ""}`} className={n <= value ? "on" : ""} onClick={() => onPick(n)}>★</button>
        : <i key={n} className={n <= Math.round(value) ? "on" : ""}>★</i>))}
    </span>);
}

/** "★ 4.6 (12)" for a school or a coach; nothing while nobody has rated them. */
export const Rated = ({ rating }) => (rating?.count ? <span className="fc-rated">★ {rating.stars.toFixed(1)} <span>({rating.count})</span></span> : null);

export function ProgressCard({ p, everywhere }) {
  if (!p) return null;
  const earned = p.badges.filter((b) => b.earned).length;
  return (
    <section className="fc-card" aria-label="Your progress">
      <div className="fc-prog">
        <div className="fc-prog-n"><b>{p.lessons}</b><span>{p.lessons === 1 ? "lesson done" : "lessons done"}</span></div>
        <div className="fc-prog-t">
          <h3>{p.level ? `Level: ${p.level}` : "Your first lesson is ahead of you"}</h3>
          <p className="fc-sub">{p.level ? (p.next_level ? `Next up: ${p.next_level}. Your coach decides when you're ready.` : "The top level. Keep going.") : "Finished lessons count here once your school marks them done."}</p>
          {p.weeks_running > 1 && <span className="fc-chip good">{p.weeks_running} weeks in a row</span>}
        </div>
      </div>
      <h3>Badges <span className="fc-sub">{earned} of {p.badges.length}</span></h3>
      <ul className="fc-badges">
        {p.badges.map((b) => (
          <li key={b.id} className={b.earned ? "on" : ""}>
            <span className="fc-badge" aria-hidden="true">{BADGE[b.id] || "★"}</span>
            <span><b>{b.name}</b><span className="fc-sub">{b.earned ? "Earned" : b.how}</span></span>
          </li>))}
      </ul>
      <p className="fc-sub">{everywhere ? "Counted from your lessons at every school." : "Counted from lessons booked on this phone. Sign in to keep them on any phone."}</p>
    </section>);
}

export function NextDay({ call, shop, spot, go }) {
  const [d, setD] = useState(undefined);
  useEffect(() => {
    let live = true;
    call(shop, "/next-good-day").then((x) => live && setD(x)).catch(() => live && setD(null));
    return () => { live = false; };
  }, [call, shop]);
  if (!spot || d === undefined || d === null) return null;
  return (
    <section className="fc-card" aria-label="Your next good day">
      <h3>Your next good day</h3>
      {d.next ? (<>
        <p style={{ margin: 0 }}><b>{d.next.day}, in the {d.next.part}</b> looks good at {spot.name}.</p>
        <p className="fc-sub">{d.next.text}. From the forecast — a guide, your coach decides on the day.</p>
        <button type="button" className="fc-btn" onClick={() => go("book", { kind: "lesson", item_id: d.lesson_id, wanted_date: d.next.date, wanted_part: d.next.part })}>Ask for that day</button>
      </>) : <p className="fc-sub">No stand-out day in the next five at {spot.name}. Look again tomorrow.</p>}
    </section>);
}

/** Rate a finished lesson: the school, and the coach when it had one. Sent once. */
export function RateLesson({ call, shop, r, school, onDone }) {
  const [stars, setStars] = useState(0);
  const [coach, setCoach] = useState(0);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const send = async () => {
    setBusy(true); setError("");
    try { await call(shop, "/rate", { token: r.token, stars, coach_stars: r.coach && coach ? coach : null, comment }); onDone(); }
    catch (e) { setError(e.message); setBusy(false); }
  };
  return (
    <div className="fc-rate">
      <b>How was it?</b>
      <label className="fc-rate-row"><span>{school}</span><Stars value={stars} onPick={setStars} label={`Stars for ${school}`} /></label>
      {r.coach && <label className="fc-rate-row"><span>{r.coach}, your coach</span><Stars value={coach} onPick={setCoach} label={`Stars for ${r.coach}`} /></label>}
      <label className="fc-field">Anything to tell the school? (optional)
        <textarea className="fc-in fc-area" rows={2} maxLength={300} value={comment} onChange={(e) => setComment(e.target.value)} /></label>
      <p className="fc-sub">Your stars count towards the averages customers see. Your words go to the school only.</p>
      {error && <p className="fc-err" role="alert">{error}</p>}
      <button type="button" className="fc-btn" disabled={!stars || busy} onClick={send}>{busy ? "Sending…" : "Send my rating"}</button>
    </div>);
}
