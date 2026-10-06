/**
 * PrintedSlip — a till slip that "prints" out of a slot, with a soft printing sound and a ding.
 * Shared by the customer receipt page (/r/<token>) and the Vula Pay coach app, so both sides see
 * the same thing. Self-contained styles (those pages don't load the dashboard CSS).
 *
 * Props: data {merchant, description, served_by, bill_cents, tip_cents, total_cents, vat_number,
 *        vat_cents, paid_at, ref, is_test}, extra [{label, value, strong}] (e.g. the coach's share),
 *        sound (bool), duration (seconds, default 2.8). Remount (change `key`) to replay.
 * Respects prefers-reduced-motion: the slip simply appears, silently.
 */
import { useEffect, useRef } from "react";
import "./PrintedSlip.css";
import { schedulePrintSound, FEED_DELAY, LEVELS, getVolume } from "./printSound";

export const rands = (c) =>
  `R ${(Number(c || 0) / 100).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`.replace(/ /g, " ");

const fmtDate = (iso) => {
  try {
    const d = new Date(iso);
    if (!iso || Number.isNaN(d.getTime())) return "";
    return d.toLocaleString("en-ZA", { dateStyle: "medium", timeStyle: "short", timeZone: "Africa/Johannesburg" });
  } catch { return ""; }
};

export const reducedMotion = () => { try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { return false; } };

let ctxSingleton = null;
const audioCtx = () => {
  try { ctxSingleton = ctxSingleton || new (window.AudioContext || window.webkitAudioContext)(); return ctxSingleton; } catch { return null; }
};

/** Plays the slip sound (style "chime" by default: no printer noise; see printSound.js for the others).
 *  Returns false, silently, when the browser hasn't allowed audio yet (no tap so far). */
export function playPrintSound(duration = 2.8, style = (() => { try { return localStorage.getItem("vp.soundstyle") || "chime"; } catch { return "chime"; } })()) {
  const vol = getVolume();
  if (vol === "off") return false;
  const ctx = audioCtx();
  if (!ctx) return false;
  try {
    if (ctx.state === "suspended") Promise.resolve(ctx.resume()).catch(() => {});
    if (ctx.state !== "running") return false;            // autoplay blocked (no tap yet): stay silent
    const comp = ctx.createDynamicsCompressor();          // a safety net so nothing can clip on a phone speaker
    comp.threshold.value = -14; comp.ratio.value = 4; comp.attack.value = 0.003; comp.release.value = 0.2;
    comp.connect(ctx.destination);
    schedulePrintSound(ctx, comp, ctx.currentTime + FEED_DELAY, duration, style, LEVELS[vol]);   // FEED_DELAY = the CSS feed delay
    return true;
  } catch { return false; }
}


export default function PrintedSlip({ data, extra = [], sound = true, duration = 2.8 }) {
  const still = reducedMotion();
  const ledRef = useRef(null);
  const printerRef = useRef(null);
  const dur = still ? 0 : duration;

  useEffect(() => {
    if (still) return undefined;
    if (sound) playPrintSound(duration);
    const t = setTimeout(() => { ledRef.current?.classList.remove("busy"); printerRef.current?.classList.remove("busy"); }, (FEED_DELAY + duration + 0.2) * 1000);
    return () => clearTimeout(t);
  }, [still, sound, duration]);

  const d = data || {};
  return (
    <div className="slip-stage" role="img" aria-label={`Receipt from ${d.merchant}: ${rands(d.total_cents)} paid`}>
      <div ref={printerRef} className={`slip-printer${still ? "" : " busy"}`} style={{ "--slip-delay": `${FEED_DELAY}s` }}><span ref={ledRef} className={`slip-led${still ? "" : " busy"}`} /></div>
      <div className="slip-window">
        <div className={`slip-paper${still ? " static" : ""}`} style={{ "--slip-dur": `${dur}s`, "--slip-delay": `${FEED_DELAY}s` }}>
          <div className="slip-c slip-b" style={{ fontSize: 15 }}>{d.merchant}</div>
          {d.vat_number && <div className="slip-c slip-small">VAT no. {d.vat_number}</div>}
          <hr className="slip-hr" />
          <div className="slip-c"><span className="slip-stamp">PAID</span></div>
          {d.is_test && <div className="slip-c"><span className="slip-test">TEST PAYMENT</span></div>}
          {fmtDate(d.paid_at) && <div className="slip-c slip-small">{fmtDate(d.paid_at)}</div>}
          <hr className="slip-hr" />
          <div className="slip-row"><span>{d.description}</span><span>{rands(d.bill_cents)}</span></div>
          {d.served_by && <div className="slip-small">Served by {d.served_by}</div>}
          {d.tip_cents > 0 && <div className="slip-row" style={{ marginTop: 4 }}><span>Tip</span><span>{rands(d.tip_cents)}</span></div>}
          <hr className="slip-hr" />
          <div className="slip-row slip-total"><span>TOTAL</span><span>{rands(d.total_cents)}</span></div>
          {d.vat_cents != null && <div className="slip-row slip-small"><span>VAT incl. in bill (15%)</span><span>{rands(d.vat_cents)}</span></div>}
          {extra.map((e) => <div key={e.label} className={`slip-row${e.strong ? " slip-b" : ""}`} style={{ marginTop: 4 }}><span>{e.label}</span><span>{e.value}</span></div>)}
          <hr className="slip-hr" />
          {d.ref && <div className="slip-c slip-small">Ref {d.ref}</div>}
          <div className="slip-bars" aria-hidden="true" />
          <div className="slip-c slip-small">Thank you!</div>
        </div>
      </div>
    </div>
  );
}
