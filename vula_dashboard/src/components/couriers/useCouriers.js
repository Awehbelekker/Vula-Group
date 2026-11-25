/**
 * useCouriers.js — courier set-up and an order's courier booking (23 Nov 2026).
 * Backend: vula_mind/vula/api/couriers.py. Booking is always two steps: prices first (nothing is
 * booked), then the chosen price is sent back with the booking — the server re-checks it and
 * refuses when it moved, so nobody books at a figure they didn't see. Amounts are cents from the
 * server and are never worked out here.
 */
import { useCallback, useEffect, useState } from 'react'
import { VULA_API } from '../../lib/authFetch'

const base = (tenantId) => `${VULA_API}/v1/commerce/${tenantId}/admin`

/** fetch → the JSON, or throws an Error with the server's own words (and .price_cents when the price moved). */
async function call(url, opts = {}) {
  const r = await fetch(url, opts.body
    ? { ...opts, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(opts.body) }
    : opts)
  const d = await r.json().catch(() => ({}))
  if (r.ok) return d
  const detail = d?.detail
  const err = new Error((typeof detail === 'string' ? detail : detail?.message) || 'That didn’t work. Please try again.')
  if (detail && typeof detail === 'object') Object.assign(err, { price_cents: detail.price_cents, missing: detail.missing })
  throw err
}

export const EMPTY_ADDRESS = { company: '', street_address: '', local_area: '', city: '', zone: '', code: '', type: 'residential' }
export const PROVINCES = [['', 'Province'], ['EC', 'Eastern Cape'], ['FS', 'Free State'], ['GP', 'Gauteng'], ['KZN', 'KwaZulu-Natal'],
  ['LP', 'Limpopo'], ['MP', 'Mpumalanga'], ['NC', 'Northern Cape'], ['NW', 'North West'], ['WC', 'Western Cape']]

/** Addresses matching what was typed, each already split into the form's fields (asked on Find, not per key). */
export const findAddress = async (tenantId, query) =>
  (await call(`${base(tenantId)}/couriers/address-search`, { method: 'POST', body: { query } })).results || []

/** Addresses for what has been typed so far (the dropdown). Never throws: no suggestions is an empty list. */
export const suggestAddress = async (tenantId, query) => {
  try { return (await call(`${base(tenantId)}/couriers/address-suggest`, { method: 'POST', body: { query } })).results || [] } catch { return [] }
}
/** The address at a pin dropped on the map. */
export const addressAt = async (tenantId, lat, lng) =>
  (await call(`${base(tenantId)}/couriers/address-at`, { method: 'POST', body: { lat, lng } })).address

export function useCourierSetup(tenantId) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const load = useCallback(async () => {
    try { setData(await call(`${base(tenantId)}/couriers`)) } catch (e) { setError(e.message) }
  }, [tenantId])
  useEffect(() => { load() }, [load])

  const run = async (fn) => {
    setSaving(true); setError(''); setSaved(false)
    try { const d = await fn(); if (d?.connected) setData(d); else if (d?.settings) setData((old) => ({ ...old, settings: d.settings })); setSaved(true); return true }
    catch (e) { setError(e.message); return false }
    finally { setSaving(false) }
  }
  const connect = (provider, api_key, mode) => run(() => call(`${base(tenantId)}/couriers`, { method: 'POST', body: { provider, api_key, mode } }))
  const makeDefault = (provider) => run(() => call(`${base(tenantId)}/couriers/${provider}/default`, { method: 'POST' }))
  const remove = (provider) => run(() => call(`${base(tenantId)}/couriers/${provider}`, { method: 'DELETE' }))
  const saveSettings = (patch) => run(() => call(`${base(tenantId)}/couriers/settings`, { method: 'PUT', body: patch }))

  const connected = Object.fromEntries((data?.connected || []).map((c) => [c.provider, c]))
  return { data, connected, error, setError, saving, saved, connect, makeDefault, remove, saveSettings }
}

/** Bookings at one address: `url` lists them (GET), prices them (POST url/quote) and books (POST url). */
function useShipping(tenantId, url) {
  const [shipments, setShipments] = useState([])
  const [connected, setConnected] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [quote, setQuote] = useState(null)

  const load = useCallback(async () => {
    try {
      const d = await call(url)
      setShipments(d.shipments || []); setConnected(!!d.connected)
    } catch { /* the panel just stays hidden */ }
    setLoaded(true)
  }, [url])
  useEffect(() => { load() }, [load])

  const run = async (what, fn) => {
    setBusy(what); setError('')
    try { return await fn() } catch (e) { setError(e.message); return null } finally { setBusy('') }
  }

  /** Prices. `opts`: {provider, address, pickup_point, parcel}. Books nothing. */
  const getPrices = (opts = {}) => run('quote', async () => {
    const q = await call(`${url}/quote`, { method: 'POST', body: opts })
    setQuote(q)
    return q
  })

  /** Book `rate` exactly as priced. A price that moved comes back as an error and fresh prices. */
  const book = (rate, opts = {}) => run('book', async () => {
    try {
      const d = await call(url, {
        method: 'POST', body: { ...opts, service_code: rate.code, expected_cents: rate.cents } })
      setQuote(null)
      await load()
      return d.shipment
    } catch (e) {
      if (e.price_cents != null) {
        const q = await call(`${url}/quote`, { method: 'POST', body: opts }).catch(() => null)
        if (q) setQuote(q)
      }
      throw e
    }
  })

  const refresh = (id) => run(`refresh:${id}`, async () => { await call(`${base(tenantId)}/shipments/${id}/refresh`, { method: 'POST' }); await load() })
  const cancel = (id) => run(`cancel:${id}`, async () => { await call(`${base(tenantId)}/shipments/${id}/cancel`, { method: 'POST' }); await load(); return true })
  const openLabel = (id, sticker = false) => run(`label:${id}`, async () => {
    const d = await call(`${base(tenantId)}/shipments/${id}/label${sticker ? '?sticker=true' : ''}`)
    window.open(d.url, '_blank', 'noopener')
  })
  const findPoints = (near, provider) => run('points', async () => {
    const d = await call(`${base(tenantId)}/couriers/pickup-points?near=${encodeURIComponent(near)}${provider ? `&provider=${provider}` : ''}`)
    return d.points || []
  })

  return { shipments, connected, loaded, busy, error, setError, quote, setQuote, getPrices, book, refresh, cancel, openLabel, findPoints }
}

/** An order's courier booking. */
export const useOrderShipping = (tenantId, orderId) => useShipping(tenantId, `${base(tenantId)}/orders/${orderId}/shipments`)
/** Parcels sent without an order (Delivery › Couriers › Send a parcel). */
export const useParcels = (tenantId) => useShipping(tenantId, `${base(tenantId)}/couriers/parcels`)

/** What the business owes or has left with its courier, read from the courier now. Null when it can't be told. */
export const courierAccount = async (tenantId) => {
  try { return (await call(`${base(tenantId)}/couriers/account`)).account } catch { return null }
}
