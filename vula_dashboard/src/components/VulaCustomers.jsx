/**
 * VulaCustomers.jsx — client list / lightweight CRM for a tenant.
 *
 * Aggregates customers from orders + WhatsApp conversation contacts (auto-captured
 * by the AI assistant — no form). Search, segment by broadcast audience, and see
 * exactly who a campaign would reach. WhatsApp any customer in one tap.
 */

import { useState, useEffect, useCallback } from 'react'

const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'

const AUDIENCES = [
  { id: 'all',         label: 'All' },
  { id: 'active_30d',  label: 'Active (30d)' },
  { id: 'high_value',  label: 'High value (>R500)' },
]

export default function VulaCustomers({ tenantId }) {
  const [rows, setRows] = useState([])
  const [count, setCount] = useState(0)
  const [totalAll, setTotalAll] = useState(0)
  const [audience, setAudience] = useState('all')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    const q = new URLSearchParams({ audience })
    if (search) q.set('search', search)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/customers?${q}`)
    const d = await r.json()
    setRows(d.customers || [])
    setCount(d.count || 0)
    setTotalAll(d.total_all || 0)
    setLoading(false)
  }, [tenantId, audience, search])

  useEffect(() => {
    const t = setTimeout(load, search ? 300 : 0)  // debounce search
    return () => clearTimeout(t)
  }, [load, search])

  const fmt = c => `R${((c || 0) / 100).toFixed(2)}`
  const waLink = phone => {
    const n = (phone || '').replace(/[^\d]/g, '').replace(/^0/, '27')
    return `https://wa.me/${n}`
  }
  const since = ts => {
    if (!ts) return '—'
    const d = new Date(ts)
    const days = Math.floor((Date.now() - d) / 86400000)
    return days === 0 ? 'today' : days === 1 ? 'yesterday' : `${days}d ago`
  }

  return (
    <div>
      <div style={s.intro}>
        <h3 style={s.h3}>👥 Customers</h3>
        <p style={s.sub}>
          Everyone who has ordered or messaged you — captured automatically. {totalAll} total contact{totalAll !== 1 ? 's' : ''}.
        </p>
      </div>

      {/* Audience segments — mirror the broadcast filters */}
      <div style={s.segs}>
        {AUDIENCES.map(a => (
          <button
            key={a.id}
            onClick={() => setAudience(a.id)}
            style={{ ...s.seg, ...(audience === a.id ? s.segActive : {}) }}
          >
            {a.label}
          </button>
        ))}
        <input
          placeholder="Search name or phone…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={s.search}
        />
      </div>

      <p style={s.reach}>
        {loading ? 'Loading…' : `${count} customer${count !== 1 ? 's' : ''} in this segment — this is who a broadcast to "${AUDIENCES.find(a => a.id === audience)?.label}" reaches.`}
      </p>

      {!loading && rows.length === 0 ? (
        <p style={s.muted}>No customers in this segment yet.</p>
      ) : (
        <div style={s.list}>
          {rows.map((c, i) => (
            <div key={i} style={s.row}>
              <div style={s.avatar}>{(c.name || c.phone || '?').charAt(0).toUpperCase()}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <span style={s.name}>{c.name || 'Unknown'}</span>
                <span style={s.meta}>
                  {c.phone} · {c.channel === 'whatsapp' ? '💬 WhatsApp' : '🌐 Web'}
                  {c.orders > 0 ? ` · ${c.orders} order${c.orders !== 1 ? 's' : ''}` : ' · no orders yet'}
                  {' · seen '}{since(c.last_order_at || c.last_seen_at)}
                </span>
              </div>
              <div style={s.right}>
                <span style={s.spent}>{fmt(c.total_spent_cents)}</span>
                <a href={waLink(c.phone)} target="_blank" rel="noreferrer" style={s.waBtn}>💬</a>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const s = {
  intro:     { marginBottom: 14 },
  h3:        { fontFamily: "'Cormorant Garamond', serif", fontSize: 20, fontWeight: 700, color: '#1E1E1E', margin: '0 0 4px' },
  sub:       { fontFamily: 'system-ui', fontSize: 13, color: '#8A8680', margin: 0 },
  segs:      { display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 },
  seg:       { padding: '6px 12px', borderRadius: 20, border: '1px solid #DDD8CE', background: '#fff', cursor: 'pointer', fontSize: 12, fontFamily: 'system-ui', color: '#8A8680' },
  segActive: { background: 'var(--accent, #2C5545)', color: '#fff', border: '1px solid var(--accent, #2C5545)' },
  search:    { marginLeft: 'auto', padding: '7px 11px', border: '1px solid #DDD8CE', borderRadius: 6, fontFamily: 'system-ui', fontSize: 13, minWidth: 180 },
  reach:     { fontFamily: 'system-ui', fontSize: 12, color: 'var(--accent, #2C5545)', background: 'rgba(44,85,69,0.07)', padding: '8px 12px', borderRadius: 6, margin: '0 0 12px' },
  muted:     { color: '#8A8680', fontSize: 13, fontFamily: 'system-ui', textAlign: 'center', padding: '24px 0' },
  list:      { display: 'flex', flexDirection: 'column', gap: 6 },
  row:       { display: 'flex', alignItems: 'center', gap: 12, background: '#fff', border: '1px solid #DDD8CE', borderRadius: 8, padding: '10px 14px' },
  avatar:    { width: 34, height: 34, borderRadius: '50%', background: 'var(--accent, #2C5545)', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'system-ui', fontWeight: 700, fontSize: 14, flexShrink: 0 },
  name:      { display: 'block', fontFamily: 'system-ui', fontSize: 14, fontWeight: 600, color: '#1E1E1E' },
  meta:      { display: 'block', fontFamily: 'system-ui', fontSize: 11, color: '#8A8680', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  right:     { display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 },
  spent:     { fontFamily: 'system-ui', fontSize: 14, fontWeight: 700, color: 'var(--accent, #2C5545)' },
  waBtn:     { textDecoration: 'none', fontSize: 16, padding: '4px 8px', borderRadius: 6, background: 'rgba(37,211,102,0.12)', border: '1px solid rgba(37,211,102,0.3)' },
}
