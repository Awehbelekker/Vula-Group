/**
 * VulaAutomations.jsx — lean trigger → action rules (P3 automations builder).
 * v1 scope: order reaches a status / product hits its reorder threshold → WhatsApp the
 * customer or the team helper. Evaluated by a backend poller every 5 minutes.
 */
import { confirmDialog } from './ui/index.jsx'
import { useState, useEffect, useCallback } from 'react'
import { VULA_API } from '../lib/authFetch'


const ORDER_STATUSES = ['paid', 'confirmed', 'packing', 'dispatched', 'delivered', 'cancelled']

const TRIGGERS = {
  order_status: { label: '📦 Order reaches a status', hint: 'Fires once per order when it reaches the chosen status.' },
  low_stock: { label: '🔔 Product stock is low', hint: 'Fires once per product per day while stock ≤ its reorder threshold (set in Products).' },
  abandoned_cart: { label: '🛒 Cart left without ordering', hint: 'Fires when a WhatsApp customer leaves items in their cart for 2+ hours (carts from the last 3 days only).' },
  reorder_due: { label: '🔁 Time to reorder', hint: "Fires once for a customer whose last order was delivered 7 days ago." },
}
const ACTIONS = {
  whatsapp_customer: { label: '💬 Message the customer', hint: 'Not available with the low-stock trigger.' },
  whatsapp_team: { label: '🧑 Message the team', hint: 'Sends to whoever is set to receive help requests (Team tab).' },
}
const PLACEHOLDERS = {
  order_status: '{{order_id}}, {{customer_name}}, {{status}}',
  low_stock: '{{product_name}}, {{stock}}, {{threshold}}',
  abandoned_cart: '{{customer_name}}, {{items}}',
  reorder_due: '{{customer_name}}, {{items}}',
}

