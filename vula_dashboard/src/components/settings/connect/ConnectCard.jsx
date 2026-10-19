/**
 * ConnectCard.jsx — the redesign's frame for one Settings connection (2026-10-19, UX redesign
 * Phase F): its state as a chip, what it's connected to as label/value rows, any error, then
 * the panel's own content and actions. The page header above already names the service.
 */
import { Card, StatusChip } from '../../ui/index.jsx'
import '../settings.css'

const STATES = {
  connected: ['Connected', 'ok'],
  needs_reconnect: ['Needs reconnecting', 'warn'],
  pending: ['Waiting', 'warn'],
  error: ['Error', 'danger'],
}

/** status: a backend status string (or null while loading); rows: [[label, value, tone?]]. */
export function ConnectCard({ status, label, rows = [], error, notice, children }) {
  const [text, tone] = STATES[status] || ['Not connected', 'muted']
  return (
    <Card className="vst-card">
      <div className="vst-row">
        <span className="vst-label">Status</span>
        {status === null ? <span className="vst-label">Checking…</span> : <StatusChip tone={tone}>{label || text}</StatusChip>}
      </div>
      {rows.filter(Boolean).length > 0 && (
        <dl className="vcn-rows">
          {rows.filter(Boolean).map(([k, v, t]) => (
            <div key={k} className="vcn-row"><dt>{k}</dt><dd className={t ? `vcn-${t}` : undefined}>{v}</dd></div>))}
        </dl>)}
      {error && <p className="vcn-error" role="alert">{error}</p>}
      {notice && <p className="vst-msg" role="status">{notice}</p>}
      {children}
    </Card>
  )
}
