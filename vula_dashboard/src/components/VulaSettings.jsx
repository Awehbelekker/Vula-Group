/**
 * VulaSettings.jsx — tenant store settings & connections.
 *
 * Lets the owner connect their OWN payment + WhatsApp (self-service), and see
 * their store details. Reuses the same connect components as the master admin
 * so a tenant is no longer dependent on Vula staff to go live.
 */

import VulaYocoConnect from './VulaYocoConnect'
import VulaWhatsAppConnect from './VulaWhatsAppConnect'
import VulaClickUpConnect from './VulaClickUpConnect'
import VulaGoogleConnect from './VulaGoogleConnect'

export default function VulaSettings({ tenantId, tenantName, adminEmail }) {
  return (
    <div>
      <div style={s.intro}>
        <h3 style={s.h3}>⚙️ Settings & connections</h3>
        <p style={s.sub}>Connect your payments and WhatsApp so your store can take orders and get paid.</p>
      </div>

      {/* Payments */}
      <section style={s.section}>
        <h4 style={s.sectionTitle}>💳 Payments (Yoco)</h4>
        <p style={s.sectionHint}>
          Connect your Yoco account to accept card, tap & QR payments. Money goes straight to you.
        </p>
        <VulaYocoConnect tenantId={tenantId} tenantName={tenantName} adminEmail={adminEmail} />
      </section>

      {/* WhatsApp */}
      <section style={s.section}>
        <h4 style={s.sectionTitle}>💬 WhatsApp</h4>
        <p style={s.sectionHint}>
          Connect your WhatsApp Business number so customers can order and chat to your AI assistant.
        </p>
        <VulaWhatsAppConnect tenantId={tenantId} tenantName={tenantName} adminEmail={adminEmail} />
      </section>

      {/* ClickUp */}
      <section style={s.section}>
        <h4 style={s.sectionTitle}>🗂️ ClickUp</h4>
        <p style={s.sectionHint}>
          Connect ClickUp to create, list and update tasks — and set reminders — straight from WhatsApp.
        </p>
        <VulaClickUpConnect tenantId={tenantId} tenantName={tenantName} />
      </section>

      {/* Google */}
      <section style={s.section}>
        <h4 style={s.sectionTitle}>🔵 Google (Drive &amp; Gmail)</h4>
        <p style={s.sectionHint}>
          Connect Google so Vula can find &amp; file Drive documents and draft Gmail replies (draft-only).
        </p>
        <VulaGoogleConnect tenantId={tenantId} tenantName={tenantName} />
      </section>

      <p style={s.footer}>Powered by Vula</p>
    </div>
  )
}

const s = {
  intro:        { marginBottom: 16 },
  h3:           { fontFamily: "'Cormorant Garamond', serif", fontSize: 20, fontWeight: 700, color: 'var(--ink, #1E1E1E)', margin: '0 0 4px' },
  sub:          { fontFamily: 'system-ui', fontSize: 13, color: '#8A8680', margin: 0 },
  section:      { marginBottom: 24 },
  sectionTitle: { fontFamily: 'system-ui', fontSize: 15, fontWeight: 700, color: 'var(--ink, #1E1E1E)', margin: '0 0 4px' },
  sectionHint:  { fontFamily: 'system-ui', fontSize: 13, color: '#8A8680', margin: '0 0 12px', lineHeight: 1.5 },
  footer:       { textAlign: 'center', fontFamily: 'system-ui', fontSize: 11, color: '#B5B0A8', marginTop: 24 },
}
