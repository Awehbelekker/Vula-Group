/**
 * FieldOpsV2.jsx — the redesigned Operate › Field ops (2026-10-20, UX redesign Phase F). Same calls
 * as VulaFieldOps (fieldops/useFieldOps.js): project status with each task's notes, photos and
 * sign-off; assigning a task (WhatsApp to the contractor when one is picked); site walkthroughs (a
 * photo checklist on WhatsApp); the morning briefings; and the contractor register. Every button
 * that messages someone says so, and sending all of today's briefings asks first.
 */
import { useState } from "react";
import { Check, ChevronDown, ChevronUp, ClipboardCheck, HardHat, MessageCircle, Plus, RefreshCw, Send, Sun, X } from "lucide-react";
import { SA_TZ } from "../../lib/dates";
import { Button, Card, EmptyState, Field, Input, PillTabs, Select, Skeleton, StatCard, StatusChip, Textarea, confirmDialog } from "../ui/index.jsx";
import { setFieldTenant, useAssignTask, useContractors, useDailyBriefing, useProjectOptions, useProjectStatus, useSignOff, useTaskRow, useWalkthrough } from "./useFieldOps";
import "../construction/construction.css";

const STATUS = {
  pending: ["Pending", "muted"], in_progress: ["In progress", "info"], awaiting_sign_off: ["Awaiting sign-off", "warn"],
  complete: ["Complete", "ok"], rejected: ["Rejected", "danger"],
};
const TABS = [
  { id: "project", label: "Project status" }, { id: "assign", label: "Assign a task" }, { id: "walkthrough", label: "Walkthrough" },
  { id: "briefings", label: "Briefings" }, { id: "contractors", label: "Contractors" },
];
const okMsg = (m) => m && !/^Error/.test(m);
const plain = (m) => String(m || "").replace(/^Success:\s*/, "");

export default function FieldOpsV2({ tenantId }) {
  setFieldTenant(tenantId);
  const [tab, setTab] = useState("project");
  if (!tenantId) return <EmptyState Icon={HardHat} title="No business selected">Open Field ops from a business's dashboard.</EmptyState>;
  return (
    <section className="vco" aria-label="Field operations">
      <p className="vcx-sub">Assign tasks, track progress, send the morning briefings and sign off site walkthroughs, all on WhatsApp.</p>
      <PillTabs label="Field ops" tabs={TABS} active={tab} onChange={setTab} />
      {tab === "project" && <ProjectStatus tenantId={tenantId} />}
      {tab === "assign" && <AssignTask tenantId={tenantId} />}
      {tab === "walkthrough" && <Walkthrough tenantId={tenantId} />}
      {tab === "briefings" && <Briefings tenantId={tenantId} />}
      {tab === "contractors" && <Contractors tenantId={tenantId} />}
    </section>
  );
}

function ProjectPick({ tenantId, value, onChange, label = "Project" }) {
  const { projects, loading } = useProjectOptions({ tenantId });
  return (
    <Field label={label}>
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{loading ? "Loading projects…" : "Choose a project"}</option>
        {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </Select>
    </Field>
  );
}

function ContractorPick({ contractors, value, onChange, label, optional }) {
  return (
    <Field label={label} hint={optional ? "Optional: pick one to WhatsApp them the task" : undefined}>
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{optional ? "Not yet" : "Choose a contractor"}</option>
        {contractors.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.trade})</option>)}
      </Select>
    </Field>
  );
}

