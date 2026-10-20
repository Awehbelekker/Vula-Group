import { useState } from "react";
import { setFieldTenant, useAssignTask, useContractors, useDailyBriefing, useProjectOptions, useProjectStatus, useSignOff, useTaskRow, useWalkthrough } from "./fieldops/useFieldOps";
import FieldOpsV2 from "./fieldops/FieldOpsV2";


const C = {
  bg: "var(--bg)", surface: "var(--surface)", border: "var(--border)",
  green: "var(--accent)", amber: "var(--warn)", red: "var(--danger)",
  blue: "#1A5276", text: "var(--text)", muted: "var(--muted)",
  surfaceAlt: "var(--surface-alt)", greenLight: "var(--ok-soft)",
};

const STATUS_STYLE = {
  pending:            { bg: "var(--surface-alt)", color: "var(--muted)", label: "Pending" },
  in_progress:        { bg: "var(--ok-soft)", color: "var(--accent)", label: "In Progress" },
  awaiting_sign_off:  { bg: "var(--warn-soft)", color: "var(--warn)", label: "Awaiting Sign-off" },
  complete:           { bg: "var(--ok-soft)", color: "#1A7A4A", label: "Complete" },
  rejected:           { bg: "var(--danger-soft)", color: "var(--danger)", label: "Rejected" },
};

function StatusBadge({ status }) {
  const s = STATUS_STYLE[status] || STATUS_STYLE.pending;
  return (
    <span style={{
      display: "inline-block", padding: "3px 10px",
      background: s.bg, color: s.color,
      borderRadius: 20, fontSize: 11, fontWeight: 600,
    }}>{s.label}</span>
  );
}

function Pill({ label, color = C.muted }) {
  return (
    <span style={{
      display: "inline-block", padding: "2px 10px",
      background: `color-mix(in srgb, ${color} 9%, transparent)`, color, borderRadius: 20,
      fontSize: 11, fontWeight: 600, letterSpacing: "0.03em",
    }}>{label}</span>
  );
}

function SectionHeader({ title, action }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
      <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: C.text }}>{title}</h3>
      {action}
    </div>
  );
}

function Card({ children, style = {} }) {
  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderRadius: 10, padding: 20, ...style,
    }}>{children}</div>
  );
}

function Btn({ children, onClick, variant = "primary", small, disabled, style = {} }) {
  const base = {
    padding: small ? "6px 14px" : "9px 20px",
    fontSize: small ? 12 : 13, fontWeight: 600,
    border: "none", borderRadius: 6, cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1, transition: "opacity 0.15s", ...style,
  };
  const colors = {
    primary: { background: C.green, color: "var(--on-accent)" },
    secondary: { background: C.surfaceAlt, color: C.text, border: `1px solid ${C.border}` },
    danger: { background: "var(--danger-soft)", color: C.red },
  };
  return (
    <button onClick={onClick} disabled={disabled} style={{ ...base, ...colors[variant] }}>
      {children}
    </button>
  );
}

function Input({ label, value, onChange, placeholder, type = "text" }) {
  return (
    <label style={{ display: "block", marginBottom: 12 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: C.muted, marginBottom: 4 }}>{label}</div>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          width: "100%", boxSizing: "border-box",
          padding: "8px 12px", fontSize: 13,
          border: `1px solid ${C.border}`, borderRadius: 6,
          background: C.bg, color: C.text, outline: "none",
        }}
      />
    </label>
  );
}

// ─── API helpers ──────────────────────────────────────────────────────────────

function ProjectSelect({ tenantId, value, onChange, label = "Project" }) {
  const { projects, loading } = useProjectOptions({ tenantId });

  return (
    <label style={{ display: "block", marginBottom: 12 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: C.muted, marginBottom: 4 }}>{label}</div>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        style={{
          width: "100%", padding: "8px 12px", fontSize: 13,
          border: `1px solid ${C.border}`, borderRadius: 6,
          background: C.bg, color: value ? C.text : C.muted,
        }}
      >
        <option value="">{loading ? "Loading projects..." : "— select project —"}</option>
        {projects.map(p => (
          <option key={p.id} value={p.id}>{p.name} ({p.id})</option>
        ))}
      </select>
    </label>
  );
}

// ─── Daily Briefing Panel ─────────────────────────────────────────────────────

