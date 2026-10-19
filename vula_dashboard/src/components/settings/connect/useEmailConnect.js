/**
 * useEmailConnect.js — IMAP/SMTP mailboxes: list, connect (with host presets), disconnect, make
 * primary, backfill (2026-10-19, UX redesign Phase F). Moved out of VulaEmailConnect unchanged —
 * same /v1/email/{status,connect,disconnect,set-primary,backfill} calls (migration 093).
 */
import { useCallback, useEffect, useState } from 'react'
import { VULA_API } from '../../../lib/authFetch'
import { confirmDialog, toast } from '../../ui/index.jsx'

export const PRESETS = {
  'GoDaddy Workspace': { imap_host: 'imap.secureserver.net', imap_port: 993, smtp_host: 'smtpout.secureserver.net', smtp_port: 465 },
  'cPanel / hosting':  { imap_host: '', imap_port: 993, smtp_host: '', smtp_port: 465 },
  'Zoho':              { imap_host: 'imap.zoho.com', imap_port: 993, smtp_host: 'smtp.zoho.com', smtp_port: 465 },
  'Gmail (app pwd)':   { imap_host: 'imap.gmail.com', imap_port: 993, smtp_host: 'smtp.gmail.com', smtp_port: 465 },
  'Office 365':        { imap_host: 'outlook.office365.com', imap_port: 993, smtp_host: 'smtp.office365.com', smtp_port: 587 },
}
export const blank = { email: '', password: '', from_name: '', imap_host: '', imap_port: 993, smtp_host: '', smtp_port: 465, send_mode: 'draft' }

export function useEmailConnect({ tenantId, adminEmail }) {
  const [accounts, setAccounts] = useState([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState(blank)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [busyAccountId, setBusyAccountId] = useState(null)

  const loadStatus = useCallback(async () => {
    if (!tenantId) return
    try {
      const r = await fetch(`${VULA_API}/v1/email/status/${tenantId}`)
      const d = await r.json()
      setAccounts(d.accounts || [])
    } catch { setAccounts([]) }
    setLoading(false)
  }, [tenantId])
  useEffect(() => { loadStatus() }, [loadStatus])

  const applyPreset = (name) => setForm(f => ({ ...f, ...(PRESETS[name] || {}) }))

  const connect = async () => {
    if (!form.email || !form.password || !form.imap_host) { setError('Email, password and IMAP host are required.'); return }
    setBusy(true); setError(null)
    try {
      const r = await fetch(`${VULA_API}/v1/email/connect`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tenant_id: tenantId, connected_by: adminEmail, ...form,
          imap_port: Number(form.imap_port), smtp_port: Number(form.smtp_port) }),
      })
      const d = await r.json()
      if (d.status === 'connected') { setForm(blank); setShowForm(false); loadStatus() }
      else setError(d.error || 'Could not connect — check the host, port and (app) password.')
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const disconnect = async (accountId) => {
    if (!(await confirmDialog('Disconnect this mailbox?', { danger: true, confirmLabel: 'Yes' }))) return
    setBusyAccountId(accountId)
    await fetch(`${VULA_API}/v1/email/disconnect/${tenantId}/${accountId}`, { method: 'DELETE' })
    setBusyAccountId(null)
    loadStatus()
  }

  const setPrimary = async (accountId) => {
    setBusyAccountId(accountId)
    await fetch(`${VULA_API}/v1/email/set-primary/${tenantId}/${accountId}`, { method: 'POST' })
    setBusyAccountId(null)
    loadStatus()
  }

  const backfill = async (accountId) => {
    setBusyAccountId(accountId)
    await fetch(`${VULA_API}/v1/email/backfill/${tenantId}?account_id=${accountId}`, { method: 'POST' })
    setBusyAccountId(null)
    toast('Backfill started in the background — check Contacts/Documents in a few minutes.')
  }

  return { accounts, loading, showForm, setShowForm, form, setForm, busy, error, setError, busyAccountId,
    applyPreset, connect, disconnect, setPrimary, backfill }
}
