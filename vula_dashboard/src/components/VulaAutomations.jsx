/**
 * VulaAutomations.jsx — lean trigger → action rules (P3 automations builder).
 * v1 scope: order reaches a status / product hits its reorder threshold → WhatsApp the
 * customer or the team helper. Evaluated by a backend poller every 5 minutes.
 */
import { ORDER_STATUSES, TRIGGERS, ACTIONS, PLACEHOLDERS, useAutomations } from './assistant/useAutomations'
import AutomationsV2 from './assistant/AutomationsV2'

export default function VulaAutomations({ v2 = false, ...props }) {
  return v2 ? <AutomationsV2 {...props} /> : <VulaAutomationsV1 {...props} />
}

function VulaAutomationsV1({ tenantId }) {
  const { rows, loading, showNew, setShowNew, form, setForm, saving, error, firings, teachText, setTeachText, teaching, teachMsg, showTeach, setShowTeach, decide, teach, create, toggle, remove } = useAutomations(tenantId)

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
