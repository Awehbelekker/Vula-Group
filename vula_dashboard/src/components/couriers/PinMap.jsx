/**
 * PinMap.jsx — drop a pin where the parcel is collected or delivered (25 Nov 2026). Loaded only
 * when someone opens it. Click the map or drag the pin; the server names the spot
 * (vula_mind/vula/commerce/address_search.py `at`) and the form's fields are filled from it. The
 * position kept is the pin's own, which is what the courier's driver follows.
 */
import { useEffect, useRef, useState } from 'react'
import { addressAt } from './useCouriers'

const SOUTH_AFRICA = { lat: -29, lng: 24, zoom: 5 }

export default function PinMap({ tenantId, lat, lng, onPick }) {
  const div = useRef(null)
  const state = useRef(null)
  const [note, setNote] = useState('Click the map where the parcel is, or drag the pin.')
  const [bad, setBad] = useState(false)

  useEffect(() => {
    let dead = false
    const name = async (at) => {
      setBad(false); setNote('Finding the address there…')
      try {
        const found = await addressAt(tenantId, at.lat, at.lng)
        if (dead) return
        onPick(found)
        setNote(`Pin set: ${found.label || 'position saved'}. Check the street number below.`)
      } catch (e) { if (!dead) { setBad(true); setNote(e.message) } }
    }
    import('leaflet').then(async (Lmod) => {
      if (dead || !div.current || state.current) return
      await import('leaflet/dist/leaflet.css')
      const L = Lmod.default || Lmod
      const has = lat != null && lng != null
      const map = L.map(div.current, { scrollWheelZoom: false })
        .setView(has ? [lat, lng] : [SOUTH_AFRICA.lat, SOUTH_AFRICA.lng], has ? 16 : SOUTH_AFRICA.zoom)
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap contributors', maxZoom: 19 }).addTo(map)
      const place = (at) => {
        if (!state.current.pin) {
          // a plain circle, so no marker image has to be downloaded
          state.current.pin = L.circleMarker(at, { radius: 10, weight: 3, color: '#ffffff', fillColor: 'var(--brand)', fillOpacity: 1 }).addTo(map) // brand-colour: white ring so the pin reads on any tile
        } else state.current.pin.setLatLng(at)
      }
      state.current = { map, L, pin: null }
      if (has) place([lat, lng])
      map.on('click', (e) => { place(e.latlng); name(e.latlng) })
    }).catch(() => { setBad(true); setNote('The map couldn’t load. Type the address in instead.') })
    return () => { dead = true; if (state.current) { state.current.map.remove(); state.current = null } }
  }, [tenantId])  // eslint-disable-line react-hooks/exhaustive-deps

  // An address picked from the suggestions moves the map to it.
  useEffect(() => {
    const s = state.current
    if (!s || lat == null || lng == null) return
    s.map.setView([lat, lng], Math.max(s.map.getZoom(), 16))
    if (s.pin) s.pin.setLatLng([lat, lng])
    else s.pin = s.L.circleMarker([lat, lng], { radius: 10, weight: 3, color: '#ffffff', fillColor: 'var(--brand)', fillOpacity: 1 }).addTo(s.map) // brand-colour: white ring so the pin reads on any tile
  }, [lat, lng])

  return (
    <div className="vcr-map-wrap">
      <div ref={div} className="vcr-map" role="application" aria-label="Map: click to drop a pin" />
      <p className={bad ? 'vsl-error' : 'vcr-sub'} role="status">{note}</p>
    </div>
  )
}
