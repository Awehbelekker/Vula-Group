/**
 * SettingsV2.jsx — the redesigned Settings (2026-10-19, UX redesign Phase F). Same data and
 * actions as the current screen (settings/useSettings.js for the business's own settings; the
 * connect components are the same ones, unchanged). One long page becomes a list of sections,
 * grouped: on a phone the list, then one section with a way back; from 1024px the list beside
 * the open section.
 */
import { useState } from 'react'
import {
  ArrowLeft, Bike, Brain, ChevronRight, ClipboardList, Clock, Cloud, Contact, CreditCard, Globe, ImageUp, LayoutGrid,
  ListChecks, Mail, MapPin, MessageCircle, Palette, ShoppingBag, ShoppingCart, Smartphone, Sparkles, X,
} from 'lucide-react'
import { Button, Card, Field, IconButton, Input, PageHeader, Select, Skeleton, StatusChip, Textarea } from '../ui/index.jsx'
import { CORNER_STYLES, DEFAULT_BRAND, DENSITIES, FONT_PAIRINGS, LOGO_SIZES } from '../../theme/tokens'
import BrandPreview from './BrandPreview'
import VulaYocoConnect from '../VulaYocoConnect'
import VulaShopifyConnect from '../VulaShopifyConnect'
import VulaShopDomain from '../VulaShopDomain'
import VulaWAProfile from '../VulaWAProfile'
import VulaWooConnect from '../VulaWooConnect'
import VulaWhatsAppConnect from '../VulaWhatsAppConnect'
import VulaClickUpConnect from '../VulaClickUpConnect'
import OAuthConnectV2 from './connect/OAuthConnectV2'
import VulaEmailConnect from '../VulaEmailConnect'
import { DAYS, useBrandKit, useBusinessHours, useBusinessProfile, useDeliverySettings, useLearnedSummary } from './useSettings'
import './settings.css'

const GROUPS = [
  { label: 'Your business', items: [
    { id: 'brand', label: 'Brand kit', Icon: Palette, hint: 'Your name, logo, colours and style — used everywhere: this dashboard, the login page, your phone app icon, invoices, emails and your storefront. Status colours (paid, overdue) always stay green and red so they read the same for everyone.' },
    { id: 'profile', label: 'Business profile', Icon: ClipboardList, hint: 'Short answers about how your business works. Vula answers customers and your team from these — anything left blank, it says it needs to check instead of guessing. You can also do this on WhatsApp: send “set up my profile”.' },
    { id: 'hours', label: 'Opening hours', Icon: Clock, hint: 'The WhatsApp assistant answers "are you open?" from this — leave a day off to mark it closed. With no hours at all, it checks with your team instead of guessing.' },
    { id: 'delivery', label: 'Delivery areas & fees', Icon: Bike, hint: 'Where you deliver and what it costs. The WhatsApp assistant answers coverage questions from this — anywhere else, it checks with your team instead of guessing.' },
  ] },
  { label: 'Getting paid & selling', items: [
    { id: 'payments', label: 'Payments (Yoco)', Icon: CreditCard, hint: 'Connect your Yoco account to accept card, tap & QR payments. Money goes straight to you.' },
    { id: 'shopify', label: 'Shopify store', Icon: ShoppingBag, hint: 'Already selling on Shopify? Connect it and your products, photos, prices and stock stay in step with Vula automatically.' },
    { id: 'woo', label: 'WooCommerce shop', Icon: ShoppingCart, hint: 'Selling on WooCommerce (WordPress)? Connect it and your products, photos, prices and stock stay in step with Vula automatically.' },
    { id: 'domain', label: 'Your shop’s own address', Icon: Globe, hint: 'Show your name, not Vula’s, when customers open your shop or a receipt from WhatsApp.' },
  ] },
  { label: 'WhatsApp', items: [
    { id: 'whatsapp', label: 'WhatsApp number', Icon: MessageCircle, hint: 'Connect your WhatsApp Business number so customers can order and chat to your AI assistant.' },
    { id: 'waprofile', label: 'Your WhatsApp profile', Icon: Contact, hint: 'What customers see when they tap your name in WhatsApp — set from your brand kit in one tap.' },
  ] },
  { label: 'Connected apps', items: [
    { id: 'google', label: 'Google (Drive & Gmail)', Icon: Cloud, hint: 'Connect Google so Vula can find & file Drive documents and draft Gmail replies (draft-only).' },
    { id: 'microsoft', label: 'Microsoft (OneDrive & Outlook)', Icon: LayoutGrid, hint: 'Connect Microsoft 365 so Vula can find & file OneDrive documents and draft Outlook replies (draft-only).' },
    { id: 'email', label: 'Email (IMAP / SMTP)', Icon: Mail, hint: 'Connect any mailbox without OAuth — GoDaddy Workspace, cPanel, Zoho. Search it, file attachments to the knowledge base, draft replies.' },
    { id: 'clickup', label: 'ClickUp', Icon: ListChecks, hint: 'Connect ClickUp to create, list and update tasks — and set reminders — straight from WhatsApp.' },
  ] },
  { label: 'Vula', items: [
    { id: 'learned', label: 'What Vula has learned', Icon: Brain, hint: "Everything Vula has picked up about how your business runs — your voice, answers learned from your team, and how it's learned to categorise your suppliers." },
  ] },
]
const ALL = GROUPS.flatMap(g => g.items)

