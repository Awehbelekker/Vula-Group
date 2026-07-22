/**
 * VulaMasterTenantDetail.jsx — lightweight per-tenant drill-in inside Master (IA overhaul,
 * 2026-07-22). Previously the ONLY way to check on one tenant was "Open as tenant" — a full
 * shell takeover that leaves Master entirely and loses your place. This is the lighter
 * alternative: usage/audit/escalations scoped to one tenant, read here without leaving Master,
 * with "Open full admin →" kept as the explicit escape hatch for anything that genuinely needs
 * the full tenant admin (order details, product edits, etc.).
 *
 * Needs almost no backend work — confirmed directly against vula_mind/vula/api/master.py:
 * /v1/master/audit already accepts ?tenant_id=, /v1/master/usage already returns a per_tenant
 * dict keyed by tenant id, and the tenant-scoped /v1/commerce/{tenant}/admin/escalations
 * endpoint (already used by VulaInbox.jsx) covers the escalations preview — all three are
 * client-side filtering/reuse here, not new endpoints.
 */
import { useEffect, useState } from 'react'
import { authFetch } from '../lib/authFetch'
import { SectionTabs } from './ui/index.jsx'
import { useSectionTabs } from '../hooks/useSectionTabs'
import { ManageTenantRow } from './VulaMasterPanel'

const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'
const C = { surface: '#FFFFFF', border: '#DDD8CE', green: 'var(--accent)', red: '#A23B2D', amber: '#B7791F', text: '#2A2A2A', muted: '#8A8680', alt: '#F0EDE5' }
const card = { background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: 12 }
const btn = { padding: '7px 13px', border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: 'pointer' }
const btnOn = { background: C.green, color: '#fff', borderColor: C.green }
const table = { width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }
const th = { padding: '8px 10px', fontWeight: 600, whiteSpace: 'nowrap' }
const td = { padding: '8px 10px', verticalAlign: 'top' }

const TABS = [
  { id: 'overview', icon: '🏢', label: 'Overview' },
  { id: 'usage', icon: '💰', label: 'Usage' },
  { id: 'audit', icon: '📜', label: 'Audit' },
  { id: 'escalations', icon: '❓', label: 'Escalations' },
]

