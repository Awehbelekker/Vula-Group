/**
 * useFlowBuilder.js — Assistant › Flows, shared by VulaFlowBuilder (current look) and
 * assistant/FlowBuilderV2 (the redesign, 2026-10-20). Moved verbatim: guided WhatsApp conversations
 * (migration 118) — trigger phrases → steps (prompt, reply type, branches) → an end action from the
 * commerce_admin tool vocabulary; on/off and delete.
 */
import { useState, useEffect, useCallback } from 'react'
import { VULA_API } from '../../lib/authFetch'
import { confirmDialog } from '../ui/index.jsx'

export const REPLY_TYPES = {
  free_text: { label: '💬 Free text', hint: 'Any reply is accepted as-is.' },
  yes_no: { label: '✅ Yes / No', hint: 'Expects a yes or no reply.' },
  multiple_choice: { label: '🔢 Multiple choice', hint: 'Customer picks from a numbered list.' },
}

export const END_ACTIONS = {
  create_booking: { label: '📅 Book an appointment', hint: 'Needs: start, customer_name, customer_phone (service optional).' },
  create_contact: { label: '📇 Save a contact', hint: 'Needs: name (phone, company, title, email optional).' },
  create_reminder: { label: '⏰ Set a reminder', hint: 'Needs: text (due_at optional).' },
  notify_team: { label: '🧑 Notify my team', hint: 'Sends a WhatsApp message to your team helper — needs: message.' },
}

export function emptyStep() {
  return { prompt: '', reply_type: 'free_text', options: '', save_as: '', branches: '', default_next: '' }
}

export function emptyForm() {
  return {
    name: '', trigger_phrases: '', steps: [emptyStep()],
    end_action_tool: 'notify_team', end_action_args: '', closing_message: '',
  }
}

// UI step refs are 1-based ("Step 2") or "end" — convert to the 0-based index / null the
// backend expects.
export function parseStepRef(val, totalSteps) {
  const v = (val || '').trim().toLowerCase()
  if (!v || v === 'end') return null
  const n = parseInt(v, 10)
  if (!isNaN(n) && n >= 1 && n <= totalSteps) return n - 1
  return null
}

export function parseLines(text) {
  const out = {}
  for (const line of (text || '').split('\n')) {
    const idx = line.indexOf(':')
    if (idx < 0) continue
    const key = line.slice(0, idx).trim()
    const val = line.slice(idx + 1).trim()
    if (key) out[key] = val
  }
  return out
}

export function useFlowBuilder(tenantId) {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [showNew, setShowNew] = useState(false)
  const [form, setForm] = useState(emptyForm())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/flows`).then(r => r.json()).catch(() => ({}))
    setRows(r.flows || [])
    setLoading(false)
  }, [tenantId])
  useEffect(() => { load() }, [load])

  function updateStep(i, patch) {
    setForm(f => ({ ...f, steps: f.steps.map((st, idx) => idx === i ? { ...st, ...patch } : st) }))
  }
  function addStep() {
    setForm(f => ({ ...f, steps: [...f.steps, emptyStep()] }))
  }
  function removeStep(i) {
    setForm(f => ({ ...f, steps: f.steps.filter((_, idx) => idx !== i) }))
  }

  async function create() {
    setError('')
    const name = form.name.trim()
    if (!name) return setError('Give the flow a name.')
    const phrases = form.trigger_phrases.split(',').map(p => p.trim()).filter(Boolean)
    if (!phrases.length) return setError('Add at least one trigger phrase.')
    if (!form.steps.length) return setError('Add at least one step.')

    const total = form.steps.length
    const steps = []
    for (let i = 0; i < form.steps.length; i++) {
      const st = form.steps[i]
      if (!st.prompt.trim()) return setError(`Step ${i + 1}: needs a prompt.`)
      const options = st.reply_type === 'multiple_choice'
        ? st.options.split(',').map(o => o.trim()).filter(Boolean) : undefined
      if (st.reply_type === 'multiple_choice' && !options.length) {
        return setError(`Step ${i + 1}: multiple_choice needs at least one option.`)
      }
      const branchLines = parseLines(st.branches)
      const branches = {}
      for (const [kw, ref] of Object.entries(branchLines)) {
        branches[kw.toLowerCase()] = parseStepRef(ref, total)
      }
      // Blank means what the dropdown says — "next step" (the flow ends after the last step).
      // It used to be sent as null, which the backend treats as "end", so every flow built with
      // the default stopped after its first question.
      const defaultNext = st.default_next.trim() === ''
        ? (i + 1 < total ? i + 1 : null)
        : parseStepRef(st.default_next, total)
      steps.push({
        prompt: st.prompt.trim(), reply_type: st.reply_type,
        ...(options ? { options } : {}), save_as: st.save_as.trim() || undefined,
        branches, default_next: defaultNext,
      })
    }

    const argMap = parseLines(form.end_action_args)
    const body = {
      name, trigger_phrases: phrases, steps,
      end_action: {
        tool: form.end_action_tool, arg_map: argMap,
        closing_message: form.closing_message.trim() || 'Thanks — we’ve got your details!',
      },
    }
    setSaving(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/flows`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then(r => r.json()).catch(() => ({ error: 'network' }))
    setSaving(false)
    if (r.error) return setError(r.error)
    setShowNew(false)
    setForm(emptyForm())
    load()
  }

  async function toggle(row) {
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/flows/${row.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: !row.enabled }),
    })
    load()
  }
  async function remove(row) {
    if (!(await confirmDialog(`Delete flow "${row.name}"?`, { danger: true, confirmLabel: 'Yes' }))) return
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/flows/${row.id}`, { method: 'DELETE' })
    load()
  }

  const total = form.steps.length

  return { rows, loading, showNew, setShowNew, form, setForm, saving, error, updateStep, addStep, removeStep, create, toggle, remove, total }
}