export default function VulaAutomations({ tenantId }) {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [showNew, setShowNew] = useState(false)
  const [form, setForm] = useState({ name: '', trigger_type: 'order_status', to_status: 'dispatched', action_type: 'whatsapp_customer', message: '' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const [firings, setFirings] = useState([])
  const [teachText, setTeachText] = useState('')
  const [teaching, setTeaching] = useState(false)
  const [teachMsg, setTeachMsg] = useState('')
  const [showTeach, setShowTeach] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/automations`).then(r => r.json()).catch(() => ({}))
    setRows(r.automations || [])
    setLoading(false)
  }, [tenantId])
  useEffect(() => { load() }, [load])

  const loadFirings = useCallback(async () => {
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/automations/firings`).then(r => r.json()).catch(() => ({}))
    setFirings(r.firings || [])
  }, [tenantId])
  useEffect(() => { loadFirings() }, [loadFirings])

  async function decide(firing, action) {
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/automations/firings/${firing.id}/${action}`, { method: 'POST' })
    loadFirings()
  }

  async function teach() {
    setTeachMsg('')
    if (!teachText.trim()) return
    setTeaching(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/automations/from-text`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: teachText.trim() }),
    }).then(r => r.json()).catch(() => ({ error: 'network' }))
    setTeaching(false)
    if (r.error) { setTeachMsg(r.error); return }
    setTeachMsg('Rule created ✓'); setTeachText(''); setShowTeach(false); load()
  }

  async function create() {
    setError('')
    if (!form.message.trim()) return setError('Write the message to send.')
    if (form.action_type === 'whatsapp_customer' && form.trigger_type === 'low_stock') {
      return setError("Messaging the customer doesn't work with the low-stock trigger (there's no customer).")
    }
    setSaving(true)
    const body = {
      name: form.name.trim() || undefined,
      trigger_type: form.trigger_type,
      trigger_config: form.trigger_type === 'order_status' ? { to_status: form.to_status } : {},
      action_type: form.action_type,
      action_config: { message: form.message.trim() },
    }
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/automations`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then(r => r.json()).catch(() => ({ error: 'network' }))
    setSaving(false)
    if (r.error) return setError(r.error)
    setShowNew(false)
    setForm({ name: '', trigger_type: 'order_status', to_status: 'dispatched', action_type: 'whatsapp_customer', message: '' })
    load()
  }

  async function toggle(row) {
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/automations/${row.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: !row.enabled }),
    })
    load()
  }

  async function remove(row) {
    if (!(await confirmDialog(`Delete automation "${row.name}"?`, { danger: true, confirmLabel: 'Yes' }))) return
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/automations/${row.id}`, { method: 'DELETE' })
    load()
  }

  return (
    <div>
      <div style={s.intro}>
        <h3 style={s.h3}>⚡ Automations</h3>
        <p style={s.sub}>Standing rules — Vula checks every few minutes and, when one matches, stages the message below for your approval. Nothing sends until you say go.</p>
      </div>

      {firings.length > 0 && (
        <div style={{ ...s.card, marginBottom: 18, background: '#FFF9EC', borderColor: '#E8D9A8' }}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>⏳ Waiting for your approval ({firings.length})</div>
          <div style={s.list}>
            {firings.map(f => (
              <div key={f.id} style={{ ...s.row2, alignItems: 'flex-start' }}>
                <div style={{ flex: 1 }}>
                  <div style={s.name}>{f.message}</div>
                  <div style={s.meta}>{ACTIONS[f.action_type]?.label || f.action_type}</div>
                </div>
                <button onClick={() => decide(f, 'approve')} style={{ ...s.miniBtn, color: '#fff', background: 'var(--accent)', borderColor: 'var(--accent)' }}>Approve & send</button>
                <button onClick={() => decide(f, 'reject')} style={{ ...s.miniBtn, color: 'var(--danger)' }}>Reject</button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ ...s.card, marginBottom: 18 }}>
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>✏️ Teach Vula a rule</div>
        <p style={s.hint}>Describe it in plain language — e.g. "when an order is dispatched, message the customer to say it's on its way".</p>
        {showTeach ? (
          <>
            <textarea value={teachText} onChange={e => setTeachText(e.target.value)} rows={2}
              placeholder="When... then..." style={{ ...s.input, resize: 'vertical', marginTop: 8 }} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
              <button onClick={teach} disabled={teaching} style={s.saveBtn}>{teaching ? 'Teaching…' : 'Create rule'}</button>
              {teachMsg && <span style={{ fontSize: 12, color: teachMsg.includes('✓') ? 'var(--ok)' : 'var(--danger)' }}>{teachMsg}</span>}
            </div>
          </>
        ) : (
          <button onClick={() => setShowTeach(true)} style={{ ...s.newBtn, marginTop: 8, marginBottom: 0 }}>Teach a rule</button>
        )}
      </div>

      <button onClick={() => setShowNew(v => !v)} style={s.newBtn}>{showNew ? 'Close' : '+ New automation (form)'}</button>

      {showNew && (
        <div style={s.card}>
          <input placeholder="Name (optional)" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} style={s.input} />

          <p style={s.label}>When…</p>
          <div style={s.row}>
            <select value={form.trigger_type} onChange={e => setForm(f => ({ ...f, trigger_type: e.target.value }))} style={s.input}>
              {Object.entries(TRIGGERS).map(([k, t]) => <option key={k} value={k}>{t.label}</option>)}
            </select>
            {form.trigger_type === 'order_status' && (
              <select value={form.to_status} onChange={e => setForm(f => ({ ...f, to_status: e.target.value }))} style={s.input}>
                {ORDER_STATUSES.map(st => <option key={st} value={st}>{st}</option>)}
              </select>
            )}
          </div>
          <p style={s.hint}>{TRIGGERS[form.trigger_type].hint}</p>

          <p style={s.label}>Then…</p>
          <select value={form.action_type} onChange={e => setForm(f => ({ ...f, action_type: e.target.value }))} style={s.input}>
            {Object.entries(ACTIONS).map(([k, a]) => <option key={k} value={k}>{a.label}</option>)}
          </select>
          <p style={s.hint}>{ACTIONS[form.action_type].hint}</p>

          <textarea placeholder="Message to send…" value={form.message} onChange={e => setForm(f => ({ ...f, message: e.target.value }))}
            rows={3} style={{ ...s.input, resize: 'vertical' }} />
          <p style={s.hint}>Placeholders you can use: {PLACEHOLDERS[form.trigger_type]}</p>

          {error && <p style={s.error}>{error}</p>}
          <button onClick={create} disabled={saving} style={s.saveBtn}>{saving ? 'Saving…' : 'Create automation'}</button>
        </div>
      )}

      {loading ? <p style={s.muted}>Loading…</p> : rows.length === 0 ? (
        <p style={s.muted}>No automations yet.</p>
      ) : (
        <div style={s.list}>
          {rows.map(r => (
            <div key={r.id} style={s.row2}>
              <div style={{ flex: 1 }}>
                <span style={s.name}>{r.name}</span>
                <div style={s.meta}>
                  {TRIGGERS[r.trigger_type]?.label || r.trigger_type}
                  {r.trigger_config?.to_status ? ` → ${r.trigger_config.to_status}` : ''}
                  {' · '}{ACTIONS[r.action_type]?.label || r.action_type}
                  {r.fire_count > 0 && ` · matched ${r.fire_count}×`}
                  {r.created_from === 'conversation' && ' · 💬 taught'}
                </div>
              </div>
              <button onClick={() => toggle(r)} style={{ ...s.miniBtn, color: r.enabled ? 'var(--ok)' : 'var(--muted)' }}>
                {r.enabled ? 'On' : 'Off'}
              </button>
              <button onClick={() => remove(r)} style={{ ...s.miniBtn, color: 'var(--danger)' }}>Delete</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const s = {
  intro: { marginBottom: 14 },
  h3: { fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 700, color: 'var(--ink)', margin: '0 0 4px' },
  sub: { fontSize: 13, color: 'var(--muted)', margin: 0 },
  newBtn: { padding: '8px 16px', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer', marginBottom: 14 },
  card: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 16, marginBottom: 18, display: 'flex', flexDirection: 'column', gap: 8 },
  row: { display: 'flex', gap: 8 },
  input: { padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13, boxSizing: 'border-box', width: '100%' },
  label: { fontSize: 12, fontWeight: 700, color: 'var(--ink)', margin: '4px 0 0'},
  hint: { fontSize: 11.5, color: 'var(--muted)', margin: 0},
  error: { color: 'var(--danger)', fontSize: 13, margin: 0 },
  saveBtn: { padding: '9px 16px', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer', alignSelf: 'flex-start' },
  muted: { color: 'var(--muted)', fontSize: 13},
  list: { display: 'flex', flexDirection: 'column', gap: 8 },
  row2: { display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' },
  name: { fontSize: 14, fontWeight: 600, color: 'var(--ink)' },
  meta: { fontSize: 12, color: 'var(--muted)', marginTop: 2 },
  miniBtn: { padding: '5px 12px', border: '1px solid var(--border)', borderRadius: 6, background: 'var(--surface)', fontSize: 12, fontWeight: 600, cursor: 'pointer'},
}
