/**
 * VulaSettings.jsx — tenant store settings & connections.
 *
 * Lets the owner connect their OWN payment + WhatsApp (self-service), and see
 * their store details. Reuses the same connect components as the master admin
 * so a tenant is no longer dependent on Vula staff to go live.
 */

import { FONT_PAIRINGS, CORNER_STYLES, DENSITIES, LOGO_SIZES } from '../theme/tokens'
import { useBrandKit } from './settings/useSettings'
import SettingsV2 from './settings/SettingsV2'
import BrandPreview from './settings/BrandPreview'

export default function VulaSettings({ ...props }) {
  return <SettingsV2 {...props} />;
}

export function BrandKitSettings({ tenantId }) {
  const {
    name, setName, tagline, setTagline, logoUrl, setLogoUrl, iconUrl, setIconUrl, accent, setAccent, secondary, setSecondary,
    ink, setInk, fontPairing, setFontPairing, corners, setCorners, density, setDensity, logoAlign, setLogoAlign, logoSize, setLogoSize,
    headerSticky, setHeaderSticky, headerNavPosition, setHeaderNavPosition, headerCtaText, setHeaderCtaText, headerCtaLink, setHeaderCtaLink,
    uploading, saving, msg, loaded, website, setWebsite, suggesting, suggestion, upload, suggest, applySuggestion, save,
  } = useBrandKit(tenantId)

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

const s = {
  intro:        { marginBottom: 16 },
  h3:           { fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 700, color: 'var(--ink)', margin: '0 0 4px' },
  sub:          { fontSize: 13, color: 'var(--muted)', margin: 0 },
  section:      { marginBottom: 24 },
  sectionTitle: { fontSize: 15, fontWeight: 700, color: 'var(--ink)', margin: '0 0 4px' },
  sectionHint:  { fontSize: 13, color: 'var(--muted)', margin: '0 0 12px', lineHeight: 1.5 },
  footer:       { textAlign: 'center', fontSize: 11, color: 'var(--faint)', marginTop: 24 },
}