export default function SettingsV2({ tenantId, tenantName, adminEmail }) {
  const [open, setOpen] = useState(null)        // null: the list (a phone); a desktop shows Brand kit
  const shown = ALL.find(i => i.id === (open || 'brand'))
  const props = { tenantId, tenantName, adminEmail }
  return (
    <div className="vst" data-open={open ? 'true' : 'false'}>
      <PageHeader sub="Your business, how you get paid, WhatsApp and the apps Vula works with." />
      <div className="vst-panes">
        <nav className="vst-nav" aria-label="Settings sections">
          {GROUPS.map(g => (
            <div key={g.label} className="vst-group">
              <h2 className="vst-group-h">{g.label}</h2>
              <Card pad={4}>
                {g.items.map(it => (
                  <button key={it.id} type="button" className="vst-item" aria-current={shown.id === it.id ? 'page' : undefined}
                    onClick={() => setOpen(it.id)}>
                    <span className="vst-item-ic" aria-hidden="true"><it.Icon size={18} strokeWidth={1.75} /></span>
                    <span className="vst-item-label">{it.label}</span>
                    <ChevronRight size={18} className="vst-item-go" aria-hidden="true" />
                  </button>
                ))}
              </Card>
            </div>
          ))}
        </nav>
        <section className="vst-page" aria-labelledby="vst-title">
          <Button size="sm" variant="quiet" Icon={ArrowLeft} className="vst-back" onClick={() => setOpen(null)}>All settings</Button>
          <div className="vst-head">
            <h2 id="vst-title" className="vst-title"><shown.Icon size={22} strokeWidth={1.75} aria-hidden="true" />{shown.label}</h2>
            <p className="vst-sub">{shown.hint}</p>
          </div>
          <Section id={shown.id} {...props} />
        </section>
      </div>
    </div>
  )
}

function Section({ id, tenantId, tenantName, adminEmail }) {
  switch (id) {
    case 'brand': return <BrandKit tenantId={tenantId} />
    case 'profile': return <BusinessProfile tenantId={tenantId} />
    case 'hours': return <Hours tenantId={tenantId} />
    case 'delivery': return <Delivery tenantId={tenantId} />
    case 'learned': return <Learned tenantId={tenantId} />
    // the connect flows are the same components as today, framed in a card
    case 'payments': return <Card><VulaYocoConnect tenantId={tenantId} tenantName={tenantName} adminEmail={adminEmail} /></Card>
    case 'shopify': return <Card><VulaShopifyConnect tenantId={tenantId} adminEmail={adminEmail} /></Card>
    case 'woo': return <Card><VulaWooConnect tenantId={tenantId} adminEmail={adminEmail} /></Card>
    case 'domain': return <Card><VulaShopDomain tenantId={tenantId} /></Card>
    case 'whatsapp': return <Card><VulaWhatsAppConnect tenantId={tenantId} tenantName={tenantName} adminEmail={adminEmail} /></Card>
    case 'waprofile': return <Card><VulaWAProfile tenantId={tenantId} /></Card>
    case 'google': return <OAuthConnectV2 provider="google" tenantId={tenantId} tenantName={tenantName} />
    case 'microsoft': return <OAuthConnectV2 provider="microsoft" tenantId={tenantId} tenantName={tenantName} />
    case 'email': return <Card><VulaEmailConnect tenantId={tenantId} adminEmail={adminEmail} /></Card>
    case 'clickup': return <Card><VulaClickUpConnect tenantId={tenantId} tenantName={tenantName} /></Card>
    default: return null
  }
}

