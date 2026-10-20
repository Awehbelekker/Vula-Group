/**
 * VulaFlowBuilder.jsx — owner-configurable WhatsApp conversation flows (MVP, migration 118).
 * A linear, keyword-branching flow: trigger phrases → a sequence of steps (prompt + expected
 * reply type + branch rules) → a terminal action drawn from the commerce_admin tool vocabulary.
 * Deliberately NOT a drag-and-drop canvas — same simple-form shell as VulaAutomations.jsx.
 */
import { REPLY_TYPES, END_ACTIONS, useFlowBuilder } from './assistant/useFlowBuilder'
import FlowBuilderV2 from './assistant/FlowBuilderV2'

export default function VulaFlowBuilder({ v2 = false, ...props }) {
  return v2 ? <FlowBuilderV2 {...props} /> : <VulaFlowBuilderV1 {...props} />
}

function VulaFlowBuilderV1({ tenantId }) {
  const { rows, loading, showNew, setShowNew, form, setForm, saving, error, updateStep, addStep, removeStep, create, toggle, remove, total } = useFlowBuilder(tenantId)

  return (
    <div>
      <div style={s.intro}>
        <h3 style={s.h3}>🧭 Flows</h3>
        <p style={s.sub}>Build a guided conversation — a customer texts a trigger phrase, Vula asks your
          questions in order, then runs an action at the end. No code, just fill in the steps below.</p>
      </div>

      <button onClick={() => setShowNew(v => !v)} style={s.newBtn}>{showNew ? 'Close' : '+ New flow'}</button>

      {showNew && (
        <div style={s.card}>
          <input placeholder="Flow name (e.g. Book a Demo)" value={form.name}
            onChange={e => setForm(f => ({ ...f, name: e.target.value }))} style={s.input} />

          <p style={s.label}>Trigger phrases</p>
          <input placeholder="e.g. book a demo, schedule a demo" value={form.trigger_phrases}
            onChange={e => setForm(f => ({ ...f, trigger_phrases: e.target.value }))} style={s.input} />
          <p style={s.hint}>Comma-separated. "hi", "menu" and a few other reserved words always take priority.</p>

          <p style={s.label}>Steps</p>
          {form.steps.map((st, i) => (
            <div key={i} style={s.stepCard}>
              <div style={s.stepHead}>
                <span style={s.stepNum}>Step {i + 1}</span>
                {form.steps.length > 1 && (
                  <button onClick={() => removeStep(i)} style={s.miniBtn}>Remove</button>
                )}
              </div>
              <textarea placeholder="What should Vula ask?" value={st.prompt}
                onChange={e => updateStep(i, { prompt: e.target.value })} rows={2}
                style={{ ...s.input, resize: 'vertical' }} />
              <div style={s.row}>
                <select value={st.reply_type} onChange={e => updateStep(i, { reply_type: e.target.value })} style={s.input}>
                  {Object.entries(REPLY_TYPES).map(([k, t]) => <option key={k} value={k}>{t.label}</option>)}
                </select>
                <input placeholder="Save answer as… (e.g. location)" value={st.save_as}
                  onChange={e => updateStep(i, { save_as: e.target.value })} style={s.input} />
              </div>
              <p style={s.hint}>{REPLY_TYPES[st.reply_type].hint}</p>
              {st.reply_type === 'multiple_choice' && (
                <input placeholder="Options, comma-separated (e.g. Cape Town, Durban, Joburg)"
                  value={st.options} onChange={e => updateStep(i, { options: e.target.value })} style={s.input} />
              )}
              {st.reply_type !== 'free_text' && (
                <>
                  <textarea placeholder={'Branch rules, one per line — e.g.\ncape town: 2\ndurban: 3'}
                    value={st.branches} onChange={e => updateStep(i, { branches: e.target.value })}
                    rows={2} style={{ ...s.input, resize: 'vertical', fontFamily: 'monospace', fontSize: 12 }} />
                  <p style={s.hint}>Keyword in the reply → step number (or "end" to finish the flow there).</p>
                </>
              )}
              <select value={st.default_next} onChange={e => updateStep(i, { default_next: e.target.value })} style={s.input}>
                <option value="">If nothing matches / next step…</option>
                {Array.from({ length: total }, (_, n) => n + 1).filter(n => n !== i + 1).map(n => (
                  <option key={n} value={String(n)}>Go to Step {n}</option>
                ))}
                <option value="end">End flow (run action)</option>
              </select>
            </div>
          ))}
          <button onClick={addStep} style={s.addStepBtn}>+ Add step</button>

          <p style={s.label}>Then, when the flow finishes…</p>
          <select value={form.end_action_tool} onChange={e => setForm(f => ({ ...f, end_action_tool: e.target.value }))} style={s.input}>
            {Object.entries(END_ACTIONS).map(([k, a]) => <option key={k} value={k}>{a.label}</option>)}
          </select>
          <p style={s.hint}>{END_ACTIONS[form.end_action_tool].hint} Use {'{{save_as key}}'} to reference an answer,
            or {'{{__phone}}'} / {'{{__customer_name}}'}.</p>
          <textarea placeholder={'Action details, one per line — e.g.\nstart: {{date}}T09:00\ncustomer_phone: {{__phone}}'}
            value={form.end_action_args} onChange={e => setForm(f => ({ ...f, end_action_args: e.target.value }))}
            rows={3} style={{ ...s.input, resize: 'vertical', fontFamily: 'monospace', fontSize: 12 }} />
          <textarea placeholder="Closing message to send the customer…" value={form.closing_message}
            onChange={e => setForm(f => ({ ...f, closing_message: e.target.value }))} rows={2}
            style={{ ...s.input, resize: 'vertical' }} />

          {error && <p style={s.error}>{error}</p>}
          <button onClick={create} disabled={saving} style={s.saveBtn}>{saving ? 'Saving…' : 'Create flow'}</button>
        </div>
      )}

      {loading ? <p style={s.muted}>Loading…</p> : rows.length === 0 ? (
        <p style={s.muted}>No flows yet.</p>
      ) : (
        <div style={s.list}>
          {rows.map(r => (
            <div key={r.id} style={s.row2}>
              <div style={{ flex: 1 }}>
                <span style={s.name}>{r.name}</span>
                <div style={s.meta}>
                  "{(r.trigger_phrases || []).join('", "')}" · {(r.steps || []).length} step(s)
                  {r.fire_count > 0 && ` · completed ${r.fire_count}×`}
                </div>
              </div>
              <button onClick={() => toggle(r)} style={{ ...s.miniBtn, color: r.enabled ? 'var(--ok)' : 'var(--muted)' }}>
                {r.enabled ? 'On' : 'Off'}
              </button>
              <button onClick={() => remove(r)} style={{ ...s.miniBtn, color: 'var(--danger)' }}>Delete</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const s = {
  intro: { marginBottom: 14 },
  h3: { fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 700, color: 'var(--ink)', margin: '0 0 4px' },
  sub: { fontSize: 13, color: 'var(--muted)', margin: 0 },
  newBtn: { padding: '8px 16px', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer', marginBottom: 14 },
  card: { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 16, marginBottom: 18, display: 'flex', flexDirection: 'column', gap: 8 },
  row: { display: 'flex', gap: 8 },
  input: { padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13, boxSizing: 'border-box', width: '100%' },
  label: { fontSize: 12, fontWeight: 700, color: 'var(--ink)', margin: '4px 0 0'},
  hint: { fontSize: 11.5, color: 'var(--muted)', margin: 0},
  error: { color: 'var(--danger)', fontSize: 13, margin: 0 },
  saveBtn: { padding: '9px 16px', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer', alignSelf: 'flex-start' },
  muted: { color: 'var(--muted)', fontSize: 13},
  list: { display: 'flex', flexDirection: 'column', gap: 8 },
  row2: { display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px' },
  name: { fontSize: 14, fontWeight: 600, color: 'var(--ink)' },
  meta: { fontSize: 12, color: 'var(--muted)', marginTop: 2 },
  miniBtn: { padding: '5px 12px', border: '1px solid var(--border)', borderRadius: 6, background: 'var(--surface)', fontSize: 12, fontWeight: 600, cursor: 'pointer'},
  stepCard: { background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 8, padding: 12, display: 'flex', flexDirection: 'column', gap: 6 },
  stepHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  stepNum: { fontSize: 12, fontWeight: 700, color: 'var(--ink)' },
  addStepBtn: { padding: '6px 12px', border: '1px dashed var(--border)', borderRadius: 6, background: 'var(--surface)', fontSize: 12, fontWeight: 600, cursor: 'pointer', alignSelf: 'flex-start' },
}
