/**
 * VulaSettings.jsx — tenant store settings & connections.
 *
 * Lets the owner connect their OWN payment + WhatsApp (self-service), and see
 * their store details. Reuses the same connect components as the master admin
 * so a tenant is no longer dependent on Vula staff to go live.
 */

import React, { useState, useEffect } from 'react'
import VulaYocoConnect from './VulaYocoConnect'
import VulaWhatsAppConnect from './VulaWhatsAppConnect'
import VulaClickUpConnect from './VulaClickUpConnect'
import VulaGoogleConnect from './VulaGoogleConnect'
import VulaMicrosoftConnect from './VulaMicrosoftConnect'
import VulaEmailConnect from './VulaEmailConnect'
import { applyBrand, FONT_PAIRINGS, CORNER_STYLES, DENSITIES, LOGO_SIZES } from '../theme/tokens'
import { uploadBrandImage } from '../lib/brandUpload'
import { VULA_API } from '../lib/authFetch'

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
          Your name, logo, colours and style — used everywhere: this dashboard, the login page, your phone app icon, invoices, emails and your storefront. Status colours (paid, overdue) always stay green and red so they read the same for everyone.
        </p>
        <BrandKitSettings tenantId={tenantId} />
      </section>

      {/* Business profile — the owner's own answers, which Vula answers from (not guesses) */}
      <section style={s.section}>
        <h4 style={s.sectionTitle}>📋 Business profile</h4>
        <p style={s.sectionHint}>
          Short answers about how your business works. Vula answers customers and your team from these — anything left blank, it says it needs to check instead of guessing. You can also do this on WhatsApp: send “set up my profile”.
        </p>
        <BusinessProfileSettings tenantId={tenantId} />
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

      {/* Opening hours (migration 158) — grounds the WhatsApp assistant's "are you open?"
          answers, so it never escalates a question that should be configured once. */}
      <section style={s.section}>
        <h4 style={s.sectionTitle}>🕐 Opening hours</h4>
        <p style={s.sectionHint}>
          The WhatsApp assistant answers "are you open?" from this — leave a day unticked to
          mark it closed. Left as no hours at all, it checks with your team instead of guessing.
        </p>
        <BusinessHoursSettings tenantId={tenantId} />
      </section>

      {/* Tenant Mind Phase 2 (2026-09-15) — one surface for voice/tone, learned answers, and
          merchant categorisation, three mechanisms that previously had no shared view. */}
      <section style={s.section}>
        <h4 style={s.sectionTitle}>🧠 What Vula has learned</h4>
        <p style={s.sectionHint}>
          Everything Vula has picked up about how your business runs — your voice, answers
          learned from your team, and how it's learned to categorise your suppliers.
        </p>
        <LearnedSummary tenantId={tenantId} />
      </section>

      <p style={s.footer}>Powered by Vula</p>
    </div>
  )
}

