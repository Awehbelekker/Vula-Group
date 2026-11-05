/**
 * ConnectionsHealth.jsx — "N connections need attention" at the top of Settings (5 Nov 2026).
 * Connections used to break without anyone seeing it (a mailbox that couldn't sign in for two
 * weeks, a ClickUp link dead since August). GET /v1/connections/{tenant} (vula/connections.py)
 * gives each one's state in plain words; this lists the broken ones with a button to the page
 * that fixes them. Nothing shows when everything works.
 */
import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { Button } from '../../ui/index.jsx'
import { VULA_API } from '../../../lib/authFetch'

const SECTION = { clickup: 'clickup', google: 'google', microsoft: 'microsoft', email: 'email', whatsapp: 'whatsapp', xero: 'xero' }

export default function ConnectionsHealth({ tenantId, onOpen }) {
  const [broken, setBroken] = useState([])
  useEffect(() => {
    if (!tenantId) return
    let live = true
    fetch(`${VULA_API}/v1/connections/${tenantId}`)
      .then(r => (r.ok ? r.json() : { connections: [] }))
      .then(d => { if (live) setBroken((d.connections || []).filter(c => c.state === 'broken')) })
      .catch(() => {})
    return () => { live = false }
  }, [tenantId])
  if (!broken.length) return null
  return (
    <div className="vcn-warn-box vst-health" role="status">
      <p className="vst-health-h"><AlertTriangle size={16} aria-hidden="true" />
        {broken.length === 1 ? '1 connection needs attention' : `${broken.length} connections need attention`}</p>
      <ul className="vst-health-list">
        {broken.map(c => (
          <li key={`${c.provider}-${c.account}`}>
            <span><b>{c.label}{c.account ? ` · ${c.account}` : ''}</b> — {c.problem || 'stopped working'}. Nothing new comes in from it until it's connected again.</span>
            {SECTION[c.provider] && <Button size="sm" variant="secondary" onClick={() => onOpen(SECTION[c.provider])}>Fix it</Button>}
          </li>))}
      </ul>
    </div>
  )
}
