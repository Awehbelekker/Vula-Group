import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";

const C = {
  bg: "#F7F4EE", surface: "#FFFFFF", border: "#DDD8CE",
  green: "var(--accent)", amber: "#C4861A", red: "#C0392B",
  blue: "#1A5276", text: "#2A2A2A", muted: "#8A8680",
  surfaceAlt: "#F0EDE5", greenLight: "#EAF2EF",
};

const STATUS_STYLE = {
  pending:            { bg: "#F0EDE5", color: "#8A8680", label: "Pending" },
  in_progress:        { bg: "#EAF2EF", color: "var(--accent)", label: "In Progress" },
  awaiting_sign_off:  { bg: "#FEF9E7", color: "#C4861A", label: "Awaiting Sign-off" },
  complete:           { bg: "#EAF2EF", color: "#1A7A4A", label: "Complete" },
  rejected:           { bg: "#FDEDEC", color: "#C0392B", label: "Rejected" },
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
      background: `${color}18`, color, borderRadius: 20,
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
    primary: { background: C.green, color: "#fff" },
    secondary: { background: C.surfaceAlt, color: C.text, border: `1px solid ${C.border}` },
    danger: { background: "#FDEDEC", color: C.red },
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

async function api(method, path, body) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(`${VULA_API}${path}`, opts);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

function ProjectSelect({ tenantId, value, onChange, label = "Project" }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!tenantId) return;
    setLoading(true);
    api("GET", `/v1/projects/${tenantId}`)
      .then(d => setProjects(d.projects || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [tenantId]);

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
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [dispatching, setDispatching] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    if (!tenantId) return;
    setLoading(true);
    try {
      const d = await api("GET", `/v1/field/daily-tasks/${tenantId}`);
      setData(d);
    } catch {}
    finally { setLoading(false); }
  }, [tenantId]);

  useEffect(() => { load(); }, [load]);

  const dispatch = async () => {
    setDispatching(true); setMsg("");
    try {
      const r = await api("POST", `/v1/field/daily-tasks/${tenantId}/dispatch`);
      setMsg(`Success: ${r.contractors_briefed} briefings sent.`);
    } catch (e) { setMsg(`Error: ${e.message}`); }
    finally { setDispatching(false); }
  };

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
  const [contractors, setContractors] = useState([]);
  const [form, setForm] = useState({ name: "", phone: "", trade: "" });
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    try {
      const d = await api("GET", `/v1/field/contractors/${tenantId}`);
      setContractors(d.contractors || []);
    } catch {}
  }, [tenantId]);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!form.name || !form.phone || !form.trade) return;
    setSaving(true);
    try {
      await api("POST", "/v1/field/contractors", { tenant_id: tenantId, ...form });
      setForm({ name: "", phone: "", trade: "" });
      setMsg("Contractor saved.");
      load();
      onRefresh?.();
    } catch (e) { setMsg(`Error: ${e.message}`); }
    finally { setSaving(false); }
  };

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
  const [projectId, setProjectId] = useState("");
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (id) => {
    const targetId = id || projectId;
    if (!targetId.trim()) return;
    setLoading(true); setError("");
    try {
      const d = await api("GET", `/v1/field/project/${targetId.trim()}/status`);
      setStatus(d);
    } catch (e) { setError(e.message); setStatus(null); }
    finally { setLoading(false); }
  }, [projectId]);

  const onProjectChange = (id) => {
    setProjectId(id);
    if (id) load(id);
    else setStatus(null);
  };

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
              { label: "Complete", value: status.tasks_complete, color: C.green },
              { label: "In Progress", value: status.tasks_in_progress, color: C.blue },
              { label: "Awaiting Sign-off", value: status.tasks_awaiting_sign_off, color: C.amber },
              { label: "Rejected", value: status.tasks_rejected, color: C.red },
            ].map(({ label, value, color }) => (
              <div key={label} style={{
                background: `${color}12`, border: `1px solid ${color}30`,
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
  const [requesting, setRequesting] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);

  const loadDetail = async () => {
    if (expanded) { setExpanded(false); return; }
    setExpanded(true);
    if (detail) return;
    setLoading(true);
    try {
      const d = await api("GET", `/v1/field/task/${task.id}`);
      setDetail(d);
    } catch {}
    finally { setLoading(false); }
  };

  const requestComplete = async (e) => {
    e.stopPropagation();
    setRequesting(true);
    try {
      await api("POST", `/v1/field/task/${task.id}/complete-request`, {});
    } finally { setRequesting(false); }
  };

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
                <div style={{ padding: 12, background: detail.sign_off.status === "approved" ? C.greenLight : "#FDEDEC", borderRadius: 8 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                    <span style={{ fontSize: 12, fontWeight: 700, color: detail.sign_off.status === "approved" ? C.green : C.red }}>
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
  const [busy, setBusy] = useState(false);
  const [notes, setNotes] = useState("");

  const sign = async (decision) => {
    setBusy(true);
    try {
      // For sign-off we use a contractor phone as approver phone (simulating manager)
      // In a real scenario, this would be the logged-in user's verified phone.
      await api("POST", `/v1/field/walkthrough/${taskId}/approve`, {
        approver_phone: "system-admin", // Placeholder
        decision,
        notes,
      });
      onRefresh();
    } catch (e) { alert(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={{ padding: 12, background: "#FEF9E7", borderRadius: 8, border: "1px solid #F9E79F" }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: C.amber, marginBottom: 8 }}>Sign-off required</div>
      <textarea
        value={notes}
        onChange={e => setNotes(e.target.value)}
        placeholder="Add review notes (optional)..."
        style={{
          width: "100%", padding: 8, fontSize: 12, borderRadius: 6, border: "1px solid #F9E79F",
          background: "#fff", marginBottom: 10, boxSizing: "border-box"
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
  const [contractors, setContractors] = useState([]);
  const [form, setForm] = useState({
    project_id: "", title: "", trade: "",
    contractor_id: "", due_date: "", notes: "",
  });
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (!tenantId) return;
    api("GET", `/v1/field/contractors/${tenantId}`)
      .then(d => setContractors(d.contractors || []))
      .catch(() => {});
  }, [tenantId]);

  const assign = async () => {
    if (!form.project_id || !form.title || !form.trade) return;
    setSaving(true); setMsg("");
    try {
      const task = await api("POST", "/v1/field/task", { tenant_id: tenantId, ...form });
      if (form.contractor_id) {
        await api("POST", "/v1/field/task/assign", {
          task_id: task.id,
          contractor_id: form.contractor_id,
          send_whatsapp: true,
        });
        setMsg(`Task created and WhatsApp sent to contractor.`);
      } else {
        setMsg(`Task created (not assigned yet).`);
      }
      setForm(f => ({ ...f, title: "", notes: "", contractor_id: "" }));
    } catch (e) { setMsg(`Error: ${e.message}`); }
    finally { setSaving(false); }
  };

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
  const [contractors, setContractors] = useState([]);
  const [form, setForm] = useState({ project_id: "", title: "", contractor_id: "", items: "" });
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    if (!tenantId) return;
    api("GET", `/v1/field/contractors/${tenantId}`)
      .then(d => setContractors(d.contractors || []))
      .catch(() => {});
  }, [tenantId]);

  const start = async () => {
    if (!form.project_id || !form.title || !form.contractor_id || !form.items.trim()) return;
    setSaving(true); setMsg(""); setResult(null);
    try {
      const items = form.items.split("\n").map(s => s.trim()).filter(Boolean);
      const d = await api("POST", "/v1/field/walkthrough/start", {
        tenant_id: tenantId,
        project_id: form.project_id,
        title: form.title,
        contractor_id: form.contractor_id,
        items,
      });
      setResult(d);
      setMsg(d.whatsapp_sent ? "Walkthrough started — WhatsApp sent." : "Walkthrough created (WhatsApp not configured).");
    } catch (e) { setMsg(`Error: ${e.message}`); }
    finally { setSaving(false); }
  };

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
              color: result?.whatsapp_sent ? C.green : C.muted,
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

export default function VulaFieldOps({ tenantId }) {
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
          <h2 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 700, color: C.text, fontFamily: "'Cormorant Garamond', serif" }}>
            Field Operations
          </h2>
          <p style={{ margin: 0, fontSize: 13, color: C.muted }}>
            Assign tasks, track progress, dispatch daily briefings, and sign off walkthroughs via WhatsApp.
          </p>
        </div>
      </div>

      {!tenantId && (
        <div style={{
          padding: 20, background: "#FEF9E7", border: `1px solid #F9E79F`,
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
