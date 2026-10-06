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
import { schedulePrintSound } from "./printSound";

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

/** Plays the slip sound (style "modern" by default; "classic" = the older dot-matrix chatter).
 *  Returns false, silently, when the browser hasn't allowed audio yet (no tap so far). */
export function playPrintSound(duration = 2.8, style = (() => { try { return localStorage.getItem("vp.soundstyle") || "modern"; } catch { return "modern"; } })()) {
  const ctx = audioCtx();
  if (!ctx) return false;
  try {
    if (ctx.state === "suspended") Promise.resolve(ctx.resume()).catch(() => {});
    if (ctx.state !== "running") return false;            // autoplay blocked (no tap yet): stay silent
    const comp = ctx.createDynamicsCompressor();          // a safety net so nothing can clip on a phone speaker
    comp.threshold.value = -14; comp.ratio.value = 4; comp.attack.value = 0.003; comp.release.value = 0.2;
    comp.connect(ctx.destination);
    schedulePrintSound(ctx, comp, ctx.currentTime + 0.15, duration, style);   // 0.15 = the CSS feed delay
    return true;
  } catch { return false; }
}

export const slipCss = `
.slip-stage{display:flex;flex-direction:column;align-items:center;width:100%}
.slip-printer{width:100%;position:relative;z-index:3;flex:none;box-sizing:border-box;height:calc(58px + env(safe-area-inset-top,0px));padding-top:env(safe-area-inset-top,0px);background:linear-gradient(#4a5057 0%,#2b3035 55%,#1f2327 100%);border-radius:0 0 18px 18px;box-shadow:0 10px 26px rgba(0,0,0,.55),inset 0 -2px 0 rgba(255,255,255,.07)}
.slip-printer:before{content:"";position:absolute;left:max(14px,calc(50% - 190px));right:max(14px,calc(50% - 190px));bottom:11px;height:8px;background:#07090a;border-radius:5px;box-shadow:inset 0 2px 4px rgba(0,0,0,.9),0 1px 0 rgba(255,255,255,.12)}
.slip-printer.busy:after{content:"";position:absolute;left:max(18px,calc(50% - 186px));right:max(18px,calc(50% - 186px));bottom:13px;height:3px;border-radius:2px;background:linear-gradient(90deg,transparent,#8dffb8 45%,#d8ffe6 50%,#8dffb8 55%,transparent);background-size:60% 100%;background-repeat:no-repeat;filter:blur(.4px);animation:slipHead .55s ease-in-out infinite alternate}
@keyframes slipHead{from{background-position:0 0}to{background-position:100% 0}}
.slip-led{position:absolute;right:18px;top:calc(14px + env(safe-area-inset-top,0px));width:9px;height:9px;border-radius:50%;background:#22c55e;box-shadow:0 0 10px #22c55e}
.slip-led.busy{animation:slipBlink .35s steps(2) infinite}
@keyframes slipBlink{50%{opacity:.25}}
.slip-window{position:relative;width:min(320px,88vw);overflow:hidden;margin-top:-4px;padding-bottom:26px;filter:drop-shadow(0 12px 14px rgba(0,0,0,.45))}
.slip-window:after{content:"";position:absolute;left:0;right:0;top:0;height:110px;pointer-events:none;background:linear-gradient(#fffef9 0%,rgba(255,254,249,.86) 30%,rgba(255,254,249,0) 100%);animation:slipInk .5s ease calc(var(--slip-dur,2.8s) + .15s) forwards}
@keyframes slipInk{to{opacity:0}}
.slip-paper{background:#fffef9 repeating-linear-gradient(0deg,rgba(0,0,0,.018) 0 1px,transparent 1px 3px);color:#1b1b1b;font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:13.5px;line-height:1.55;padding:20px 20px 26px;position:relative;transform:translateY(-102%);transform-origin:50% 0;animation:slipFeed var(--slip-dur,2.8s) steps(46,end) .15s forwards,slipSettle .7s cubic-bezier(.2,.8,.3,1) calc(var(--slip-dur,2.8s) + .15s) forwards}
.slip-paper.static{animation:none;transform:none}
@keyframes slipFeed{to{transform:translateY(0)}}
@keyframes slipSettle{0%{transform:translateY(0)}28%{transform:translateY(12px) rotate(.5deg)}58%{transform:translateY(-3px) rotate(-.25deg)}100%{transform:none}}
.slip-paper:after{content:"";position:absolute;left:0;right:0;bottom:-11px;height:12px;background:linear-gradient(-45deg,transparent 7px,#fffef9 0) 0 0/14px 12px repeat-x,linear-gradient(45deg,transparent 7px,#fffef9 0) 0 0/14px 12px repeat-x}
.slip-c{text-align:center}.slip-b{font-weight:700}.slip-hr{border:0;border-top:1px dashed #9a9a92;margin:10px 0}
.slip-row{display:flex;justify-content:space-between;gap:10px}.slip-row span:last-child{white-space:nowrap}
.slip-total{font-size:20px;font-weight:800}.slip-small{font-size:11.5px;color:#6a6a62}
.slip-stamp{display:inline-block;border:2px solid #15803d;color:#15803d;padding:1px 10px;border-radius:4px;font-weight:800;letter-spacing:.12em;transform:rotate(-4deg);margin:2px 0 4px}
.slip-test{display:inline-block;background:#fde68a;color:#92400e;font-weight:700;padding:0 8px;border-radius:3px;font-size:11px}
.slip-bars{height:32px;margin:10px auto 2px;width:80%;background:repeating-linear-gradient(90deg,#111 0 2px,transparent 2px 4px,#111 4px 5px,transparent 5px 8px,#111 8px 11px,transparent 11px 12px)}
@media (prefers-reduced-motion:reduce){.slip-paper{animation:none;transform:none}.slip-window:after{display:none}.slip-led.busy,.slip-printer.busy:after{animation:none}}
@media print{.slip-printer,.noprint{display:none!important}.slip-window{filter:none;margin:0;width:100%}.slip-window:after{display:none}.slip-paper{animation:none!important;transform:none!important}body{background:#fff!important}}
`;

export default function PrintedSlip({ data, extra = [], sound = true, duration = 2.8 }) {
  const still = reducedMotion();
  const ledRef = useRef(null);
  const printerRef = useRef(null);
  const dur = still ? 0 : duration;

  useEffect(() => {
    if (still) return undefined;
    if (sound) playPrintSound(duration);
    const t = setTimeout(() => { ledRef.current?.classList.remove("busy"); printerRef.current?.classList.remove("busy"); }, (duration + 0.2) * 1000);
    return () => clearTimeout(t);
  }, [still, sound, duration]);

  const d = data || {};
  return (
    <div className="slip-stage" role="img" aria-label={`Receipt from ${d.merchant}: ${rands(d.total_cents)} paid`}>
      <style>{slipCss}</style>
      <div ref={printerRef} className={`slip-printer${still ? "" : " busy"}`}><span ref={ledRef} className={`slip-led${still ? "" : " busy"}`} /></div>
      <div className="slip-window">
        <div className={`slip-paper${still ? " static" : ""}`} style={{ "--slip-dur": `${dur}s` }}>
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
