/**
 * Slip sound design. `schedulePrintSound(ctx, out, t0, dur, style)` schedules everything on any
 * AudioContext (live or Offline), so it can be rendered and measured in tests.
 *
 * "modern" (default) — a modern thermal printer, not a dot-matrix:
 *   quiet motor whirr that spins up and settles, a soft paper glide with a fine flutter, faint
 *   feed ticks in step with the animation, a crisp tear-off, then a glassy 4-note chime with a
 *   little shimmer (feedback delay). Everything is low-passed/band-passed so it stays smooth.
 * "classic" — the earlier dot-matrix chatter and two-note ding.
 */
export const STEPS = 46; // matches the CSS feed animation: steps(46)

function noiseBuffer(ctx, seconds, seed = 1) {
  const n = Math.floor(ctx.sampleRate * seconds), buf = ctx.createBuffer(1, n, ctx.sampleRate), d = buf.getChannelData(0);
  let s = seed >>> 0;                                   // tiny deterministic PRNG: same noise every render
  for (let i = 0; i < n; i++) { s = (s * 1664525 + 1013904223) >>> 0; d[i] = (s / 2147483648) - 1; }
  return buf;
}

function modern(ctx, out, t0, dur) {
  const master = ctx.createGain(); master.gain.value = 0.9; master.connect(out);

  // shimmer bus (feedback delay) for the chime
  const dly = ctx.createDelay(1), fb = ctx.createGain(), wet = ctx.createGain(), lp = ctx.createBiquadFilter();
  dly.delayTime.value = 0.17; fb.gain.value = 0.3; wet.gain.value = 0.24; lp.type = "lowpass"; lp.frequency.value = 5200;
  dly.connect(lp); lp.connect(fb); fb.connect(dly); lp.connect(wet); wet.connect(master);

  // 1. motor whirr: saw -> lowpass, pitch eases up then settles, gentle vibrato
  const motor = ctx.createOscillator(), mf = ctx.createBiquadFilter(), mg = ctx.createGain(), vib = ctx.createOscillator(), vg = ctx.createGain();
  motor.type = "sawtooth"; mf.type = "lowpass"; mf.frequency.value = 1500; mf.Q.value = 0.8;   // pitched up so phone speakers can reproduce it
  motor.frequency.setValueAtTime(190, t0); motor.frequency.exponentialRampToValueAtTime(290, t0 + 0.28);
  motor.frequency.setValueAtTime(290, t0 + dur - 0.2); motor.frequency.exponentialRampToValueAtTime(230, t0 + dur + 0.1);
  vib.frequency.value = 6; vg.gain.value = 3.2; vib.connect(vg); vg.connect(motor.frequency);
  mg.gain.setValueAtTime(0.0001, t0); mg.gain.exponentialRampToValueAtTime(0.045, t0 + 0.14);
  mg.gain.setValueAtTime(0.045, t0 + dur - 0.12); mg.gain.exponentialRampToValueAtTime(0.0001, t0 + dur + 0.1);
  motor.connect(mf); mf.connect(mg); mg.connect(master);
  motor.start(t0); vib.start(t0); motor.stop(t0 + dur + 0.15); vib.stop(t0 + dur + 0.15);

  // 2. paper glide: band-passed noise with a fine flutter
  const paper = ctx.createBufferSource(), pf = ctx.createBiquadFilter(), pg = ctx.createGain(), flut = ctx.createOscillator(), fg = ctx.createGain();
  paper.buffer = noiseBuffer(ctx, 1.2, 7); paper.loop = true; pf.type = "bandpass"; pf.frequency.value = 2400; pf.Q.value = 0.55;
  flut.frequency.value = 23; fg.gain.value = 0.012; flut.connect(fg); fg.connect(pg.gain);
  pg.gain.setValueAtTime(0.0001, t0); pg.gain.exponentialRampToValueAtTime(0.03, t0 + 0.12);
  pg.gain.setValueAtTime(0.03, t0 + dur - 0.1); pg.gain.exponentialRampToValueAtTime(0.0001, t0 + dur + 0.05);
  paper.connect(pf); pf.connect(pg); pg.connect(master);
  paper.start(t0); flut.start(t0); paper.stop(t0 + dur + 0.1); flut.stop(t0 + dur + 0.1);

  // 3. feed ticks, one per animation step, very soft
  const step = dur / STEPS;
  for (let i = 0; i < STEPS; i++) {
    const o = ctx.createOscillator(), g = ctx.createGain(), at = t0 + i * step;
    o.type = "sine"; o.frequency.value = i % 2 ? 1180 : 1010;
    g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(0.028, at + 0.002); g.gain.exponentialRampToValueAtTime(0.0001, at + 0.014);
    o.connect(g); g.connect(master); o.start(at); o.stop(at + 0.02);
  }

  // 4. tear-off: a crisp high-passed noise burst
  const tear = ctx.createBufferSource(), tf = ctx.createBiquadFilter(), tg = ctx.createGain(), te = t0 + dur + 0.04;
  tear.buffer = noiseBuffer(ctx, 0.12, 99); tf.type = "highpass"; tf.frequency.value = 3800;
  tg.gain.setValueAtTime(0.0001, te); tg.gain.exponentialRampToValueAtTime(0.1, te + 0.006); tg.gain.exponentialRampToValueAtTime(0.0001, te + 0.075);
  tear.connect(tf); tf.connect(tg); tg.connect(master); tear.start(te); tear.stop(te + 0.12);

  // 5. success chime: A5 C#6 E6 A6, soft attack, glassy decay, a touch into the shimmer bus
  [880, 1108.73, 1318.51, 1760].forEach((hz, i) => {
    const at = t0 + dur + 0.17 + i * 0.075;
    [[hz, "sine", 0.1], [hz * 2, "triangle", 0.025]].forEach(([f, type, peak]) => {
      const o = ctx.createOscillator(), g = ctx.createGain(), send = ctx.createGain();
      o.type = type; o.frequency.value = f; send.gain.value = 0.7;
      g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(peak, at + 0.01); g.gain.exponentialRampToValueAtTime(0.0001, at + 0.75);
      o.connect(g); g.connect(master); g.connect(send); send.connect(dly); o.start(at); o.stop(at + 0.8);
    });
  });
  return t0 + dur + 0.17 + 3 * 0.075 + 0.8 + 0.9;   // ~ when the tail has died away
}

function classic(ctx, out, t0, dur) {
  const buf = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 0.03), ctx.sampleRate), ch = buf.getChannelData(0);
  for (let i = 0; i < ch.length; i++) ch[i] = (Math.sin(i * 12.9898) * 43758.5453 % 1) * (1 - i / ch.length);
  for (let k = 0; k < Math.floor(dur / 0.07); k++) {
    const s = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
    s.buffer = buf; f.type = "bandpass"; f.frequency.value = 1800 + (k % 3) * 400; f.Q.value = 0.9; g.gain.value = 0.22 + (k % 2) * 0.06;
    s.connect(f); f.connect(g); g.connect(out); s.start(t0 + k * 0.07);
  }
  [988, 1319].forEach((hz, i) => {
    const o = ctx.createOscillator(), g = ctx.createGain(), at = t0 + dur + i * 0.12;
    o.type = "sine"; o.frequency.value = hz; o.connect(g); g.connect(out);
    g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(0.16, at + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, at + 0.45);
    o.start(at); o.stop(at + 0.5);
  });
  return t0 + dur + 0.8;
}

export function schedulePrintSound(ctx, out, t0, dur = 2.8, style = "modern") {
  return (style === "classic" ? classic : modern)(ctx, out, t0, dur);
}