const Saved = ({ children }) => (children ? <span className="vst-msg" role="status">{String(children).replace(' ✓', '')}</span> : null)

function Choices({ label, options, value, onChange, shape }) {
  return (
    <div className="vst-choice">
      <span className="vst-label" id={`vst-${label}`}>{label}</span>
      <div className="vst-chips" role="group" aria-labelledby={`vst-${label}`}>
        {Object.entries(options).map(([k, l]) => (
          <button key={k} type="button" className="vui-chip" aria-pressed={value === k} onClick={() => onChange(k)}
            style={shape ? { borderRadius: k === 'sharp' ? 2 : k === 'soft' ? 6 : 12 } : undefined}>{l}</button>))}
      </div>
    </div>
  )
}

// ── Brand kit ──
function BrandKit({ tenantId }) {
  const b = useBrandKit(tenantId)
  if (!b.loaded) return <Card><Skeleton lines={4} /></Card>
  const colour = (label, value, set, optional) => (
    <div className="vst-colour">
      <span className="vst-label">{label}</span>
      <input type="color" className="vst-swatch" value={value || DEFAULT_BRAND.pickerEmpty} onChange={e => set(e.target.value)} aria-label={`${label} picker`} />
      <Input className="vst-hex" value={value} placeholder={optional ? 'optional' : ''} onChange={e => set(e.target.value)} aria-label={label} />
      {optional && value && <IconButton Icon={X} quiet label={`Clear ${label}`} onClick={() => set('')} />}
    </div>)
  const image = (kind, url, set, label, accept) => (
    <div className="vst-row">
      <label className="vst-upload">
        <ImageUp size={16} aria-hidden="true" />
        {b.uploading === kind ? 'Uploading…' : url ? `Replace ${label}` : `Upload ${label}`}
        <input type="file" accept={accept} onChange={e => b.upload(e, kind)} className="vst-file" />
      </label>
      {url && <img src={url} alt={label} className={kind === 'icon' ? 'vst-icon-img' : 'vst-logo-img'} />}
      {url && <IconButton Icon={X} quiet label={`Remove ${label}`} onClick={() => set('')} />}
    </div>)
  return (
    <div className="vst-brand">
      <div className="vst-stack">
        <Card className="vst-card">
          <h3 className="vst-h">Name and logo</h3>
          <Field label="Business name"><Input value={b.name} onChange={e => b.setName(e.target.value)} placeholder="Business / brand name" /></Field>
          <Field label="Tagline" hint="Under your name in the app."><Input value={b.tagline} onChange={e => b.setTagline(e.target.value)} /></Field>
          {image('logo', b.logoUrl, b.setLogoUrl, 'logo', 'image/png,image/jpeg,image/webp,image/svg+xml')}
          <Choices label="Logo size" options={LOGO_SIZES} value={b.logoSize} onChange={b.setLogoSize} />
          <p className="vst-sub">Everywhere: this dashboard, login, invoices, emails, your WhatsApp menu and phone app icon. Empty margins are trimmed when you upload.</p>
          {image('icon', b.iconUrl, b.setIconUrl, 'app icon', 'image/png,image/jpeg,image/webp')}
          <p className="vst-sub"><Smartphone size={14} aria-hidden="true" /> Square — your app icon on a phone's home screen.</p>
        </Card>

        <Card className="vst-card">
          <h3 className="vst-h">Colours</h3>
          <div className="vst-suggest">
            <span className="vst-strong"><Sparkles size={16} aria-hidden="true" />Suggest from my logo or website</span>
            <div className="vst-row">
              <Input className="vst-grow" placeholder="Website (optional), e.g. gerflor.co.za" value={b.website} onChange={e => b.setWebsite(e.target.value)} aria-label="Website" />
              <Button variant="secondary" onClick={b.suggest} disabled={b.suggesting || (!b.logoUrl && !b.website)}>{b.suggesting ? 'Looking…' : 'Suggest'}</Button>
            </div>
            {b.suggestion && (
              <div className="vst-row">
                {[b.suggestion.accent_color, b.suggestion.secondary_color, b.suggestion.ink_color].filter(Boolean).map(c =>
                  <span key={c} title={c} className="vst-dot" style={{ background: c }} />)}
                <span className="vst-sub vst-grow">From {b.suggestion.why}.</span>
                <Button size="sm" onClick={b.applySuggestion}>Use these colours</Button>
              </div>)}
          </div>
          {colour('Main colour', b.accent, b.setAccent)}
          {colour('Second colour', b.secondary, b.setSecondary, true)}
          {colour('Heading colour', b.ink, b.setInk)}
        </Card>

        <Card className="vst-card">
          <h3 className="vst-h">Style</h3>
          <Field label="Heading font">
            <Select value={b.fontPairing} onChange={e => b.setFontPairing(e.target.value)}>
              {Object.entries(FONT_PAIRINGS).map(([key, p]) => <option key={key} value={key}>{p.label}</option>)}
            </Select>
          </Field>
          <Choices label="Corners" options={CORNER_STYLES} value={b.corners} onChange={b.setCorners} shape />
          <Choices label="Spacing" options={DENSITIES} value={b.density} onChange={b.setDensity} />
        </Card>

        <Card className="vst-card">
          <h3 className="vst-h">Storefront header</h3>
          <div className="vst-grid">
            <Field label="Logo position">
              <Select value={b.logoAlign} onChange={e => b.setLogoAlign(e.target.value)}>
                <option value="left">Left</option><option value="center">Centered</option>
              </Select>
            </Field>
            <Field label="Menu position">
              <Select value={b.headerNavPosition} onChange={e => b.setHeaderNavPosition(e.target.value)}>
                <option value="right">Right of logo</option><option value="center">Centered</option><option value="below-logo">Below logo</option>
              </Select>
            </Field>
            <Field label="Button text" hint="Blank for none."><Input placeholder="e.g. Get a quote" value={b.headerCtaText} onChange={e => b.setHeaderCtaText(e.target.value)} /></Field>
            <Field label="Button link"><Input placeholder="e.g. #contact or /shop" value={b.headerCtaLink} onChange={e => b.setHeaderCtaLink(e.target.value)} /></Field>
          </div>
          <label className="vst-check"><input type="checkbox" checked={b.headerSticky} onChange={e => b.setHeaderSticky(e.target.checked)} />Header stays on screen when scrolling</label>
        </Card>
      </div>

      <aside className="vst-preview">
        <BrandPreview name={b.name} tagline={b.tagline} logoUrl={b.logoUrl} iconUrl={b.iconUrl} v2 />
      </aside>

      <div className="vst-save">
        <Button onClick={b.save} disabled={b.saving}>{b.saving ? 'Saving…' : 'Save brand kit'}</Button>
        <Saved>{b.msg}</Saved>
      </div>
    </div>
  )
}

