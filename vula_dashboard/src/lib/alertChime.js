/**
 * alertChime.js — a soft two-note chime when an alert arrives while Vula is open on screen
 * (2026-10-27, Ian: "Yes add the chime too"). public/sw.js shows that notification silently and
 * tells the open app, so the counter tablet makes one sound, not two; with Vula not on screen the
 * phone's own notification sound plays instead.
 *
 * Browsers only let a page make sound after someone has tapped it, so the audio is unlocked on
 * the first tap; before that the chime stays quiet (the notification itself still shows).
 * On / off is this device's setting (Notifications), kept on sign-out.
 */
const KEY = "vula.alert.chime";
let ctx = null;

export function chimeOn() {
  try { return localStorage.getItem(KEY) !== "off"; } catch { return true; }
}
export function setChimeOn(on) {
  try { localStorage.setItem(KEY, on ? "on" : "off"); } catch { /* private mode */ }
}

function unlock() {
  try {
    ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
  } catch { ctx = null; }
}

/** Two short notes (E6 → A6), low and soft. True when it played. */
export function playAlertChime() {
  if (!chimeOn() || !ctx || ctx.state !== "running") return false;
  try {
    const t0 = ctx.currentTime + 0.02;
    [[1318.5, 0], [1760, 0.14]].forEach(([freq, at]) => {
      const osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, t0 + at);
      gain.gain.exponentialRampToValueAtTime(0.18, t0 + at + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + at + 0.45);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t0 + at);
      osc.stop(t0 + at + 0.5);
    });
    return true;
  } catch { return false; }
}

/** Once, at start-up (main.jsx): unlock audio on the first tap; chime when the worker says an alert came. */
export function listenForAlerts() {
  if (typeof window === "undefined") return;
  ["pointerdown", "keydown"].forEach((ev) => window.addEventListener(ev, unlock, { capture: true, passive: true }));
  navigator.serviceWorker?.addEventListener("message", (e) => {
    if (e.data && e.data.type === "vula-alert" && document.visibilityState === "visible") {
      window.__vulaChimes = (window.__vulaChimes || 0) + 1;      // read by the browser tests
      playAlertChime();
    }
  });
}
