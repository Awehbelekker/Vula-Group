/**
 * useSettings.js — the Settings screen's data and actions (2026-10-19, UX redesign Phase F).
 * Shared by VulaSettings.jsx and settings/SettingsV2.jsx; moved out unchanged — same endpoints,
 * same bodies (money in integer cents), same messages. One hook per section.
 */
import { useEffect, useRef, useState } from 'react'
import { applyBrand, DEFAULT_BRAND } from '../../theme/tokens'
import { uploadBrandImage } from '../../lib/brandUpload'
import { VULA_API } from '../../lib/authFetch'

const API = VULA_API
const hex6 = (v, d) => (/^#[0-9a-fA-F]{6}$/.test(v || '') ? v : d)

/** Brand kit: name, logo, colours and style — applied live, saved with "Save brand kit". */
export function useBrandKit(tenantId) {
  const [name, setName] = useState('')
  const [tagline, setTagline] = useState('')
  const [logoUrl, setLogoUrl] = useState('')
  const [iconUrl, setIconUrl] = useState('')
  const [accent, setAccent] = useState(DEFAULT_BRAND.accent)
  const [secondary, setSecondary] = useState('')
  const [ink, setInk] = useState(DEFAULT_BRAND.ink)
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

  useEffect(() => {
    fetch(`${API}/v1/commerce/${tenantId}/admin/invoice-settings`)
      .then(r => r.json())
      .then(d => {
        const st = d.settings || {}
        setName(st.trading_as || st.company_name || '')
        setTagline(st.tagline || '')
        setLogoUrl(st.logo_url || '')
        setIconUrl(st.icon_url || '')
        setAccent(hex6(st.accent_color, DEFAULT_BRAND.accent))
        setSecondary(hex6(st.secondary_color, ''))
        setInk(hex6(st.ink_color, DEFAULT_BRAND.ink))
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

  return {
    name, setName, tagline, setTagline, logoUrl, setLogoUrl, iconUrl, setIconUrl, accent, setAccent, secondary, setSecondary,
    ink, setInk, fontPairing, setFontPairing, corners, setCorners, density, setDensity, logoAlign, setLogoAlign, logoSize, setLogoSize,
    headerSticky, setHeaderSticky, headerNavPosition, setHeaderNavPosition, headerCtaText, setHeaderCtaText, headerCtaLink, setHeaderCtaLink,
    uploading, saving, msg, loaded, website, setWebsite, suggesting, suggestion, upload, suggest, applySuggestion, save,
  }
}

/** Delivery areas, fees (rand as typed → integer cents on save) and the shop pin + radius. */
export function useDeliverySettings(tenantId) {
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
  const mapRef = useRef(null)     // { map, L, marker, circle }
  const mapDivRef = useRef(null)

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

  return {
    areas, setAreas, fee, setFee, freeOver, setFreeOver, minOrder, setMinOrder, origin, originLabel, radiusKm, setRadiusKm,
    geoSearch, setGeoSearch, msg, busy, mapDivRef, findAddress, save,
  }
}

export const DAYS = [
  { key: 'mon', label: 'Mon' }, { key: 'tue', label: 'Tue' }, { key: 'wed', label: 'Wed' },
  { key: 'thu', label: 'Thu' }, { key: 'fri', label: 'Fri' }, { key: 'sat', label: 'Sat' },
  { key: 'sun', label: 'Sun' },
]

/** Opening hours, one row per day — what the assistant answers "are you open?" from. */
export function useBusinessHours(tenantId) {
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

  return { days, setDay, note, setNote, afterHours, setAfterHours, msg, busy, save }
}

/** What Vula has learned: voice suggestion (accept/dismiss), learned answers (keep/bin), suppliers. */
export function useLearnedSummary(tenantId) {
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

  async function reviewLearned(id, action) {
    setBusy(true)
    try {
      await fetch(`${API}/v1/commerce/${tenantId}/admin/learned-answers/${id}/${action}`, { method: 'POST' })
      load()
    } finally { setBusy(false) }
  }

  return { data, busy, msg, respondToVoiceSuggestion, reviewLearned }
}

/** The business profile: the owner's own answers, and which of them customers may be told. */
export function useBusinessProfile(tenantId) {
  const [data, setData] = useState(null)
  const [draft, setDraft] = useState({})
  // 2026-10-17: which answers the customer assistant may use; the rest stay with the team.
  const [publicKeys, setPublicKeys] = useState([])
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    if (!tenantId) return
    fetch(`${API}/v1/commerce/${tenantId}/admin/business-profile`)
      .then(r => r.json()).then(d => { setData(d); setDraft(d.answers || {}); setPublicKeys(d.public_keys || []) })
      .catch(() => setMsg('Couldn’t load the profile.'))
  }, [tenantId])
  const save = async () => {
    setSaving(true); setMsg('')
    try {
      const r = await fetch(`${API}/v1/commerce/${tenantId}/admin/business-profile`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answers: draft, public_keys: publicKeys }),
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'Save failed')
      setData(d); setPublicKeys(d.public_keys || publicKeys)
      setMsg(d.warning || `Saved — ${d.answered} of ${d.total} answered.`)
    } catch (e) { setMsg(e.message) } finally { setSaving(false) }
  }
  const togglePublic = (key, on) => setPublicKeys(on ? [...publicKeys, key] : publicKeys.filter(k => k !== key))
  return { data, draft, setDraft, publicKeys, togglePublic, msg, saving, save }
}
