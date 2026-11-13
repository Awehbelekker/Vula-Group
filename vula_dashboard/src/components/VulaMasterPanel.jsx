/**
 * VulaMasterPanel.jsx — 🛠 Master: Vula's own operator panel across ALL tenants.
 * Sub-tabs: Tenants (provisioning/state) · Health (platform ops) · Usage (AI+infra cost) ·
 * Users (logins) · Audit (admin action trail).
 * Every call goes through authFetch → /v1/master/* — server-verified master JWT required
 * (vula/api/master.py + master_auth.py), the first real auth boundary in the platform.
 */
import { useEffect, useState, useCallback } from 'react'
import { Brain, Building2, Coins, FlaskConical, HeartPulse, MessagesSquare, Rocket, ScrollText, Search, Target, UserRound } from 'lucide-react'
import { SectionTabs } from './ui/index.jsx'
import { useSectionTabs } from '../hooks/useSectionTabs'
import TenantsV2 from './master/TenantsV2'
import { HealthV2, UsageV2, UsersV2 } from './master/OpsV2'
import { AuditV2, BenchmarkV2, ConversationsCheckV2, KnowledgeV2, ModelsV2, OnboardV2 } from './master/ToolsV2'
import TenantDetailV2 from './master/TenantDetailV2'
import ProspectsV2 from './master/ProspectsV2'
import { authFetch } from '../lib/authFetch'
import { useManageTenant } from './master/useMasterTenants'

const C = { surface: 'var(--surface)', border: 'var(--border)', green: 'var(--accent)', red: 'var(--danger)', amber: 'var(--warn)', text: 'var(--text)', muted: 'var(--muted)', alt: 'var(--surface-alt)' }

const SUBTABS = [
  { id: 'tenants', label: 'Tenants', Icon: Building2 },
  { id: 'prospects', label: 'Prospects', Icon: Search },
  { id: 'onboard', label: 'Onboard', Icon: Rocket },
  { id: 'health', label: 'Health', Icon: HeartPulse },
  { id: 'usage', label: 'Usage', Icon: Coins },
  { id: 'users', label: 'Users', Icon: UserRound },
  { id: 'knowledge', label: 'Knowledge', Icon: Brain },
  { id: 'audit', label: 'Audit', Icon: ScrollText },
  { id: 'models', label: 'Models', Icon: FlaskConical },
  { id: 'benchmark', label: 'Benchmark', Icon: Target },
  { id: 'conversations', label: 'Conversations', Icon: MessagesSquare },
]

