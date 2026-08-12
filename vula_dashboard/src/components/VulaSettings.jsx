/**
 * VulaSettings.jsx — tenant store settings & connections.
 *
 * Lets the owner connect their OWN payment + WhatsApp (self-service), and see
 * their store details. Reuses the same connect components as the master admin
 * so a tenant is no longer dependent on Vula staff to go live.
 */

import React, { useState, useEffect } from 'react'
import { supabase } from '../lib/supabase'
import VulaYocoConnect from './VulaYocoConnect'
import VulaWhatsAppConnect from './VulaWhatsAppConnect'
import VulaClickUpConnect from './VulaClickUpConnect'
import VulaGoogleConnect from './VulaGoogleConnect'
import VulaMicrosoftConnect from './VulaMicrosoftConnect'
import VulaEmailConnect from './VulaEmailConnect'
import { applyAccent, applyInk, applyFontPairing, FONT_PAIRINGS } from '../theme/tokens'

export default function VulaSettings({ tenantId, tenantName, adminEmail }) {
  return (
    <div>
      <div style={s.intro}>
        <h3 style={s.h3}>⚙️ Settings & connections</h3>
        <p style={s.sub}>Connect your payments and WhatsApp so your store can take orders and get paid.</p>
      </div>

      {/* Brand kit — one place for logo + accent colour, instead of buried in Invoices */}
      <section style={s.section}>
        <h4 style={s.sectionTitle}>🎨 Brand kit</h4>
        <p style={s.sectionHint}>
          Your logo and accent colour — used on invoices, your storefront pages, and throughout this dashboard.
        </p>
        <BrandKitSettings tenantId={tenantId} />
      </section>

      {/* Payments */}
      <section style={s.section}>
        <h4 style={s.sectionTitle}>💳 Payments (Yoco)</h4>
        <p style={s.sectionHint}>
          Connect your Yoco account to accept card, tap & QR payments. Money goes straight to you.
        </p>
        <VulaYocoConnect tenantId={tenantId} tenantName={tenantName} adminEmail={adminEmail} />
      </section>

      {/* WhatsApp */}
      <section style={s.section}>
        <h4 style={s.sectionTitle}>💬 WhatsApp</h4>
        <p style={s.sectionHint}>
          Connect your WhatsApp Business number so customers can order and chat to your AI assistant.
        </p>
        <VulaWhatsAppConnect tenantId={tenantId} tenantName={tenantName} adminEmail={adminEmail} />
      </section>

      {/* ClickUp */}
      <section style={s.section}>
        <h4 style={s.sectionTitle}>🗂️ ClickUp</h4>
        <p style={s.sectionHint}>
          Connect ClickUp to create, list and update tasks — and set reminders — straight from WhatsApp.
        </p>
        <VulaClickUpConnect tenantId={tenantId} tenantName={tenantName} />
      </section>

      {/* Google */}
      <section style={s.section}>
        <h4 style={s.sectionTitle}>🔵 Google (Drive &amp; Gmail)</h4>
        <p style={s.sectionHint}>
          Connect Google so Vula can find &amp; file Drive documents and draft Gmail replies (draft-only).
        </p>
        <VulaGoogleConnect tenantId={tenantId} tenantName={tenantName} />
      </section>

      {/* Microsoft */}
      <section style={s.section}>
        <h4 style={s.sectionTitle}>🟦 Microsoft (OneDrive &amp; Outlook)</h4>
        <p style={s.sectionHint}>
          Connect Microsoft 365 so Vula can find &amp; file OneDrive documents and draft Outlook replies (draft-only).
        </p>
        <VulaMicrosoftConnect tenantId={tenantId} tenantName={tenantName} />
      </section>

      {/* Email (IMAP/SMTP) */}
      <section style={s.section}>
        <h4 style={s.sectionTitle}>✉️ Email (IMAP / SMTP)</h4>
        <p style={s.sectionHint}>
          Connect any mailbox without OAuth — GoDaddy Workspace, cPanel, Zoho. Search it, file attachments to the KB, draft replies.
        </p>
        <VulaEmailConnect tenantId={tenantId} adminEmail={adminEmail} />
      </section>

      {/* Delivery coverage + fee rules (migration 070) — grounds the WhatsApp assistant's
          delivery answers, so it never guesses coverage again. */}
      <section style={s.section}>
        <h4 style={s.sectionTitle}>🛵 Delivery areas &amp; fees</h4>
        <p style={s.sectionHint}>
          Where you deliver and what it costs. The WhatsApp assistant answers coverage questions
          from this list — anywhere else, it checks with your team instead of guessing.
        </p>
        <DeliverySettings tenantId={tenantId} />
      </section>

      <p style={s.footer}>Powered by Vula</p>
    </div>
  )
}

