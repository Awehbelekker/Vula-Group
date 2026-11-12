/**
 * useMyVula.js — "How Vula talks to you": the signed-in person's own style, within the company
 * voice the owner sets. Fixed choices only (style, reply length, language) plus what to call them;
 * the server keeps them on the person's team record, so they apply on WhatsApp and here alike.
 *
 * `known` is false for a login the server can't tie to a team member (it has nowhere to keep the
 * choices), and the style choices aren't shown.
 *
 * Also the person's private notes: things they asked Vula to remember about them. Only they can
 * see, add or remove them; `memoryOn` is false when the workspace hasn't switched the feature on.
 */
import { useCallback, useEffect, useState } from 'react'
import { VULA_API } from '../../lib/authFetch'

const EMPTY = { style: '', reply_length: '', language: '', nickname: '' }

export function useMyVula(tenantId) {
  const [known, setKnown] = useState(false)
  const [prefs, setPrefs] = useState(EMPTY)
  const [options, setOptions] = useState({ style: [], reply_length: [], language: [] })
  const [status, setStatus] = useState('')        // '' | 'saving' | 'saved' | an error to show
  const url = `${VULA_API}/v1/commerce/${tenantId}/admin/me/prefs`

  const load = useCallback(async () => {
    if (!tenantId) return
    try {
      const r = await fetch(url)
      if (!r.ok) { setKnown(false); return }
      const d = await r.json()
      setKnown(!!d.known)
      setPrefs({ ...EMPTY, ...(d.prefs || {}) })
      setOptions({ style: [], reply_length: [], language: [], ...(d.options || {}) })
    } catch { setKnown(false) }
  }, [tenantId, url])
  useEffect(() => { load() }, [load])

  const set = (key, value) => { setPrefs(p => ({ ...p, [key]: value })); setStatus('') }

  const save = async () => {
    setStatus('saving')
    try {
      const r = await fetch(url, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(prefs),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setStatus(d.detail || "Couldn't save that. Please try again."); return }
      setPrefs({ ...EMPTY, ...(d.prefs || {}) })
      setStatus('saved')
    } catch { setStatus("Couldn't reach Vula. Please try again.") }
  }

  // ── what Vula remembers about me (private notes) ──
  const [memoryOn, setMemoryOn] = useState(false)
  const [notes, setNotes] = useState([])
  const [draft, setDraft] = useState('')
  const [memoryStatus, setMemoryStatus] = useState('')   // '' | 'working' | an error to show
  const memUrl = `${VULA_API}/v1/commerce/${tenantId}/admin/me/memory`

  const loadNotes = useCallback(async () => {
    if (!tenantId) return
    try {
      const r = await fetch(memUrl)
      if (!r.ok) { setMemoryOn(false); return }
      const d = await r.json()
      setMemoryOn(!!d.enabled)
      setNotes(d.notes || [])
    } catch { setMemoryOn(false) }
  }, [tenantId, memUrl])
  useEffect(() => { loadNotes() }, [loadNotes])

  // Every change is followed by a fresh read, so the list shows what is really kept.
  const change = async (path, init) => {
    setMemoryStatus('working')
    try {
      const r = await fetch(memUrl + path, init)
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setMemoryStatus(d.detail || "That didn't work. Please try again.")
        return false
      }
      await loadNotes()
      setMemoryStatus('')
      return true
    } catch { setMemoryStatus("Couldn't reach Vula. Please try again."); return false }
  }
  const addNote = async () => {
    const text = draft.trim()
    if (!text) return
    const ok = await change('', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) })
    if (ok) setDraft('')
  }
  const forgetNote = (id) => change(`/${id}`, { method: 'DELETE' })
  const forgetAll = () => change('', { method: 'DELETE' })

  return { known, prefs, options, status, set, save,
    memoryOn, notes, draft, setDraft, memoryStatus, addNote, forgetNote, forgetAll }
}
