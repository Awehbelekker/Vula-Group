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

const LANG_NAMES = {
  en: 'English', af: 'Afrikaans', zu: 'isiZulu', xh: 'isiXhosa', st: 'Sesotho',
  nso: 'Sepedi', tn: 'Setswana', ts: 'Xitsonga', ve: 'Tshivenda', ss: 'siSwati', nr: 'isiNdebele',
}

export default function VulaCustomers({ tenantId }) {
  const [rows, setRows] = useState([])
  const [count, setCount] = useState(0)
  const [totalAll, setTotalAll] = useState(0)
  const [audience, setAudience] = useState('all')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [openPhone, setOpenPhone] = useState(null)
  const [detail, setDetail] = useState(null)
  const [noteDraft, setNoteDraft] = useState('')
  const [savingNote, setSavingNote] = useState(false)

  const openHistory = async (phone) => {
    if (openPhone === phone) { setOpenPhone(null); return }
    setOpenPhone(phone); setDetail(null); setNoteDraft('')
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/customers/${encodeURIComponent(phone)}/detail`)
    const d = await r.json()
    setDetail(d); setNoteDraft(d.note || '')
  }

  const saveNote = async (phone) => {
    setSavingNote(true)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/customers/${encodeURIComponent(phone)}/note`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note: noteDraft }),
    }).catch(() => {})
    setSavingNote(false)
  }

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
            <div key={i}>
              <div style={{ ...s.row, cursor: 'pointer' }} onClick={() => openHistory(c.phone)}>
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
                  <a href={waLink(c.phone)} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} style={s.waBtn}>💬</a>
                  <span style={{ color: '#8A8680', fontSize: 16, transform: openPhone === c.phone ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }}>›</span>
                </div>
              </div>
              {openPhone === c.phone && (
                <div style={{ background: 'var(--surface-alt, #F0EDE5)', borderRadius: 10, padding: 14, margin: '2px 0 10px' }}>
                  {!detail ? (
                    <div style={{ fontSize: 12.5, color: '#8A8680' }}>Loading…</div>
                  ) : (
                    <>
                      {/* Stat cards */}
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 8, marginBottom: 12 }}>
                        <Stat label="Lifetime value" value={fmt(detail.stats?.lifetime_value_cents)} strong />
                        <Stat label="Paid orders" value={detail.stats?.paid_order_count ?? 0} />
                        <Stat label="Avg order" value={fmt(detail.stats?.avg_order_cents)} />
                        {detail.stats?.invoice_outstanding_cents > 0 &&
                          <Stat label="Owes (invoices)" value={fmt(detail.stats.invoice_outstanding_cents)} />}
                      </div>
                      {/* Profile line */}
                      <div style={{ fontSize: 12, color: '#8A8680', marginBottom: 10, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                        {detail.profile?.email && <span>✉️ {detail.profile.email}</span>}
                        {detail.profile?.preferred_language &&
                          <span>🗣️ {LANG_NAMES[detail.profile.preferred_language] || detail.profile.preferred_language}</span>}
                        {detail.profile?.first_order_at && <span>🗓️ First order {String(detail.profile.first_order_at).slice(0, 10)}</span>}
                      </div>
                      {/* Notes */}
                      <div style={{ marginBottom: 12 }}>
                        <div style={s.secLabel}>Internal note</div>
                        <textarea
                          value={noteDraft}
                          onChange={e => setNoteDraft(e.target.value)}
                          onBlur={() => saveNote(c.phone)}
                          placeholder="Add a private note about this customer (preferences, allergies, delivery quirks)…"
                          style={{ width: '100%', minHeight: 48, boxSizing: 'border-box', padding: '8px 10px', border: '1px solid #DDD8CE', borderRadius: 6, fontFamily: 'system-ui', fontSize: 12.5, resize: 'vertical', background: '#fff' }}
                        />
                        {savingNote && <span style={{ fontSize: 11, color: '#8A8680' }}>saving…</span>}
                      </div>
                      {/* Timeline */}
                      <div style={s.secLabel}>Interaction history</div>
                      {(detail.events || []).length === 0 && <div style={{ fontSize: 12.5, color: '#8A8680' }}>No recorded orders, invoices or chats yet.</div>}
                      {(detail.events || []).map((e, j) => (
                        <div key={j} style={{ display: 'flex', gap: 10, padding: '6px 0', borderTop: j ? '1px solid #E5DFCF' : 'none', fontSize: 12.5 }}>
                          <span style={{ width: 78, color: '#8A8680', flexShrink: 0 }}>{(e.at || '').slice(0, 10)}</span>
                          <span style={{ flexShrink: 0 }}>{e.type === 'order' ? '📦' : e.type === 'message' ? '💬' : (e.type === 'quote' ? '📝' : '🧾')}</span>
                          <span style={{ flex: 1, color: '#2A2A2A' }}>
                            <b>{e.title}</b>{e.detail ? ` · ${e.detail}` : ''}
                          </span>
                          {e.amount_cents != null && <span style={{ fontFamily: "'Source Code Pro', monospace", color: '#2A2A2A' }}>{fmt(e.amount_cents)}</span>}
                        </div>
                      ))}
                    </>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, strong }) {
  return (
    <div style={{ background: '#fff', border: '1px solid #DDD8CE', borderRadius: 8, padding: '8px 10px' }}>
      <div style={{ fontSize: 10, textTransform: 'uppercase', color: '#8A8680', fontFamily: "'Source Code Pro', monospace", marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: strong ? 17 : 15, fontWeight: 700, color: strong ? 'var(--accent, #2C5545)' : '#1E1E1E' }}>{value}</div>
    </div>
  )
}

const s = {
  intro:     { marginBottom: 14 },
  secLabel:  { fontSize: 11, textTransform: 'uppercase', color: '#8A8680', fontFamily: "'Source Code Pro', monospace", marginBottom: 8 },
  h3:        { fontFamily: "'Cormorant Garamond', serif", fontSize: 20, fontWeight: 700, color: '#1E1E1E', margin: '0 0 4px' },
  sub:       { fontFamily: 'system-ui', fontSize: 13, color: '#8A8680', margin: 0 },
  segs:      { display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 },
  seg:       { padding: '6px 12px', borderRadius: 20, border: '1px solid #DDD8CE', background: '#fff', cursor: 'pointer', fontSize: 12, fontFamily: 'system-ui', color: '#8A8680' },
  segActive: { background: 'var(--accent, var(--accent))', color: '#fff', border: '1px solid var(--accent, var(--accent))' },
  search:    { marginLeft: 'auto', padding: '7px 11px', border: '1px solid #DDD8CE', borderRadius: 6, fontFamily: 'system-ui', fontSize: 13, minWidth: 180 },
  reach:     { fontFamily: 'system-ui', fontSize: 12, color: 'var(--accent, var(--accent))', background: 'rgba(44,85,69,0.07)', padding: '8px 12px', borderRadius: 6, margin: '0 0 12px' },
  muted:     { color: '#8A8680', fontSize: 13, fontFamily: 'system-ui', textAlign: 'center', padding: '24px 0' },
  list:      { display: 'flex', flexDirection: 'column', gap: 6 },
  row:       { display: 'flex', alignItems: 'center', gap: 12, background: '#fff', border: '1px solid #DDD8CE', borderRadius: 8, padding: '10px 14px' },
  avatar:    { width: 34, height: 34, borderRadius: '50%', background: 'var(--accent, var(--accent))', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'system-ui', fontWeight: 700, fontSize: 14, flexShrink: 0 },
  name:      { display: 'block', fontFamily: 'system-ui', fontSize: 14, fontWeight: 600, color: '#1E1E1E' },
  meta:      { display: 'block', fontFamily: 'system-ui', fontSize: 11, color: '#8A8680', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  right:     { display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 },
  spent:     { fontFamily: 'system-ui', fontSize: 14, fontWeight: 700, color: 'var(--accent, var(--accent))' },
  waBtn:     { textDecoration: 'none', fontSize: 16, padding: '4px 8px', borderRadius: 6, background: 'rgba(37,211,102,0.12)', border: '1px solid rgba(37,211,102,0.3)' },
}
