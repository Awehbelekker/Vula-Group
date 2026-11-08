/**
 * FloatingToolbar — the phone's main controls, floating above the home bar (2026-10-17, UX
 * redesign Phase C). The redesigned shell (Phase D) puts Search, Add, Today, Inbox and More
 * here; Add is the `primary` item (amber). While it shows, toasts sit above it
 * (--vui-toolbar-h) and screens should end with <div className="vui-toolbar-pad" /> so their
 * last row isn't hidden behind it.
 *
 * Three looks, the brand kit's "Phone menu" (migration 251, from Ian's Glass Nav Kit,
 * 2026-11-08), read from <html data-nav> (theme/tokens.js::applyBrand) unless `navStyle` is given:
 *   classic — the dark bar, as before.
 *   glass   — a frosted bar; a glass pill sits under the current page and stretches as it moves.
 *   float   — a solid bar; Add is a raised round button sitting in a notch cut into the bar.
 * Only the look changes: which buttons show still depends on the person's role. Colours come
 * from the brand tokens (--brand, --on-brand), which are contrast-checked for light AND dark,
 * so a near-black brand (DIGG) still shows. Motion is off when the phone asks for less.
 *
 * items: [{ id, label, Icon, onClick, count?, primary?, current? }]
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";

const STYLES = ["classic", "glass", "float"];
const BAR_H = { classic: "72px", glass: "72px", float: "104px" };  // float: Add stands 28px above the bar
const NOTCH = { r: 31, cy: -2, k: 16 };   // the notch around the raised Add button (centre 2px above the bar)

const readStyle = () => {
  const s = typeof document !== "undefined" ? document.documentElement.dataset.nav : "";
  return STYLES.includes(s) ? s : "classic";
};

/** The brand's phone-menu style, following live changes (the Brand kit previews as you pick). */
function useNavStyle(forced) {
  const [style, setStyle] = useState(readStyle);
  useEffect(() => {
    if (forced) return undefined;
    setStyle(readStyle());
    const mo = new MutationObserver(() => setStyle(readStyle()));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-nav"] });
    return () => mo.disconnect();
  }, [forced]);
  return STYLES.includes(forced) ? forced : style;
}

const lessMotion = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** A pill W×H with a smooth circular notch centred at x = cx (ported from the Glass Nav Kit's
 *  signed-distance outline: the notch blends into the bar instead of meeting it at a corner). */
export function notchPath(W, H, cx) {
  const r2 = (x) => Math.round(x * 100) / 100;
  const t = 0.5, b = H - 0.5, l = 0.5, r = W - 0.5;
  const R = (b - t) / 2, pcx = (l + r) / 2, pcy = (t + b) / 2, hx = (r - l) / 2 - R;
  const { r: cr, cy, k } = NOTCH;
  const sd = (x, y) => {
    const qx = Math.abs(x - pcx) - hx, qy = Math.abs(y - pcy) - 0, mx = Math.max(qx, 0), my = Math.max(qy, 0);
    const dp = Math.sqrt(mx * mx + my * my) + Math.min(Math.max(qx, qy), 0) - R;
    const dc = Math.hypot(x - cx, y - cy) - cr;
    const h = Math.max(k - Math.abs(dp + dc), 0) / k;
    return Math.max(dp, -dc) + (h * h * k) / 4;
  };
  const cnt = Math.max(24, Math.round((r - l) / 2)), top = [], bot = [];
  for (let i = 0; i <= cnt; i++) {
    const x = l + 0.4 + ((r - l - 0.8) * i) / cnt;
    let lo = t - 1, hi = pcy;
    for (let it = 0; it < 14; it++) { const m = (lo + hi) / 2; if (sd(x, m) < 0) hi = m; else lo = m; }
    const ex = Math.max(Math.abs(x - pcx) - hx, 0);
    top.push(`${r2(x)},${r2(hi)}`);
    bot.unshift(`${r2(x)},${r2(pcy + Math.sqrt(Math.max(R * R - ex * ex, 0)))}`);
  }
  return `M${top.join(" L")} L${bot.join(" L")} Z`;
}

export function FloatingToolbar({ items = [], label = "Quick actions", navStyle, preview = false }) {
  const style = useNavStyle(navStyle);
  const navRef = useRef(null);
  const btns = useRef([]);
  const indRef = useRef(null);
  const [ind, setInd] = useState(null);      // glass: {left, width} of the current item
  const [notch, setNotch] = useState(null);  // float: {d, W, H}

  useEffect(() => {
    if (preview) return undefined;
    const root = document.documentElement;
    root.style.setProperty("--vui-toolbar-h", BAR_H[style]);
    return () => root.style.removeProperty("--vui-toolbar-h");
  }, [style, preview]);

  const current = items.findIndex((it) => it.current && !it.primary);
  const primary = items.findIndex((it) => it.primary);
  const shape = items.map((it) => `${it.id}${it.current ? "*" : ""}`).join(",");

  useLayoutEffect(() => {
    const nav = navRef.current;
    if (!nav) return undefined;
    const measure = () => {
      const cur = btns.current[current];
      setInd(style === "glass" && cur ? { left: cur.offsetLeft, width: cur.offsetWidth } : null);
      const add = btns.current[primary];
      if (style === "float" && add && nav.offsetWidth) {
        const W = nav.offsetWidth, H = nav.offsetHeight;
        setNotch({ d: notchPath(W, H, add.offsetLeft + add.offsetWidth / 2), W, H });
      } else setNotch(null);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(nav);
    return () => ro.disconnect();
  }, [style, current, primary, shape]);

  // glass: the pill squashes and stretches like liquid as it slides to the new page
  const last = useRef(current);
  useEffect(() => {
    if (last.current !== current && indRef.current?.animate && !lessMotion()) {
      indRef.current.animate([{ transform: "scale(1)" }, { transform: "scale(1.22,.82)" },
        { transform: "scale(.95,1.06)" }, { transform: "scale(1)" }], { duration: 560, easing: "ease-out" });
    }
    last.current = current;
  }, [current]);

  return (
    <nav ref={navRef} className={`vui-toolbar${preview ? " vui-toolbar--preview" : ""}`} data-nav={style}
      data-notch={notch ? "" : undefined} aria-label={label}>
      {style !== "classic" && (
        <span className="vui-toolbar-bg" aria-hidden="true"
          style={notch ? { clipPath: `path('${notch.d}')`, WebkitClipPath: `path('${notch.d}')` } : undefined} />)}
      {notch && (
        <svg className="vui-toolbar-line" aria-hidden="true" viewBox={`0 0 ${notch.W} ${notch.H}`} width={notch.W} height={notch.H}>
          <path d={notch.d} />
        </svg>)}
      {ind && <span ref={indRef} className="vui-toolbar-ind" aria-hidden="true" style={{ left: ind.left, width: ind.width }} />}
      {items.map(({ id, label: l, Icon, onClick, count, primary: isPrimary, current: isCurrent }, i) => (
        <button key={id} ref={(el) => { btns.current[i] = el; }} type="button"
          className={`vui-tool${isPrimary ? " vui-tool--primary" : ""}`} onClick={onClick}
          aria-label={count ? `${l} (${count})` : l} aria-current={isCurrent ? "page" : undefined}>
          <Icon size={isPrimary ? 22 : 20} strokeWidth={1.75} aria-hidden="true" />
          {!isPrimary && <span aria-hidden="true">{l}</span>}
          {count ? <span className="vui-count" aria-hidden="true">{count > 99 ? "99+" : count}</span> : null}
        </button>))}
    </nav>
  );
}