function DailyBriefingPanel({ tenantId }) {
  const { data, loading, dispatching, msg, load, dispatch } = useDailyBriefing({ tenantId });

  return (
    <Card>
      <SectionHeader
        title="Morning Briefings"
        action={<Btn small onClick={load} disabled={loading}>Refresh</Btn>}
      />
      <div style={{ marginBottom: 20, fontSize: 13, color: C.muted }}>
        Every morning at 06:30, Vula automatically sends WhatsApp briefings to contractors with tasks due today.
        Use this panel to review or manually trigger the dispatch.
      </div>

      {loading ? (
        <div style={{ padding: 40, textAlign: "center", color: C.muted }}>Loading today's tasks...</div>
      ) : !data || data.tasks.length === 0 ? (
        <div style={{ padding: 40, textAlign: "center", background: C.surfaceAlt, borderRadius: 8, color: C.muted }}>
          No tasks due today.
        </div>
      ) : (
        <>
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: C.muted, marginBottom: 8 }}>
              {data.tasks.length} tasks for {new Set(data.tasks.map(t => t.contractor_id)).size} contractors
            </div>
            {data.tasks.map(t => (
              <div key={t.task_id} style={{
                padding: "8px 12px", marginBottom: 4, background: C.surfaceAlt, borderRadius: 6,
                display: "flex", justifyContent: "space-between", fontSize: 12,
              }}>
                <span><strong>{t.contractor_name || "Unassigned"}</strong>: {t.title}</span>
                <span style={{ color: C.muted }}>{t.project_id}</span>
              </div>
            ))}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Btn onClick={dispatch} disabled={dispatching}>
              {dispatching ? "Sending..." : "Dispatch Briefings Now"}
            </Btn>
            {msg && <div style={{ fontSize: 13, color: C.green, fontWeight: 600 }}>{msg}</div>}
          </div>
        </>
      )}
    </Card>
  );
}

// ─── Contractor Panel ─────────────────────────────────────────────────────────

function ContractorPanel({ tenantId, onRefresh }) {
  const { contractors, form, setForm, saving, msg, save } = useContractors({ tenantId, onRefresh });

  return (
    <Card>
      <SectionHeader title="Contractors" />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 600, color: C.muted, marginBottom: 8 }}>Register contractor</div>
          <Input label="Name" value={form.name} onChange={v => setForm(f => ({ ...f, name: v }))} placeholder="Jane Dlamini" />
          <Input label="WhatsApp number" value={form.phone} onChange={v => setForm(f => ({ ...f, phone: v }))} placeholder="+27821234567" />
          <Input label="Trade" value={form.trade} onChange={v => setForm(f => ({ ...f, trade: v }))} placeholder="electrician" />
          <Btn onClick={save} disabled={saving}>{saving ? "Saving…" : "Add contractor"}</Btn>
          {msg && <div style={{ marginTop: 8, fontSize: 12, color: C.muted }}>{msg}</div>}
        </div>
        <div>
          <div style={{ fontSize: 12, fontWeight: 600, color: C.muted, marginBottom: 8 }}>
            {contractors.length} registered
          </div>
          {contractors.length === 0 && (
            <div style={{ color: C.muted, fontSize: 13 }}>No contractors yet. Add one to get started.</div>
          )}
          {contractors.map(c => (
            <div key={c.id} style={{
              padding: "10px 12px", marginBottom: 6,
              background: C.surfaceAlt, borderRadius: 8,
              display: "flex", justifyContent: "space-between", alignItems: "center",
            }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: 13 }}>{c.name}</div>
                <div style={{ fontSize: 11, color: C.muted }}>{c.phone}</div>
              </div>
              <Pill label={c.trade} color={C.green} />
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

// ─── Project Status Panel ─────────────────────────────────────────────────────

function ProjectPanel({ tenantId }) {
  const { projectId, status, error, load, onProjectChange } = useProjectStatus({ tenantId });

  return (
    <Card>
      <SectionHeader title="Project Status" />
      <div style={{ marginBottom: 16 }}>
        <ProjectSelect tenantId={tenantId} value={projectId} onChange={onProjectChange} />
      </div>

      {error && <div style={{ color: C.red, fontSize: 13, marginBottom: 12 }}>{error}</div>}

      {status && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 20 }}>
            {[
              { label: "Complete", value: status.tasks_complete, color: 'var(--ok)' },
              { label: "In Progress", value: status.tasks_in_progress, color: C.blue },
              { label: "Awaiting Sign-off", value: status.tasks_awaiting_sign_off, color: C.amber },
              { label: "Rejected", value: status.tasks_rejected, color: C.red },
            ].map(({ label, value, color }) => (
              <div key={label} style={{
                background: `color-mix(in srgb, ${color} 7%, transparent)`, border: `1px solid color-mix(in srgb, ${color} 19%, transparent)`,
                borderRadius: 8, padding: "12px 16px", textAlign: "center",
              }}>
                <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
                <div style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>{label}</div>
              </div>
            ))}
          </div>

          <div style={{
            marginBottom: 16, height: 8, background: C.border, borderRadius: 4, overflow: "hidden",
          }}>
            <div style={{
              height: "100%", width: `${status.completion_pct}%`,
              background: C.green, transition: "width 0.4s",
            }} />
          </div>
          <div style={{ fontSize: 12, color: C.muted, marginBottom: 20 }}>
            {status.completion_pct}% complete · {status.team_size} team members
          </div>

          <div style={{ fontSize: 12, fontWeight: 600, color: C.muted, marginBottom: 8 }}>Tasks</div>
          {(status.tasks || []).map(t => (
            <TaskRow key={t.id} task={t} projectId={projectId} onRefresh={load} />
          ))}
        </>
      )}
    </Card>
  );
}

