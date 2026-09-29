/**
 * GlassCard — a Home card that behaves like a pane of glass (2026-09-29, Ian: "a true glass
 * feel, where it's actually bending the light and the background like real glass").
 *
 * Three layers, all in index.css (.vula-glass) except the bend:
 *  - frost: the background behind the card is blurred, saturated and tinted;
 *  - light: a highlight catches the top edge and sweeps across the pane;
 *  - bend (Chromium only): an SVG displacement map sized to THIS card magnifies the background
 *    along a ~26px bevel on every edge, the way a thick glass edge does. The map has to be in
 *    pixels of the card itself — Chrome resolves a percentage-sized map against the wrong box
 *    and shifts the whole backdrop — so a ResizeObserver rebuilds it when the card resizes.
 *    The bevel samples INWARD (left edge pulls from its right, top from below), so it never
 *    needs pixels outside the card, which a backdrop filter doesn't have.
 * Safari and Firefox can't run an SVG filter as a backdrop filter; they get the frost and light.
 */
import { useEffect, useId, useRef, useState } from 'react'

const BEVEL = 26          // px of edge that bends
const STRENGTH = 34       // displacement scale: the edge samples up to STRENGTH/2 px inward

// Chrome, Edge, Opera, Samsung Internet (userAgentData is Chromium-only; iOS browsers are WebKit).
const CAN_BEND = typeof navigator !== 'undefined'
  && !!navigator.userAgentData?.brands?.some(b => b.brand === 'Chromium')

// A curved bevel, not a straight ramp: strongest right at the edge, easing to neutral (128).
const RAMP = [[0, 255], [0.25, 212], [0.55, 168], [1, 128]]

function stops(frac, colour) {
  const head = RAMP.map(([t, v]) => `<stop offset="${(t * frac).toFixed(4)}" stop-color="${colour(v)}"/>`)
  const tail = [...RAMP].reverse().map(([t, v]) =>
    `<stop offset="${(1 - t * frac).toFixed(4)}" stop-color="${colour(255 - v)}"/>`)
  return head.join('') + tail.join('')
}

export function bevelMap(w, h, bevel = BEVEL) {
  const fx = Math.min(0.5, bevel / w)
  const fy = Math.min(0.5, bevel / h)
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><defs>`
    + `<linearGradient id="x" x1="0" x2="1" y1="0" y2="0">${stops(fx, v => `rgb(${v},0,0)`)}</linearGradient>`
    + `<linearGradient id="y" x1="0" x2="0" y1="0" y2="1">${stops(fy, v => `rgb(0,${v},0)`)}</linearGradient>`
    + `</defs><rect width="${w}" height="${h}" fill="url(#x)"/>`
    + `<rect width="${w}" height="${h}" fill="url(#y)" style="mix-blend-mode:screen"/></svg>`
  return 'data:image/svg+xml,' + encodeURIComponent(svg)
}

export default function GlassCard({ dark = false, children }) {
  const ref = useRef(null)
  const [size, setSize] = useState(null)
  const id = 'vula-bend-' + useId().replace(/[^a-zA-Z0-9_-]/g, '')

  useEffect(() => {
    if (!CAN_BEND || !ref.current || typeof ResizeObserver === 'undefined') return
    const el = ref.current
    const measure = () => {
      const w = Math.round(el.offsetWidth), h = Math.round(el.offsetHeight)
      setSize(s => (s && s.w === w && s.h === h) ? s : (w > 0 && h > 0 ? { w, h } : null))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const bend = CAN_BEND && size
  return (
    <div ref={ref} className={`vula-glass${dark ? ' dark' : ''}${bend ? ' refract' : ''}`}
         style={bend ? { '--vula-bend': `url(#${id})` } : undefined}>
      {bend && (
        <svg width="0" height="0" style={{ position: 'absolute' }} aria-hidden="true" focusable="false">
          <filter id={id} x="0" y="0" width={size.w} height={size.h} filterUnits="userSpaceOnUse"
                  colorInterpolationFilters="sRGB">
            <feImage href={bevelMap(size.w, size.h)} x="0" y="0" width={size.w} height={size.h}
                     preserveAspectRatio="none" result="map" />
            <feDisplacementMap in="SourceGraphic" in2="map" scale={STRENGTH}
                               xChannelSelector="R" yChannelSelector="G" />
          </filter>
        </svg>
      )}
      {children}
    </div>
  )
}