// ── Business profile ──
function BusinessProfile({ tenantId }) {
  const p = useBusinessProfile(tenantId)
  if (!p.data) return <Card>{p.msg ? <p className="vst-sub">{p.msg}</p> : <Skeleton lines={4} />}</Card>
  return (
    <Card className="vst-card">
      <p className="vst-sub">
        <StatusChip tone={p.data.answered === p.data.total ? 'ok' : 'warn'}>{p.data.answered} of {p.data.total} answered</StatusChip>{' '}
        Ticked answers can be used when Vula replies to customers; the rest are only for you and your team.
      </p>
      {p.data.questions.map(q => (
        <div key={q.key} className="vst-q">
          <label htmlFor={`bp-${q.key}`} className="vst-q-label">{q.q}</label>
          <Textarea id={`bp-${q.key}`} rows={2} value={p.draft[q.key] || ''} onChange={e => p.setDraft({ ...p.draft, [q.key]: e.target.value })} style={{ minHeight: 64 }} />
          <label className="vst-check">
            <input type="checkbox" checked={p.publicKeys.includes(q.key)} onChange={e => p.togglePublic(q.key, e.target.checked)} />
            Customers can see this answer
          </label>
        </div>
      ))}
      <div className="vst-save vst-save--in">
        <Button onClick={p.save} disabled={p.saving}>{p.saving ? 'Saving…' : 'Save profile'}</Button>
        <Saved>{p.msg}</Saved>
      </div>
    </Card>
  )
}

