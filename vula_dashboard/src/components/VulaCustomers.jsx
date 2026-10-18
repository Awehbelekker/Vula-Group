/**
 * VulaCustomers.jsx — client list / lightweight CRM for a tenant.
 *
 * Aggregates customers from orders + WhatsApp conversation contacts (auto-captured
 * by the AI assistant — no form). Search, segment by broadcast audience, and see
 * exactly who a campaign would reach. WhatsApp any customer in one tap.
 * Data and actions live in customers/useCustomers.js (shared with customers/CustomersV2.jsx).
 */

import { whatsAppLink } from '../lib/phone'
import { FiledLibrary } from './VulaDocuments'
import { AUDIENCES, LANG_NAMES, seenAgo, useCustomers } from './customers/useCustomers'
import CustomersV2 from './customers/CustomersV2'


/** v2: the redesigned Customers (same hook, so the same data and actions). */
export default function VulaCustomers({ tenantId, v2 = false }) {
  if (v2) return <CustomersV2 tenantId={tenantId} />
  return <CustomersV1 tenantId={tenantId} />
}

function CustomersV1({ tenantId }) {
  const {
    rows, count, totalAll, audience, setAudience, search, setSearch, loading,
    openPhone, detail, openHistory, printStatement, noteDraft, setNoteDraft, savingNote, saveNote,
    tagsDraft, setTagsDraft, areaDraft, setAreaDraft, savingTags, saveTags, exportCsv,
  } = useCustomers(tenantId)

  const fmt = c => `R${((c || 0) / 100).toFixed(2)}`
  const waLink = phone => whatsAppLink(phone)
  const since = seenAgo

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
        <button
          onClick={exportCsv}
          disabled={!rows.length}
          style={{ ...s.seg, marginLeft: 'auto' }}
        >⬇ Export CSV</button>
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
              <div style={{ ...s.row, cursor: 'pointer' }} onClick={() => openHistory(c)}>
                <div style={s.avatar}>{(c.name || c.phone || '?').charAt(0).toUpperCase()}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <span style={s.name}>{c.name || 'Unknown'}</span>
                  <span style={s.meta}>
                    {c.phone} · {c.channel === 'whatsapp' ? '💬 WhatsApp' : '🌐 Web'}
                    {c.orders > 0 ? ` · ${c.orders} order${c.orders !== 1 ? 's' : ''}` : ' · no orders yet'}
                    {' · seen '}{since(c.last_order_at || c.last_seen_at)}
                    {c.area ? ` · 📍 ${c.area}` : ''}
                    {(c.tags || []).length > 0 ? ` · 🏷 ${c.tags.join(', ')}` : ''}
                  </span>
                </div>
                <div style={s.right}>
                  <span style={s.spent}>{fmt(c.total_spent_cents)}</span>
                  <a href={waLink(c.phone)} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} style={s.waBtn}>💬</a>
                  <span style={{ color: 'var(--muted)', fontSize: 16, transform: openPhone === c.phone ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }}>›</span>
                </div>
              </div>
              {openPhone === c.phone && (
                <div style={{ background: 'var(--surface-alt)', borderRadius: 10, padding: 14, margin: '2px 0 10px' }}>
                  {!detail ? (
                    <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>Loading…</div>
                  ) : (
                    <>
                      {/* Stat cards */}
                      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 12 }}>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 8, flex: 1 }}>
                          <Stat label="Lifetime value" value={fmt(detail.stats?.lifetime_value_cents)} strong />
                          <Stat label="Paid orders" value={detail.stats?.paid_order_count ?? 0} />
                          <Stat label="Avg order" value={fmt(detail.stats?.avg_order_cents)} />
                          {detail.stats?.invoice_outstanding_cents > 0 &&
                            <Stat label="Owes (invoices)" value={fmt(detail.stats.invoice_outstanding_cents)} />}
                        </div>
                        <button onClick={() => printStatement(c.phone, c.name)} style={s.statementBtn}>📄 Statement</button>
                      </div>
                      {/* Profile line */}
                      <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
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
                          style={{ width: '100%', minHeight: 48, boxSizing: 'border-box', padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12.5, resize: 'vertical', background: 'var(--surface)' }}
                        />
                        {savingNote && <span style={{ fontSize: 11, color: 'var(--muted)' }}>saving…</span>}
                      </div>
                      {/* Tags + area — the data segments actually filter on (see Broadcast → New segment) */}
                      <div style={{ marginBottom: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        <div style={{ flex: 1, minWidth: 160 }}>
                          <div style={s.secLabel}>Tags</div>
                          <input
                            value={tagsDraft}
                            onChange={e => setTagsDraft(e.target.value)}
                            onBlur={() => saveTags(c.phone)}
                            placeholder="vip, wholesale, allergy-nuts…"
                            style={{ width: '100%', boxSizing: 'border-box', padding: '7px 10px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12.5, background: 'var(--surface)' }}
                          />
                        </div>
                        <div style={{ width: 150 }}>
                          <div style={s.secLabel}>Area</div>
                          <input
                            value={areaDraft}
                            onChange={e => setAreaDraft(e.target.value)}
                            onBlur={() => saveTags(c.phone)}
                            placeholder="e.g. Tableview"
                            style={{ width: '100%', boxSizing: 'border-box', padding: '7px 10px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12.5, background: 'var(--surface)' }}
                          />
                        </div>
                        {savingTags && <span style={{ fontSize: 11, color: 'var(--muted)', alignSelf: 'flex-end' }}>saving…</span>}
                      </div>
                      {/* Conversation — the actual WhatsApp exchange (Customer-360 depth) */}
                      {(detail.conversation || []).length > 0 && (
                        <div style={{ marginBottom: 12 }}>
                          <div style={s.secLabel}>Conversation</div>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                            {detail.conversation.map((m, j) => (
                              <div key={j} style={{
                                alignSelf: m.role === 'user' ? 'flex-start' : 'flex-end',
                                background: m.role === 'user' ? '#fff' : 'var(--accent-soft, rgba(44,85,69,.10))',
                                border: '1px solid var(--border)', borderRadius: 10, padding: '6px 10px',
                                fontSize: 12, maxWidth: '85%',
                              }}>
                                {m.text}
                                <span style={{ display: 'block', fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>{String(m.at || '').slice(5, 16).replace('T', ' ')}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {/* Broadcast engagement */}
                      {(detail.engagement || []).length > 0 && (
                        <div style={{ marginBottom: 12 }}>
                          <div style={s.secLabel}>Broadcast engagement</div>
                          {detail.engagement.map((g, j) => (
                            <div key={j} style={{ display: 'flex', gap: 8, fontSize: 12.5, padding: '4px 0', alignItems: 'center' }}>
                              <span>📢 {g.campaign}</span>
                              <span style={{
                                marginLeft: 'auto', fontSize: 11, fontWeight: 600,
                                color: g.status === 'clicked' ? 'var(--accent)' : (g.status === 'failed' ? 'var(--danger)' : 'var(--muted)'),
                              }}>{g.status}</span>
                            </div>
                          ))}
                        </div>
                      )}
                      {/* Timeline */}
                      <div style={s.secLabel}>Interaction history</div>
                      {(detail.events || []).length === 0 && <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>No recorded orders, invoices or chats yet.</div>}
                      {(detail.events || []).map((e, j) => (
                        <div key={j} style={{ display: 'flex', gap: 10, padding: '6px 0', borderTop: j ? '1px solid var(--border)' : 'none', fontSize: 12.5 }}>
                          <span style={{ width: 78, color: 'var(--muted)', flexShrink: 0 }}>{(e.at || '').slice(0, 10)}</span>
                          <span style={{ flexShrink: 0 }}>{e.type === 'order' ? '📦' : e.type === 'message' ? '💬' : (e.type === 'quote' ? '📝' : '🧾')}</span>
                          <span style={{ flex: 1, color: 'var(--text)' }}>
                            <b>{e.title}</b>{e.detail ? ` · ${e.detail}` : ''}
                          </span>
                          {e.amount_cents != null && <span style={{ fontFamily: "'Source Code Pro', monospace", color: 'var(--text)' }}>{fmt(e.amount_cents)}</span>}
                        </div>
                      ))}
                      {/* Documents & media filed against this customer (migration 109) — same
                          modern grid+lightbox the main Documents tab uses, scoped to just them. */}
                      <div style={{ marginTop: 14 }}>
                        <FiledLibrary tenantId={tenantId} customerPhone={c.phone} title="📎 Documents & media" />
                      </div>
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
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px' }}>
      <div style={{ fontSize: 10, textTransform: 'uppercase', color: 'var(--muted)', fontFamily: "'Source Code Pro', monospace", marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: strong ? 17 : 15, fontWeight: 700, color: strong ? 'var(--accent)' : 'var(--ink)' }}>{value}</div>
    </div>
  )
}

const s = {
  intro:     { marginBottom: 14 },
  secLabel:  { fontSize: 11, textTransform: 'uppercase', color: 'var(--muted)', fontFamily: "'Source Code Pro', monospace", marginBottom: 8 },
  statementBtn: { padding: '7px 12px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12, fontWeight: 600, color: 'var(--text)', cursor: 'pointer', flexShrink: 0, whiteSpace: 'nowrap' },
  h3:        { fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 700, color: 'var(--ink)', margin: '0 0 4px' },
  sub:       { fontSize: 13, color: 'var(--muted)', margin: 0 },
  segs:      { display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 },
  seg:       { padding: '6px 12px', borderRadius: 20, border: '1px solid var(--border)', background: 'var(--surface)', cursor: 'pointer', fontSize: 12, color: 'var(--muted)' },
  segActive: { background: 'var(--accent, var(--accent))', color: '#fff', border: '1px solid var(--accent, var(--accent))' },
  search:    { marginLeft: 'auto', padding: '7px 11px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13, minWidth: 180 },
  reach:     { fontSize: 12, color: 'var(--accent, var(--accent))', background: 'rgba(44,85,69,0.07)', padding: '8px 12px', borderRadius: 6, margin: '0 0 12px' },
  muted:     { color: 'var(--muted)', fontSize: 13, textAlign: 'center', padding: '24px 0' },
  list:      { display: 'flex', flexDirection: 'column', gap: 6 },
  row:       { display: 'flex', alignItems: 'center', gap: 12, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px' },
  avatar:    { width: 34, height: 34, borderRadius: '50%', background: 'var(--accent, var(--accent))', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: 14, flexShrink: 0 },
  name:      { display: 'block', fontSize: 14, fontWeight: 600, color: 'var(--ink)' },
  meta:      { display: 'block', fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  right:     { display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 },
  spent:     { fontSize: 14, fontWeight: 700, color: 'var(--accent, var(--accent))' },
  waBtn:     { textDecoration: 'none', fontSize: 16, padding: '4px 8px', borderRadius: 6, background: 'rgba(37,211,102,0.12)', border: '1px solid rgba(37,211,102,0.3)' },
}
