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

/** Printer chatter: short filtered-noise ticks while the paper feeds, then a soft two-note ding. */
export function playPrintSound(duration = 2.8) {
  const ctx = audioCtx();
  if (!ctx) return false;
  try {
    if (ctx.state === "suspended") Promise.resolve(ctx.resume()).catch(() => {});
    if (ctx.state !== "running") return false;            // autoplay blocked (no tap yet): stay silent
    const t0 = ctx.currentTime + 0.05;
    const buf = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 0.03), ctx.sampleRate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < ch.length; i++) ch[i] = (Math.random() * 2 - 1) * (1 - i / ch.length);
    const ticks = Math.floor(duration / 0.07);
    for (let i = 0; i < ticks; i++) {
      const src = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
      src.buffer = buf; f.type = "bandpass"; f.frequency.value = 1800 + (i % 3) * 400; f.Q.value = 0.9;
      g.gain.value = 0.22 + (i % 2) * 0.06;
      src.connect(f); f.connect(g); g.connect(ctx.destination);
      src.start(t0 + i * 0.07);
    }
    [988, 1319].forEach((hz, i) => {
      const o = ctx.createOscillator(), g = ctx.createGain(), at = t0 + duration + i * 0.12;
      o.type = "sine"; o.frequency.value = hz; o.connect(g); g.connect(ctx.destination);
      g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(0.16, at + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, at + 0.45);
      o.start(at); o.stop(at + 0.5);
    });
    return true;
  } catch { return false; }
}

export const slipCss = `
.slip-stage{display:flex;flex-direction:column;align-items:center;width:100%}
.slip-window{width:min(300px,84vw);overflow:hidden;padding-top:14px;margin-bottom:-10px;filter:drop-shadow(0 6px 8px rgba(0,0,0,.16))}
.slip-printer{width:min(330px,92vw);height:46px;background:linear-gradient(#41464c,#262a2e);border-radius:8px 8px 14px 14px;position:relative;box-shadow:0 8px 18px rgba(0,0,0,.28);z-index:2}
.slip-printer:before{content:"";position:absolute;left:14px;right:14px;top:9px;height:7px;background:#0d0f10;border-radius:4px;box-shadow:inset 0 2px 3px rgba(0,0,0,.85)}
.slip-led{position:absolute;right:16px;bottom:11px;width:8px;height:8px;border-radius:50%;background:#22c55e;box-shadow:0 0 8px #22c55e}
.slip-led.busy{animation:slipBlink .35s steps(2) infinite}
@keyframes slipBlink{50%{opacity:.25}}
.slip-paper{background:#fffef9 repeating-linear-gradient(0deg,rgba(0,0,0,.018) 0 1px,transparent 1px 3px);color:#1b1b1b;font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:13px;line-height:1.5;padding:16px 18px 24px;position:relative;transform:translateY(102%);animation:slipFeed var(--slip-dur,2.8s) steps(44,end) .15s forwards}
.slip-paper.static{animation:none;transform:none}
@keyframes slipFeed{to{transform:translateY(0)}}
.slip-paper:before{content:"";position:absolute;left:0;right:0;top:-10px;height:11px;background:linear-gradient(135deg,#fffef9 33%,transparent 33%) -7px 0/14px 11px,linear-gradient(225deg,#fffef9 33%,transparent 33%) -7px 0/14px 11px}
.slip-c{text-align:center}.slip-b{font-weight:700}.slip-hr{border:0;border-top:1px dashed #9a9a92;margin:9px 0}
.slip-row{display:flex;justify-content:space-between;gap:10px}.slip-row span:last-child{white-space:nowrap}
.slip-total{font-size:19px;font-weight:800}.slip-small{font-size:11px;color:#6a6a62}
.slip-stamp{display:inline-block;border:2px solid #15803d;color:#15803d;padding:1px 10px;border-radius:4px;font-weight:800;letter-spacing:.12em;transform:rotate(-4deg);margin:2px 0 4px}
.slip-test{display:inline-block;background:#fde68a;color:#92400e;font-weight:700;padding:0 8px;border-radius:3px;font-size:11px}
.slip-bars{height:30px;margin:8px auto 2px;width:80%;background:repeating-linear-gradient(90deg,#111 0 2px,transparent 2px 4px,#111 4px 5px,transparent 5px 8px,#111 8px 11px,transparent 11px 12px)}
@media (prefers-reduced-motion:reduce){.slip-paper{animation:none;transform:none}.slip-led.busy{animation:none}}
@media print{.slip-printer,.noprint{display:none!important}.slip-window{filter:none;margin:0;width:100%}.slip-paper{animation:none!important;transform:none!important}body{background:#fff!important}}
`;

export default function PrintedSlip({ data, extra = [], sound = true, duration = 2.8 }) {
  const still = reducedMotion();
  const ledRef = useRef(null);
  const dur = still ? 0 : duration;

  useEffect(() => {
    if (still) return undefined;
    if (sound) playPrintSound(duration);
    const t = setTimeout(() => ledRef.current?.classList.remove("busy"), (duration + 0.2) * 1000);
    return () => clearTimeout(t);
  }, [still, sound, duration]);

  const d = data || {};
  return (
    <div className="slip-stage" role="img" aria-label={`Receipt from ${d.merchant}: ${rands(d.total_cents)} paid`}>
      <style>{slipCss}</style>
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
      <div className="slip-printer"><span ref={ledRef} className={`slip-led${still ? "" : " busy"}`} /></div>
    </div>
  );
}