// ── Opening hours ──
function Hours({ tenantId }) {
  const h = useBusinessHours(tenantId)
  return (
    <Card className="vst-card">
      <ul className="vst-days" aria-label="Opening hours">
        {DAYS.map(d => {
          const day = h.days[d.key]
          return (
            <li key={d.key} className="vst-day">
              <label className="vst-check vst-day-name">
                <input type="checkbox" checked={!day.closed} onChange={e => h.setDay(d.key, { closed: !e.target.checked })} />{d.label}
              </label>
              {day.closed ? <span className="vst-sub">Closed</span> : (
                <span className="vst-times">
                  <Input type="time" aria-label={`${d.label} opens`} value={day.open} onChange={e => h.setDay(d.key, { open: e.target.value })} />
                  <span aria-hidden="true">–</span>
                  <Input type="time" aria-label={`${d.label} closes`} value={day.close} onChange={e => h.setDay(d.key, { close: e.target.value })} />
                </span>)}
            </li>)
        })}
      </ul>
      <Field label="Note shown with your hours (optional)"><Input placeholder="Closed on public holidays" value={h.note} onChange={e => h.setNote(e.target.value)} /></Field>
      <Field label="Your own after-hours message (optional)" hint={'Replaces the default "we\'re closed, back at …".'}>
        <Input placeholder="We're offline for the weekend — WhatsApp us Monday from 8am!" value={h.afterHours} onChange={e => h.setAfterHours(e.target.value)} />
      </Field>
      <div className="vst-save vst-save--in">
        <Button onClick={h.save} disabled={h.busy}>{h.busy ? 'Saving…' : 'Save opening hours'}</Button>
        <Saved>{h.msg}</Saved>
      </div>
    </Card>
  )
}