export function BrandKitSettings({ tenantId }) {
  const API = VULA_API
  const [name, setName] = useState('')
  const [tagline, setTagline] = useState('')
  const [logoUrl, setLogoUrl] = useState('')
  const [iconUrl, setIconUrl] = useState('')
  const [accent, setAccent] = useState('#2C5545')
  const [secondary, setSecondary] = useState('')
  const [ink, setInk] = useState('#1E1E1E')
  const [fontPairing, setFontPairing] = useState('vula')
  const [corners, setCorners] = useState('rounded')
  const [density, setDensity] = useState('comfortable')
  // Storefront header layout (migration 128) — logoAlign/logoSize already existed (migration
  // 103, invoice branding) and are reused as-is here: same tenant-editable fields now drive both
  // invoice PDFs and the storefront header, one setting instead of two parallel ones.
  const [logoAlign, setLogoAlign] = useState('left')
  const [logoSize, setLogoSize] = useState('md')
  const [headerSticky, setHeaderSticky] = useState(true)
  const [headerNavPosition, setHeaderNavPosition] = useState('right')
  const [headerCtaText, setHeaderCtaText] = useState('')
  const [headerCtaLink, setHeaderCtaLink] = useState('')
  const [uploading, setUploading] = useState('')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [website, setWebsite] = useState('')
  const [suggesting, setSuggesting] = useState(false)
  const [suggestion, setSuggestion] = useState(null)
  const hex6 = (v, d) => (/^#[0-9a-fA-F]{6}$/.test(v || '') ? v : d)

  useEffect(() => {
    fetch(`${API}/v1/commerce/${tenantId}/admin/invoice-settings`)
      .then(r => r.json())
      .then(d => {
        const st = d.settings || {}
        setName(st.trading_as || st.company_name || '')
        setTagline(st.tagline || '')
        setLogoUrl(st.logo_url || '')
        setIconUrl(st.icon_url || '')
        setAccent(hex6(st.accent_color, '#2C5545'))
        setSecondary(hex6(st.secondary_color, ''))
        setInk(hex6(st.ink_color, '#1E1E1E'))
        setFontPairing(st.font_pairing || 'vula')
        setCorners(st.corner_style || 'rounded')
        setDensity(st.density || 'comfortable')
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

  // Live: every change shows across the whole dashboard straight away (nothing is saved until
  // "Save brand kit"; leaving without saving is undone on the next load of the saved brand).
  const draft = { accent_color: accent, secondary_color: secondary || null, ink_color: ink,
    font_pairing: fontPairing, corner_style: corners, density, logo_size: logoSize, logo_url: logoUrl, icon_url: iconUrl }
  useEffect(() => { if (loaded) applyBrand(draft) }, [loaded, accent, secondary, ink, fontPairing, corners, density, logoSize])  // eslint-disable-line

  async function upload(e, kind) {
    const file = (e.target.files || [])[0]
    if (!file) return
    setUploading(kind)
    try {
      const url = await uploadBrandImage(tenantId, file, kind)
      if (url) (kind === 'icon' ? setIconUrl : setLogoUrl)(url)
    } finally { setUploading('') }
  }

  async function suggest() {
    setSuggesting(true); setMsg(''); setSuggestion(null)
    try {
      const r = await fetch(`${API}/v1/commerce/${tenantId}/admin/brand/suggest`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ logo_url: logoUrl || undefined, website_url: website || undefined }) })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg(d.detail || 'No suggestion found.'); return }
      setSuggestion(d.suggestion)
    } finally { setSuggesting(false) }
  }
  function applySuggestion() {
    const sg = suggestion || {}
    if (sg.accent_color) setAccent(sg.accent_color)
    setSecondary(sg.secondary_color || '')
    if (sg.ink_color) setInk(sg.ink_color)
    if (sg.logo_url && !logoUrl) setLogoUrl(sg.logo_url)
    setSuggestion(null)
    setMsg('Suggestion applied — check the preview, then Save.')
  }

  async function save() {
    setSaving(true); setMsg('')
    try {
      const r = await fetch(`${API}/v1/commerce/${tenantId}/admin/invoice-settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          trading_as: name, tagline, logo_url: logoUrl, icon_url: iconUrl,
          accent_color: accent, secondary_color: secondary || null, ink_color: ink, font_pairing: fontPairing,
          corner_style: corners, density,
          logo_align: logoAlign, logo_size: logoSize, header_sticky: headerSticky,
          header_nav_position: headerNavPosition, header_cta_text: headerCtaText, header_cta_link: headerCtaLink,
        }),
      })
      if (!r.ok) { const d = await r.json().catch(() => ({})); setMsg(d.detail || 'Could not save.'); return }
      window.dispatchEvent(new Event('vula-brand-changed'))   // shell name/logo/app icon update now
      setMsg('Saved ✓')
      setTimeout(() => setMsg(''), 2500)
    } finally { setSaving(false) }
  }

  if (!loaded) return <p style={s.sectionHint}>Loading…</p>

  const row = { display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }
  const lab = { fontSize: 13, color: 'var(--muted)', width: 110 }
  const colour = (label, value, set, optional) => (
    <div style={row}>
      <span style={lab}>{label}</span>
      <input type="color" value={value || '#888888'} onChange={e => set(e.target.value)} style={bk.colorSwatch} aria-label={label} />
      <input value={value} placeholder={optional ? 'optional' : ''} onChange={e => set(e.target.value)} style={{ ...bk.input, width: 110, fontFamily: 'var(--font-mono)' }} />
      {optional && value && <button type="button" onClick={() => set('')} style={bk.clearBtn} aria-label={`Clear ${label}`}>×</button>}
    </div>)

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 18, alignItems: 'start' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <input placeholder="Business / brand name" value={name} onChange={e => setName(e.target.value)} style={bk.input} aria-label="Business name" />
        <input placeholder="Tagline (under your name in the app)" value={tagline} onChange={e => setTagline(e.target.value)} style={bk.input} aria-label="Tagline" />
        <div style={row}>
          <label style={bk.uploadBtn}>
            {uploading === 'logo' ? 'Uploading…' : (logoUrl ? '↻ Replace logo' : '📷 Upload logo')}
            <input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" onChange={e => upload(e, 'logo')} style={{ display: 'none' }} />
          </label>
          {logoUrl && <img src={logoUrl} alt="logo" style={{ maxHeight: 44, maxWidth: 160, objectFit: 'contain' }} />}
          {logoUrl && <button type="button" onClick={() => setLogoUrl('')} style={bk.clearBtn} aria-label="Remove logo">×</button>}
        </div>
        <div style={row}>
          <span style={lab}>Logo size</span>
          {Object.entries(LOGO_SIZES).map(([k, l]) => (
            <button key={k} type="button" onClick={() => setLogoSize(k)} aria-pressed={logoSize === k}
              style={{ ...bk.choice, ...(logoSize === k ? bk.choiceOn : {}) }}>{l}</button>))}
          <span style={{ fontSize: 11.5, color: 'var(--muted)', flexBasis: '100%', paddingLeft: 120 }}>Everywhere: this dashboard, login, invoices, emails, your WhatsApp menu and phone app icon. Empty margins are trimmed when you upload.</span>
        </div>
        <div style={row}>
          <label style={bk.uploadBtn}>
            {uploading === 'icon' ? 'Uploading…' : (iconUrl ? '↻ Replace app icon' : '📱 App icon (square)')}
            <input type="file" accept="image/png,image/jpeg,image/webp" onChange={e => upload(e, 'icon')} style={{ display: 'none' }} />
          </label>
          {iconUrl && <img src={iconUrl} alt="app icon" style={{ width: 40, height: 40, borderRadius: 10, objectFit: 'cover' }} />}
          {iconUrl && <button type="button" onClick={() => setIconUrl('')} style={bk.clearBtn} aria-label="Remove app icon">×</button>}
        </div>

        <div style={{ ...bk.suggestBox }}>
          <b style={{ fontSize: 13 }}>✨ Suggest from my logo or website</b>
          <div style={{ ...row, marginTop: 6 }}>
            <input placeholder="Website (optional), e.g. gerflor.co.za" value={website} onChange={e => setWebsite(e.target.value)} style={{ ...bk.input, flex: '1 1 180px' }} aria-label="Website" />
            <button type="button" onClick={suggest} disabled={suggesting || (!logoUrl && !website)} style={bk.saveBtn}>{suggesting ? 'Looking…' : 'Suggest'}</button>
          </div>
          {suggestion && (
            <div style={{ marginTop: 8, fontSize: 12.5 }}>
              <div style={{ display: 'flex', gap: 6, margin: '4px 0' }}>
                {[suggestion.accent_color, suggestion.secondary_color, suggestion.ink_color].filter(Boolean).map(c =>
                  <span key={c} title={c} style={{ width: 28, height: 28, borderRadius: 6, background: c, border: '1px solid var(--border)' }} />)}
              </div>
              <span style={{ color: 'var(--muted)' }}>From {suggestion.why}.</span>
              <div style={{ marginTop: 6 }}><button type="button" onClick={applySuggestion} style={bk.saveBtn}>Use these colours</button></div>
            </div>)}
        </div>

        {colour('Main colour', accent, setAccent)}
        {colour('Second colour', secondary, setSecondary, true)}
        {colour('Heading colour', ink, setInk)}
        <div style={row}>
          <span style={lab}>Heading font</span>
          <select value={fontPairing} onChange={e => setFontPairing(e.target.value)} style={{ ...bk.input, width: 220 }}>
            {Object.entries(FONT_PAIRINGS).map(([key, p]) => <option key={key} value={key}>{p.label}</option>)}
          </select>
        </div>
        <div style={row}>
          <span style={lab}>Corners</span>
          {Object.entries(CORNER_STYLES).map(([k, l]) => (
            <button key={k} type="button" onClick={() => setCorners(k)} aria-pressed={corners === k}
              style={{ ...bk.choice, ...(corners === k ? bk.choiceOn : {}), borderRadius: k === 'sharp' ? 2 : k === 'soft' ? 6 : 12 }}>{l}</button>))}
        </div>
        <div style={row}>
          <span style={lab}>Spacing</span>
          {Object.entries(DENSITIES).map(([k, l]) => (
            <button key={k} type="button" onClick={() => setDensity(k)} aria-pressed={density === k}
              style={{ ...bk.choice, ...(density === k ? bk.choiceOn : {}) }}>{l}</button>))}
        </div>
      </div>

      <BrandPreview name={name} tagline={tagline} logoUrl={logoUrl} iconUrl={iconUrl} />

      <div style={{ gridColumn: '1 / -1', borderTop: '1px solid var(--border-soft)', paddingTop: 12 }}>
        <p style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--muted)', margin: '0 0 8px' }}>Storefront header</p>
        <div style={{ ...row, marginBottom: 8 }}>
          <span style={lab}>Logo position</span>
          <select value={logoAlign} onChange={e => setLogoAlign(e.target.value)} style={{ ...bk.input, width: 140 }}>
            <option value="left">Left</option>
            <option value="center">Centered</option>
          </select>

        </div>
        <div style={{ ...row, marginBottom: 8 }}>
          <span style={lab}>Menu position</span>
          <select value={headerNavPosition} onChange={e => setHeaderNavPosition(e.target.value)} style={{ ...bk.input, width: 140 }}>
            <option value="right">Right of logo</option>
            <option value="center">Centered</option>
            <option value="below-logo">Below logo</option>
          </select>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--muted)', marginLeft: 10 }}>
            <input type="checkbox" checked={headerSticky} onChange={e => setHeaderSticky(e.target.checked)} />
            Sticky on scroll
          </label>
        </div>
        <div style={row}>
          <span style={lab}>Button</span>
          <input placeholder="Button text (e.g. Get a quote) — blank for none" value={headerCtaText} onChange={e => setHeaderCtaText(e.target.value)} style={{ ...bk.input, flex: 1, minWidth: 160 }} />
          <input placeholder="Link (e.g. #contact or /shop)" value={headerCtaLink} onChange={e => setHeaderCtaLink(e.target.value)} style={{ ...bk.input, flex: 1, minWidth: 160 }} />
        </div>
      </div>
      <div style={{ ...row, gridColumn: '1 / -1' }}>
        <button onClick={save} disabled={saving} style={bk.saveBtn}>{saving ? 'Saving…' : 'Save brand kit'}</button>
        {msg && <span role="status" style={{ color: 'var(--muted)', fontSize: 13 }}>{msg}</span>}
      </div>
    </div>
  )
}

/** What the brand looks like, built from the real tokens: the app header, a card with a
 * button and status badges, an invoice header and the phone app icon. */
function BrandPreview({ name, tagline, logoUrl, iconUrl }) {
  const initial = (name || 'V').trim()[0]
  return (
    <div aria-label="Brand preview" style={{ background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 'var(--r-card)', padding: 'var(--s4)', display: 'flex', flexDirection: 'column', gap: 'var(--s3)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface)', borderRadius: 'var(--r-card)', padding: 'var(--s3)', boxShadow: 'var(--shadow-sm)' }}>
        {logoUrl ? <img src={logoUrl} alt="" className="vshell-logoimg" />
          : <span style={{ width: 28, height: 28, borderRadius: 'var(--r-input)', background: 'var(--accent)', color: 'var(--on-accent)', display: 'grid', placeItems: 'center', fontWeight: 700 }}>{initial}</span>}
        <div style={{ minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-display)', color: 'var(--ink)', fontWeight: 600, fontSize: 16, lineHeight: 1.1 }}>{name || 'Your business'}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>{tagline || 'Business admin'}</div>
        </div>
      </div>
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--r-card)', padding: 'var(--s4)' }}>
        <div style={{ fontFamily: 'var(--font-display)', color: 'var(--ink)', fontSize: 18, fontWeight: 600 }}>Invoice INV-0042</div>
        <div style={{ fontSize: 12.5, color: 'var(--muted)', margin: '4px 0 10px' }}>R 12 450.00 · due 30 Sep</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
          <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 'var(--r-pill)', background: 'var(--ok-soft)', color: 'var(--ok)' }}>Paid</span>
          <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 'var(--r-pill)', background: 'var(--danger-soft)', color: 'var(--danger)' }}>Overdue</span>
          <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 'var(--r-pill)', background: 'var(--accent-soft)', color: 'var(--accent)' }}>Draft</span>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <span style={{ padding: '7px 14px', borderRadius: 'var(--r-input)', background: 'var(--accent)', color: 'var(--on-accent)', fontSize: 13, fontWeight: 600 }}>Send invoice</span>
          <span style={{ padding: '7px 14px', borderRadius: 'var(--r-input)', border: '1px solid var(--accent-2)', color: 'var(--accent-2)', fontSize: 13, fontWeight: 600 }}>Preview</span>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        {iconUrl ? <img src={iconUrl} alt="" style={{ width: 48, height: 48, borderRadius: 12, objectFit: 'cover', boxShadow: 'var(--shadow-md)' }} />
          : <span style={{ width: 48, height: 48, borderRadius: 12, background: 'var(--accent)', color: 'var(--on-accent)', display: 'grid', placeItems: 'center', fontWeight: 700, fontSize: 20, boxShadow: 'var(--shadow-md)' }}>{initial}</span>}
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>Your app icon on a phone's home screen</span>
      </div>
    </div>
  )
}

const bk = {
  input:      { padding: '9px 12px', border: '1px solid var(--border)', borderRadius: 'var(--r-input)', fontSize: 14, boxSizing: 'border-box', background: 'var(--surface)', color: 'var(--text)' },
  uploadBtn:  { padding: '8px 14px', background: 'var(--accent-soft)', color: 'var(--accent)', border: '1px solid var(--accent)', borderRadius: 'var(--r-input)', fontSize: 13, cursor: 'pointer' },
  clearBtn:   { background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 18, minWidth: 32, minHeight: 32 },
  colorSwatch:{ width: 40, height: 34, padding: 2, border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer', background: 'var(--surface)' },
  saveBtn:    { padding: '10px 18px', background: 'var(--accent)', color: 'var(--on-accent)', border: 'none', borderRadius: 'var(--r-input)', fontSize: 13, fontWeight: 600, cursor: 'pointer', alignSelf: 'flex-start' },
  choice:     { padding: '7px 12px', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13, cursor: 'pointer' },
  choiceOn:   { borderColor: 'var(--accent)', background: 'var(--accent-soft)', color: 'var(--accent)', fontWeight: 600 },
  suggestBox: { border: '1px dashed var(--accent)', borderRadius: 'var(--r-card)', padding: 'var(--s3)', background: 'var(--accent-soft)' },
}

function DeliverySettings({ tenantId }) {
  const API = VULA_API
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
        radius: 8, color: 'var(--accent)', fillColor: 'var(--accent)', fillOpacity: 0.9,
      }).addTo(map)
      const circle = L.circle([start.lat, start.lng], {
        radius: radiusKm * 1000, color: 'var(--accent)', weight: 1.5, fillOpacity: 0.08,
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

  const inp = { padding: '9px 11px', border: '1px solid var(--border)', borderRadius: 8, fontSize: 13, boxSizing: 'border-box' }
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* Marketplace-style location + radius */}
      <div>
        <p style={{ fontSize: 12.5, fontWeight: 600, margin: '0 0 6px' }}>
          📍 Shop location &amp; delivery radius
        </p>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
          <input value={geoSearch} onChange={e => setGeoSearch(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && findAddress()}
            placeholder="Search your shop's address or suburb…" style={{ ...inp, flex: 1, minWidth: 200 }} />
          <button onClick={findAddress}
            style={{ padding: '9px 14px', border: '1px solid var(--border)', borderRadius: 8, background: 'var(--surface)', fontSize: 13, cursor: 'pointer'}}>
            Find
          </button>
          <select value={radiusKm} onChange={e => setRadiusKm(Number(e.target.value))} style={inp}>
            {[5, 10, 15, 20, 25, 30, 40, 50].map(k => <option key={k} value={k}>{k} km radius</option>)}
          </select>
        </div>
        <div ref={mapDivRef} style={{ height: 260, borderRadius: 10, border: '1px solid var(--border)', overflow: 'hidden' }} />
        <p style={{ fontSize: 11.5, color: 'var(--muted)', margin: '6px 0 0' }}>
          {origin
            ? <>Pinned: <b>{originLabel || 'your shop'}</b> · {radiusKm} km radius. Customers who share a WhatsApp location pin get an instant in/out answer.</>
            : 'Search or click the map to drop your shop pin — then the radius circle shows exactly where you deliver.'}
        </p>
      </div>

      <label style={{ fontSize: 12, color: 'var(--muted)'}}>
        Delivery areas (comma-separated — e.g. Table View, Blouberg, Parklands)
        <input value={areas} onChange={e => setAreas(e.target.value)} placeholder="Table View, Blouberg, Parklands…"
          style={{ ...inp, width: '100%', marginTop: 4 }} />
      </label>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--muted)', flex: 1, minWidth: 140 }}>
          Delivery fee (R)
          <input value={fee} onChange={e => setFee(e.target.value)} placeholder="80" style={{ ...inp, width: '100%', marginTop: 4 }} />
        </label>
        <label style={{ fontSize: 12, color: 'var(--muted)', flex: 1, minWidth: 140 }}>
          Free delivery over (R)
          <input value={freeOver} onChange={e => setFreeOver(e.target.value)} placeholder="500" style={{ ...inp, width: '100%', marginTop: 4 }} />
        </label>
        <label style={{ fontSize: 12, color: 'var(--muted)', flex: 1, minWidth: 140 }}>
          Minimum order (R)
          <input value={minOrder} onChange={e => setMinOrder(e.target.value)} placeholder="none" style={{ ...inp, width: '100%', marginTop: 4 }} />
        </label>
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <button onClick={save} disabled={busy}
          style={{ padding: '9px 18px', border: 'none', borderRadius: 8, background: 'var(--accent)', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer'}}>
          {busy ? 'Saving…' : 'Save delivery settings'}
        </button>
        {msg && <span style={{ fontSize: 12.5, color: 'var(--accent)'}}>{msg}</span>}
      </div>
    </div>
  )
}

const DAYS = [
  { key: 'mon', label: 'Mon' }, { key: 'tue', label: 'Tue' }, { key: 'wed', label: 'Wed' },
  { key: 'thu', label: 'Thu' }, { key: 'fri', label: 'Fri' }, { key: 'sat', label: 'Sat' },
  { key: 'sun', label: 'Sun' },
]

function BusinessHoursSettings({ tenantId }) {
  const API = VULA_API
  // One row per day: { closed, open, close }. Defaults to a common SA trading week until the
  // owner's own settings load — never sent unless they hit Save.
  const [days, setDays] = useState(() => Object.fromEntries(DAYS.map(d => [d.key,
    { closed: d.key === 'sun', open: '08:00', close: d.key === 'sat' ? '13:00' : '17:00' }])))
  const [note, setNote] = useState('')
  const [afterHours, setAfterHours] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetch(`${API}/v1/commerce/${tenantId}/admin/order-settings`)
      .then(r => r.json())
      .then(d => {
        const st = d.settings || {}
        if (st.business_hours) {
          setDays(prev => {
            const next = { ...prev }
            for (const day of DAYS) {
              const cfg = st.business_hours[day.key]
              next[day.key] = cfg && cfg.open && cfg.close
                ? { closed: false, open: cfg.open, close: cfg.close }
                : { ...prev[day.key], closed: true }
            }
            return next
          })
        }
        if (st.business_hours_note) setNote(st.business_hours_note)
        if (st.after_hours_message) setAfterHours(st.after_hours_message)
      }).catch(() => {})
  }, [tenantId])  // eslint-disable-line

  function setDay(key, patch) {
    setDays(prev => ({ ...prev, [key]: { ...prev[key], ...patch } }))
  }

  async function save() {
    setBusy(true)
    try {
      const business_hours = Object.fromEntries(DAYS.map(d => {
        const day = days[d.key]
        return [d.key, day.closed ? null : { open: day.open, close: day.close }]
      }))
      const body = {
        business_hours,
        business_hours_note: note.trim() || null,
        after_hours_message: afterHours.trim() || null,
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

  const inp = { padding: '9px 11px', border: '1px solid var(--border)', borderRadius: 8, fontSize: 13, boxSizing: 'border-box' }
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {DAYS.map(d => {
          const day = days[d.key]
          return (
            <div key={d.key} style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <label style={{ fontSize: 12.5, width: 40, display: 'flex', alignItems: 'center', gap: 6 }}>
                <input type="checkbox" checked={!day.closed}
                  onChange={e => setDay(d.key, { closed: !e.target.checked })} />
                {d.label}
              </label>
              {day.closed ? (
                <span style={{ fontSize: 12.5, color: 'var(--faint)'}}>Closed</span>
              ) : (
                <>
                  <input type="time" value={day.open} onChange={e => setDay(d.key, { open: e.target.value })} style={inp} />
                  <span style={{ fontSize: 12.5, color: 'var(--muted)' }}>–</span>
                  <input type="time" value={day.close} onChange={e => setDay(d.key, { close: e.target.value })} style={inp} />
                </>
              )}
            </div>
          )
        })}
      </div>
      <label style={{ fontSize: 12, color: 'var(--muted)'}}>
        Note shown alongside your hours (optional — e.g. "Closed on public holidays")
        <input value={note} onChange={e => setNote(e.target.value)} placeholder="Closed on public holidays"
          style={{ ...inp, width: '100%', marginTop: 4 }} />
      </label>
      <label style={{ fontSize: 12, color: 'var(--muted)'}}>
        Custom after-hours message (optional — replaces the default "we're closed, back at X")
        <input value={afterHours} onChange={e => setAfterHours(e.target.value)}
          placeholder="We're offline for the weekend — WhatsApp us Monday from 8am!"
          style={{ ...inp, width: '100%', marginTop: 4 }} />
      </label>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <button onClick={save} disabled={busy}
          style={{ padding: '9px 18px', border: 'none', borderRadius: 8, background: 'var(--accent)', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer'}}>
          {busy ? 'Saving…' : 'Save opening hours'}
        </button>
        {msg && <span style={{ fontSize: 12.5, color: 'var(--accent)'}}>{msg}</span>}
      </div>
    </div>
  )
}

function LearnedSummary({ tenantId }) {
  const API = VULA_API
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  function load() {
    fetch(`${API}/v1/commerce/${tenantId}/admin/learned-summary`)
      .then(r => r.json()).then(setData).catch(() => {})
  }
  useEffect(load, [tenantId])  // eslint-disable-line

  async function respondToVoiceSuggestion(accept) {
    if (!data) return
    setBusy(true)
    try {
      const persona_prompt = accept ? data.voice.suggested : data.voice.current
      const r = await fetch(`${API}/v1/commerce/${tenantId}/admin/persona`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ persona_prompt }),
      })
      const d = await r.json()
      setMsg(d.error ? String(d.error) : (accept ? 'Voice updated.' : 'Dismissed.'))
      load()
    } catch (e) { setMsg('Could not save — try again.') } finally {
      setBusy(false); setTimeout(() => setMsg(''), 5000)
    }
  }

  if (!data) return <p style={{ ...s.sectionHint, margin: 0 }}>Loading…</p>

  const card = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 14, marginBottom: 12 }
  const label = { fontSize: 13, fontWeight: 700, color: 'var(--ink)', margin: '0 0 6px' }
  const hint = { fontSize: 12.5, color: 'var(--muted)', margin: 0, lineHeight: 1.5 }
  const btn = (primary) => ({
    padding: '7px 14px', borderRadius: 7, fontSize: 12.5, fontWeight: 600, cursor: 'pointer', border: primary ? 'none' : '1px solid var(--border)',
    background: primary ? 'var(--accent)' : '#fff', color: primary ? '#fff' : 'var(--ink)',
  })

  return (
    <div>
      {/* Voice / tone — the only section with an action, reusing the existing accept/dismiss
          PATCH endpoint built for exactly this (see admin_set_persona's own docstring). */}
      <div style={card}>
        <p style={label}>🗣️ Voice &amp; tone</p>
        {data.voice.current
          ? <p style={{ ...hint, marginBottom: 8 }}>Current: "{data.voice.current}"</p>
          : <p style={{ ...hint, marginBottom: 8 }}>No voice set yet — Vula uses its default tone.</p>}
        {data.voice.suggested ? (
          <div style={{ background: 'var(--bg)', borderRadius: 8, padding: 10, marginTop: 4 }}>
            <p style={{ ...hint, color: 'var(--ink)', marginBottom: 8 }}>
              Suggested, from your own real messages: "{data.voice.suggested}"
            </p>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button style={btn(true)} disabled={busy} onClick={() => respondToVoiceSuggestion(true)}>Accept</button>
              <button style={btn(false)} disabled={busy} onClick={() => respondToVoiceSuggestion(false)}>Dismiss</button>
              {msg && <span style={{ fontSize: 12, color: 'var(--accent)'}}>{msg}</span>}
            </div>
          </div>
        ) : (
          <p style={hint}>No suggestion pending — Vula quietly re-checks as you send more real messages.</p>
        )}
      </div>

      {/* Learned answers — reviewed on WhatsApp (Keep/Bin), shown here for visibility. */}
      <div style={card}>
        <p style={label}>💬 Answers learned from your team</p>
        <p style={hint}>
          {data.learned_answers.approved_count} approved and in use
          {data.learned_answers.pending_count > 0
            ? `, ${data.learned_answers.pending_count} waiting for your Keep/Bin reply on WhatsApp.`
            : '.'}
        </p>
        {data.learned_answers.recent_approved.length > 0 && (
          <ul style={{ margin: '8px 0 0', padding: '0 0 0 18px', ...hint }}>
            {data.learned_answers.recent_approved.slice(0, 5).map(r => (
              <li key={r.id} style={{ marginBottom: 4 }}>"{r.question}"</li>
            ))}
          </ul>
        )}
      </div>

      {/* Merchant categorisation — decided automatically from bank statements + research. */}
      <div style={card}>
        <p style={label}>🏪 Supplier categorisation</p>
        <p style={hint}>
          {data.merchant_profiles.decided_count} of {data.merchant_profiles.total_count} suppliers
          categorised{data.merchant_profiles.total_count === 0 ? ' yet — this fills in as bank statements come through.' : '.'}
        </p>
      </div>
    </div>
  )
}

function BusinessProfileSettings({ tenantId }) {
  const API = VULA_API
  const [data, setData] = useState(null)
  const [draft, setDraft] = useState({})
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    if (!tenantId) return
    fetch(`${API}/v1/commerce/${tenantId}/admin/business-profile`)
      .then(r => r.json()).then(d => { setData(d); setDraft(d.answers || {}) })
      .catch(() => setMsg('Couldn\u2019t load the profile.'))
  }, [tenantId])
  const save = async () => {
    setSaving(true); setMsg('')
    try {
      const r = await fetch(`${API}/v1/commerce/${tenantId}/admin/business-profile`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answers: draft }),
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'Save failed')
      setData(d); setMsg(`Saved — ${d.answered} of ${d.total} answered.`)
    } catch (e) { setMsg(e.message) } finally { setSaving(false) }
  }
  if (!data) return <div style={{ fontSize: 13, color: 'var(--muted)' }}>{msg || 'Loading…'}</div>
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ fontSize: 13, color: 'var(--muted)' }}>{data.answered} of {data.total} answered</div>
      {data.questions.map(q => (
        <label key={q.key} style={{ display: 'grid', gap: 4, fontSize: 13, color: 'var(--text)' }}>
          {q.q}
          <textarea rows={2} style={{ ...s.input, fontFamily: 'var(--font-body)', resize: 'vertical' }}
            value={draft[q.key] || ''} onChange={e => setDraft({ ...draft, [q.key]: e.target.value })} />
        </label>
      ))}
      <button style={s.saveBtn} onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save profile'}</button>
      {msg && <div style={{ fontSize: 13, color: 'var(--muted)' }}>{msg}</div>}
    </div>
  )
}

const s = {
  intro:        { marginBottom: 16 },
  h3:           { fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 700, color: 'var(--ink)', margin: '0 0 4px' },
  sub:          { fontSize: 13, color: 'var(--muted)', margin: 0 },
  section:      { marginBottom: 24 },
  sectionTitle: { fontSize: 15, fontWeight: 700, color: 'var(--ink)', margin: '0 0 4px' },
  sectionHint:  { fontSize: 13, color: 'var(--muted)', margin: '0 0 12px', lineHeight: 1.5 },
  footer:       { textAlign: 'center', fontSize: 11, color: 'var(--faint)', marginTop: 24 },
}