// #/master/tenant/{id} — bookmarkable/shareable deep link into the tenant drill-in (2026-09-16).
// App.jsx seeds activeTab from this same pattern on first load so the Master shell is even
// mounted; this just reads back which tenant once it is.
function readDetailTenantFromHash() {
  const m = window.location.hash.match(/^#\/master\/tenant\/([^/?]+)/)
  return m ? decodeURIComponent(m[1]) : null
}

// activeTab/onTabChange: optional external control (App.jsx lifts this in Phase 5 so returning
// from a tenant visit restores the sub-tab the operator was on) — uncontrolled by default.
export default function VulaMasterPanel({ onOpenTenant, activeTab, onTabChange }) {
  const { tabs, active: tab, setActive: setTab } = useSectionTabs(SUBTABS, {
    defaultTabId: 'tenants',   // Tenants is the real operator landing — who's signed up, paid,
                                // trial, active/suspended. Health (routing %, scheduler leases,
                                // VRL pass-rates) is engineering telemetry, better reached
                                // deliberately than dumped as the first thing you see (2026-09-16).
    active: activeTab, onChange: onTabChange,
  })
  const [err, setErr] = useState('')
  const [notice, setNotice] = useState('')   // e.g. "Created. Reading their website…"
  const [prefill, setPrefill] = useState(null)   // signup → pre-filled "+ New tenant" form (P1.4)
  const [fromProspect, setFromProspect] = useState(null)   // Prospects › Make them a tenant: link it once created
  // Go-live readiness pass (Phase 3.4): create_tenant only sets core config — WhatsApp
  // connection, KB seeding, and branding remain separate manual steps a master admin has to
  // remember to do afterward. The existing Onboard tab's 9-step checklist already covers all
  // of that; this just gets a master admin straight there for the tenant they JUST created,
  // instead of leaving them on the Tenants tab with nothing pointing at what's still missing.
  const [onboardSelect, setOnboardSelect] = useState(null)
  const [loginPrefill, setLoginPrefill] = useState(null)   // Onboard › Team & logins → New login
  // Lightweight per-tenant drill-in (IA overhaul 2026-07-22) — replaces the whole panel body
  // while active. URL-addressable (2026-09-16): #/master/tenant/{id} so a support-ticket link
  // can point straight at a tenant's detail view, and the browser back button steps out of it
  // instead of doing nothing (neither worked when this was local-only state).
  const [detailTenant, setDetailTenant] = useState(() => readDetailTenantFromHash())

  useEffect(() => {
    const onHash = () => setDetailTenant(readDetailTenantFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const openDetail = useCallback((tenantId) => {
    window.location.hash = `#/master/tenant/${tenantId}`
    setDetailTenant(tenantId)
  }, [])
  const closeDetail = useCallback(() => {
    // Assign rather than history.back(): a direct #/master/tenant/x link may have no local
    // "list" entry to go back to (fresh tab, shared link) — this is always predictable instead.
    if (window.location.hash.startsWith('#/master/tenant/')) window.location.hash = '#/master'
    else setDetailTenant(null)
  }, [])

  const Tenants = TenantsV2   // the redesign's list (master/TenantsV2.jsx)
  const Onboard = OnboardV2

  if (detailTenant) {
    const Detail = TenantDetailV2
    return <Detail tenantId={detailTenant} onOpenTenant={onOpenTenant} onBack={closeDetail} />
  }

  return (
    // no 1000px cap (2026-10-18): the shell already bounds the width; tables use the room
    <div className="vula-master" style={{ color: C.text, minWidth: 0 }}>
      <SectionTabs tabs={tabs} active={tab} onChange={(id) => { setTab(id); setErr(''); setNotice('') }} variant={'pill'} label="Master" />
      {err && <div style={{ fontSize: 13, color: C.red, marginBottom: 10 }}>{err}</div>}
      {notice && <div role="status" style={{ fontSize: 13, color: 'var(--ok-text)', marginBottom: 10 }}>{notice}</div>}
      {tab === 'tenants' && <Tenants onError={setErr} onOpenTenant={onOpenTenant} onViewDetail={openDetail} prefill={prefill} onConsumePrefill={() => setPrefill(null)}
        onCreated={(tenantId, note) => {
          if (fromProspect) {
            authFetch(`/v1/master/prospects/${fromProspect}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tenant_id: tenantId }) }).catch(() => {})
            setFromProspect(null)
          }
          setOnboardSelect(tenantId); setNotice(note || ''); setTab('onboard')
        }} />}
      {tab === 'prospects' && <ProspectsV2 onError={setErr} onMakeTenant={(p) => { setPrefill(p); setFromProspect(p.prospect_id); setTab('tenants') }} />}
      {tab === 'onboard' && <Onboard onError={setErr} onOpenTenant={onOpenTenant} onProvision={(s) => { setPrefill(s); setTab('tenants') }}
        selectTenantId={onboardSelect} onConsumeSelect={() => setOnboardSelect(null)} onViewDetail={openDetail}
        onAddLogin={(tenantId) => { setLoginPrefill({ tenant_id: tenantId, role: 'owner' }); setTab('users') }} />}
      {tab === 'health' && (<HealthV2 onError={setErr} onViewDetail={openDetail} />)}
      {tab === 'usage' && (<UsageV2 onError={setErr} onViewDetail={openDetail} />)}
      {tab === 'users' && (<UsersV2 onError={setErr} prefill={loginPrefill} onConsumePrefill={() => setLoginPrefill(null)} />)}
      {tab === 'knowledge' && (<KnowledgeV2 onError={setErr} onViewDetail={openDetail} />)}
      {tab === 'audit' && (<AuditV2 onError={setErr} onViewDetail={openDetail} />)}
      {tab === 'models' && (<ModelsV2 onError={setErr} />)}
      {tab === 'benchmark' && (<BenchmarkV2 onError={setErr} />)}
      {tab === 'conversations' && (<ConversationsCheckV2 onError={setErr} />)}
    </div>
  )
}

/* ── Inline module + plan editor for one tenant (P1.4) ─────────────────────── */
// Exported (2026-07-22) so VulaMasterTenantDetail.jsx's Overview sub-tab can reuse it too,
// instead of duplicating the plan/store-url/gateway/modules editor a second time.
export function ManageTenantRow({ tenant, registry, onSave }) {
  const { modules, plan, setPlan, storeUrl, setStoreUrl, gateway, setGateway, shareNetwork, setShareNetwork,
    spendCap, setSpendCap, allModules, toggle, patch } = useManageTenant(tenant, registry)

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
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <b style={{ fontSize: 12.5 }}>Daily LLM spend cap (USD)</b>
          <input value={spendCap} onChange={e => setSpendCap(e.target.value)} placeholder="uncapped" type="number" min="0" step="0.01" style={{ ...input, flex: 1 }} />
        </div>
      </div>
      <div style={{ fontSize: 11.5, color: C.muted }}>
        Empty = uncapped. On breach, generation soft-degrades to local-only for the rest of the day (never blocked) and the team gets a WhatsApp alert. See the Usage tab for today's spend vs. cap per tenant.
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
      <label style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12.5, cursor: 'pointer' }}>
        <input type="checkbox" checked={shareNetwork} onChange={e => setShareNetwork(e.target.checked)} />
        Share this tenant's reviewed answers with other Vula businesses (opt-in — see Knowledge tab)
      </label>
      <button style={{ ...btn, ...btnOn, alignSelf: 'flex-start' }}
        onClick={() => onSave(patch())}>Save changes</button>
    </div>
  )
}

const btn = { padding: '7px 13px', border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: 'pointer' }
const btnOn = { background: C.green, color: 'var(--on-accent)', borderColor: C.green }

const input = { padding: '7px 10px', border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, background: C.surface, color: C.text}

