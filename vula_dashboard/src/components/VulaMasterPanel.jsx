/**
 * VulaMasterPanel.jsx — 🛠 Master: Vula's own operator panel across ALL tenants.
 * Sub-tabs: Tenants (provisioning/state) · Health (platform ops) · Usage (AI+infra cost) ·
 * Users (logins) · Audit (admin action trail).
 * Every call goes through authFetch → /v1/master/* — server-verified master JWT required
 * (vula/api/master.py + master_auth.py), the first real auth boundary in the platform.
 */
import { useEffect, useState, Fragment } from 'react'
import { authFetch } from '../lib/authFetch'
import { SectionTabs } from './ui/index.jsx'
import { useSectionTabs } from '../hooks/useSectionTabs'
import VulaMasterTenantDetail from './VulaMasterTenantDetail'

const C = { surface: '#FFFFFF', border: '#DDD8CE', green: 'var(--accent)', red: '#A23B2D', amber: '#B7791F', text: '#2A2A2A', muted: '#8A8680', alt: '#F0EDE5' }

const SUBTABS = [
  { id: 'tenants', label: 'Tenants', icon: '🏢' },
  { id: 'onboard', label: 'Onboard', icon: '🚀' },
  { id: 'health', label: 'Health', icon: '💓' },
  { id: 'usage', label: 'Usage', icon: '💰' },
  { id: 'users', label: 'Users', icon: '👤' },
  { id: 'audit', label: 'Audit', icon: '📜' },
]

// activeTab/onTabChange: optional external control (App.jsx lifts this in Phase 5 so returning
// from a tenant visit restores the sub-tab the operator was on) — uncontrolled by default.
export default function VulaMasterPanel({ onOpenTenant, activeTab, onTabChange }) {
  const { tabs, active: tab, setActive: setTab } = useSectionTabs(SUBTABS, {
    defaultTabId: 'health',   // Health is the real operator landing (P1.4)
    active: activeTab, onChange: onTabChange,
  })
  const [err, setErr] = useState('')
  const [prefill, setPrefill] = useState(null)   // signup → pre-filled "+ New tenant" form (P1.4)
  // Lightweight per-tenant drill-in (IA overhaul 2026-07-22) — replaces the whole panel body
  // while active, same convention VulaMerchantAdmin uses for its own showCreate/showSettings.
  const [detailTenant, setDetailTenant] = useState(null)

  if (detailTenant) {
    return <VulaMasterTenantDetail tenantId={detailTenant} onOpenTenant={onOpenTenant} onBack={() => setDetailTenant(null)} />
  }

  return (
    <div style={{ fontFamily: 'system-ui', color: C.text, maxWidth: 1000, padding: '16px 24px' }}>
      <SectionTabs tabs={tabs} active={tab} onChange={(id) => { setTab(id); setErr('') }} />
      {err && <div style={{ fontSize: 13, color: C.red, marginBottom: 10 }}>{err}</div>}
      {tab === 'tenants' && <TenantsPanel onError={setErr} onOpenTenant={onOpenTenant} onViewDetail={setDetailTenant} prefill={prefill} onConsumePrefill={() => setPrefill(null)} />}
      {tab === 'onboard' && <OnboardPanel onError={setErr} onOpenTenant={onOpenTenant} onProvision={(s) => { setPrefill(s); setTab('tenants') }} />}
      {tab === 'health' && <HealthPanel onError={setErr} onViewDetail={setDetailTenant} />}
      {tab === 'usage' && <UsagePanel onError={setErr} onViewDetail={setDetailTenant} />}
      {tab === 'users' && <UsersPanel onError={setErr} />}
      {tab === 'audit' && <AuditPanel onError={setErr} onViewDetail={setDetailTenant} />}
    </div>
  )
}