export default function VulaMasterTenantDetail({ tenantId, onOpenTenant, onBack }) {
  const { tabs, active, setActive } = useSectionTabs(TABS, { defaultTabId: 'overview' })
  const [err, setErr] = useState('')

  return (
    <div style={{ fontFamily: 'system-ui', color: C.text, maxWidth: 1000, padding: '16px 24px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        {onBack && <button onClick={onBack} style={btn}>← Tenants</button>}
        <h3 style={{ margin: 0, fontSize: 18, fontFamily: 'monospace' }}>{tenantId}</h3>
        {onOpenTenant && (
          <button onClick={() => onOpenTenant(tenantId)} style={{ ...btn, ...btnOn, marginLeft: 'auto' }}>
            Open full admin →
          </button>
        )}
      </div>
      <SectionTabs tabs={tabs} active={active} onChange={setActive} />
      {err && <div style={{ fontSize: 13, color: C.red, marginBottom: 10 }}>{err}</div>}
      {active === 'overview' && <TenantOverviewTab tenantId={tenantId} onError={setErr} />}
      {active === 'usage' && <TenantUsageTab tenantId={tenantId} onError={setErr} />}
      {active === 'audit' && <TenantAuditTab tenantId={tenantId} onError={setErr} />}
      {active === 'escalations' && <TenantEscalationsTab tenantId={tenantId} onError={setErr} />}
    </div>
  )
}

function TenantOverviewTab({ tenantId, onError }) {
  const [tenant, setTenant] = useState(null)
  const [registry, setRegistry] = useState({ business_types: [], modules: [] })

  const load = async () => {
    try {
      const [t, r] = await Promise.all([
        authFetch('/v1/master/tenants'),
        authFetch('/v1/tenants/registry'),
      ])
      setTenant((t.tenants || []).find((x) => x.tenant_id === tenantId) || null)
      setRegistry(r)
    } catch (e) { onError(e.message) }
  }
  useEffect(() => { load() }, [tenantId])  // eslint-disable-line

  const save = async (patch) => {
    try {
      await authFetch(`/v1/master/tenants/${tenantId}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      load()
    } catch (e) { onError(e.message) }
  }

  if (!tenant) return <div style={{ color: C.muted, fontSize: 13 }}>Loading…</div>
  return (
    <div style={card}>
      <div style={{ display: 'flex', gap: 14, fontSize: 12.5, marginBottom: 10, flexWrap: 'wrap' }}>
        <span><b>{tenant.display_name || tenant.tenant_id}</b></span>
        <span style={{ color: C.muted }}>{tenant.business_type || '—'}</span>
        <span style={{ color: tenant.paid ? C.green : C.amber, fontWeight: 600 }}>{tenant.paid ? 'Paid' : (tenant.signup_status || '—')}</span>
        <span style={{ color: tenant.active === false ? C.red : C.green, fontWeight: 600 }}>{tenant.active === false ? 'Suspended' : 'Active'}</span>
        <span style={{ color: C.muted }}>{tenant.logins} logins</span>
      </div>
      <ManageTenantRow tenant={tenant} registry={registry} onSave={save} />
    </div>
  )
}

function TenantUsageTab({ tenantId, onError }) {
  const [u, setU] = useState(null)
  useEffect(() => { authFetch('/v1/master/usage?days=14').then(setU).catch((e) => onError(e.message)) }, [tenantId])  // eslint-disable-line
  if (!u) return <div style={{ color: C.muted, fontSize: 13 }}>Loading…</div>
  const t = (u.per_tenant || {})[tenantId]
  if (!t) return <div style={{ color: C.muted, fontSize: 13 }}>No usage recorded for this tenant in the last 14 days.</div>
  return (
    <div style={{ ...card, display: 'flex', gap: 24, flexWrap: 'wrap' }}>
      <Stat label="AI calls (14d)" value={t.calls} />
      <Stat label="AI cost" value={`$${(t.ai_cost_usd || 0).toFixed(2)}`} />
      <Stat label="Infra cost/day" value={`$${(t.infra_cost_usd || 0).toFixed(2)}`} />
      <Stat label="Vectors" value={t.vectors ?? '—'} />
      <Stat label="Storage" value={t.storage_mb != null ? `${Number(t.storage_mb).toFixed(0)} MB` : '—'} />
    </div>
  )
}
function Stat({ label, value }) {
  return (
    <div>
      <div style={{ fontSize: 20, fontFamily: 'monospace', fontWeight: 700 }}>{value}</div>
      <div style={{ fontSize: 11, color: C.muted }}>{label}</div>
    </div>
  )
}

function TenantAuditTab({ tenantId, onError }) {
  const [events, setEvents] = useState(null)
  useEffect(() => {
    authFetch(`/v1/master/audit?tenant_id=${tenantId}&limit=50`).then((d) => setEvents(d.events || [])).catch((e) => onError(e.message))
  }, [tenantId])  // eslint-disable-line
  if (!events) return <div style={{ color: C.muted, fontSize: 13 }}>Loading…</div>
  return (
    <div style={{ ...card, padding: 0, overflowX: 'auto' }}>
      <table style={table}>
        <thead><tr style={{ textAlign: 'left', color: C.muted, background: C.alt }}>
          {['When', 'Who', 'Action', 'Detail'].map((x) => <th key={x} style={th}>{x}</th>)}
        </tr></thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id} style={{ borderTop: `1px solid ${C.border}` }}>
              <td style={{ ...td, color: C.muted, whiteSpace: 'nowrap' }}>{(e.created_at || '').slice(0, 16).replace('T', ' ')}</td>
              <td style={td}>{e.actor_email || e.actor_id}</td>
              <td style={{ ...td, fontWeight: 600 }}>{e.action}</td>
              <td style={{ ...td, color: C.muted, fontSize: 11.5, maxWidth: 320, whiteSpace: 'normal' }}>{JSON.stringify(e.detail)}</td>
            </tr>
          ))}
          {!events.length && <tr><td style={td} colSpan={4}>No admin actions recorded for this tenant yet.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

// Read-only preview — reuses the same tenant-scoped endpoint VulaInbox.jsx uses for the full
// interactive thread view. Deliberately not answer/dismiss-able from here (kept lightweight);
// "Open full admin →" is where a real reply happens.
function TenantEscalationsTab({ tenantId, onError }) {
  const [rows, setRows] = useState(null)
  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/escalations?status=open&limit=20`)
      .then((r) => r.json()).then((d) => setRows(d.escalations || [])).catch((e) => onError(e.message))
  }, [tenantId])  // eslint-disable-line
  if (!rows) return <div style={{ color: C.muted, fontSize: 13 }}>Loading…</div>
  if (!rows.length) return <div style={{ color: C.muted, fontSize: 13 }}>No open escalations for this tenant. ✓</div>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {rows.map((e) => (
        <div key={e.id} style={card}>
          <div style={{ display: 'flex', gap: 10, fontSize: 12, color: C.muted, marginBottom: 4 }}>
            <span>{e.customer_phone}</span>
            <span style={{ marginLeft: 'auto' }}>{(e.created_at || '').slice(0, 16).replace('T', ' ')}</span>
          </div>
          <div style={{ fontSize: 13 }}>{e.question}</div>
        </div>
      ))}
    </div>
  )
}