// ── Delivery ──
function Delivery({ tenantId }) {
  const d = useDeliverySettings(tenantId)
  const rand = (label, value, set, placeholder) => (
    <Field label={label}>
      <span className="vst-rand"><span aria-hidden="true">R</span>
        <Input inputMode="decimal" value={value} onChange={e => set(e.target.value)} placeholder={placeholder} aria-label={label} /></span>
    </Field>)
  return (
    <div className="vst-stack">
      <Card className="vst-card">
        <h3 className="vst-h"><MapPin size={18} aria-hidden="true" />Shop location and delivery radius</h3>
        <div className="vst-row">
          <Input className="vst-grow" aria-label="Your shop's address or suburb" placeholder="Search your shop's address or suburb…" value={d.geoSearch}
            onChange={e => d.setGeoSearch(e.target.value)} onKeyDown={e => e.key === 'Enter' && d.findAddress()} />
          <Button variant="secondary" onClick={d.findAddress}>Find</Button>
          <Select aria-label="Delivery radius" value={d.radiusKm} onChange={e => d.setRadiusKm(Number(e.target.value))} style={{ width: 'auto' }}>
            {[5, 10, 15, 20, 25, 30, 40, 50].map(k => <option key={k} value={k}>{k} km radius</option>)}
          </Select>
        </div>
        <div ref={d.mapDivRef} className="vst-map" />
        <p className="vst-sub">
          {d.origin
            ? <>Pinned: <strong>{d.originLabel || 'your shop'}</strong> · {d.radiusKm} km radius. Customers who share a WhatsApp location pin get an instant in/out answer.</>
            : 'Search or tap the map to drop your shop pin — the circle then shows exactly where you deliver.'}
        </p>
      </Card>
      <Card className="vst-card">
        <h3 className="vst-h">Areas and fees</h3>
        <Field label="Delivery areas" hint="Separate with commas — e.g. Table View, Blouberg, Parklands.">
          <Input value={d.areas} onChange={e => d.setAreas(e.target.value)} placeholder="Table View, Blouberg, Parklands…" />
        </Field>
        <div className="vst-grid">
          {rand('Delivery fee', d.fee, d.setFee, '80')}
          {rand('Free delivery over', d.freeOver, d.setFreeOver, '500')}
          {rand('Minimum order', d.minOrder, d.setMinOrder, 'none')}
        </div>
        <div className="vst-save vst-save--in">
          <Button onClick={d.save} disabled={d.busy}>{d.busy ? 'Saving…' : 'Save delivery settings'}</Button>
          <Saved>{d.msg}</Saved>
        </div>
      </Card>
    </div>
  )
}

// ── What Vula has learned ──
function Learned({ tenantId }) {
  const l = useLearnedSummary(tenantId)
  const data = l.data
  if (!data) return <Card><Skeleton lines={4} /></Card>
  return (
    <div className="vst-stack">
      <Card className="vst-card">
        <h3 className="vst-h">Voice and tone</h3>
        <p className="vst-body">{data.voice.current ? <>Current: “{data.voice.current}”</> : 'No voice set yet — Vula uses its default tone.'}</p>
        {data.voice.suggested ? (
          <div className="vst-quote">
            <p className="vst-body">Suggested, from your own real messages: “{data.voice.suggested}”</p>
            <div className="vst-row">
              <Button disabled={l.busy} onClick={() => l.respondToVoiceSuggestion(true)}>Accept</Button>
              <Button variant="secondary" disabled={l.busy} onClick={() => l.respondToVoiceSuggestion(false)}>Dismiss</Button>
              <Saved>{l.msg}</Saved>
            </div>
          </div>
        ) : <p className="vst-sub">No suggestion pending — Vula quietly re-checks as you send more real messages.</p>}
      </Card>

      <Card className="vst-card">
        <h3 className="vst-h">Answers learned from your team</h3>
        <p className="vst-sub">
          {data.learned_answers.approved_count} approved and in use
          {data.learned_answers.pending_count > 0 ? `, ${data.learned_answers.pending_count} waiting for you to Keep or Bin (here or on WhatsApp).` : '.'}
        </p>
        {(data.learned_answers.pending || []).slice(0, 10).map(r => (
          <div key={r.id} className="vst-quote">
            <p className="vst-body">Q: “{r.question}”</p>
            <p className="vst-sub">A: {r.answer}</p>
            <div className="vst-row">
              <Button size="sm" disabled={l.busy} onClick={() => l.reviewLearned(r.id, 'approve')}>Keep — use this answer</Button>
              <Button size="sm" variant="secondary" disabled={l.busy} onClick={() => l.reviewLearned(r.id, 'reject')}>Bin</Button>
            </div>
          </div>
        ))}
        {data.learned_answers.recent_approved.length > 0 && (
          <ul className="vst-list">{data.learned_answers.recent_approved.slice(0, 5).map(r => <li key={r.id}>“{r.question}”</li>)}</ul>)}
      </Card>

      <Card className="vst-card">
        <h3 className="vst-h">Supplier categorisation</h3>
        <p className="vst-sub">
          {data.merchant_profiles.decided_count} of {data.merchant_profiles.total_count} suppliers
          categorised{data.merchant_profiles.total_count === 0 ? ' yet — this fills in as bank statements come through.' : '.'}
        </p>
      </Card>
    </div>
  )
}