function BrandKitSettings({ tenantId }) {
  const API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'
  const [name, setName] = useState('')
  const [logoUrl, setLogoUrl] = useState('')
  const [accent, setAccent] = useState('#2C5545')
  const [ink, setInk] = useState('#1E1E1E')
  const [fontPairing, setFontPairing] = useState('vula')
  // Storefront header layout (migration 128) — logoAlign/logoSize already existed (migration
  // 103, invoice branding) and are reused as-is here: same tenant-editable fields now drive both
  // invoice PDFs and the storefront header, one setting instead of two parallel ones.
  const [logoAlign, setLogoAlign] = useState('left')
  const [logoSize, setLogoSize] = useState('md')
  const [headerSticky, setHeaderSticky] = useState(true)
  const [headerNavPosition, setHeaderNavPosition] = useState('right')
  const [headerCtaText, setHeaderCtaText] = useState('')
  const [headerCtaLink, setHeaderCtaLink] = useState('')
  const [uploading, setUploading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    fetch(`${API}/v1/commerce/${tenantId}/admin/invoice-settings`)
      .then(r => r.json())
      .then(d => {
        const st = d.settings || {}
        setName(st.trading_as || st.company_name || '')
        setLogoUrl(st.logo_url || '')
        setAccent(/^#[0-9a-fA-F]{6}$/.test(st.accent_color || '') ? st.accent_color : '#2C5545')
        setInk(/^#[0-9a-fA-F]{6}$/.test(st.ink_color || '') ? st.ink_color : '#1E1E1E')
        setFontPairing(st.font_pairing || 'vula')
        setLogoAlign(st.logo_align || 'left')
        setLogoSize(st.logo_size || 'md')
        setHeaderSticky(st.header_sticky !== false)
        setHeaderNavPosition(st.header_nav_position || 'right')
        setHeaderCtaText(st.header_cta_text || '')
        setHeaderCtaLink(st.header_cta_link || '')
        setLoaded(true)
      })
      .catch(() => setLoaded(true))
  }, [tenantId])  // eslint-disable-line

  async function uploadLogo(e) {
    const file = (e.target.files || [])[0]
    if (!file) return
    setUploading(true)
    try {
      const clean = file.name.replace(/[^a-zA-Z0-9.-]/g, '-').toLowerCase()
      const path = `${tenantId}/logo/${Date.now()}-${clean}`
      const { error } = await supabase.storage.from('product-images').upload(path, file, { cacheControl: '3600', upsert: true })
      if (!error) {
        const { data } = supabase.storage.from('product-images').getPublicUrl(path)
        if (data?.publicUrl) setLogoUrl(data.publicUrl)
      }
    } finally { setUploading(false) }
  }

  async function save() {
    setSaving(true); setMsg('')
    try {
      await fetch(`${API}/v1/commerce/${tenantId}/admin/invoice-settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          trading_as: name, logo_url: logoUrl, accent_color: accent, ink_color: ink, font_pairing: fontPairing,
          logo_align: logoAlign, logo_size: logoSize, header_sticky: headerSticky,
          header_nav_position: headerNavPosition, header_cta_text: headerCtaText, header_cta_link: headerCtaLink,
        }),
      })
      // Live preview — no refresh needed to see any of it take effect.
      applyAccent(accent)
      applyInk(ink)
      applyFontPairing(fontPairing)
      setMsg('Saved ✓')
      setTimeout(() => setMsg(''), 2500)
    } finally { setSaving(false) }
  }

  if (!loaded) return <p style={s.sectionHint}>Loading…</p>

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <input placeholder="Business / brand name" value={name} onChange={e => setName(e.target.value)} style={bk.input} />
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={bk.uploadBtn}>
          {uploading ? 'Uploading…' : (logoUrl ? '↻ Replace logo' : '📷 Upload logo')}
          <input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" onChange={uploadLogo} style={{ display: 'none' }} />
        </label>
        {logoUrl && <img src={logoUrl} alt="logo" style={{ maxHeight: 44, maxWidth: 160, objectFit: 'contain' }} />}
        {logoUrl && <button type="button" onClick={() => setLogoUrl('')} style={bk.clearBtn}>×</button>}
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <span style={{ fontSize: 13, color: '#8A8680', width: 100 }}>Accent colour</span>
        <input type="color" value={accent} onChange={e => setAccent(e.target.value)} style={bk.colorSwatch} />
        <input value={accent} onChange={e => setAccent(e.target.value)} style={{ ...bk.input, width: 100, fontFamily: "'Source Code Pro', monospace" }} />
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <span style={{ fontSize: 13, color: '#8A8680', width: 100 }}>Text colour</span>
        <input type="color" value={ink} onChange={e => setInk(e.target.value)} style={bk.colorSwatch} />
        <input value={ink} onChange={e => setInk(e.target.value)} style={{ ...bk.input, width: 100, fontFamily: "'Source Code Pro', monospace" }} />
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <span style={{ fontSize: 13, color: '#8A8680', width: 100 }}>Heading font</span>
        <select value={fontPairing} onChange={e => setFontPairing(e.target.value)} style={{ ...bk.input, width: 220 }}>
          {Object.entries(FONT_PAIRINGS).map(([key, p]) => <option key={key} value={key}>{p.label}</option>)}
        </select>
      </div>
      <div style={{ borderTop: '1px solid #EEE9DF', margin: '4px 0', paddingTop: 12 }}>
        <p style={{ fontSize: 12.5, fontWeight: 600, color: '#8A8680', margin: '0 0 8px' }}>Storefront header</p>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 8 }}>
          <span style={{ fontSize: 13, color: '#8A8680', width: 100 }}>Logo position</span>
          <select value={logoAlign} onChange={e => setLogoAlign(e.target.value)} style={{ ...bk.input, width: 140 }}>
            <option value="left">Left</option>
            <option value="center">Centered</option>
          </select>
          <span style={{ fontSize: 13, color: '#8A8680', marginLeft: 10 }}>Size</span>
          <select value={logoSize} onChange={e => setLogoSize(e.target.value)} style={{ ...bk.input, width: 100 }}>
            <option value="sm">Small</option>
            <option value="md">Medium</option>
            <option value="lg">Large</option>
          </select>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 8 }}>
          <span style={{ fontSize: 13, color: '#8A8680', width: 100 }}>Menu position</span>
          <select value={headerNavPosition} onChange={e => setHeaderNavPosition(e.target.value)} style={{ ...bk.input, width: 140 }}>
            <option value="right">Right of logo</option>
            <option value="center">Centered</option>
            <option value="below-logo">Below logo</option>
          </select>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#8A8680', marginLeft: 10 }}>
            <input type="checkbox" checked={headerSticky} onChange={e => setHeaderSticky(e.target.checked)} />
            Sticky on scroll
          </label>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span style={{ fontSize: 13, color: '#8A8680', width: 100 }}>Button</span>
          <input placeholder="Button text (e.g. Get a quote) — blank for none" value={headerCtaText} onChange={e => setHeaderCtaText(e.target.value)} style={{ ...bk.input, flex: 1, minWidth: 160 }} />
          <input placeholder="Link (e.g. #contact or /shop)" value={headerCtaLink} onChange={e => setHeaderCtaLink(e.target.value)} style={{ ...bk.input, flex: 1, minWidth: 160 }} />
        </div>
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <button onClick={save} disabled={saving} style={bk.saveBtn}>{saving ? 'Saving…' : 'Save brand kit'}</button>
        {msg && <span style={{ color: 'var(--accent, #2C5545)', fontSize: 13 }}>{msg}</span>}
      </div>
    </div>
  )
}

const bk = {
  input:      { padding: '9px 12px', border: '1px solid #DDD8CE', borderRadius: 8, fontFamily: 'system-ui', fontSize: 14, boxSizing: 'border-box' },
  uploadBtn:  { padding: '8px 14px', background: 'var(--accent-soft, rgba(44,85,69,0.1))', color: 'var(--accent, #2C5545)', border: '1px solid var(--accent, #2C5545)', borderRadius: 8, fontSize: 13, cursor: 'pointer', fontFamily: 'system-ui' },
  clearBtn:   { background: 'none', border: 'none', color: '#8A8680', cursor: 'pointer', fontSize: 18 },
  colorSwatch:{ width: 40, height: 34, padding: 2, border: '1px solid #DDD8CE', borderRadius: 6, cursor: 'pointer', background: '#fff' },
  saveBtn:    { padding: '10px 18px', background: 'var(--accent, #2C5545)', color: '#fff', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui', alignSelf: 'flex-start' },
}

function DeliverySettings({ tenantId }) {
  const API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'
  const [areas, setAreas] = useState('')
  const [fee, setFee] = useState('')
  const [freeOver, setFreeOver] = useState('')
  const [minOrder, setMinOrder] = useState('')
  // Marketplace-style geo coverage (migration 074): shop pin + radius on a real map.
  const [origin, setOrigin] = useState(null)          // { lat, lng }
  const [originLabel, setOriginLabel] = useState('')
  const [radiusKm, setRadiusKm] = useState(15)
  const [geoSearch, setGeoSearch] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const mapRef = React.useRef(null)     // { map, L, marker, circle }
  const mapDivRef = React.useRef(null)

  useEffect(() => {
    fetch(`${API}/v1/commerce/${tenantId}/admin/order-settings`)
      .then(r => r.json())
      .then(d => {
        const st = d.settings || {}
        setAreas((st.delivery_areas || []).join(', '))
        setFee(st.delivery_fee_cents != null ? String(st.delivery_fee_cents / 100) : '')
        setFreeOver(st.free_delivery_over_cents != null ? String(st.free_delivery_over_cents / 100) : '')
        setMinOrder(st.min_order_cents != null ? String(st.min_order_cents / 100) : '')
        if (st.origin_lat != null && st.origin_lng != null) setOrigin({ lat: st.origin_lat, lng: st.origin_lng })
        if (st.origin_label) setOriginLabel(st.origin_label)
        if (st.delivery_radius_km) setRadiusKm(st.delivery_radius_km)
      }).catch(() => {})
  }, [tenantId])  // eslint-disable-line

  // Lazy-load Leaflet + OSM tiles once; click-to-place pin, live radius circle.
  useEffect(() => {
    let dead = false
    import('leaflet').then(async (Lmod) => {
      if (dead || !mapDivRef.current || mapRef.current) return
      await import('leaflet/dist/leaflet.css')
      const L = Lmod.default || Lmod
      const start = origin || { lat: -33.82, lng: 18.49 }   // Cape Town west coast default
      const map = L.map(mapDivRef.current).setView([start.lat, start.lng], origin ? 11 : 9)
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap contributors', maxZoom: 18,
      }).addTo(map)
      const marker = L.circleMarker([start.lat, start.lng], {
        radius: 8, color: 'var(--accent, #2C5545)', fillColor: 'var(--accent, #2C5545)', fillOpacity: 0.9,
      }).addTo(map)
      const circle = L.circle([start.lat, start.lng], {
        radius: radiusKm * 1000, color: 'var(--accent, #2C5545)', weight: 1.5, fillOpacity: 0.08,
      }).addTo(map)
      map.on('click', (e) => {
        setOrigin({ lat: e.latlng.lat, lng: e.latlng.lng })
        setOriginLabel((l) => l || 'Pinned location')
      })
      mapRef.current = { map, L, marker, circle }
    }).catch(() => {})
    return () => { dead = true; if (mapRef.current) { mapRef.current.map.remove(); mapRef.current = null } }
  }, [])  // eslint-disable-line

  // Keep the pin + circle in sync with state.
  useEffect(() => {
    const m = mapRef.current
    if (!m || !origin) return
    m.marker.setLatLng([origin.lat, origin.lng])
    m.circle.setLatLng([origin.lat, origin.lng])
    m.circle.setRadius(radiusKm * 1000)
  }, [origin, radiusKm])

  async function findAddress() {
    if (!geoSearch.trim()) return
    try {
      const r = await fetch(`${API}/v1/commerce/${tenantId}/admin/geo/geocode`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: geoSearch }),
      })
      const d = await r.json()
      if (d.lat != null) {
        setOrigin({ lat: d.lat, lng: d.lng })
        setOriginLabel(d.label || geoSearch)
        mapRef.current?.map.setView([d.lat, d.lng], 12)
      } else setMsg(d.detail || 'Not found — try suburb + city.')
    } catch { setMsg('Search failed — try again.') }
  }

  const rands = v => { const n = parseFloat(v); return Number.isFinite(n) ? Math.round(n * 100) : null }

  async function save() {
    setBusy(true)
    try {
      const body = {
        delivery_areas: areas.split(',').map(a => a.trim()).filter(Boolean),
        delivery_fee_cents: rands(fee),
        free_delivery_over_cents: rands(freeOver),
        min_order_cents: rands(minOrder),
        origin_lat: origin?.lat ?? null,
        origin_lng: origin?.lng ?? null,
        origin_label: originLabel || null,
        delivery_radius_km: origin ? Number(radiusKm) : null,
      }
      const r = await fetch(`${API}/v1/commerce/${tenantId}/admin/order-settings`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      })
      const d = await r.json()
      setMsg(d.error ? String(d.error) : 'Saved — the assistant uses this immediately.')
    } catch (e) { setMsg('Could not save — try again.') } finally {
      setBusy(false); setTimeout(() => setMsg(''), 6000)
    }
  }

  const inp = { padding: '9px 11px', border: '1px solid #DDD8CE', borderRadius: 8, fontSize: 13, fontFamily: 'system-ui', boxSizing: 'border-box' }
  return (
    <div style={{ background: '#fff', border: '1px solid #DDD8CE', borderRadius: 10, padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* Marketplace-style location + radius */}
      <div>
        <p style={{ fontSize: 12.5, fontWeight: 600, fontFamily: 'system-ui', margin: '0 0 6px' }}>
          📍 Shop location &amp; delivery radius
        </p>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
          <input value={geoSearch} onChange={e => setGeoSearch(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && findAddress()}
            placeholder="Search your shop's address or suburb…" style={{ ...inp, flex: 1, minWidth: 200 }} />
          <button onClick={findAddress}
            style={{ padding: '9px 14px', border: '1px solid #DDD8CE', borderRadius: 8, background: '#fff', fontSize: 13, cursor: 'pointer', fontFamily: 'system-ui' }}>
            Find
          </button>
          <select value={radiusKm} onChange={e => setRadiusKm(Number(e.target.value))} style={inp}>
            {[5, 10, 15, 20, 25, 30, 40, 50].map(k => <option key={k} value={k}>{k} km radius</option>)}
          </select>
        </div>
        <div ref={mapDivRef} style={{ height: 260, borderRadius: 10, border: '1px solid #DDD8CE', overflow: 'hidden' }} />
        <p style={{ fontSize: 11.5, color: '#8A8680', fontFamily: 'system-ui', margin: '6px 0 0' }}>
          {origin
            ? <>Pinned: <b>{originLabel || 'your shop'}</b> · {radiusKm} km radius. Customers who share a WhatsApp location pin get an instant in/out answer.</>
            : 'Search or click the map to drop your shop pin — then the radius circle shows exactly where you deliver.'}
        </p>
      </div>

      <label style={{ fontSize: 12, color: '#8A8680', fontFamily: 'system-ui' }}>
        Delivery areas (comma-separated — e.g. Table View, Blouberg, Parklands)
        <input value={areas} onChange={e => setAreas(e.target.value)} placeholder="Table View, Blouberg, Parklands…"
          style={{ ...inp, width: '100%', marginTop: 4 }} />
      </label>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: '#8A8680', fontFamily: 'system-ui', flex: 1, minWidth: 140 }}>
          Delivery fee (R)
          <input value={fee} onChange={e => setFee(e.target.value)} placeholder="80" style={{ ...inp, width: '100%', marginTop: 4 }} />
        </label>
        <label style={{ fontSize: 12, color: '#8A8680', fontFamily: 'system-ui', flex: 1, minWidth: 140 }}>
          Free delivery over (R)
          <input value={freeOver} onChange={e => setFreeOver(e.target.value)} placeholder="500" style={{ ...inp, width: '100%', marginTop: 4 }} />
        </label>
        <label style={{ fontSize: 12, color: '#8A8680', fontFamily: 'system-ui', flex: 1, minWidth: 140 }}>
          Minimum order (R)
          <input value={minOrder} onChange={e => setMinOrder(e.target.value)} placeholder="none" style={{ ...inp, width: '100%', marginTop: 4 }} />
        </label>
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <button onClick={save} disabled={busy}
          style={{ padding: '9px 18px', border: 'none', borderRadius: 8, background: 'var(--accent, #2C5545)', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' }}>
          {busy ? 'Saving…' : 'Save delivery settings'}
        </button>
        {msg && <span style={{ fontSize: 12.5, color: 'var(--accent, #2C5545)', fontFamily: 'system-ui' }}>{msg}</span>}
      </div>
    </div>
  )
}

const s = {
  intro:        { marginBottom: 16 },
  h3:           { fontFamily: "'Cormorant Garamond', serif", fontSize: 20, fontWeight: 700, color: 'var(--ink, #1E1E1E)', margin: '0 0 4px' },
  sub:          { fontFamily: 'system-ui', fontSize: 13, color: '#8A8680', margin: 0 },
  section:      { marginBottom: 24 },
  sectionTitle: { fontFamily: 'system-ui', fontSize: 15, fontWeight: 700, color: 'var(--ink, #1E1E1E)', margin: '0 0 4px' },
  sectionHint:  { fontFamily: 'system-ui', fontSize: 13, color: '#8A8680', margin: '0 0 12px', lineHeight: 1.5 },
  footer:       { textAlign: 'center', fontFamily: 'system-ui', fontSize: 11, color: '#B5B0A8', marginTop: 24 },
}
