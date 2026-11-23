/**
 * CourierSetup.jsx — Delivery › Couriers (23 Nov 2026): connect the business's own courier
 * account (The Courier Guy, PUDO lockers), say where parcels are collected from and how big the
 * usual parcel is. The key is checked with the courier before it's saved and never shown back.
 * Changing any of it is for the owner or a manager (the server refuses anyone else).
 */
import { useEffect, useState } from 'react'
import { Copy, KeyRound, Star, Truck, Unplug } from 'lucide-react'
import { Button, Card, Field, Input, Select, Sheet, StatusChip, confirmDialog, toast } from '../ui/index.jsx'
import { EMPTY_ADDRESS, PROVINCES, useCourierSetup } from './useCouriers'
import './couriers.css'

const PARCEL = { length_cm: '', width_cm: '', height_cm: '', weight_kg: '' }

export function AddressFields({ value, onChange, company = true }) {
  const set = (k) => (e) => onChange({ ...value, [k]: e.target.value })
  return (
    <div className="vcr-form">
      {company && <Field label="Business or building name"><Input value={value.company || ''} onChange={set('company')} /></Field>}
      <Field label="Street address"><Input value={value.street_address || ''} onChange={set('street_address')} placeholder="12 Main Road" /></Field>
      <div className="vcr-two">
        <Field label="Suburb"><Input value={value.local_area || ''} onChange={set('local_area')} /></Field>
        <Field label="Town or city"><Input value={value.city || ''} onChange={set('city')} /></Field>
      </div>
      <div className="vcr-two">
        <Field label="Province"><Select value={value.zone || ''} onChange={set('zone')}>
          {PROVINCES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</Select></Field>
        <Field label="Postal code"><Input inputMode="numeric" maxLength={4} value={value.code || ''} onChange={set('code')} /></Field>
      </div>
    </div>
  )
}

export function ParcelFields({ value, onChange }) {
  const set = (k) => (e) => onChange({ ...value, [k]: e.target.value })
  return (
    <div className="vcr-four">
      <Field label="Length (cm)"><Input inputMode="decimal" value={value.length_cm ?? ''} onChange={set('length_cm')} /></Field>
      <Field label="Width (cm)"><Input inputMode="decimal" value={value.width_cm ?? ''} onChange={set('width_cm')} /></Field>
      <Field label="Height (cm)"><Input inputMode="decimal" value={value.height_cm ?? ''} onChange={set('height_cm')} /></Field>
      <Field label="Weight (kg)"><Input inputMode="decimal" value={value.weight_kg ?? ''} onChange={set('weight_kg')} /></Field>
    </div>
  )
}

export default function CourierSetup({ tenantId }) {
  const c = useCourierSetup(tenantId)
  const [openId, setOpenId] = useState(null)
  const [key, setKey] = useState('')
  const [mode, setMode] = useState('live')
  const [address, setAddress] = useState(EMPTY_ADDRESS)
  const [contact, setContact] = useState({ name: '', mobile_number: '', email: '' })
  const [parcel, setParcel] = useState(PARCEL)

  const settings = c.data?.settings
  useEffect(() => {
    if (!settings) return
    setAddress({ ...EMPTY_ADDRESS, type: 'business', ...(settings.collection_address || {}) })
    setContact({ name: '', mobile_number: '', email: '', ...(settings.collection_contact || {}) })
    setParcel({ ...PARCEL, ...(settings.default_parcel || {}) })
  }, [settings])

  if (!c.data) return null
  const courier = (c.data.available || []).find((a) => a.id === openId)
  const anyConnected = (c.data.connected || []).length > 0

  const open = (id) => { setOpenId(id); setKey(''); setMode(c.connected[id]?.mode || 'live'); c.setError('') }
  const connect = async () => { if (await c.connect(openId, key.trim(), mode)) { setOpenId(null); toast('Courier connected.') } }
  const remove = async (a) => {
    if (await confirmDialog(`Disconnect ${a.label}? Parcels already booked stay booked with them.`, { danger: true, confirmLabel: 'Disconnect' })) c.remove(a.id)
  }
  const copy = (text) => navigator.clipboard?.writeText(text).then(() => toast('Copied.'))
  const saveDetails = () => c.saveSettings({ collection_address: { ...address, type: 'business' }, collection_contact: contact, default_parcel: parcel })

  return (
    <Card className="vsl-card vcr">
      <div className="vsl-top">
        <h2 className="vsl-slot-h">Couriers</h2>
        {c.saved && <span className="vsl-msg" role="status">Saved</span>}
      </div>
      <p className="vsl-sub">Connect your own courier account, then book a courier from any order. The courier bills you directly.</p>
      {c.error && !openId && <p className="vsl-error" role="alert">{c.error}</p>}

      <ul className="vcr-list" aria-label="Couriers">
        {(c.data.available || []).map((a) => {
          const on = c.connected[a.id]
          return (
            <li key={a.id} className="vcr-row">
              <span className="vcr-ic" aria-hidden="true"><Truck size={18} strokeWidth={1.75} /></span>
              <span className="vcr-name">{a.label}</span>
              {on && <StatusChip tone={on.is_default ? 'accent' : 'ok'}>{on.is_default ? 'Default' : 'Connected'}</StatusChip>}
              {on && on.mode === 'test' && <StatusChip tone="warn">Test</StatusChip>}
              <span className="vcr-row-actions">
                <Button size="sm" variant={on ? 'secondary' : 'primary'} Icon={KeyRound} onClick={() => open(a.id)}>{on ? 'Change key' : 'Connect'}</Button>
                {on && !on.is_default && <Button size="sm" variant="secondary" Icon={Star} onClick={() => c.makeDefault(a.id)}>Make default</Button>}
                {on && <Button size="sm" variant="quiet" Icon={Unplug} onClick={() => remove(a)}>Disconnect</Button>}
              </span>
              {on?.webhook_url && (
                <p className="vcr-hook">
                  For live tracking, add this address in The Courier Guy's portal under Settings › Webhook subscriptions (Tracking event). Without it Vula checks every hour.
                  <span className="vcr-hook-url"><code>{on.webhook_url}</code>
                    <Button size="sm" variant="quiet" Icon={Copy} onClick={() => copy(on.webhook_url)}>Copy</Button></span>
                </p>)}
            </li>)
        })}
      </ul>

      {anyConnected && (
        <div className="vsl-divide vcr-form">
          <h3 className="vcr-h">Where parcels are collected</h3>
          <AddressFields value={address} onChange={setAddress} />
          <div className="vcr-two">
            <Field label="Who hands over the parcel"><Input value={contact.name} onChange={(e) => setContact({ ...contact, name: e.target.value })} /></Field>
            <Field label="Their cellphone"><Input type="tel" value={contact.mobile_number} onChange={(e) => setContact({ ...contact, mobile_number: e.target.value })} placeholder="082…" /></Field>
          </div>
          <h3 className="vcr-h">Your usual parcel</h3>
          <p className="vsl-sub">Used when an order's own size isn't known. You can change it on each booking.</p>
          <ParcelFields value={parcel} onChange={setParcel} />
          <div><Button onClick={saveDetails} disabled={c.saving}>{c.saving ? 'Saving…' : 'Save'}</Button></div>
        </div>)}

      <Sheet open={!!courier} title={courier ? `${c.connected[courier.id] ? 'Change key for' : 'Connect'} ${courier.label}` : ''} onClose={() => setOpenId(null)}
        footer={courier && <>
          <Button variant="secondary" onClick={() => setOpenId(null)}>Cancel</Button>
          <Button onClick={connect} disabled={c.saving || !key.trim()}>{c.saving ? 'Checking the key…' : 'Connect'}</Button>
        </>}>
        {courier && (
          <div className="vcr-form">
            <p className="vcr-where"><KeyRound size={16} aria-hidden="true" /><span><strong>Where to find it:</strong> {courier.help}</span></p>
            <Field label="API key"><Input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} /></Field>
            {courier.modes.length > 1 && (
              <Field label="Mode"><Select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="live">Live</option><option value="test">Test (sandbox account)</option></Select></Field>)}
            {c.error && <p className="vsl-error" role="alert">{c.error}</p>}
          </div>)}
      </Sheet>
    </Card>
  )
}
