/** BrandPreview.jsx — moved out of VulaSettings.jsx unchanged (2026-10-19) so both looks share it. */

/** What the brand looks like, built from the real tokens: the app header, a card with a
 * button and status badges, an invoice header and the phone app icon. */
// v2: the redesign's text shades, so the chips and the outline button read at 4.5:1 (the current
// look keeps its own colours).
export default function BrandPreview({ name, tagline, logoUrl, iconUrl, v2 = false }) {
  const initial = (name || 'V').trim()[0]
  const tone = (t) => (v2 ? `var(--${t === 'accent' ? 'brand' : t}-text)` : `var(--${t})`)
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
          <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 'var(--r-pill)', background: 'var(--ok-soft)', color: tone('ok') }}>Paid</span>
          <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 'var(--r-pill)', background: 'var(--danger-soft)', color: tone('danger') }}>Overdue</span>
          <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 'var(--r-pill)', background: 'var(--accent-soft)', color: tone('accent') }}>Draft</span>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <span style={{ padding: '7px 14px', borderRadius: 'var(--r-input)', background: 'var(--accent)', color: 'var(--on-accent)', fontSize: 13, fontWeight: 600 }}>Send invoice</span>
          <span style={{ padding: '7px 14px', borderRadius: 'var(--r-input)', border: '1px solid var(--accent-2)', color: v2 ? 'var(--ink)' : 'var(--accent-2)', fontSize: 13, fontWeight: 600 }}>Preview</span>
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