function TaskRow({ task, projectId, onRefresh, tenantId }) {
  const { requesting, expanded, detail, setDetail, loading, loadDetail, requestComplete } = useTaskRow({ task, projectId, onRefresh, tenantId });

  return (
    <div style={{
      padding: "0", marginBottom: 8,
      border: `1px solid ${C.border}`, borderRadius: 8,
      background: C.surface, overflow: "hidden",
    }}>
      <div
        onClick={loadDetail}
        style={{
          padding: "12px 14px", cursor: "pointer",
          display: "flex", justifyContent: "space-between", alignItems: "center",
          background: expanded ? C.surfaceAlt : "transparent",
        }}
      >
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 2 }}>{task.title}</div>
          <div style={{ fontSize: 11, color: C.muted }}>
            {task.trade} · {task.due_date || "no due date"}
            {task.evidence_count > 0 && ` · ${task.evidence_count} photo${task.evidence_count > 1 ? "s" : ""}`}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <StatusBadge status={task.status} />
          {task.status === "in_progress" && (
            <Btn small variant="secondary" onClick={requestComplete} disabled={requesting}>
              {requesting ? "Sending…" : "Chase"}
            </Btn>
          )}
          <span style={{ fontSize: 12, color: C.muted, marginLeft: 4 }}>{expanded ? "▲" : "▼"}</span>
        </div>
      </div>

      {expanded && (
        <div style={{ padding: "14px", borderTop: `1px solid ${C.border}`, background: C.surface }}>
          {loading ? <div style={{ fontSize: 12, color: C.muted }}>Loading details…</div> : !detail ? (
            <div style={{ fontSize: 12, color: C.red }}>Could not load task details.</div>
          ) : (
            <>
              {detail.notes && (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 4 }}>Notes</div>
                  <div style={{ fontSize: 13, color: C.text }}>{detail.notes}</div>
                </div>
              )}

              {detail.evidence && detail.evidence.length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: "uppercase", marginBottom: 8 }}>Evidence Photos</div>
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                    {detail.evidence.map(e => (
                      <div key={e.id} style={{ width: 120 }}>
                        <a href={e.photo_url} target="_blank" rel="noreferrer">
                          <img src={e.photo_url} alt="evidence" style={{ width: "100%", height: 90, objectFit: "cover", borderRadius: 6, border: `1px solid ${C.border}` }} />
                        </a>
                        {e.caption && <div style={{ fontSize: 10, color: C.muted, marginTop: 4, lineHeight: 1.2 }}>{e.caption}</div>}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {detail.sign_off ? (
                <div style={{ padding: 12, background: detail.sign_off.status === "approved" ? C.greenLight : "var(--danger-soft)", borderRadius: 8 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                    <span style={{ fontSize: 12, fontWeight: 700, color: detail.sign_off.status === "approved" ? 'var(--ok)' : C.red }}>
                      {detail.sign_off.status === "approved" ? "✓ Approved" : "✗ Rejected"}
                    </span>
                    <span style={{ fontSize: 11, color: C.muted }}>{detail.sign_off.approved_at?.slice(0, 16).replace("T", " ")}</span>
                  </div>
                  {detail.sign_off.notes && <div style={{ fontSize: 12, color: C.text }}>{detail.sign_off.notes}</div>}
                  <div style={{ fontSize: 11, color: C.muted, marginTop: 4 }}>By: {detail.sign_off.approver}</div>
                </div>
              ) : task.status === "awaiting_sign_off" && (
                <SignOffActions taskId={task.id} onRefresh={() => { setDetail(null); onRefresh(); }} />
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function SignOffActions({ taskId, onRefresh }) {
  const { busy, notes, setNotes, sign } = useSignOff({ taskId, onRefresh });

  return (
    <div style={{ padding: 12, background: "var(--warn-soft)", borderRadius: 8, border: "1px solid var(--warn-soft)" }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: C.amber, marginBottom: 8 }}>Sign-off required</div>
      <textarea
        value={notes}
        onChange={e => setNotes(e.target.value)}
        placeholder="Add review notes (optional)..."
        style={{
          width: "100%", padding: 8, fontSize: 12, borderRadius: 6, border: "1px solid var(--warn-soft)",
          background: "var(--surface)", marginBottom: 10, boxSizing: "border-box"
        }}
      />
      <div style={{ display: "flex", gap: 8 }}>
        <Btn small onClick={() => sign("approved")} disabled={busy}>Approve</Btn>
        <Btn small variant="danger" onClick={() => sign("rejected")} disabled={busy}>Reject</Btn>
      </div>
    </div>
  );
}

// ─── Assign Task Panel ────────────────────────────────────────────────────────

function AssignTaskPanel({ tenantId }) {
  const { contractors, form, setForm, saving, msg, assign } = useAssignTask({ tenantId });

  return (
    <Card>
      <SectionHeader title="Assign Task" />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div>
          <ProjectSelect tenantId={tenantId} value={form.project_id} onChange={v => setForm(f => ({ ...f, project_id: v }))} />
          <Input label="Task title" value={form.title} onChange={v => setForm(f => ({ ...f, title: v }))} placeholder="Install roof trusses" />
          <Input label="Trade" value={form.trade} onChange={v => setForm(f => ({ ...f, trade: v }))} placeholder="carpenter" />
          <Input label="Due date" type="date" value={form.due_date} onChange={v => setForm(f => ({ ...f, due_date: v }))} placeholder="" />
        </div>
        <div>
          <label style={{ display: "block", marginBottom: 12 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: C.muted, marginBottom: 4 }}>Assign to contractor</div>
            <select
              value={form.contractor_id}
              onChange={e => setForm(f => ({ ...f, contractor_id: e.target.value }))}
              style={{
                width: "100%", padding: "8px 12px", fontSize: 13,
                border: `1px solid ${C.border}`, borderRadius: 6,
                background: C.bg, color: form.contractor_id ? C.text : C.muted,
              }}
            >
              <option value="">— optional —</option>
              {contractors.map(c => (
                <option key={c.id} value={c.id}>{c.name} ({c.trade})</option>
              ))}
            </select>
          </label>
          <label style={{ display: "block", marginBottom: 12 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: C.muted, marginBottom: 4 }}>Notes</div>
            <textarea
              value={form.notes}
              onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
              placeholder="Any special instructions…"
              rows={4}
              style={{
                width: "100%", boxSizing: "border-box",
                padding: "8px 12px", fontSize: 13, resize: "vertical",
                border: `1px solid ${C.border}`, borderRadius: 6,
                background: C.bg, color: C.text, outline: "none",
              }}
            />
          </label>
          <Btn onClick={assign} disabled={saving}>{saving ? "Saving…" : "Create & assign"}</Btn>
          {msg && <div style={{ marginTop: 8, fontSize: 12, color: C.muted }}>{msg}</div>}
        </div>
      </div>
    </Card>
  );
}

// ─── Walkthrough Panel ────────────────────────────────────────────────────────

function WalkthroughPanel({ tenantId }) {
  const { contractors, form, setForm, saving, msg, result, start } = useWalkthrough({ tenantId });

  return (
    <Card>
      <SectionHeader title="Site Walkthrough" />
      <div style={{ marginBottom: 12, fontSize: 13, color: C.muted }}>
        Send a structured photo checklist to a contractor via WhatsApp. They send photos for each item, then the architect approves.
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div>
          <ProjectSelect tenantId={tenantId} value={form.project_id} onChange={v => setForm(f => ({ ...f, project_id: v }))} />
          <Input label="Walkthrough title" value={form.title} onChange={v => setForm(f => ({ ...f, title: v }))} placeholder="Roof completion inspection" />
          <label style={{ display: "block", marginBottom: 12 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: C.muted, marginBottom: 4 }}>Contractor</div>
            <select
              value={form.contractor_id}
              onChange={e => setForm(f => ({ ...f, contractor_id: e.target.value }))}
              style={{
                width: "100%", padding: "8px 12px", fontSize: 13,
                border: `1px solid ${C.border}`, borderRadius: 6,
                background: C.bg, color: form.contractor_id ? C.text : C.muted,
              }}
            >
              <option value="">Select contractor…</option>
              {contractors.map(c => (
                <option key={c.id} value={c.id}>{c.name} ({c.trade})</option>
              ))}
            </select>
          </label>
        </div>
        <div>
          <label style={{ display: "block", marginBottom: 12 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: C.muted, marginBottom: 4 }}>
              Checklist items (one per line)
            </div>
            <textarea
              value={form.items}
              onChange={e => setForm(f => ({ ...f, items: e.target.value }))}
              placeholder={"Foundation slab\nBrickwork to window height\nRoof plate level\nWall ties installed"}
              rows={6}
              style={{
                width: "100%", boxSizing: "border-box",
                padding: "8px 12px", fontSize: 13, resize: "vertical",
                border: `1px solid ${C.border}`, borderRadius: 6,
                background: C.bg, color: C.text, outline: "none",
              }}
            />
          </label>
          <Btn onClick={start} disabled={saving}>{saving ? "Sending…" : "Start walkthrough"}</Btn>
          {msg && (
            <div style={{
              marginTop: 8, fontSize: 12,
              color: result?.whatsapp_sent ? 'var(--ok)' : C.muted,
            }}>{msg}</div>
          )}
          {result && (
            <div style={{
              marginTop: 10, padding: "10px 12px",
              background: C.greenLight, borderRadius: 8, fontSize: 12,
            }}>
              ID: <code style={{ fontFamily: "monospace" }}>{result.walkthrough_id}</code>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

// ─── Root component ───────────────────────────────────────────────────────────

function VulaFieldOpsV1({ tenantId }) {
  setFieldTenant(tenantId);
  const [tab, setTab] = useState("project");

  const TABS = [
    { id: "project", label: "Project Status" },
    { id: "assign", label: "Assign Task" },
    { id: "walkthrough", label: "Walkthrough" },
    { id: "briefings", label: "Briefings" },
    { id: "contractors", label: "Contractors" },
  ];

  return (
    <div style={{ padding: "0", maxWidth: 1100, margin: "0 auto" }}>
      {/* Header */}
      <div style={{ marginBottom: 24, display: "flex", alignItems: "center", gap: 20, flexWrap: "wrap" }}>
        <div>
          <h2 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 700, color: C.text, fontFamily: "var(--font-display)" }}>
            Field Operations
          </h2>
          <p style={{ margin: 0, fontSize: 13, color: C.muted }}>
            Assign tasks, track progress, dispatch daily briefings, and sign off walkthroughs via WhatsApp.
          </p>
        </div>
      </div>

      {!tenantId && (
        <div style={{
          padding: 20, background: "var(--warn-soft)", border: `1px solid var(--warn-soft)`,
          borderRadius: 8, marginBottom: 24, fontSize: 13, color: C.amber,
        }}>
          Error: No Tenant ID provided.
        </div>
      )}

      {/* Sub-tabs */}
      <div style={{ display: "flex", gap: 0, borderBottom: `1px solid ${C.border}`, marginBottom: 24 }}>
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} style={{
            padding: "10px 18px", border: "none", background: "none", cursor: "pointer",
            fontSize: 13, fontWeight: tab === t.id ? 600 : 400,
            color: tab === t.id ? C.green : C.muted,
            borderBottom: tab === t.id ? `2px solid ${C.green}` : "2px solid transparent",
          }}>{t.label}</button>
        ))}
      </div>

      {/* Panels */}
      {tab === "project" && <ProjectPanel tenantId={tenantId} />}
      {tab === "assign" && <AssignTaskPanel tenantId={tenantId} />}
      {tab === "walkthrough" && <WalkthroughPanel tenantId={tenantId} />}
      {tab === "briefings" && <DailyBriefingPanel tenantId={tenantId} />}
      {tab === "contractors" && <ContractorPanel tenantId={tenantId} />}
    </div>
  );
}

export default function VulaFieldOps({ v2 = false, ...props }) {
  return v2 ? <FieldOpsV2 {...props} /> : <VulaFieldOpsV1 {...props} />;
}