function ProjectStatus({ tenantId }) {
  const x = useProjectStatus({ tenantId });
  const s = x.status;
  return (
    <div className="vco">
      <div style={{ maxWidth: 420 }}><ProjectPick tenantId={tenantId} value={x.projectId} onChange={x.onProjectChange} /></div>
      {x.error && <p className="vcx-error" role="alert">{x.error}</p>}
      {!x.projectId && <EmptyState Icon={ClipboardCheck} title="Pick a project">See its tasks, photos and what's waiting for your sign-off.</EmptyState>}
      {s && (
        <>
          <div className="vcx-tiles">
            <StatCard variant="tile" label="Complete" value={String(s.tasks_complete)} />
            <StatCard variant="tile" label="In progress" value={String(s.tasks_in_progress)} />
            <StatCard variant={s.tasks_awaiting_sign_off ? "hot" : "tile"} label="Awaiting sign-off" value={String(s.tasks_awaiting_sign_off)} />
            <StatCard variant="tile" label="Rejected" value={String(s.tasks_rejected)} />
          </div>
          <div className="vcx-meter" role="progressbar" aria-label="Project complete" aria-valuemin={0} aria-valuemax={100} aria-valuenow={s.completion_pct}><span style={{ width: `${s.completion_pct}%` }} /></div>
          <p className="vcx-muted">{s.completion_pct}% complete · {s.team_size} on the team</p>
          <h2 className="vcx-overline">Tasks</h2>
          {(s.tasks || []).length === 0 ? <p className="vcx-muted">No tasks on this project yet.</p>
            : (s.tasks || []).map((t) => <Task key={t.id} task={t} projectId={x.projectId} onRefresh={x.load} />)}
        </>)}
    </div>
  );
}

function Task({ task, projectId, onRefresh }) {
  const x = useTaskRow({ task, projectId, onRefresh });
  const [label, tone] = STATUS[task.status] || STATUS.pending;
  const id = `vcx-task-${task.id}`;
  const d = x.detail;
  return (
    <div className="vcx-task">
      <div className="vcx-task-head">
        <button type="button" className="vcx-task-toggle" aria-expanded={x.expanded} aria-controls={id} onClick={x.loadDetail}>
          <span className="vcx-grow"><b>{task.title}</b>
            <span className="vcx-muted">{[task.trade, task.due_date ? `due ${task.due_date}` : "no due date",
              task.evidence_count > 0 ? `${task.evidence_count} photo${task.evidence_count > 1 ? "s" : ""}` : null].filter(Boolean).join(" · ")}</span></span>
          {x.expanded ? <ChevronUp size={18} aria-hidden="true" /> : <ChevronDown size={18} aria-hidden="true" />}
        </button>
        <StatusChip tone={tone}>{label}</StatusChip>
        {task.status === "in_progress" && (
          <Button size="sm" variant="secondary" Icon={MessageCircle} disabled={x.requesting} aria-label={`Chase ${task.title} on WhatsApp`} onClick={() => x.requestComplete()}>
            {x.requesting ? "Sending…" : "Chase on WhatsApp"}</Button>)}
      </div>
      {x.expanded && (
        <div className="vcx-task-body" id={id}>
          {x.loading ? <Skeleton lines={2} /> : !d ? <p className="vcx-error" role="alert">Couldn't load this task's details.</p> : (
            <>
              {d.notes && <div><h3 className="vcx-overline" style={{ marginTop: 0 }}>Notes</h3><p className="vcx-body">{d.notes}</p></div>}
              {d.evidence?.length > 0 && (
                <div>
                  <h3 className="vcx-overline" style={{ marginTop: 0 }}>Photos from site</h3>
                  <ul className="vcx-photos" style={{ listStyle: "none", margin: 0, padding: 0 }}>
                    {d.evidence.map((e) => (
                      <li key={e.id}><a href={e.photo_url} target="_blank" rel="noreferrer"><img src={e.photo_url} alt={e.caption || `Photo for ${task.title}`} /></a>
                        {e.caption && <span className="vcx-muted">{e.caption}</span>}</li>))}
                  </ul>
                </div>)}
              {d.sign_off ? (
                <div className="vcx-signoff">
                  <div className="vcx-row"><StatusChip tone={d.sign_off.status === "approved" ? "ok" : "danger"}>{d.sign_off.status === "approved" ? "Approved" : "Rejected"}</StatusChip>
                    <span className="vcx-muted">{d.sign_off.approved_at ? new Date(d.sign_off.approved_at).toLocaleString("en-ZA", { timeZone: SA_TZ, dateStyle: "medium", timeStyle: "short" }) : ""} · by {d.sign_off.approver}</span></div>
                  {d.sign_off.notes && <p className="vcx-body">{d.sign_off.notes}</p>}
                </div>
              ) : task.status === "awaiting_sign_off" && <SignOff taskId={task.id} title={task.title} onRefresh={() => { x.setDetail(null); onRefresh(); }} />}
            </>)}
        </div>)}
    </div>
  );
}

function SignOff({ taskId, title, onRefresh }) {
  const x = useSignOff({ taskId, onRefresh });
  return (
    <div className="vcx-signoff" role="group" aria-label={`Sign off ${title}`}>
      <b>Sign-off needed</b>
      <Field label="Review notes" hint="Optional"><Textarea rows={2} value={x.notes} onChange={(e) => x.setNotes(e.target.value)} /></Field>
      <div className="vcx-row">
        <Button size="sm" Icon={Check} disabled={x.busy} onClick={() => x.sign("approved")}>Approve</Button>
        <Button size="sm" variant="secondary" Icon={X} className="vcx-danger" disabled={x.busy} onClick={() => x.sign("rejected")}>Reject</Button>
      </div>
    </div>
  );
}

function AssignTask({ tenantId }) {
  const x = useAssignTask({ tenantId });
  const f = x.form;
  const set = (k) => (e) => x.setForm((v) => ({ ...v, [k]: e.target.value }));
  const ready = f.project_id && f.title && f.trade;
  return (
    <Card className="vcx-card" role="group" aria-labelledby="vcx-assign">
      <h2 className="vcx-h" id="vcx-assign">Assign a task</h2>
      <form className="vcx-form" onSubmit={(e) => { e.preventDefault(); x.assign(); }}>
        <div className="vcx-grid">
          <ProjectPick tenantId={tenantId} value={f.project_id} onChange={(v) => x.setForm((o) => ({ ...o, project_id: v }))} />
          <Field label="Task"><Input value={f.title} onChange={set("title")} placeholder="Install roof trusses" /></Field>
          <Field label="Trade"><Input value={f.trade} onChange={set("trade")} placeholder="carpenter" /></Field>
          <Field label="Due date" hint="Optional"><Input type="date" value={f.due_date} onChange={set("due_date")} /></Field>
          <ContractorPick contractors={x.contractors} value={f.contractor_id} label="Contractor" optional onChange={(v) => x.setForm((o) => ({ ...o, contractor_id: v }))} />
        </div>
        <Field label="Notes" hint="Optional: special instructions"><Textarea rows={3} value={f.notes} onChange={set("notes")} /></Field>
        <div className="vcx-row">
          <Button type="submit" Icon={f.contractor_id ? Send : Plus} disabled={x.saving || !ready}>
            {x.saving ? "Saving…" : f.contractor_id ? "Create & send on WhatsApp" : "Create task"}</Button>
          {x.msg && <span className={okMsg(x.msg) ? "vcx-ok" : "vcx-error"} role="status">{x.msg}</span>}
        </div>
      </form>
    </Card>
  );
}

function Walkthrough({ tenantId }) {
  const x = useWalkthrough({ tenantId });
  const f = x.form;
  const set = (k) => (e) => x.setForm((v) => ({ ...v, [k]: e.target.value }));
  const ready = f.project_id && f.title && f.contractor_id && f.items.trim();
  return (
    <Card className="vcx-card" role="group" aria-labelledby="vcx-walk">
      <h2 className="vcx-h" id="vcx-walk">Site walkthrough</h2>
      <p className="vcx-muted">Sends a photo checklist to a contractor on WhatsApp. They send a photo for each item, then you approve.</p>
      <form className="vcx-form" onSubmit={(e) => { e.preventDefault(); x.start(); }}>
        <div className="vcx-grid">
          <ProjectPick tenantId={tenantId} value={f.project_id} onChange={(v) => x.setForm((o) => ({ ...o, project_id: v }))} />
          <Field label="Walkthrough"><Input value={f.title} onChange={set("title")} placeholder="Roof completion inspection" /></Field>
          <ContractorPick contractors={x.contractors} value={f.contractor_id} label="Contractor" onChange={(v) => x.setForm((o) => ({ ...o, contractor_id: v }))} />
        </div>
        <Field label="Checklist" hint="One item per line"><Textarea rows={5} value={f.items} onChange={set("items")} placeholder={"Foundation slab\nBrickwork to window height\nRoof plate level"} /></Field>
        <div className="vcx-row">
          <Button type="submit" Icon={Send} disabled={x.saving || !ready}>{x.saving ? "Sending…" : "Send walkthrough on WhatsApp"}</Button>
          {x.msg && <span className={okMsg(x.msg) ? (x.result?.whatsapp_sent ? "vcx-ok" : "vcx-warn") : "vcx-error"} role="status">{x.msg}</span>}
        </div>
        {x.result && <p className="vcx-muted">Walkthrough ID <code>{x.result.walkthrough_id}</code></p>}
      </form>
    </Card>
  );
}

function Briefings({ tenantId }) {
  const x = useDailyBriefing({ tenantId });
  const tasks = x.data?.tasks || [];
  const people = new Set(tasks.map((t) => t.contractor_id)).size;
  const send = async () => {
    if (await confirmDialog(`Send today's WhatsApp briefing to ${people} ${people === 1 ? "contractor" : "contractors"} now?`, { confirmLabel: "Send" })) x.dispatch();
  };
  return (
    <Card className="vcx-card" role="group" aria-labelledby="vcx-brief">
      <div className="vcx-row"><h2 className="vcx-h" id="vcx-brief" style={{ flex: 1 }}>Morning briefings</h2>
        <Button size="sm" variant="secondary" Icon={RefreshCw} disabled={x.loading} onClick={x.load}>Refresh</Button></div>
      <p className="vcx-muted">Every morning at 06:30 Vula WhatsApps each contractor the tasks they have due that day. Check them here, or send them now.</p>
      {x.loading ? <Skeleton lines={3} /> : tasks.length === 0
        ? <EmptyState Icon={Sun} title="No tasks due today">Nothing to brief anyone about.</EmptyState>
        : <>
            <p className="vcx-muted">{tasks.length} {tasks.length === 1 ? "task" : "tasks"} for {people} {people === 1 ? "contractor" : "contractors"}</p>
            <ul className="vcx-list" aria-label="Today's tasks">
              {tasks.map((t) => (
                <li key={t.task_id} className="vcx-item" style={{ paddingLeft: 0, paddingRight: 0 }}>
                  <span className="vcx-grow"><span><b>{t.contractor_name || "Unassigned"}</b>: {t.title}</span><span className="vcx-muted">{t.project_id}</span></span>
                </li>))}
            </ul>
            <div className="vcx-row">
              <Button Icon={Send} disabled={x.dispatching} onClick={send}>{x.dispatching ? "Sending…" : "Send briefings now"}</Button>
              {x.msg && <span className={okMsg(x.msg) ? "vcx-ok" : "vcx-error"} role="status">{plain(x.msg)}</span>}
            </div>
          </>}
    </Card>
  );
}

function Contractors({ tenantId }) {
  const x = useContractors({ tenantId });
  const f = x.form;
  const set = (k) => (e) => x.setForm((v) => ({ ...v, [k]: e.target.value }));
  return (
    <div className="vcx-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))" }}>
      <Card className="vcx-card" role="group" aria-labelledby="vcx-con-add">
        <h2 className="vcx-h" id="vcx-con-add">Add a contractor</h2>
        <form className="vcx-form" onSubmit={(e) => { e.preventDefault(); x.save(); }}>
          <Field label="Name"><Input value={f.name} onChange={set("name")} placeholder="Jane Dlamini" /></Field>
          <Field label="WhatsApp number"><Input type="tel" value={f.phone} onChange={set("phone")} placeholder="+27821234567" /></Field>
          <Field label="Trade"><Input value={f.trade} onChange={set("trade")} placeholder="electrician" /></Field>
          <div className="vcx-row">
            <Button type="submit" Icon={Plus} disabled={x.saving || !f.name || !f.phone || !f.trade}>{x.saving ? "Saving…" : "Add contractor"}</Button>
            {x.msg && <span className={okMsg(x.msg) ? "vcx-ok" : "vcx-error"} role="status">{x.msg}</span>}
          </div>
        </form>
      </Card>
      <Card className="vcx-card" role="group" aria-labelledby="vcx-con-list">
        <h2 className="vcx-h" id="vcx-con-list">{x.contractors.length} registered</h2>
        {x.contractors.length === 0 ? <p className="vcx-muted">No contractors yet. Add one to get started.</p> : (
          <ul className="vcx-list" aria-label="Contractors">
            {x.contractors.map((c) => (
              <li key={c.id} className="vcx-item" style={{ paddingLeft: 0, paddingRight: 0 }}>
                <span className="vcx-grow"><b>{c.name}</b><span className="vcx-muted">{c.phone}</span></span>
                <StatusChip outline>{c.trade}</StatusChip>
              </li>))}
          </ul>)}
      </Card>
    </div>
  );
}
