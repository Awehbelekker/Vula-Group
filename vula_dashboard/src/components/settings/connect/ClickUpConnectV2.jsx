/**
 * ClickUpConnectV2.jsx — the redesigned ClickUp connection in Settings (2026-10-19, UX redesign
 * Phase F). Same flow as VulaClickUpConnect (connect/useClickUpConnect.js): workspace, the
 * default list, sync health, the "needs reconnecting" warning, Connect / Reconnect, "Send missed
 * documents", and the personal-token way round a popup that never comes back.
 */
import { KeyRound, Link2, RefreshCw, Send } from 'lucide-react'
import { Button, Field, Input, Select } from '../../ui/index.jsx'
import { ConnectCard } from './ConnectCard.jsx'
import { useClickUpConnect } from './useClickUpConnect'
import { timeAgo } from './useOAuthConnect'

export default function ClickUpConnectV2({ tenantId, tenantName }) {
  const x = useClickUpConnect(tenantId)
  const connected = x.status === 'connected'
  const a = x.account || {}
  const sync = connected && a.last_synced_at && (a.last_sync_status === 'error'
    ? ['Sync', `Failing — ${timeAgo(a.last_synced_at)}${a.last_sync_error ? ` · ${String(a.last_sync_error).slice(0, 120)}` : ''}`, 'danger']
    : ['Sync', `Last synced ${timeAgo(a.last_synced_at)}`])
  const rows = connected ? [
    ['Workspace', a.team_id ? `#${a.team_id}` : '—'],
    x.lists.length ? null : ['Default list', a.default_list_id || '—'],
    sync,
  ] : []

  return (
    <ConnectCard status={x.status} rows={rows} error={x.error} notice={x.notice}>
      {connected && x.lists.length > 0 && (
        <Field label="Default list" hint="New tasks from WhatsApp go here unless you say otherwise.">
          <Select value={a.default_list_id || ''} onChange={(e) => x.setDefaultList(e.target.value)}>
            {x.lists.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </Select>
        </Field>)}

      {x.status === 'needs_reconnect' && (
        <p className="vcn-error" role="alert">
          <strong>ClickUp has stopped accepting Vula's sign-in.</strong>{' '}
          {a.last_sync_error ? `(${String(a.last_sync_error).slice(0, 120)}) ` : ''}
          Documents are still filed in Vula, and are sent to ClickUp once you reconnect.
        </p>)}

      {connected ? (
        <div className="vst-row">
          <Button variant="secondary" Icon={RefreshCw} onClick={x.handleConnect} disabled={x.loading}>{x.loading ? 'Opening…' : 'Reconnect'}</Button>
          <Button variant="secondary" Icon={Send} onClick={x.refileMissing}>Send missed documents</Button>
        </div>
      ) : (
        <>
          <p className="vst-body">
            Connect {tenantName || 'your'} ClickUp so you can create, list and update tasks — and set reminders — straight from WhatsApp. One click, no API keys.
          </p>
          <div className="vst-row">
            <Button Icon={Link2} onClick={x.handleConnect} disabled={x.loading}>{x.loading ? 'Opening ClickUp…' : 'Connect ClickUp'}</Button>
          </div>
          <p className="vst-sub">A ClickUp window opens for you to approve. Takes about 30 seconds.</p>
        </>
      )}

      {/* shown connected or not: a dead token can still read "Connected" (3 Oct) */}
      {!x.showToken ? (
        <div><Button size="sm" variant="quiet" Icon={KeyRound} onClick={() => x.setShowToken(true)}>
          {connected ? 'Reconnect with a ClickUp token' : "Window didn't come back? Connect with a ClickUp token"}
        </Button></div>
      ) : (
        <form className="vst-card vcn-token" onSubmit={(e) => { e.preventDefault(); x.connectWithToken(); }}>
          <p className="vst-sub">In ClickUp: your avatar › Settings › Apps › API Token › Generate, then copy it (starts with <code>pk_</code>).</p>
          <Field label="ClickUp API token">
            <Input type="password" value={x.token} onChange={(e) => x.setToken(e.target.value)} placeholder="pk_…" autoComplete="off" />
          </Field>
          <div className="vst-row"><Button type="submit" disabled={x.loading || !x.token.trim()}>{x.loading ? 'Checking…' : 'Connect with token'}</Button></div>
        </form>
      )}
    </ConnectCard>
  )
}