/* ── Onboarding cockpit — guided go-live checklist per tenant (UI overhaul P3) ── */
function OnboardPanel({ onError, onOpenTenant, onProvision }) {
  const [tenants, setTenants] = useState([])
  const [selected, setSelected] = useState('')
  const [setup, setSetup] = useState(null)
  const [signups, setSignups] = useState([])

  useEffect(() => {
    authFetch('/v1/master/tenants').then(d => {
      const list = d.tenants || []
      setTenants(list)
      if (list.length && !selected) setSelected(list[0].tenant_id)
    }).catch(e => onError(e.message))
    authFetch('/v1/admin/signups?limit=50').then(d => setSignups(d.signups || [])).catch(() => {})
  }, [])  // eslint-disable-line

  useEffect(() => {
    if (!selected) return
    setSetup(null)
    authFetch(`/v1/master/tenants/${selected}/setup`).then(setSetup).catch(e => onError(e.message))
  }, [selected])  // eslint-disable-line

  const knownIds = new Set(tenants.map(t => t.tenant_id))
  const unprovisioned = signups.filter(s => !s.tenant_id || !knownIds.has(s.tenant_id))

  return (
    <div>
      {unprovisioned.length > 0 && (
        <div style={{ ...card, marginBottom: 14, borderColor: C.amber }}>
          <h4 style={h4}>🆕 New signups — not yet provisioned ({unprovisioned.length})</h4>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {unprovisioned.map((s, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderTop: i > 0 ? `1px solid ${C.border}` : 'none', flexWrap: 'wrap' }}>
                <div style={{ flex: 1, minWidth: 180 }}>
                  <b style={{ fontSize: 13 }}>{s.company_name || s.contact_name || s.email}</b>
                  <div style={{ fontSize: 11.5, color: C.muted }}>{s.contact_name} · {s.email}{s.whatsapp ? ` · ${s.whatsapp}` : ''} · {s.industry || '—'} · {s.plan || 'starter'}</div>
                </div>
                <button style={{ ...btn, ...btnOn }} onClick={() => onProvision({
                  tenant_id: (s.company_name || s.contact_name || 'tenant').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, ''),
                  display_name: s.company_name || s.contact_name || '',
                  business_type: s.industry || 'retail', plan: s.plan || 'starter',
                })}>Provision →</button>
              </div>
            ))}
          </div>
        </div>
      )}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <select value={selected} onChange={e => setSelected(e.target.value)} style={input}>
          {tenants.map(t => <option key={t.tenant_id} value={t.tenant_id}>{t.display_name || t.tenant_id}</option>)}
        </select>
        {setup && (
          <span style={{ fontSize: 13, fontWeight: 600, color: setup.progress_pct === 100 ? C.green : C.amber }}>
            {setup.done} of {setup.total} · {setup.progress_pct}%
          </span>
        )}
        {onOpenTenant && selected && (
          <button style={{ ...btn, marginLeft: 'auto' }} onClick={() => onOpenTenant(selected)}>Open as tenant →</button>
        )}
      </div>
      {!setup ? <div style={{ color: C.muted, fontSize: 13 }}>Loading checklist…</div> : (
        <div style={card}>
          <div style={{ height: 6, borderRadius: 99, background: C.alt, overflow: 'hidden', marginBottom: 10 }}>
            <div style={{ width: `${setup.progress_pct}%`, height: '100%', background: C.green, borderRadius: 99 }} />
          </div>
          {setup.steps.map((s, i) => (
            <div key={s.id} style={{ display: 'flex', gap: 11, padding: '9px 0', borderBottom: i < setup.steps.length - 1 ? `1px solid ${C.border}` : 'none', alignItems: 'flex-start' }}>
              <span style={{
                width: 22, height: 22, borderRadius: 99, flex: 'none', display: 'flex', alignItems: 'center',
                justifyContent: 'center', fontSize: 11, fontWeight: 700,
                background: s.done ? C.green : C.surface, color: s.done ? '#fff' : C.muted,
                border: s.done ? `1.5px solid ${C.green}` : `1.5px solid ${C.border}`,
              }}>{s.done ? '✓' : i + 1}</span>
              <div style={{ flex: 1 }}>
                <b style={{ fontSize: 12.5, color: s.done ? C.muted : C.text }}>{s.label}</b>
                <div style={{ fontSize: 11.5, color: C.muted }}>{s.detail}</div>
              </div>
              {!s.done && onOpenTenant && (
                <button style={{ ...miniBtn, alignSelf: 'center', color: C.green, fontWeight: 600 }}
                  onClick={() => onOpenTenant(selected)}>Do this →</button>
              )}
            </div>
          ))}
          <p style={{ fontSize: 11.5, color: C.muted, margin: '10px 0 0' }}>
            Computed live from the tenant's real state — connect/configure things and this updates itself.
          </p>
        </div>
      )}
    </div>
  )
}

