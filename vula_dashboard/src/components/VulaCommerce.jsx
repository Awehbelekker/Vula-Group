/**
 * VulaCommerce.jsx
 *
 * Vula Admin → Commerce tenant management.
 * Shows all commerce tenants (Off the Hook, Awake SA, Kelp, etc) and
 * for each one: connection status to WhatsApp, Yoco, and product count.
 *
 * Each tenant card has a "Connect WhatsApp" button using Meta Embedded Signup.
 */

import { useState, useEffect } from 'react'
import VulaWhatsAppConnect from './VulaWhatsAppConnect'

const VULA_API = import.meta.env.VITE_API_URL ?? '/api'

const COLORS = {
  bg: '#F7F4EE',
  surface: '#FFFFFF',
  border: '#DDD8CE',
  green: '#2C5545',
  amber: '#C4861A',
  charcoal: '#1E1E1E',
  text: '#2A2A2A',
  muted: '#8A8680',
}

export default function VulaCommerce() {
  const [tenants, setTenants] = useState([])
  const [accounts, setAccounts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selectedTenant, setSelectedTenant] = useState(null)

  useEffect(() => { load() }, [])

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const accResp = await fetch(`${VULA_API}/v1/whatsapp/accounts`)
      const accData = await accResp.json()
      setAccounts(accData.accounts || [])

      // Tenant list — for now hardcoded; later from /v1/admin/tenants
      setTenants([
        { id: 'off-the-hook', name: 'Off the Hook', business: 'Quality food delivered to your door', site: 'offthehook.co.za' },
        { id: 'awake-sa', name: 'Awake South Africa', business: 'Premium eFoil distribution', site: 'awakesa.co.za' },
      ])
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  const accountFor = (tenantId) => accounts.find(a => a.tenant_id === tenantId)

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;600;700&family=Source+Code+Pro:wght@400;500&display=swap');
      `}</style>

      <div style={{ maxWidth: 1100, margin: '0 auto', padding: '32px 24px', fontFamily: 'system-ui' }}>
        <div style={{ marginBottom: 28 }}>
          <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 32, fontWeight: 700, color: COLORS.charcoal, marginBottom: 4 }}>
            Vula Commerce — Tenants
          </h1>
          <p style={{ fontSize: 13, color: COLORS.muted, fontFamily: "'Source Code Pro', monospace" }}>
            Manage products, payments and WhatsApp for each commerce client
          </p>
        </div>

        {error && (
          <div style={{ padding: 16, borderRadius: 8, background: '#FEF2F2', border: '1px solid #FECACA', color: '#991B1B', marginBottom: 20, fontSize: 13 }}>
            {error}
          </div>
        )}

        {loading ? (
          <p style={{ color: COLORS.muted, fontSize: 13 }}>Loading tenants…</p>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: 20 }}>
            {tenants.map(t => (
              <TenantCard
                key={t.id}
                tenant={t}
                account={accountFor(t.id)}
                onManage={() => setSelectedTenant(t)}
              />
            ))}
          </div>
        )}

        {/* Detail drawer */}
        {selectedTenant && (
          <div
            onClick={() => setSelectedTenant(null)}
            style={{
              position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
              display: 'flex', justifyContent: 'flex-end', zIndex: 100,
            }}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              style={{
                width: 540, background: COLORS.bg, padding: 32,
                overflowY: 'auto', boxShadow: '-4px 0 24px rgba(0,0,0,0.2)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 24 }}>
                <h2 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 26, fontWeight: 700, color: COLORS.charcoal, margin: 0 }}>
                  {selectedTenant.name}
                </h2>
                <button
                  onClick={() => setSelectedTenant(null)}
                  style={{ background: 'transparent', border: 'none', cursor: 'pointer', fontSize: 24, color: COLORS.muted }}
                >×</button>
              </div>

              <p style={{ fontSize: 13, color: COLORS.muted, marginBottom: 24 }}>
                {selectedTenant.business} · {selectedTenant.site}
              </p>

              <VulaWhatsAppConnect
                tenantId={selectedTenant.id}
                tenantName={selectedTenant.name}
                adminEmail="ian@vula.co.za"
              />
            </div>
          </div>
        )}
      </div>
    </>
  )
}

function TenantCard({ tenant, account, onManage }) {
  const waStatus = account?.status || 'not_connected'
  const waColor = waStatus === 'connected' ? '#22c55e' : waStatus === 'error' ? '#ef4444' : COLORS.muted

  return (
    <div style={{
      background: COLORS.surface,
      border: `1px solid ${COLORS.border}`,
      borderRadius: 10,
      padding: 20,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
        <div>
          <h3 style={{ margin: 0, color: COLORS.charcoal, fontFamily: "'Cormorant Garamond', serif", fontSize: 22, fontWeight: 700 }}>
            {tenant.name}
          </h3>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: COLORS.muted, fontFamily: "'Source Code Pro', monospace" }}>
            {tenant.site}
          </p>
        </div>
        <span style={{
          padding: '4px 10px', borderRadius: 20, fontSize: 11, fontWeight: 600,
          color: waColor, background: `${waColor}18`,
          fontFamily: "'Source Code Pro', monospace",
        }}>
          {waStatus === 'connected' ? '✓ WhatsApp' : waStatus === 'not_connected' ? 'No WhatsApp' : waStatus}
        </span>
      </div>

      <p style={{ fontSize: 13, color: COLORS.text, lineHeight: 1.5, marginBottom: 16 }}>
        {tenant.business}
      </p>

      {account?.phone_number && (
        <div style={{ fontSize: 12, color: COLORS.muted, marginBottom: 12, fontFamily: "'Source Code Pro', monospace" }}>
          📱 {account.phone_number}
        </div>
      )}

      <button
        onClick={onManage}
        style={{
          width: '100%', padding: '10px 16px', borderRadius: 6,
          background: COLORS.green, color: '#fff', border: 'none',
          fontSize: 13, fontWeight: 600, cursor: 'pointer',
          fontFamily: "'Source Code Pro', monospace",
        }}
      >
        Manage tenant →
      </button>
    </div>
  )
}