/* ── Tenants & provisioning ─────────────────────────────────────────────────── */
function TenantsPanel({ onError, onOpenTenant, onViewDetail, prefill, onConsumePrefill }) {
  const [rows, setRows] = useState([])
  const [registry, setRegistry] = useState({ business_types: [], modules: [] })
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ tenant_id: '', display_name: '', business_type: 'retail' })
  const [busy, setBusy] = useState(false)
  const [managing, setManaging] = useState(null)   // tenant_id being module/plan-edited

  const load = async () => {
    try {
      const [t, r] = await Promise.all([
        authFetch('/v1/master/tenants'),
        authFetch('/v1/tenants/registry'),
      ])
      setRows(t.tenants || []); setRegistry(r)
    } catch (e) { onError(e.message) }
  }
  useEffect(() => { load() }, [])

  useEffect(() => {
    if (!prefill) return
    setForm({ tenant_id: prefill.tenant_id || '', display_name: prefill.display_name || '', business_type: prefill.business_type || 'retail' })
    setCreating(true)
    onConsumePrefill && onConsumePrefill()
  }, [prefill])  // eslint-disable-line

  const create = async () => {
    if (!form.tenant_id.trim()) return onError('tenant_id required (slug, e.g. my-shop)')
    setBusy(true)
    try {
      const r = await authFetch('/v1/tenants', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      if (r.error) onError(r.error)
      else { setCreating(false); setForm({ tenant_id: '', display_name: '', business_type: 'retail' }); load() }
    } catch (e) { onError(e.message) } finally { setBusy(false) }
  }

  const toggleActive = async (t) => {
    try {
      await authFetch(`/v1/master/tenants/${t.tenant_id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active: t.active === false }),
      })
      load()
    } catch (e) { onError(e.message) }
  }

  const saveManage = async (tenant_id, patch) => {
    try {
      await authFetch(`/v1/master/tenants/${tenant_id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      load()
    } catch (e) { onError(e.message) }
  }

  const markPaid = async (t) => {
    try { await authFetch(`/v1/master/tenants/${t.tenant_id}/mark-paid`, { method: 'POST' }); load() }
    catch (e) { onError(e.message) }
  }
  const extendTrial = async (t) => {
    try {
      await authFetch(`/v1/master/tenants/${t.tenant_id}/extend-trial`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ days: 14 }),
      })
      load()
    } catch (e) { onError(e.message) }
  }
  const cancelSub = async (t) => {
    if (!confirm(`Cancel ${t.display_name || t.tenant_id}'s subscription? This also suspends them — bot, checkout, and dashboard logins stop working immediately.`)) return
    try { await authFetch(`/v1/master/tenants/${t.tenant_id}/cancel`, { method: 'POST' }); load() }
    catch (e) { onError(e.message) }
  }
  const reactivateSub = async (t) => {
    try { await authFetch(`/v1/master/tenants/${t.tenant_id}/reactivate`, { method: 'POST' }); load() }
    catch (e) { onError(e.message) }
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <h4 style={h4}>Tenants</h4>
        <button style={{ ...btn, ...btnOn, marginLeft: 'auto' }} onClick={() => setCreating(c => !c)}>{creating ? 'Close' : '+ New tenant'}</button>
      </div>
      {creating && (
        <div style={{ ...card, margin: '10px 0', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input placeholder="tenant-slug" value={form.tenant_id} onChange={e => setForm(f => ({ ...f, tenant_id: e.target.value }))} style={{ ...input, fontFamily: 'monospace' }} />
          <input placeholder="Display name" value={form.display_name} onChange={e => setForm(f => ({ ...f, display_name: e.target.value }))} style={{ ...input, flex: 1, minWidth: 160 }} />
          <select value={form.business_type} onChange={e => setForm(f => ({ ...f, business_type: e.target.value }))} style={input}>
            {(registry.business_types || []).map(b => <option key={b.id} value={b.id}>{b.label}</option>)}
          </select>
          <button style={{ ...btn, ...btnOn }} disabled={busy} onClick={create}>{busy ? 'Creating…' : 'Create'}</button>
        </div>
      )}
      <div style={{ ...card, padding: 0, overflowX: 'auto', marginTop: 10 }}>
        <table style={table}>
          <thead><tr style={{ textAlign: 'left', color: C.muted, background: C.alt }}>
            {['Tenant', 'Type', 'Modules', 'Paid', 'Trial ends', 'Logins', 'Active', ''].map(h => <th key={h} style={th}>{h}</th>)}
          </tr></thead>
          <tbody>
            {rows.map(t => (
              <Fragment key={t.tenant_id}>
                <tr style={{ borderTop: `1px solid ${C.border}` }}>
                  <td style={td}><b>{t.display_name || t.tenant_id}</b><div style={{ color: C.muted, fontFamily: 'monospace', fontSize: 11 }}>{t.tenant_id}</div></td>
                  <td style={td}>{t.business_type || '—'}</td>
                  <td style={{ ...td, color: C.muted, fontSize: 11.5 }}>{(t.modules || []).length} enabled · <span style={{ color: C.text, fontWeight: 600 }}>{t.plan || 'starter'}</span></td>
                  <td style={{ ...td, color: t.paid ? C.green : C.amber, fontWeight: 600 }}>{t.paid ? 'Paid' : (t.signup_status || '—')}</td>
                  <td style={{ ...td, color: C.muted }}>{(t.trial_ends || '').slice(0, 10) || '—'}</td>
                  <td style={td}>{t.logins}</td>
                  <td style={{ ...td, color: t.active === false ? C.red : C.green, fontWeight: 600 }}>{t.active === false ? 'Suspended' : 'Active'}</td>
                  <td style={{ ...td, whiteSpace: 'nowrap' }}>
                    {onViewDetail && <button style={{ ...miniBtn, marginRight: 6 }} onClick={() => onViewDetail(t.tenant_id)}>Details</button>}
                    {onOpenTenant && <button style={{ ...miniBtn, marginRight: 6, color: C.green, fontWeight: 600 }} onClick={() => onOpenTenant(t.tenant_id)}>Open →</button>}
                    <button style={{ ...miniBtn, marginRight: 6 }} onClick={() => setManaging(managing === t.tenant_id ? null : t.tenant_id)}>
                      {managing === t.tenant_id ? 'Close' : 'Manage'}
                    </button>
                    <button style={miniBtn} onClick={() => toggleActive(t)}>{t.active === false ? 'Activate' : 'Suspend'}</button>
                  </td>
                </tr>
                {managing === t.tenant_id && (
                  <tr>
                    <td colSpan={8} style={{ padding: 0, background: C.alt }}>
                      <ManageTenantRow tenant={t} registry={registry} onSave={(patch) => saveManage(t.tenant_id, patch)} />
                      <div style={{ padding: '0 14px 14px', display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', borderTop: `1px solid ${C.border}`, marginTop: 4, paddingTop: 12 }}>
                        <b style={{ fontSize: 12.5 }}>Billing</b>
                        <button style={miniBtn} onClick={() => markPaid(t)}>Mark paid</button>
                        <button style={miniBtn} onClick={() => extendTrial(t)}>+14 day trial</button>
                        {t.signup_status === 'cancelled'
                          ? <button style={{ ...miniBtn, color: C.green, fontWeight: 600 }} onClick={() => reactivateSub(t)}>Reactivate</button>
                          : <button style={{ ...miniBtn, color: C.red }} onClick={() => cancelSub(t)}>Cancel subscription</button>}
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/* ── Inline module + plan editor for one tenant (P1.4) ─────────────────────── */
// Exported (2026-07-22) so VulaMasterTenantDetail.jsx's Overview sub-tab can reuse it too,
// instead of duplicating the plan/store-url/gateway/modules editor a second time.
export function ManageTenantRow({ tenant, registry, onSave }) {
  const [modules, setModules] = useState(tenant.modules || [])
  const [plan, setPlan] = useState(tenant.plan || 'starter')
  const [storeUrl, setStoreUrl] = useState(tenant.store_url || '')
  const [gateway, setGateway] = useState(tenant.default_payment_provider || '')
  const allModules = registry.modules || []

  const toggle = (id) => setModules(m => m.includes(id) ? m.filter(x => x !== id) : [...m, id])

  return (
    <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <b style={{ fontSize: 12.5 }}>Plan</b>
          <select value={plan} onChange={e => setPlan(e.target.value)} style={input}>
            <option value="starter">Starter</option>
            <option value="growth">Growth</option>
            <option value="business">Business</option>
          </select>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <b style={{ fontSize: 12.5 }}>Store URL</b>
          <input value={storeUrl} onChange={e => setStoreUrl(e.target.value)} placeholder="https://…" style={{ ...input, flex: 1 }} />
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <b style={{ fontSize: 12.5 }}>Default gateway</b>
          <input value={gateway} onChange={e => setGateway(e.target.value)} placeholder="yoco / payfast / …" style={{ ...input, flex: 1 }} />
        </div>
      </div>
      <div>
        <b style={{ fontSize: 12.5, display: 'block', marginBottom: 6 }}>Modules</b>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {allModules.map(m => (
            <label key={m.id} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 6, padding: '4px 9px', cursor: 'pointer' }}>
              <input type="checkbox" checked={modules.includes(m.id)} onChange={() => toggle(m.id)} />
              {m.label}
            </label>
          ))}
        </div>
      </div>
      <button style={{ ...btn, ...btnOn, alignSelf: 'flex-start' }}
        onClick={() => onSave({ modules, plan, store_url: storeUrl, default_payment_provider: gateway })}>Save changes</button>
    </div>
  )
}

/* ── Platform health ───────────────────────────────────────────────────────── */
function HealthPanel({ onError, onViewDetail }) {
  const [h, setH] = useState(null)
  useEffect(() => { authFetch('/v1/master/health').then(setH).catch(e => onError(e.message)) }, [])
  if (!h) return <div style={{ color: C.muted, fontSize: 13 }}>Loading…</div>
  const router = h.llm_router_24h || {}
  const localPct = router.total ? Math.round((router.local / router.total) * 100) : null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={card}>
        <h4 style={h4}>📱 WhatsApp lines</h4>
        {(Array.isArray(h.whatsapp) ? h.whatsapp : []).map(w => (
          <div key={w.tenant_id} style={{ display: 'flex', gap: 10, fontSize: 12.5, padding: '4px 0', flexWrap: 'wrap' }}>
            {onViewDetail
              ? <button onClick={() => onViewDetail(w.tenant_id)} style={{ ...miniBtn, minWidth: 110, textAlign: 'left', fontWeight: 700 }}>{w.tenant_id}</button>
              : <b style={{ minWidth: 110 }}>{w.tenant_id}</b>}
            <span style={{ fontFamily: 'monospace' }}>{w.phone_number}</span>
            <span style={{ color: w.status === 'connected' ? C.green : C.red, fontWeight: 600 }}>{w.status}</span>
            <span style={{ color: C.muted }}>{w.webhook_registered ? 'webhook ✓' : 'webhook ✗'}</span>
            {w.last_error && <span style={{ color: C.red }}>{String(w.last_error).slice(0, 60)}</span>}
          </div>
        ))}
      </div>
      <div style={card}>
        <h4 style={h4}>⏰ Scheduler</h4>
        <div style={{ fontSize: 12.5 }}>
          Leader: <b style={{ fontFamily: 'monospace' }}>{h.scheduler?.leader || '—'}</b>
          <span style={{ color: h.scheduler?.lease_expired ? C.red : C.green, marginLeft: 10, fontWeight: 600 }}>
            {h.scheduler?.lease_expired ? 'LEASE EXPIRED' : 'lease healthy'}
          </span>
        </div>
        <table style={{ ...table, marginTop: 8 }}>
          <thead><tr style={{ textAlign: 'left', color: C.muted }}>{['Tenant', 'Job', 'On', 'Time', 'Template', 'Last fired'].map(x => <th key={x} style={{ ...th, padding: '4px 8px' }}>{x}</th>)}</tr></thead>
          <tbody>
            {(Array.isArray(h.scheduled_jobs) ? h.scheduled_jobs : []).map((j, i) => (
              <tr key={i} style={{ borderTop: `1px solid ${C.border}` }}>
                <td style={tdSm}>{j.tenant_id}</td><td style={tdSm}>{j.job_type}</td>
                <td style={{ ...tdSm, color: j.enabled ? C.green : C.red }}>{j.enabled ? '✓' : '✗'}</td>
                <td style={tdSm}>{j.hour != null ? `${String(j.hour).padStart(2, '0')}:${String(j.minute || 0).padStart(2, '0')}` : '—'}</td>
                <td style={{ ...tdSm, fontFamily: 'monospace', fontSize: 11 }}>{j.template_name || 'default'}</td>
                <td style={{ ...tdSm, color: C.muted }}>{(j.last_fired_at || '—').slice(0, 16).replace('T', ' ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ ...card, flex: 1, minWidth: 220 }}>
          <h4 style={h4}>🧠 LLM routing (24h)</h4>
          <div style={{ fontSize: 12.5 }}>
            {router.total || 0} requests · {localPct != null ? `${localPct}% local` : '—'} · {router.cloud || 0} cloud
            {Object.entries(router.escalation_reasons || {}).map(([r, n]) => (
              <div key={r} style={{ color: C.muted }}>↳ escalated {n}× — {r}</div>
            ))}
          </div>
        </div>
        <div style={{ ...card, flex: 1, minWidth: 220 }}>
          <h4 style={h4}>❓ Escalations</h4>
          <div style={{ fontSize: 12.5 }}>
            Open: <b style={{ color: (h.escalations?.open || 0) > 0 ? C.amber : C.green }}>{h.escalations?.open ?? '—'}</b>
            {h.escalations?.oldest && <div style={{ color: C.muted }}>oldest: {String(h.escalations.oldest).slice(0, 16).replace('T', ' ')}</div>}
          </div>
        </div>
        <div style={{ ...card, flex: 1, minWidth: 220 }}>
          <h4 style={h4}>⚠️ Webhook failures (24h)</h4>
          <div style={{ fontSize: 12.5 }}>
            <b style={{ color: (h.webhook_failures_24h?.count || 0) > 0 ? C.red : C.green }}>
              {h.webhook_failures_24h?.count ?? '—'}
            </b>
            {h.webhook_failures_24h?.note && <div style={{ color: C.muted }}>{h.webhook_failures_24h.note}</div>}
            {(h.webhook_failures_24h?.recent || []).slice(0, 4).map((f, i) => (
              <div key={i} style={{ color: C.muted, marginTop: 4, fontSize: 11.5 }}>
                {(f.created_at || '').slice(5, 16).replace('T', ' ')} · {f.tenant_id || '?'} · {f.msg_type} — {String(f.error || '').slice(0, 60)}
              </div>
            ))}
          </div>
        </div>
      </div>

      {(h.migration_state || []).some(m => !m.applied) && (
        <div style={{ ...card, borderColor: C.amber }}>
          <h4 style={h4}>🗄️ Migrations not yet applied</h4>
          {(h.migration_state || []).filter(m => !m.applied).map(m => (
            <div key={m.migration} style={{ fontSize: 12.5, padding: '3px 0' }}>
              <b>{m.migration}</b> — {m.note} <span style={{ color: C.muted }}>({m.table})</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/* ── Usage & billing ───────────────────────────────────────────────────────── */
function UsagePanel({ onError, onViewDetail }) {
  const [u, setU] = useState(null)
  useEffect(() => { authFetch('/v1/master/usage?days=14').then(setU).catch(e => onError(e.message)) }, [])
  if (!u) return <div style={{ color: C.muted, fontSize: 13 }}>Loading…</div>
  const tenants = Object.entries(u.per_tenant || {})
  return (
    <div style={{ ...card, padding: 0, overflowX: 'auto' }}>
      <table style={table}>
        <thead><tr style={{ textAlign: 'left', color: C.muted, background: C.alt }}>
          {['Tenant', 'AI calls (14d)', 'AI cost', 'Infra cost/day', 'Vectors', 'Storage'].map(x => <th key={x} style={th}>{x}</th>)}
        </tr></thead>
        <tbody>
          {tenants.map(([tid, t]) => (
            <tr key={tid} style={{ borderTop: `1px solid ${C.border}` }}>
              <td style={{ ...td, fontWeight: 600 }}>
                {onViewDetail ? <button onClick={() => onViewDetail(tid)} style={miniBtn}>{tid}</button> : tid}
              </td>
              <td style={td}>{t.calls}</td>
              <td style={td}>${(t.ai_cost_usd || 0).toFixed(2)}</td>
              <td style={td}>${(t.infra_cost_usd || 0).toFixed(2)}</td>
              <td style={td}>{t.vectors ?? '—'}</td>
              <td style={td}>{t.storage_mb != null ? `${Number(t.storage_mb).toFixed(0)} MB` : '—'}</td>
            </tr>
          ))}
          {!tenants.length && <tr><td style={td} colSpan={6}>No usage recorded in the last 14 days.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

/* ── Users ─────────────────────────────────────────────────────────────────── */
function UsersPanel({ onError }) {
  const [users, setUsers] = useState(null)
  const [tenants, setTenants] = useState([])
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ tenant_id: '', email: '', name: '', role: 'staff' })
  const [busy, setBusy] = useState(null)
  const [notice, setNotice] = useState('')

  const load = () => {
    authFetch('/v1/master/users').then(d => setUsers(d.users || [])).catch(e => onError(e.message))
  }
  useEffect(() => {
    load()
    authFetch('/v1/master/tenants').then(d => {
      const list = d.tenants || []
      setTenants(list)
      if (list.length) setForm(f => ({ ...f, tenant_id: f.tenant_id || list[0].tenant_id }))
    }).catch(() => {})
  }, [])  // eslint-disable-line

  const create = async () => {
    if (!form.tenant_id || !form.email.trim()) return onError('tenant + email required')
    setBusy('create')
    try {
      const r = await authFetch(`/v1/master/tenants/${form.tenant_id}/users`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: form.email.trim(), name: form.name.trim() || undefined, role: form.role }),
      })
      if (r.error) onError(r.error)
      else {
        setNotice(`Created ${r.email} — temp password: ${r.temp_password}`)
        setForm(f => ({ ...f, email: '', name: '' })); setCreating(false); load()
      }
    } catch (e) { onError(e.message) } finally { setBusy(null) }
  }

  const reset = async (r) => {
    setBusy(r.user_id)
    try {
      const d = await authFetch(`/v1/master/tenants/${r.tenant_id}/users/${r.user_id}/reset`, { method: 'POST' })
      if (d.error) onError(d.error)
      else setNotice(`New temp password for ${r.email || r.user_id}: ${d.temp_password}`)
    } catch (e) { onError(e.message) } finally { setBusy(null) }
  }

  const remove = async (r) => {
    setBusy(r.user_id)
    try {
      await authFetch(`/v1/master/tenants/${r.tenant_id}/users/${r.user_id}`, { method: 'DELETE' })
      load()
    } catch (e) { onError(e.message) } finally { setBusy(null) }
  }

  if (!users) return <div style={{ color: C.muted, fontSize: 13 }}>Loading…</div>
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <h4 style={h4}>Users</h4>
        <button style={{ ...btn, ...btnOn, marginLeft: 'auto' }} onClick={() => setCreating(c => !c)}>{creating ? 'Close' : '+ New login'}</button>
      </div>
      {notice && (
        <div style={{ ...card, marginBottom: 10, color: C.green, fontSize: 12.5, display: 'flex', gap: 10, alignItems: 'center' }}>
          {notice}
          <button style={{ ...miniBtn, marginLeft: 'auto' }} onClick={() => setNotice('')}>Dismiss</button>
        </div>
      )}
      {creating && (
        <div style={{ ...card, marginBottom: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <select value={form.tenant_id} onChange={e => setForm(f => ({ ...f, tenant_id: e.target.value }))} style={input}>
            {tenants.map(t => <option key={t.tenant_id} value={t.tenant_id}>{t.display_name || t.tenant_id}</option>)}
          </select>
          <input placeholder="email" value={form.email} onChange={e => setForm(f => ({ ...f, email: e.target.value }))} style={{ ...input, flex: 1, minWidth: 160 }} />
          <input placeholder="name (optional)" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} style={{ ...input, minWidth: 140 }} />
          <select value={form.role} onChange={e => setForm(f => ({ ...f, role: e.target.value }))} style={input}>
            <option value="owner">Owner</option>
            <option value="manager">Manager</option>
            <option value="staff">Staff</option>
          </select>
          <button style={{ ...btn, ...btnOn }} disabled={busy === 'create'} onClick={create}>{busy === 'create' ? 'Creating…' : 'Create'}</button>
        </div>
      )}
      <div style={{ ...card, padding: 0, overflowX: 'auto' }}>
        <table style={table}>
          <thead><tr style={{ textAlign: 'left', color: C.muted, background: C.alt }}>
            {['Email', 'Tenant', 'Role', 'Created', ''].map(x => <th key={x} style={th}>{x}</th>)}
          </tr></thead>
          <tbody>
            {users.map((r, i) => (
              <tr key={i} style={{ borderTop: `1px solid ${C.border}` }}>
                <td style={td}>{r.email || r.user_id}</td>
                <td style={{ ...td, fontFamily: 'monospace', fontSize: 11.5 }}>{r.tenant_id}</td>
                <td style={{ ...td, fontWeight: 600, color: r.role === 'master' ? C.green : C.text }}>{r.role}</td>
                <td style={{ ...td, color: C.muted }}>{(r.created_at || '').slice(0, 10)}</td>
                <td style={{ ...td, whiteSpace: 'nowrap' }}>
                  <button style={{ ...miniBtn, marginRight: 6 }} disabled={busy === r.user_id} onClick={() => reset(r)}>Reset pwd</button>
                  <button style={miniBtn} disabled={busy === r.user_id} onClick={() => remove(r)}>Remove</button>
                </td>
              </tr>
            ))}
            {!users.length && <tr><td style={td} colSpan={5}>No login accounts yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/* ── Audit ─────────────────────────────────────────────────────────────────── */
function AuditPanel({ onError, onViewDetail }) {
  const [events, setEvents] = useState(null)
  useEffect(() => { authFetch('/v1/master/audit?limit=100').then(d => setEvents(d.events || [])).catch(e => onError(e.message)) }, [])
  if (!events) return <div style={{ color: C.muted, fontSize: 13 }}>Loading…</div>
  return (
    <div style={{ ...card, padding: 0, overflowX: 'auto' }}>
      <table style={table}>
        <thead><tr style={{ textAlign: 'left', color: C.muted, background: C.alt }}>
          {['When', 'Who', 'Action', 'Tenant', 'Detail'].map(x => <th key={x} style={th}>{x}</th>)}
        </tr></thead>
        <tbody>
          {events.map(e => (
            <tr key={e.id} style={{ borderTop: `1px solid ${C.border}` }}>
              <td style={{ ...td, color: C.muted, whiteSpace: 'nowrap' }}>{(e.created_at || '').slice(0, 16).replace('T', ' ')}</td>
              <td style={td}>{e.actor_email || e.actor_id}</td>
              <td style={{ ...td, fontWeight: 600 }}>{e.action}</td>
              <td style={{ ...td, fontFamily: 'monospace', fontSize: 11.5 }}>
                {e.tenant_id
                  ? (onViewDetail ? <button onClick={() => onViewDetail(e.tenant_id)} style={miniBtn}>{e.tenant_id}</button> : e.tenant_id)
                  : '—'}
              </td>
              <td style={{ ...td, color: C.muted, fontSize: 11.5, maxWidth: 320, whiteSpace: 'normal' }}>{JSON.stringify(e.detail)}</td>
            </tr>
          ))}
          {/* Real gap: no admin actions logged yet, distinct from migration 072 not being applied
              at all — that specific case already has its own card in HealthPanel above. */}
          {!events.length && <tr><td style={td} colSpan={5}>No admin actions recorded yet — actions you take here (suspend, edit modules, create users) will show up in this trail.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

const card = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 12 }
const btn = { padding: '7px 13px', border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: 'pointer' }
const btnOn = { background: C.green, color: '#fff', borderColor: C.green }
const miniBtn = { padding: '3px 9px', border: `1px solid ${C.border}`, borderRadius: 5, background: C.surface, fontSize: 11.5, cursor: 'pointer' }
const input = { padding: '7px 10px', border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, background: C.surface, color: C.text, fontFamily: 'system-ui' }
const table = { width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }
const th = { padding: '8px 10px', fontWeight: 600, whiteSpace: 'nowrap' }
const td = { padding: '8px 10px', verticalAlign: 'top' }
const tdSm = { padding: '4px 8px', verticalAlign: 'top', fontSize: 12 }
const h4 = { fontSize: 14, fontWeight: 600, margin: '2px 0 8px' }
